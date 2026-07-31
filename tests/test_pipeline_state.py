"""
test_pipeline_state.py —— PipelineStateMachine 测试。

测试状态转换、原子写入、status.json 序列化、前端轮询安全性。
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from willy.pipeline_state import (
    State, PipelineStatus, PipelineStateMachine,
)


# ============================================================
# State 枚举
# ============================================================

class TestStateEnum:
    """State 枚举完整测试。"""

    def test_all_states_defined(self):
        expected = {"idle", "running", "retrying", "escalated", "done", "aborted"}
        actual = {s.value for s in State}
        assert actual == expected

    def test_state_values_are_strings(self):
        for s in State:
            assert isinstance(s.value, str)

    def test_state_transitions_well_defined(self):
        """典型状态流转: IDLE → RUNNING → DONE"""
        for s in State:
            json.dumps(s.value)


# ============================================================
# PipelineStatus 数据类
# ============================================================

class TestPipelineStatus:
    """PipelineStatus 数据类测试。"""

    def test_default_values(self):
        ps = PipelineStatus()
        assert ps.state == "idle"  # default is State.IDLE.value = "idle"
        assert ps.step == 0
        assert ps.total_steps == 7
        assert ps.step_label == ""
        assert ps.layer == ""
        assert ps.error == ""
        assert ps.error_kind == ""
        assert ps.escalation == {}
        assert ps.actions == []

    def test_all_fields_serializable(self):
        """所有字段应对 JSON 可序列化。"""
        ps = PipelineStatus(
            state="running",
            step=3,
            step_label="RESP 电荷",
            layer="quantum",
            error="SCF 未收敛",
            error_kind="scf_not_converged",
            total_steps=7,
        )
        d = ps.__dict__
        serialized = json.dumps(d, default=str)
        assert len(serialized) > 0

    def test_escalation_field_defaults_to_dict(self):
        ps = PipelineStatus(state="escalated")
        assert isinstance(ps.escalation, dict)

    def test_escalation_field_with_dict(self):
        ps = PipelineStatus(
            state="escalated",
            escalation={
                "layer": "quantum",
                "step": "struct_maker",
                "error_kind": "scf_not_converged",
                "attempts_made": 5,
                "actions_tried": ["retry1", "retry2"],
                "recommendation": "手动检查",
                "backup_plan": "更换基组",
            }
        )
        assert ps.escalation["layer"] == "quantum"
        assert ps.escalation["attempts_made"] == 5

    def test_actions_list_accumulation(self):
        ps = PipelineStatus(actions=[])
        ps.actions.append("retry: scf=xqc")
        ps.actions.append("fallback: 更换基组")
        assert len(ps.actions) == 2

    def test_error_field(self):
        """error 字段存储最近的错误消息。"""
        ps = PipelineStatus(error="Gaussian 崩溃", error_kind="gaussian_crash")
        assert ps.error == "Gaussian 崩溃"
        assert ps.error_kind == "gaussian_crash"


# ============================================================
# PipelineStateMachine
# ============================================================

class TestPipelineStateMachine:
    """PipelineStateMachine 完整测试。"""

    @pytest.fixture
    def sm(self, tmp_path, monkeypatch):
        """在 tmp_path 中创建状态机。"""
        import willy.pipeline_state as pstate
        monkeypatch.setattr(pstate, "get_project_root", lambda: tmp_path)
        sm = PipelineStateMachine(total_steps=7)
        return sm

    def test_initial_state_is_idle(self, sm):
        assert sm._status.state == "idle"
        assert sm._status.step == 0

    def test_transition_updates_state(self, sm):
        sm.transition(State.RUNNING)
        assert sm._status.state == "running"

    def test_set_step(self, sm):
        sm.transition(State.RUNNING)
        sm.set_step(3, "RESP 电荷", "quantum")
        assert sm._status.step == 3
        assert sm._status.step_label == "RESP 电荷"
        assert sm._status.layer == "quantum"

    def test_mark_done(self, sm):
        sm.transition(State.RUNNING)
        sm.set_step(1, "struct_maker", "quantum")
        sm.mark_done(1)
        assert sm._status.state == "running"  # 仍然运行
        assert 1 in sm._status.done_steps

    def test_set_error(self, sm):
        sm.transition(State.RUNNING)
        sm.set_error("gaussian 崩溃", "gaussian_crash")
        assert "gaussian" in sm._status.error
        assert sm._status.error_kind == "gaussian_crash"

    def test_start_retry(self, sm):
        sm.start_retry("QuantumAgent", 1, 5)
        assert sm._status.state == "retrying"
        assert sm._status.agent == "QuantumAgent"
        assert sm._status.retry_n == 1
        assert sm._status.retry_max == 5

    def test_add_action(self, sm):
        sm.add_action("尝试: 添加 scf=xqc")
        assert "scf=xqc" in sm._status.actions[0]

    def test_set_escalated(self, sm):
        escalation = {
            "layer": "quantum", "step": "struct_maker",
            "error_kind": "scf_not_converged",
            "attempts_made": 5, "actions_tried": ["retry"],
            "recommendation": "检查分子", "backup_plan": "更换方法",
        }
        sm.set_escalated(escalation)
        assert sm._status.state == "escalated"
        assert sm._status.escalation["layer"] == "quantum"

    def test_set_aborted(self, sm):
        sm.set_error("依赖缺失", "dependency_missing")
        sm.set_aborted()
        assert sm._status.state == "aborted"
        assert "依赖缺失" in sm._status.error

    def test_full_pipeline_flow(self, sm):
        """完整的流水线状态流转: IDLE → RUNNING → (每个步骤) → DONE。"""
        sm.transition(State.RUNNING)
        assert sm._status.state == "running"

        steps = [
            (1, "g16 结构优化", "quantum"),
            (2, "fchk→mol2", "quantum"),
            (3, "RESP 电荷", "quantum"),
            (4, "拓扑生成", "topology"),
            (5, "主拓扑", "topology"),
            (6, "生成 MDP", "simulation"),
            (7, "Packmol 盒子", "simulation"),
        ]
        for i, name, layer in steps:
            sm.set_step(i, name, layer)
            sm.mark_done(i)

        sm.transition(State.DONE)
        assert sm._status.state == "done"

    def test_error_then_retry_then_done_flow(self, sm):
        """错误 → 重试 → 恢复 → 完成流程。"""
        sm.transition(State.RUNNING)
        sm.set_step(3, "RESP 电荷", "quantum")
        sm.set_error("Gaussian 崩溃", "gaussian_crash")
        # 注意：set_error 不清除 state
        assert sm._status.error != ""

        sm.start_retry("QuantumAgent", 1, 5)
        assert sm._status.state == "retrying"
        sm.add_action("重试: 添加 scf=xqc")

        # Agent 成功
        sm.transition(State.RUNNING, step=3)
        # set_step 会清除错误
        sm.set_step(3, "RESP 电荷", "quantum")
        assert sm._status.error == ""  # set_step 清除错误
        sm.mark_done(3)
        sm.transition(State.DONE)

    def test_final_escalation_flow(self, sm):
        """所有重试用尽 → 升级流程。"""
        sm.transition(State.RUNNING)
        sm.set_step(1, "g16 结构优化", "quantum")
        sm.set_error("SCF 不收敛", "scf_not_converged")

        for i in range(5):
            sm.start_retry("QuantumAgent", i + 1, 5)
            sm.add_action(f"重试尝试 {i+1}")

        sm.set_escalated({
            "layer": "quantum", "step": "struct_maker",
            "error_kind": "scf_not_converged",
            "attempts_made": 5,
            "actions_tried": sm._status.actions.copy(),
            "recommendation": "手动检查输入结构",
            "backup_plan": "尝试不同基组",
        })
        assert sm._status.state == "escalated"

    # ---- status.json 持久化 ----

    def test_writes_status_json(self, sm, tmp_path, monkeypatch):
        """状态机应在初始化时写入 status.json。"""
        import willy.pipeline_state as pstate
        monkeypatch.setattr(pstate, "get_project_root", lambda: tmp_path)
        # 状态机已在 sm fixture 中创建，检查 status.json 是否存在
        status_path = tmp_path / "status.json"
        assert status_path.exists()
        saved = json.loads(status_path.read_text())
        assert saved["state"] == "idle"

    def test_atomic_write(self, sm, tmp_path, monkeypatch):
        """写入应为原子操作（先 .tmp，再 os.replace）。"""
        import willy.pipeline_state as pstate
        monkeypatch.setattr(pstate, "get_project_root", lambda: tmp_path)
        sm.transition(State.RUNNING)
        sm.set_step(1, "struct_maker", "quantum")

        status_path = tmp_path / "status.json"
        tmp_path_file = tmp_path / "status.json.tmp"

        assert status_path.exists()
        # .tmp 文件在 os.replace 之后不应存在
        assert not tmp_path_file.exists()

    def test_read_static_method(self, sm, tmp_path, monkeypatch):
        """read() 应读取当前的 status.json。"""
        import willy.pipeline_state as pstate
        monkeypatch.setattr(pstate, "get_project_root", lambda: tmp_path)
        sm.transition(State.RUNNING)
        sm.set_step(2, "fchk→mol2", "quantum")

        loaded = PipelineStateMachine.read()
        assert loaded.state == "running"
        assert loaded.step == 2

    def test_read_missing_file_returns_idle(self, tmp_path, monkeypatch):
        """status.json 不存在时应返回 IDLE。"""
        import willy.pipeline_state as pstate
        monkeypatch.setattr(pstate, "get_project_root", lambda: tmp_path)
        loaded = PipelineStateMachine.read()
        assert loaded.state == "idle"

    def test_read_corrupted_file_returns_idle(self, sm, tmp_path, monkeypatch):
        """status.json 损坏时应返回 IDLE。"""
        import willy.pipeline_state as pstate
        monkeypatch.setattr(pstate, "get_project_root", lambda: tmp_path)
        status_path = tmp_path / "status.json"
        status_path.write_text("not valid json {{{")
        loaded = PipelineStateMachine.read()
        assert loaded.state == "idle"

    # ---- 边界情况 ----

    def test_error_message_handling(self, sm, tmp_path, monkeypatch):
        """错误消息处理。"""
        import willy.pipeline_state as pstate
        monkeypatch.setattr(pstate, "get_project_root", lambda: tmp_path)
        sm.set_error("E" * 500, "unknown")
        assert sm._status.error != ""

    def test_rapid_state_transitions(self, sm, tmp_path, monkeypatch):
        """快速状态转换不应崩溃。"""
        import willy.pipeline_state as pstate
        monkeypatch.setattr(pstate, "get_project_root", lambda: tmp_path)
        for _ in range(10):
            sm.transition(State.RUNNING)
            sm.transition(State.RETRYING)
        sm.transition(State.DONE)
        assert sm._status.state == "done"

    def test_custom_total_steps(self, tmp_path, monkeypatch):
        """自定义 total_steps 应被存储。"""
        import willy.pipeline_state as pstate
        monkeypatch.setattr(pstate, "get_project_root", lambda: tmp_path)
        sm = PipelineStateMachine(total_steps=11)
        assert sm._status.total_steps == 11
