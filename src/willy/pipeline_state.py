"""
pipeline_state.py
=================
流水线状态机 —— 管理执行状态、写入共享状态文件供前端轮询。

状态流转:
  IDLE → RUNNING → RETRYING → RUNNING → ... → DONE
                      ↓                    ↑
                   ESCALATED ───────────────┘
                      ↓
                   ABORTED

用法:
  sm = PipelineStateMachine()
  sm.transition(State.RUNNING, step=1, label="g16 优化")
  sm.set_error("TFSI: SCF 不收敛")
  sm.transition(State.RETRYING, agent="quantum", retry_n=1)
  sm.add_action("换基组为 6-31g(d)")
  sm.transition(State.DONE)

前端通过读 STATUS_PATH (status.json) 获取实时状态。
"""

from __future__ import annotations
import json
import os
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

from willy._paths import get_project_root

STATUS_FILE = "status.json"


class State(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    RETRYING = "retrying"
    ESCALATED = "escalated"
    DONE = "done"
    ABORTED = "aborted"


@dataclass
class PipelineStatus:
    """写入 status.json 的完整状态对象。"""
    state: str = State.IDLE.value
    step: int = 0                     # 当前步骤 index (1-10)
    step_label: str = ""              # 步骤描述
    layer: str = ""                   # "config" | "quantum" | "topology" | "simulation"
    error: str = ""                   # 最近的错误消息
    error_kind: str = ""              # ErrorKind 值
    agent: str = ""                   # 当前 Agent 名（仅 retrying 时）
    retry_n: int = 0                  # 当前重试次数
    retry_max: int = 0                # 最大重试次数
    actions: list[str] = field(default_factory=list)  # Agent 已尝试的动作
    escalation: dict = field(default_factory=dict)     # escalation 详情
    started_at: str = ""              # ISO timestamp
    updated_at: str = ""              # ISO timestamp
    total_steps: int = 7              # 总步骤数
    done_steps: list[int] = field(default_factory=list)  # 已完成的步骤 index
    progress_detail: str = ""                              # 当前步骤的详细信息（如正在处理的分子名）
    extra: dict = field(default_factory=dict)              # 额外信息 (如 run_dir 用于断点续跑)


class PipelineStateMachine:
    """
    流水线状态机 —— 管理状态转换并写入 status.json。

    线程安全：通过原子写（临时文件 + rename）保证读一致性。
    """

    def __init__(self, total_steps: int = 7):
        self._path = get_project_root() / STATUS_FILE
        self._total = total_steps
        self._status = PipelineStatus(
            state=State.IDLE.value,
            total_steps=total_steps,
            started_at=_now(),
            updated_at=_now(),
        )
        self._write()

    # ── 状态转换 ──

    def transition(self, state: State, **kwargs):
        """切换状态并写入文件。kwargs 会更新 status 的对应字段。"""
        self._status.state = state.value
        self._status.updated_at = _now()
        for k, v in kwargs.items():
            if hasattr(self._status, k):
                setattr(self._status, k, v)
        if state == State.DONE:
            self._status.step = self._total + 1
        self._write()

    def set_step(self, step: int, label: str, layer: str = ""):
        """设置当前步骤。"""
        self._status.step = step
        self._status.step_label = label
        self._status.layer = layer
        self._status.error = ""
        self._status.error_kind = ""
        self._status.agent = ""
        self._status.retry_n = 0
        self._status.retry_max = 0
        self._status.actions = []
        self._write()

    def mark_done(self, step: int):
        """标记步骤成功完成。"""
        if step not in self._status.done_steps:
            self._status.done_steps.append(step)
        self._write()

    def set_detail(self, detail: str = ""):
        """更新当前步骤的详细信息（如正在处理的分子名）。"""
        self._status.progress_detail = detail
        self._status.updated_at = _now()
        self._write()

    def set_error(self, message: str, kind: str = ""):
        """记录当前步骤的错误。不清除 retry 上下文。"""
        self._status.error = message
        self._status.error_kind = kind
        self._status.updated_at = _now()
        self._write()

    def start_retry(self, agent: str, retry_n: int, retry_max: int):
        """进入 RETRYING 状态。不清空 actions，允许跨重试累积动作历史。"""
        self._status.state = State.RETRYING.value
        self._status.agent = agent
        self._status.retry_n = retry_n
        self._status.retry_max = retry_max
        self._status.updated_at = _now()
        self._write()

    def add_action(self, action: str):
        """追加一条 Agent 动作记录。"""
        self._status.actions.append(action)
        self._status.updated_at = _now()
        self._write()

    def set_escalated(self, escalation: dict):
        """进入 ESCALATED 状态。"""
        self._status.state = State.ESCALATED.value
        self._status.escalation = escalation
        self._status.updated_at = _now()
        self._write()

    def set_aborted(self):
        """进入 ABORTED 状态（用户手动中止）。"""
        self._status.state = State.ABORTED.value
        self._status.updated_at = _now()
        self._write()

    # ── 读取 ──

    @staticmethod
    def read() -> PipelineStatus:
        """静态方法：读取当前状态。供前端轮询。"""
        path = get_project_root() / STATUS_FILE
        if not path.exists():
            return PipelineStatus(state=State.IDLE.value)
        try:
            data = json.loads(path.read_text())
            return PipelineStatus(**{k: v for k, v in data.items() if k in PipelineStatus.__dataclass_fields__})
        except (json.JSONDecodeError, TypeError):
            return PipelineStatus(state=State.IDLE.value)

    # ── 内部 ──

    def _write(self):
        """原子写入 status.json。"""
        d = asdict(self._status)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, self._path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
