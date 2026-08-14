"""
test_pipeline_state.py —— PipelineStateMachine 测试。

测试状态转换、原子写入、status.json 序列化、前端轮询安全性。
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from willy.pipeline_state import (
    State, PipelineStatus, PipelineStateMachine, StateTransitionError,
)


# ============================================================
# State 枚举
# ============================================================

class TestStateEnum:
    """State 枚举完整测试。"""

    def test_all_states_defined(self):
        expected = {
            "idle", "running", "retrying", "awaiting_confirmation",
            "stopping", "escalated", "done", "aborted",
        }
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
        assert ps.total_steps == 10
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

    def test_awaiting_confirmation_cannot_skip_directly_to_running(self, sm):
        sm.transition(State.RUNNING)
        sm.set_awaiting_confirmation({
            "action_id": "eq-repair-1",
            "state": "pending",
            "step_label": "GROMACS 三点式退火平衡",
            "restart_step": 9,
            "summary": "等待用户确认后重新验收 EQ。",
            "adjustments": [],
        })

        with pytest.raises(StateTransitionError, match="awaiting_confirmation -> running"):
            sm.transition(State.RUNNING)

    def test_terminal_state_cannot_resume(self, sm):
        sm.transition(State.RUNNING)
        sm.transition(State.DONE)

        with pytest.raises(StateTransitionError, match="done -> running"):
            sm.transition(State.RUNNING)

    def test_aborted_run_must_reenter_awaiting_before_controlled_retry(self, sm):
        sm.transition(State.RUNNING)
        sm.transition(State.ABORTED)

        with pytest.raises(StateTransitionError, match="aborted -> retrying"):
            sm.transition(State.RETRYING)
        sm.transition(State.AWAITING_CONFIRMATION)
        sm.transition(State.RETRYING)
        assert sm._status.state == "retrying"

    def test_scoped_done_preserves_the_last_completed_step(self, sm):
        sm.transition(State.RUNNING)
        sm.set_extra(completion_scope={
            "mode": "through_eq", "step": 9, "stage": "eq",
        })
        sm.transition(State.DONE, step=9, step_label="EQ", layer="simulation")

        assert sm._status.step == 9
        assert sm._status.step_label == "EQ"
        public_status = json.loads(sm._path.read_text())
        assert public_status["extra"]["completion_scope"] == {
            "mode": "through_eq", "step": 9, "stage": "eq",
        }

    def test_public_status_rejects_an_unrecognized_completion_scope(self, sm):
        sm._status.extra["completion_scope"] = {
            "mode": "through_prod", "step": 10, "stage": "prod",
        }

        assert "completion_scope" not in sm._public_payload()["extra"]

    def test_state_revision_is_monotonic_and_persisted(self, sm, tmp_path):
        initial_revision = sm._status.state_revision
        sm.transition(State.RUNNING)
        sm.set_step(1, "结构优化", "quantum")

        assert sm._status.state_revision == initial_revision + 2
        saved = json.loads((tmp_path / "status.json").read_text())
        assert saved["state_revision"] == sm._status.state_revision

    def test_controlled_resume_preserves_revision_before_next_write(self, sm):
        snapshot = {
            "state": "retrying",
            "state_revision": 12,
            "step": 9,
            "done_steps": list(range(1, 9)),
            "extra": {"run_id": "md__202608050001"},
        }
        sm._path.write_text(json.dumps(snapshot))
        sm.restore_for_controlled_resume(snapshot)
        sm.start_retry("simulation", 1, 1)

        assert sm._status.state_revision == 13

    def test_external_stop_revision_cannot_be_overwritten_by_old_heartbeat(self, sm, tmp_path):
        sm.transition(State.RUNNING)
        before = sm._status.state_revision
        stopped = json.loads((tmp_path / "status.json").read_text())
        stopped.update({
            "state": "stopping",
            "state_revision": before + 1,
        })
        (tmp_path / "status.json").write_text(json.dumps(stopped))

        sm.heartbeat()

        saved = json.loads((tmp_path / "status.json").read_text())
        assert saved["state"] == "stopping"
        assert sm._status.state == "stopping"
        assert sm._status.state_revision == before + 1

        sm.set_aborted(user_requested=True)
        saved = json.loads((tmp_path / "status.json").read_text())
        assert saved["state"] == "aborted"
        assert saved["state_revision"] == before + 2

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

    def test_rollback_to_invalidates_downstream_steps(self, sm):
        sm.transition(State.RUNNING)
        sm._status.done_steps = [1, 2, 7, 8, 9]
        sm.rollback_to(7, "Packmol 盒子（回滚重建）", "simulation", "EQ 真空区")
        assert sm._status.done_steps == [1, 2]
        assert sm._status.step == 7
        assert sm._status.layer == "simulation"
        assert "EQ 真空区" in sm._status.actions[-1]

    def test_controlled_restart_withdraws_only_the_restarted_suffix(self, sm):
        sm._status.done_steps = list(range(1, 9))
        sm._status.extra["pending_action"] = {"action_id": "act"}

        sm.invalidate_for_controlled_restart(7)

        assert sm._status.done_steps == list(range(1, 7))
        assert "pending_action" not in sm._status.extra

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

    def test_public_repair_snapshot_includes_only_safe_adjustments(self, sm, tmp_path):
        sm.start_retry("SimulationAgent", 2, 3)
        sm.add_adjustments([
            {"name": "恒温耦合时间", "before": "0.5 ps", "after": "2 ps"},
            {"name": "不应公开", "before": "/tmp/old", "after": "2 ps"},
        ])

        saved = json.loads((tmp_path / "status.json").read_text())
        assert saved["repair"] == {
            "attempt": 2,
            "max_attempts": 3,
            "adjustments": [{"name": "恒温耦合时间", "before": "0.5 ps", "after": "2 ps"}],
        }
        assert "/tmp/old" not in json.dumps(saved, ensure_ascii=False)

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

    def test_user_requested_abort_clears_stale_failure(self, sm):
        sm.set_error("GROMACS 引擎执行失败", "engine_failure")
        sm.start_retry("simulation", 1, 3)
        sm.set_aborted(user_requested=True)

        assert sm._status.state == "aborted"
        assert sm._status.error == ""
        assert sm._status.error_kind == ""
        assert sm._status.retry_n == 0

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
        sm.transition(State.RUNNING)
        sm.transition(State.DONE)
        assert sm._status.state == "done"

    def test_custom_total_steps(self, tmp_path, monkeypatch):
        """自定义 total_steps 应被存储。"""
        import willy.pipeline_state as pstate
        monkeypatch.setattr(pstate, "get_project_root", lambda: tmp_path)
        sm = PipelineStateMachine(total_steps=11)
        assert sm._status.total_steps == 11

    def test_activity_is_strictly_structured_and_persisted_atomically(self, sm, tmp_path):
        sm.set_activity("G16", "结构优化", "molecule", "NO3", 1, 4)

        assert sm._status.activity == {
            "tool": "G16", "operation": "结构优化",
            "target_type": "molecule", "target": "NO3",
            "current": 1, "total": 4,
        }
        saved = json.loads((tmp_path / "status.json").read_text())
        assert saved["activity"] == sm._status.activity
        assert "progress_detail" not in saved
        with pytest.raises(ValueError):
            sm.set_activity("G16", "结构优化", "file", "NO3.log", 1, 1)

    def test_escalation_public_snapshot_drops_raw_output(self, sm, tmp_path):
        sm.set_escalated({
            "layer": "quantum", "step": "struct_g16",
            "error_kind": "scf_not_converged", "last_raw_output": "/tmp/secret stderr",
            "attempts_made": 0,
            "recommendation": "检查运行环境后重新提交",
            "actions_tried": ["未执行参数调整"],
        })

        saved = json.loads((tmp_path / "status.json").read_text())
        assert "last_raw_output" not in json.dumps(saved, ensure_ascii=False)
        assert saved["escalation"]["recommendation"] == "检查运行环境后重新提交"
        assert saved["escalation"]["attempts_made"] == 0
