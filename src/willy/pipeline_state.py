"""
pipeline_state.py
=================
流水线状态机 —— 管理执行状态、写入共享状态文件供前端轮询。

状态流转:
  IDLE → RUNNING ⇄ RETRYING → RUNNING → DONE
  RUNNING / RETRYING → AWAITING_CONFIRMATION → RETRYING
  RUNNING / RETRYING → ESCALATED → ABORTED → AWAITING_CONFIRMATION
  RUNNING / RETRYING / AWAITING_CONFIRMATION → STOPPING → ABORTED
  非完成状态的执行或控制故障 → ESCALATED；人工处理后复查 → ABORTED，不自动续跑。
  确认调参方案创建独立子工程，不将父工程直接切换为 RUNNING。

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
import fcntl
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from willy._paths import get_project_root
from willy.step_registry import EQ_STEP, STEP_REGISTRY

STATUS_FILE = "status.json"
STATUS_LOCK_FILE = ".status.lock"


class State(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    RETRYING = "retrying"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    STOPPING = "stopping"
    ESCALATED = "escalated"
    DONE = "done"
    ABORTED = "aborted"


class StateTransitionError(ValueError):
    """Raised when a caller attempts to bypass the workflow state graph."""


class StateRevisionConflict(RuntimeError):
    """Raised when another control path has advanced a non-terminal status."""


_ALLOWED_TRANSITIONS: dict[State, frozenset[State]] = {
    # Recovery terminals are accepted here for a process that adopts an
    # already-failed run before its first local status write.
    State.IDLE: frozenset({
        State.RUNNING, State.RETRYING, State.AWAITING_CONFIRMATION,
        State.STOPPING, State.ESCALATED, State.ABORTED,
    }),
    State.RUNNING: frozenset({
        State.RETRYING, State.AWAITING_CONFIRMATION, State.STOPPING,
        State.ESCALATED, State.DONE, State.ABORTED,
    }),
    State.RETRYING: frozenset({
        State.RUNNING, State.AWAITING_CONFIRMATION, State.STOPPING,
        State.ESCALATED, State.ABORTED,
    }),
    State.AWAITING_CONFIRMATION: frozenset({
        State.RETRYING, State.STOPPING, State.ABORTED, State.ESCALATED,
    }),
    State.STOPPING: frozenset({State.ABORTED, State.ESCALATED}),
    State.ESCALATED: frozenset({State.ABORTED}),
    State.DONE: frozenset(),
    # A user may explicitly re-open an interrupted run through the bounded
    # Run Assistant control path.  It must first become awaiting_confirmation;
    # no direct aborted -> running/retrying transition is permitted.
    State.ABORTED: frozenset({State.AWAITING_CONFIRMATION, State.ESCALATED}),
}


def validate_state_transition(current: State | str, target: State | str) -> None:
    """Reject impossible status changes before they can be persisted."""
    try:
        current_state = current if isinstance(current, State) else State(current)
        target_state = target if isinstance(target, State) else State(target)
    except ValueError as exc:
        raise StateTransitionError("状态机收到未知状态") from exc
    if current_state == target_state:
        return
    if target_state not in _ALLOWED_TRANSITIONS[current_state]:
        raise StateTransitionError(
            f"非法状态流转: {current_state.value} -> {target_state.value}"
        )


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
    adjustments: list[dict[str, str]] = field(default_factory=list)  # 已应用的安全配置差异
    escalation: dict = field(default_factory=dict)     # escalation 详情
    fault: dict = field(default_factory=dict)
    started_at: str = ""              # ISO timestamp
    updated_at: str = ""              # ISO timestamp
    state_revision: int = 0           # Monotonic revision for compare-and-write control
    total_steps: int = STEP_REGISTRY.total_steps  # 总步骤数
    done_steps: list[int] = field(default_factory=list)  # 已完成的步骤 index
    activity: dict = field(default_factory=dict)         # 公开工序活动
    progress_detail: str = ""                              # 当前步骤的详细信息（如正在处理的分子名）
    extra: dict = field(default_factory=dict)              # 额外信息 (如 run_dir 用于断点续跑)


class PipelineStateMachine:
    """
    流水线状态机 —— 管理状态转换并写入 status.json。

    线程安全：通过原子写（临时文件 + rename）保证读一致性。
    """

    def __init__(self, total_steps: int = STEP_REGISTRY.total_steps,
                 on_update: Callable[[PipelineStatus, str], None] | None = None,
                 defer_writes: bool = False):
        self._path = get_project_root() / STATUS_FILE
        self._writes_enabled = not defer_writes
        self._total = total_steps
        self._on_update = on_update
        self._status = PipelineStatus(
            state=State.IDLE.value,
            total_steps=total_steps,
            started_at=_now(),
            updated_at=_now(),
        )
        if self._writes_enabled:
            self._write("status_initialized")

    def bind_observer(self, on_update: Callable[[PipelineStatus, str], None] | None) -> None:
        """Attach a best-effort observer for per-run audit persistence."""
        self._on_update = on_update

    def bind_status_path(self, path: str | Path) -> None:
        """Enable persistence only after the state belongs to one run directory."""
        self._path = Path(path)
        self._writes_enabled = True

    @property
    def total_steps(self) -> int:
        """Expose the configured total without requiring callers to read private state."""
        return self._total

    def set_extra(self, **kwargs) -> None:
        """Set structured metadata such as run_id without exposing private state."""
        self._status.extra.update(kwargs)
        self._status.updated_at = _now()
        self._write("run_bound")

    def restore_for_controlled_resume(self, snapshot: dict[str, object]) -> None:
        """Restore only validated public progress before a server-owned retry."""
        if isinstance(snapshot, dict):
            state = snapshot.get("state")
            if state in {item.value for item in State}:
                self._status.state = state
            revision = snapshot.get("state_revision", 0)
            if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0:
                self._status.state_revision = revision
            step = snapshot.get("step")
            if isinstance(step, int) and not isinstance(step, bool) and 0 <= step <= self._total:
                self._status.step = step
            for field in ("step_label", "layer", "error", "error_kind"):
                value = snapshot.get(field)
                if isinstance(value, str):
                    setattr(self._status, field, value[:240])
        done_steps = snapshot.get("done_steps", []) if isinstance(snapshot, dict) else []
        self._status.done_steps = sorted({
            step for step in done_steps
            if isinstance(step, int) and not isinstance(step, bool) and 0 < step <= self._total
        })
        extra = snapshot.get("extra", {}) if isinstance(snapshot, dict) else {}
        if isinstance(extra, dict) and isinstance(extra.get("run_id"), str):
            self._status.extra["run_id"] = extra["run_id"]
        pending_action = _safe_pending_action(extra.get("pending_action")) if isinstance(extra, dict) else {}
        if pending_action:
            self._status.extra["pending_action"] = pending_action
        self._status.updated_at = _now()

    def invalidate_for_controlled_restart(self, step: int) -> None:
        """Withdraw completed claims at and below a user-approved restart."""
        if isinstance(step, bool) or not isinstance(step, int) or not 1 <= step <= self._total:
            raise ValueError("受控重跑步骤无效")
        self._status.done_steps = [done for done in self._status.done_steps if done < step]
        self._status.extra.pop("pending_action", None)
        self._status.updated_at = _now()

    # ── 状态转换 ──

    def _set_state(self, state: State) -> None:
        validate_state_transition(self._status.state, state)
        if state == State.ESCALATED and self._status.state != State.ESCALATED.value:
            from willy.run_faults import FAULT_KINDS, new_fault
            kind = self._status.error_kind if self._status.error_kind in FAULT_KINDS else "unknown"
            phase = "stopping" if self._status.state == State.STOPPING.value else "recovery"
            self._status.fault = new_fault(self._status.__dict__, phase=phase, error_kind=kind)
        self._status.state = state.value

    def transition(self, state: State, **kwargs):
        """切换状态并写入文件。kwargs 会更新 status 的对应字段。"""
        self._set_state(state)
        self._status.updated_at = _now()
        for k, v in kwargs.items():
            if hasattr(self._status, k):
                setattr(self._status, k, v)
        # A deliberately scoped run (for example an EQ-only acceptance trial)
        # carries its last completed step explicitly.  Normal full runs retain
        # the historic terminal step ``total_steps + 1``.
        if state == State.DONE and "step" not in kwargs:
            self._status.step = self._total + 1
        event_type = {
            State.DONE: "run_finished",
            State.STOPPING: "run_stop_requested",
            State.ABORTED: "run_aborted",
            State.ESCALATED: "run_escalated",
        }.get(state, "state_changed")
        self._write(event_type)

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
        self._status.adjustments = []
        self._status.activity = {}
        self._status.progress_detail = ""
        self._status.extra.pop("pending_action", None)
        self._status.updated_at = _now()
        self._write("step_started")

    def set_activity(
        self,
        tool: str,
        operation: str,
        target_type: str,
        target: str,
        current: int,
        total: int,
    ) -> None:
        """Set the strictly structured, user-visible activity snapshot.

        This method deliberately accepts a fixed signature so arbitrary
        engine details cannot accidentally enter ``status.json``.
        """
        allowed_target_types = {"molecule", "stage", "system"}
        if target_type not in allowed_target_types:
            raise ValueError(f"activity.target_type 无效: {target_type}")
        if not all(isinstance(value, str) and value.strip() for value in (tool, operation, target)):
            raise ValueError("activity 的 tool、operation、target 必须是非空字符串")
        if isinstance(current, bool) or isinstance(total, bool) or not isinstance(current, int) or not isinstance(total, int):
            raise ValueError("activity 的 current、total 必须是整数")
        if total < 0 or current < 0 or current > total:
            raise ValueError("activity 的进度范围无效")
        self._status.activity = {
            "tool": tool.strip(),
            "operation": operation.strip(),
            "target_type": target_type,
            "target": target.strip(),
            "current": current,
            "total": total,
        }
        self._status.updated_at = _now()
        self._write("activity_updated")

    def heartbeat(self) -> None:
        """Refresh the current public state while a long-running engine is alive.

        The caller must not infer scientific progress from this timestamp. It
        only proves that the supervised process is still being observed.
        """
        self._status.updated_at = _now()
        self._write("runtime_heartbeat")

    def mark_done(self, step: int):
        """标记步骤成功完成。"""
        if step not in self._status.done_steps:
            self._status.done_steps.append(step)
        self._status.updated_at = _now()
        self._write("step_succeeded")

    def rollback_to(self, step: int, label: str, layer: str, reason: str) -> None:
        """Invalidate a failed stage and its downstream steps before rerunning.

        Rollback is a first-class state event rather than a silent loop jump so
        both the UI and the per-run audit show why the workflow returned to an
        earlier stage.
        """
        self._status.done_steps = [done for done in self._status.done_steps if done < step]
        self._set_state(State.RUNNING)
        self._status.step = step
        self._status.step_label = label
        self._status.layer = layer
        self._status.error = ""
        self._status.error_kind = ""
        self._status.agent = ""
        self._status.retry_n = 0
        self._status.retry_max = 0
        self._status.actions.append(f"回滚到 Step {step}: {reason}")
        self._status.progress_detail = ""
        self._status.activity = {}
        self._status.updated_at = _now()
        self._write("step_rolled_back")

    def set_detail(self, detail: str = ""):
        """更新当前步骤的详细信息（如正在处理的分子名）。"""
        self._status.progress_detail = detail
        self._status.updated_at = _now()
        self._write("status_detail")

    def set_error(self, message: str, kind: str = "", summary: str | None = None):
        """记录当前步骤的错误。不清除 retry 上下文。"""
        self._status.error = summary if summary is not None else _safe_public_error(message)
        self._status.error_kind = kind
        self._status.updated_at = _now()
        self._write("step_failed")

    def start_retry(self, agent: str, retry_n: int, retry_max: int):
        """进入 RETRYING 状态。不清空 actions，允许跨重试累积动作历史。"""
        self._set_state(State.RETRYING)
        self._status.agent = agent
        self._status.retry_n = retry_n
        self._status.retry_max = retry_max
        self._status.updated_at = _now()
        self._write("agent_retrying")

    def add_action(self, action: str):
        """追加一条 Agent 动作记录。"""
        self._status.actions.append(action)
        self._status.updated_at = _now()
        self._write("agent_action")

    def add_adjustments(self, adjustments: list[dict[str, object]]) -> None:
        """Record applied, user-safe configuration changes for the active repair.

        This deliberately accepts only a small structured vocabulary.  Agent
        prose, tool arguments, engine output, and file paths remain internal.
        """
        safe_adjustments: list[dict[str, str]] = []
        for adjustment in adjustments:
            if not isinstance(adjustment, dict):
                continue
            name = _safe_adjustment_text(adjustment.get("name"))
            before = _safe_adjustment_text(adjustment.get("before"))
            after = _safe_adjustment_text(adjustment.get("after"))
            if name and before and after and before != after:
                safe_adjustments.append({"name": name, "before": before, "after": after})
        if not safe_adjustments:
            return
        self._status.adjustments = safe_adjustments[-8:]
        self._status.updated_at = _now()
        self._write("agent_adjusted_config")

    def set_escalated(self, escalation: dict):
        """进入 ESCALATED 状态。"""
        self._set_state(State.ESCALATED)
        # Retry counters describe an active repair attempt only. Once the run
        # is escalated, the durable escalation payload is the sole source of
        # truth for attempts made; retaining retrying metadata misleads the UI.
        self._status.agent = ""
        self._status.retry_n = 0
        self._status.retry_max = 0
        # Raw engine output is intentionally retained only in the in-memory
        # LayerAgent context, never in the public status snapshot.
        allowed = {
            "layer", "step", "error_kind", "attempts_made",
            "actions_tried", "recommendation", "backup_plan",
        }
        self._status.escalation = {
            key: value for key, value in (escalation or {}).items()
            if key in allowed
        }
        self._status.updated_at = _now()
        self._write("run_escalated")

    def set_awaiting_confirmation(self, pending_action: dict[str, object]) -> None:
        """Hold a failed run until a server-validated user approval arrives."""
        safe_action = _safe_pending_action(pending_action)
        if not safe_action:
            raise ValueError("待确认动作缺少公开摘要")
        self._set_state(State.AWAITING_CONFIRMATION)
        self._status.agent = ""
        self._status.retry_n = 0
        self._status.retry_max = 0
        self._status.escalation = {}
        self._status.extra["pending_action"] = safe_action
        self._status.updated_at = _now()
        self._write("run_awaiting_confirmation")

    def set_aborted(self, *, user_requested: bool = False):
        """Enter ``aborted``, clearing stale failure details for a user stop."""
        self._set_state(State.ABORTED)
        if user_requested:
            self._status.error = ""
            self._status.error_kind = ""
            self._status.agent = ""
            self._status.retry_n = 0
            self._status.retry_max = 0
            self._status.actions = []
            self._status.adjustments = []
            self._status.escalation = {}
        self._status.updated_at = _now()
        self._write("run_aborted")

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

    def _write(self, event_type: str = "status_updated"):
        """原子写入 status.json。"""
        if self._writes_enabled:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._status_lock():
                persisted = self._read_persisted_payload()
                if persisted is not None:
                    persisted_revision = _state_revision(persisted.get("state_revision"))
                    if persisted_revision != self._status.state_revision:
                        persisted_state = persisted.get("state")
                        if (
                            persisted_state == State.STOPPING.value
                            and self._status.state == State.ABORTED.value
                        ):
                            # A stop request advanced the status externally;
                            # the runner may only finalize that request.
                            self._status.state_revision = persisted_revision
                        elif persisted_state in {State.STOPPING.value, State.ABORTED.value, State.ESCALATED.value}:
                            self._adopt_persisted_status(persisted)
                            return
                        else:
                            raise StateRevisionConflict(
                                "状态快照已被其他控制路径更新"
                            )
                self._status.state_revision += 1
                d = self._public_payload()
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
                os.replace(tmp, self._path)
        if self._on_update is not None:
            try:
                self._on_update(self._status, event_type)
            except Exception as exc:
                # Audit persistence cannot make the scientific pipeline fail.
                print(f"[pipeline_state] ⚠ 运行审计写入失败: {exc}")

    @contextmanager
    def _status_lock(self):
        """Hold the shared run-local lock used by status CAS writers."""
        path = self._path.parent / STATUS_LOCK_FILE
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read_persisted_payload(self) -> dict | None:
        if not self._path.is_file():
            return None
        try:
            payload = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _adopt_persisted_status(self, payload: dict) -> None:
        """Accept an external stop snapshot without writing over it."""
        fields = PipelineStatus.__dataclass_fields__
        values = {key: value for key, value in payload.items() if key in fields}
        revision = _state_revision(values.get("state_revision"))
        values["state_revision"] = revision
        state = values.get("state")
        if state not in {State.STOPPING.value, State.ABORTED.value, State.ESCALATED.value}:
            raise StateRevisionConflict("外部状态快照无效")
        self._status = PipelineStatus(**values)

    def _public_payload(self) -> dict:
        """Return the safe state persisted for UI polling and run audit."""
        from willy.run_faults import public_fault
        status = self._status
        extra = {}
        if status.extra.get("run_id"):
            extra["run_id"] = status.extra["run_id"]
        pending_action = _safe_pending_action(status.extra.get("pending_action"))
        if pending_action:
            extra["pending_action"] = pending_action
        completion_scope = _safe_completion_scope(status.extra.get("completion_scope"))
        if completion_scope:
            extra["completion_scope"] = completion_scope
        return {
            "state": status.state,
            **({"fault": public_fault(status.fault)} if public_fault(status.fault) else {}),
            "step": status.step,
            "step_label": status.step_label,
            "layer": status.layer,
            "error": status.error,
            "error_kind": status.error_kind,
            "repair": _public_repair(status),
            "escalation": _public_escalation(status.escalation),
            "activity": dict(status.activity),
            "started_at": status.started_at,
            "updated_at": status.updated_at,
            "state_revision": status.state_revision,
            "total_steps": status.total_steps,
            "done_steps": list(status.done_steps),
            "extra": extra,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_revision(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _safe_public_error(message: object) -> str:
    """Keep legacy callers from writing engine output into public state."""
    text = str(message or "")
    lowered = text.lower()
    sensitive_markers = (
        "raw_output", "stderr", "stdout", "traceback", "command line",
        "command=", " /", "\\", "--",
    )
    if any(marker in lowered for marker in sensitive_markers):
        return "执行失败"
    return text[:240]


def _safe_adjustment_text(value: object) -> str:
    """Keep public configuration values compact and free of path-like text."""
    text = str(value if value is not None else "").replace("\n", " ").replace("\r", " ").strip()
    if not text or "/" in text or "\\" in text or ".." in text:
        return ""
    return text[:80]


def _safe_knowledge_source(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    status = value.get("knowledge_status")
    source = value.get("advice_source")
    if status not in {"retrieved", "not_matched", "unavailable"} or source not in {"knowledge_base", "llm_unverified"}:
        return {}
    entries = []
    for item in value.get("knowledge_entries", []) if isinstance(value.get("knowledge_entries"), list) else []:
        if len(entries) >= 3 or not isinstance(item, dict) or isinstance(item.get("number"), bool):
            continue
        try:
            number = int(item.get("number"))
        except (TypeError, ValueError):
            continue
        name = _safe_adjustment_text(item.get("name"))
        if name:
            entries.append({"number": number, "name": name})
    return {
        "knowledge_status": status,
        "knowledge_entries": entries,
        "advice_source": source,
        "compatibility_notice": _safe_adjustment_text(value.get("compatibility_notice"))[:300],
    }


def _safe_recovery_plan(value: object) -> dict:
    """Keep the mandatory EQ recovery explanation bounded for status.json."""
    if not isinstance(value, dict):
        return {}

    def points(raw: object, *, limit: int = 6) -> list[str]:
        items = raw if isinstance(raw, list) else []
        result: list[str] = []
        for item in items[:limit]:
            text = _safe_adjustment_text(item)
            if text and text not in result:
                result.append(text)
        return result

    def adjustments(raw: object) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict) or len(result) >= 8:
                continue
            candidate = {
                key: _safe_adjustment_text(item.get(key))
                for key in ("name", "before", "after")
            }
            purpose = _safe_adjustment_text(item.get("purpose"))
            if all(candidate.values()):
                if purpose:
                    candidate["purpose"] = purpose
                result.append(candidate)
        return result

    def path(raw: object) -> dict:
        if not isinstance(raw, dict) or raw.get("applicable") is not True:
            summary = _safe_adjustment_text(raw.get("summary")) if isinstance(raw, dict) else ""
            return {
                "applicable": False,
                "summary": summary or "当前证据不足以提供可执行方案。",
                "evidence": points(raw.get("evidence")) if isinstance(raw, dict) else [],
            }
        options = []
        entries = raw.get("options", []) if isinstance(raw.get("options"), list) else []
        for item in entries[:3]:
            if not isinstance(item, dict):
                continue
            restart = item.get("restart_step")
            summary = _safe_adjustment_text(item.get("summary"))
            if (
                isinstance(restart, bool)
                or not isinstance(restart, int)
                or not STEP_REGISTRY.controlled_restart_allowed(EQ_STEP, restart)
                or not summary
            ):
                continue
            options.append({
                "title": _safe_adjustment_text(item.get("title")) or "恢复方案",
                "summary": summary,
                "restart_step": restart,
                "adjustments": adjustments(item.get("adjustments")),
                "evidence": points(item.get("evidence")),
            })
        if not options:
            return {
                "applicable": False,
                "summary": "当前证据不足以提供可执行方案。",
                "evidence": [],
            }
        return {"applicable": True, "options": options}

    problem = _safe_adjustment_text(value.get("problem"))
    if not problem:
        return {}
    return {
        "problem": problem,
        "current_step_retry": path(value.get("current_step_retry")),
        "upstream_retry": path(value.get("upstream_retry")),
        "evidence": points(value.get("evidence")),
        # EQ recovery always crosses the scientific-protocol confirmation
        # boundary, regardless of the text returned by a model.
        "risk_level": "high",
        "manual_review_required": True,
    }


def _safe_completion_scope(value: object) -> dict[str, object]:
    """Expose only the fixed, non-executable EQ-only acceptance scope."""
    if not isinstance(value, dict):
        return {}
    if (
        value.get("mode") == "through_eq"
        and value.get("stage") == "eq"
        and value.get("step") == EQ_STEP
    ):
        return {"mode": "through_eq", "step": EQ_STEP, "stage": "eq"}
    return {}


def _safe_pending_action(value: object) -> dict:
    """Project the pending repair to a compact, UI-safe public contract."""
    if not isinstance(value, dict):
        return {}
    action_id = _safe_adjustment_text(value.get("action_id"))
    state = value.get("state")
    step_label = _safe_adjustment_text(value.get("step_label"))
    summary = _safe_adjustment_text(value.get("summary"))
    restart_step = value.get("restart_step")
    if (
        not action_id
        or state not in {"pending", "awaiting_confirmation"}
        or not step_label
        or not summary
        or isinstance(restart_step, bool)
        or not isinstance(restart_step, int)
        or not STEP_REGISTRY.controlled_restart_allowed(EQ_STEP, restart_step)
    ):
        return {}
    adjustments = []
    raw_adjustments = value.get("adjustments", [])
    if isinstance(raw_adjustments, list):
        for raw in raw_adjustments[:8]:
            if not isinstance(raw, dict):
                continue
            name = _safe_adjustment_text(raw.get("name"))
            before = _safe_adjustment_text(raw.get("before"))
            after = _safe_adjustment_text(raw.get("after"))
            purpose = _safe_adjustment_text(raw.get("purpose") or raw.get("reason"))
            if name and before and after:
                item = {"name": name, "before": before, "after": after}
                if purpose:
                    item["purpose"] = purpose
                adjustments.append(item)
    public = {
        "action_id": action_id,
        "state": "pending",
        "step_label": step_label,
        "restart_step": restart_step,
        "summary": summary,
        "adjustments": adjustments,
    }
    public.update(_safe_knowledge_source(value))
    recovery_plan = _safe_recovery_plan(value.get("recovery_plan"))
    if recovery_plan:
        public["recovery_plan"] = recovery_plan
    if isinstance(value.get("selected_option_id"), str):
        public["selected_option_id"] = value["selected_option_id"]
    public["selection_required"] = bool(value.get("selection_required", False))
    editable_parameters = []
    for raw in value.get("editable_parameters", []) if isinstance(value.get("editable_parameters"), list) else []:
        if not isinstance(raw, dict) or len(editable_parameters) >= 8:
            continue
        item = {key: _safe_adjustment_text(raw.get(key)) for key in ("name", "current", "range")}
        purpose = _safe_adjustment_text(raw.get("purpose"))
        if all(item.values()):
            if purpose:
                item["purpose"] = purpose
            editable_parameters.append(item)
    if editable_parameters:
        public["editable_parameters"] = editable_parameters
    options = []
    for ordinal, option in enumerate(value.get("options", []) if isinstance(value.get("options"), list) else [], 1):
        if not isinstance(option, dict) or len(options) >= 3:
            continue
        title = _safe_adjustment_text(option.get("title")) or f"方案{ordinal}"
        cause = _safe_adjustment_text(option.get("cause"))
        evidence = _safe_adjustment_text(option.get("evidence"))
        option_summary = _safe_adjustment_text(option.get("summary"))
        option_restart = option.get("restart_step")
        if not option_summary or not isinstance(option_restart, int) or not STEP_REGISTRY.controlled_restart_allowed(EQ_STEP, option_restart):
            continue
        option_adjustments = []
        for raw in option.get("adjustments", []) if isinstance(option.get("adjustments"), list) else []:
            if not isinstance(raw, dict) or len(option_adjustments) >= 8:
                continue
            item = {key: _safe_adjustment_text(raw.get(key)) for key in ("name", "before", "after")}
            purpose = _safe_adjustment_text(raw.get("purpose") or raw.get("reason"))
            if all(item.values()):
                if purpose:
                    item["purpose"] = purpose
                option_adjustments.append(item)
        option_public = {
            "option_id": _safe_adjustment_text(option.get("option_id")) or f"option_{ordinal}",
            "ordinal": ordinal,
            "title": title,
            "cause": cause,
            "evidence": evidence,
            "summary": option_summary,
            "restart_step": option_restart,
            "adjustments": option_adjustments,
            "editable_parameters": [
                {
                    **{key: _safe_adjustment_text(raw.get(key)) for key in ("name", "current", "range")},
                    **({"purpose": _safe_adjustment_text(raw.get("purpose"))} if _safe_adjustment_text(raw.get("purpose")) else {}),
                }
                for raw in option.get("editable_parameters", [])[:8]
                if isinstance(raw, dict)
                and all(_safe_adjustment_text(raw.get(key)) for key in ("name", "current", "range"))
            ],
        }
        option_public.update(_safe_knowledge_source(option))
        options.append(option_public)
    if len(options) > 1:
        public["options"] = options
    return public


def _public_repair(status: PipelineStatus) -> dict:
    """Return the bounded repair state that is safe for UI polling."""
    adjustments = []
    for adjustment in status.adjustments[-8:]:
        if not isinstance(adjustment, dict):
            continue
        name = _safe_adjustment_text(adjustment.get("name"))
        before = _safe_adjustment_text(adjustment.get("before"))
        after = _safe_adjustment_text(adjustment.get("after"))
        if name and before and after and before != after:
            adjustments.append({"name": name, "before": before, "after": after})
    if status.state != State.RETRYING.value and not adjustments:
        return {}
    return {
        "attempt": max(0, int(status.retry_n)),
        "max_attempts": max(0, int(status.retry_max)),
        "adjustments": adjustments,
    }


def _public_escalation(value: object) -> dict:
    """Project a recovery conclusion without exposing logs, paths, or prompts."""
    if not isinstance(value, dict):
        return {}

    def clean(item: object, limit: int) -> str:
        text = str(item or "").replace("\n", " ").replace("\r", " ").strip()
        if not text or "/" in text or "\\" in text or ".." in text:
            return ""
        return text[:limit]

    attempts = value.get("attempts_made", 0)
    if isinstance(attempts, bool):
        attempts = 0
    try:
        attempts = max(0, min(int(attempts), 99))
    except (TypeError, ValueError):
        attempts = 0
    actions = [
        text for text in (clean(item, 160) for item in value.get("actions_tried", []))
        if text
    ][:8] if isinstance(value.get("actions_tried"), list) else []
    public = {
        "layer": clean(value.get("layer"), 80),
        "step": clean(value.get("step"), 120),
        "error_kind": clean(value.get("error_kind"), 80),
        "attempts_made": attempts,
        "actions_tried": actions,
        "recommendation": clean(value.get("recommendation"), 300),
        "backup_plan": clean(value.get("backup_plan"), 300),
    }
    return {key: item for key, item in public.items() if item not in ("", [])}
