"""前端运行控制的安全默认值测试。"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
from html import unescape
import json
import stat

import pytest

import willy.frontend_api as frontend_api
from willy.errors import StepResult
from willy.run_registry import RunRegistry
from willy.simulation.pending_action import PendingActionError, create_eq_pending_action
from willy.simulation.protocol import default_md_config


def _record_run_status(tmp_path, run_id, status):
    run_dir = tmp_path / "md_run" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text("{}")
    registry = RunRegistry(tmp_path)
    registry.register_run(run_dir, backend="g16", total_steps=10)
    registry.record_status(run_dir, status, "step_started")
    return run_dir


def test_stop_during_gromacs_requests_checkpoint_first_shutdown(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_dir = _record_run_status(tmp_path, "run-001", {"state": "running", "step": 9})
    monkeypatch.setattr(frontend_api, "get_active_run_id", lambda: "run-001")

    with patch.object(frontend_api, "_kill_process_group") as kill:
        message = frontend_api.stop_pipeline()

    assert "安全停止" in message
    assert (run_dir / "stop.request").is_file()
    assert RunRegistry(tmp_path).get_run_status(run_dir.name)["state"] == "stopping"
    kill.assert_not_called()


def test_clean_stop_unlinks_only_known_root_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    for name in ("model.inp", "model.pdb"):
        (tmp_path / name).write_text("artifact")

    monkeypatch.setattr(frontend_api, "get_active_run_id", lambda: None)
    with patch.object(frontend_api, "_kill_process_group"):
        message = frontend_api.stop_pipeline(clean=True)

    assert message == "当前没有活动流水线；已清理临时产物。"
    assert not (tmp_path / "model.inp").exists()
    assert not (tmp_path / "model.pdb").exists()


def test_reconcile_stale_latest_run_marks_transient_status_aborted_with_audit(tmp_path, monkeypatch):
    from willy.simulation.manifest import request_safe_stop

    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_dir = _record_run_status(tmp_path, "md_stale_202608020001", {
        "state": "retrying", "step": 9, "error": "旧错误", "error_kind": "engine_failure",
    })
    request_safe_stop(run_dir)
    monkeypatch.setattr(frontend_api, "is_pipeline_running", lambda: False)

    reconciled = frontend_api.reconcile_stale_pipeline_state()
    status = RunRegistry(tmp_path).get_run_status(run_dir.name)

    assert reconciled == run_dir.name
    assert status["state"] == "aborted"
    assert status["error"] == ""
    assert status["error_kind"] == ""
    assert not (run_dir / "stop.request").exists()
    events = (run_dir / "events.jsonl").read_text()
    assert "run_aborted_after_process_exit" in events
    assert '"source": "stale_reconciliation"' in events
    assert "当前工序" not in frontend_api.get_run_summary_markdown(run_dir.name)


def test_frontend_polling_does_not_reconcile_or_mutate_status(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_dir = _record_run_status(tmp_path, "md_read_only_202608020001", {
        "state": "running", "step": 9, "activity": {},
    })
    monkeypatch.setattr(frontend_api, "get_active_run_id", lambda: run_dir.name)

    with patch.object(frontend_api, "reconcile_stale_pipeline_state") as reconcile:
        assert frontend_api.get_latest_run_control_state() == "running"

    reconcile.assert_not_called()
    assert RunRegistry(tmp_path).get_run_status(run_dir.name, reconcile=False)["state"] == "running"


def test_legacy_pipeline_group_keeps_controls_in_running_state(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    (tmp_path / ".pipeline.pid").write_text("4242")
    monkeypatch.setattr(frontend_api, "pipeline_launch_is_active", lambda root: False)
    monkeypatch.setattr(frontend_api, "_legacy_runner_pid_is_alive", lambda pid: False)
    monkeypatch.setattr(frontend_api, "_legacy_pipeline_group_is_alive", lambda pgid: pgid == 4242)

    assert frontend_api.is_pipeline_running()
    assert (tmp_path / ".pipeline.pid").is_file()


def test_reconcile_skips_run_with_fresh_gromacs_eta_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_dir = _record_run_status(tmp_path, "md_live_202608020001", {
        "state": "running", "step": 9,
        "activity": {
            "tool": "GROMACS", "operation": "运行模拟",
            "target_type": "stage", "target": "eq", "current": 2, "total": 2,
        },
    })
    observed_at = datetime.now(timezone.utc).isoformat()
    (run_dir / "mdrun_eta.json").write_text(json.dumps({
        "schema_version": 2,
        "stage": "eq",
        "status": "waiting",
        "observed_at": observed_at,
        "process_alive": True,
        "source": "gmx_verbose",
    }))
    monkeypatch.setattr(frontend_api, "is_pipeline_running", lambda: False)

    assert frontend_api.reconcile_stale_pipeline_state() is None
    status = RunRegistry(tmp_path).get_run_status(run_dir.name, reconcile=False)
    assert status["state"] == "running"
    assert "run_aborted_after_process_exit" not in (run_dir / "events.jsonl").read_text()


def test_reconcile_abandons_abort_when_revision_advances_during_check(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_dir = _record_run_status(tmp_path, "md_race_202608020001", {
        "state": "running", "step": 9, "activity": {},
    })
    monkeypatch.setattr(frontend_api, "is_pipeline_running", lambda: False)
    registry = RunRegistry(tmp_path)
    original = registry.get_run_status
    calls = {"count": 0}

    def get_status(run_id, *, reconcile=True):
        calls["count"] += 1
        status = original(run_id, reconcile=reconcile)
        if calls["count"] == 2:
            status["state_revision"] += 1
        return status

    monkeypatch.setattr(frontend_api.RunRegistry, "get_run_status", lambda self, run_id, **kwargs: get_status(run_id, **kwargs))

    assert frontend_api.reconcile_stale_pipeline_state() is None
    assert RunRegistry(tmp_path).get_run_status(run_dir.name, reconcile=False)["state"] == "running"


def test_stopping_status_uses_public_checkpoint_message(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_id = "md_stop_202608020001"
    _record_run_status(tmp_path, run_id, {
        "state": "stopping", "step": 9,
        "activity": {
            "tool": "GROMACS", "operation": "运行模拟",
            "target_type": "stage", "target": "eq", "current": 2, "total": 2,
        },
    })

    markdown = frontend_api.get_run_summary_markdown(run_id)

    assert "状态：正在安全停止" in markdown
    assert "等待当前工序写入 checkpoint" in markdown


def test_escalated_protocol_change_tells_user_the_run_was_not_modified(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_id = "md_confirmation_202608020001"
    _record_run_status(tmp_path, run_id, {
        "state": "escalated",
        "step": 9,
        "error_kind": "user_confirmation_required",
        "error": "GROMACS 输入预处理失败：eq\n原因：模拟协议变更等待用户确认",
    })

    markdown = frontend_api.get_run_summary_markdown(run_id)

    assert "等待用户确认模拟协议变更" in markdown
    assert "当前运行未改写" in markdown


def test_escalated_runtime_failure_shows_recovery_conclusion(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_id = "md_runtime_202608110001"
    _record_run_status(tmp_path, run_id, {
        "state": "escalated",
        "step": 7,
        "error_kind": "runtime_unavailable",
        "error": "Packmol 初始盒子构建失败：当前体系\n原因：运行环境不兼容",
        "escalation": {
            "layer": "simulation",
            "step": "Packmol 盒子构建",
            "error_kind": "runtime_unavailable",
            "attempts_made": 0,
            "actions_tried": ["检测到运行环境不可用，未执行自动参数修复"],
            "recommendation": "请安装与当前系统兼容的 Packmol 后重新提交。",
        },
    })

    markdown = frontend_api.get_run_summary_markdown(run_id)

    assert "自动修复：未执行参数调整" in markdown
    assert "处理建议：请安装与当前系统兼容的 Packmol 后重新提交。" in markdown
    assert "第 1/3 次" not in markdown


def test_awaiting_confirmation_summary_explains_the_llm_and_user_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_id = "md_waiting_202608020001"
    _record_run_status(tmp_path, run_id, {
        "state": "awaiting_confirmation",
        "step": 9,
        "error": "EQ 验收未通过",
        "error_kind": "equilibration_failed",
    })

    markdown = frontend_api.get_run_summary_markdown(run_id)

    assert "状态：等待用户确认调整方案" in markdown
    assert "LLM 已返回方案" in markdown
    assert "未启动重跑" in markdown


def test_get_pending_action_reads_only_the_waiting_public_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_id = "md__202608030001"
    run_dir = tmp_path / "md_run" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps({
        "residues": {"Li": 1},
        "molecules": {"Li": {"charge": 0, "spin": 1}},
        "md": default_md_config(),
    }))
    registry = RunRegistry(tmp_path)
    registry.register_run(run_dir, backend="g16", total_steps=10)
    action = create_eq_pending_action(run_dir, proposal={
        "summary": "降低时间步长并重新验收 EQ。",
        "adjustments": [{"field": "dt", "after": 0.0005, "purpose": "稳定积分"}],
    })
    registry.record_status(run_dir, {
        "state": "awaiting_confirmation", "step": 9,
        "step_label": "GROMACS 三点式退火平衡", "layer": "simulation",
        "error": "GROMACS 运行模拟失败：eq", "error_kind": "equilibration_failed",
        "activity": {}, "done_steps": list(range(1, 9)),
        "extra": {"run_id": run_id, "pending_action": {
            "action_id": action["action_id"], "state": "pending",
            "step_label": "GROMACS 三点式退火平衡", "restart_step": 9,
            "summary": action["summary"],
            "adjustments": [{
                "name": "时间步长", "before": "0.001 ps", "after": "0.0005 ps",
                "purpose": "稳定积分", "value": "must-not-leak",
            }],
        }},
    }, "run_awaiting_confirmation")

    pending = frontend_api.get_pending_action()

    assert pending == {
        "run_id": run_id,
        "action_id": action["action_id"],
        "state_revision": 1,
        "config_fingerprint": action["config_sha256"],
        "status": "awaiting_confirmation",
        "step_label": "GROMACS 三点式退火平衡",
        "restart_step": 9,
        "summary": "降低时间步长并重新验收 EQ。",
        "adjustments": [{
            "name": "时间步长", "before": "0.001 ps", "after": "0.0005 ps",
            "purpose": "稳定积分",
        }],
    }
    assert "must-not-leak" not in json.dumps(pending, ensure_ascii=False)


def test_pending_action_never_leaks_from_old_run_to_newest_run(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    registry = RunRegistry(tmp_path)
    old_id = "md__202608040001"
    old_dir = tmp_path / "md_run" / old_id
    old_dir.mkdir(parents=True)
    (old_dir / "config.json").write_text(json.dumps({
        "residues": {"Li": 1},
        "molecules": {"Li": {"charge": 0, "spin": 1}},
        "md": default_md_config(),
    }))
    registry.register_run(old_dir, backend="g16", total_steps=10)
    action = create_eq_pending_action(old_dir, proposal={
        "summary": "旧工程的 EQ 调整。",
        "adjustments": [{"field": "dt", "after": 0.0005}],
    })
    registry.record_status(old_dir, {
        "state": "awaiting_confirmation", "step": 9,
        "activity": {}, "done_steps": list(range(1, 9)),
        "extra": {"pending_action": {
            "action_id": action["action_id"], "state": "pending",
            "step_label": "GROMACS 三点式退火平衡", "restart_step": 9,
            "summary": action["summary"], "adjustments": [],
        }},
    }, "run_awaiting_confirmation")

    new_id = "md__202608040002"
    _record_run_status(tmp_path, new_id, {
        "state": "running", "step": 1, "activity": {}, "done_steps": [],
    })

    assert frontend_api.latest_run_id() == new_id
    assert frontend_api.get_pending_action() is None
    assert frontend_api.get_pending_action(old_id)["run_id"] == old_id


def test_run_panel_snapshot_resolves_the_run_once_for_status_and_action(monkeypatch):
    calls = []
    monkeypatch.setattr(frontend_api, "latest_run_id", lambda: "md_current")

    def fake_summary(run_id, *, include_error=True):
        calls.append(("summary", run_id, include_error))
        return "状态摘要" if include_error else "实时状态摘要"

    monkeypatch.setattr(
        frontend_api,
        "get_run_summary_markdown",
        fake_summary,
    )
    monkeypatch.setattr(
        frontend_api,
        "get_pending_action",
        lambda run_id: calls.append(("action", run_id)) or {"action_id": "act-1"},
    )

    snapshot = frontend_api.get_run_panel_snapshot()

    assert snapshot == {
        "run_id": "md_current",
        "summary": "状态摘要",
        "live_summary": "实时状态摘要",
        "pending_action": {"action_id": "act-1"},
        "error_event": None,
        "status_event_id": "md_current:status:unavailable:none:act-1:none",
        "timeline_events": True,
    }
    assert calls == [
        ("summary", "md_current", True),
        ("summary", "md_current", False),
        ("action", "md_current"),
    ]


def test_active_pipeline_run_wins_over_newer_historical_index_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    active_id = "md__202608040001"
    historical_id = "md__202608040002"
    _record_run_status(tmp_path, active_id, {
        "state": "running", "step": 1, "activity": {}, "done_steps": [],
    })
    _record_run_status(tmp_path, historical_id, {
        "state": "awaiting_confirmation", "step": 9, "activity": {}, "done_steps": [],
    })
    monkeypatch.setattr(frontend_api, "get_active_run_id", lambda: active_id)

    assert frontend_api.latest_run_id() == active_id


def test_confirm_pending_action_reserves_the_same_run_and_spawns_controlled_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_id = "md__202608030001"
    run_dir = tmp_path / "md_run" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps({
        "residues": {"Li": 1},
        "molecules": {"Li": {"charge": 0, "spin": 1}},
        "md": default_md_config(),
    }))
    registry = RunRegistry(tmp_path)
    registry.register_run(run_dir, backend="orca", total_steps=10)
    action = create_eq_pending_action(run_dir, proposal={
        "adjustments": [{"field": "dt", "after": 0.0005}],
    })
    registry.record_status(run_dir, {
        "state": "awaiting_confirmation", "step": 9,
        "step_label": "GROMACS 三点式退火平衡", "layer": "simulation",
        "error": "EQ 验收未通过", "error_kind": "equilibration_failed",
        "activity": {}, "done_steps": list(range(1, 9)),
        "extra": {"pending_action": {
            "action_id": action["action_id"], "state": "pending",
            "step_label": "GROMACS 三点式退火平衡", "restart_step": 9,
            "summary": action["summary"], "adjustments": [],
        }},
    }, "run_awaiting_confirmation")

    spawned = []

    class Reservation:
        def __init__(self):
            self.run_dir = run_dir
            self.fd = 19
            self.token = "reservation-token"

        def mark_runner_started(self, pid):
            self.pid = pid

        def detach_parent(self):
            return None

        def release(self):
            raise AssertionError("valid launch must not release the reservation")

    class Process:
        pid = 4321

        def wait(self):
            return 0

    class IdleThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            return None

    monkeypatch.setattr(frontend_api, "reserve_existing_run_launch", lambda root, selected: Reservation())
    monkeypatch.setattr(frontend_api.subprocess, "Popen", lambda args, **kwargs: spawned.append((args, kwargs)) or Process())
    monkeypatch.setattr(frontend_api.threading, "Thread", IdleThread)

    pending = frontend_api.get_pending_action(run_id)
    assert pending is not None
    fingerprint_reply = frontend_api.confirm_pending_action(
        action["action_id"],
        run_id,
        state_revision=pending["state_revision"],
        config_fingerprint="0" * 64,
    )
    assert "已更新" in fingerprint_reply
    assert spawned == []
    stale_reply = frontend_api.confirm_pending_action(
        action["action_id"],
        run_id,
        state_revision=pending["state_revision"] + 1,
        config_fingerprint=pending["config_fingerprint"],
    )
    assert "已更新" in stale_reply
    assert spawned == []

    reply = frontend_api.confirm_pending_action(
        action["action_id"],
        run_id,
        state_revision=pending["state_revision"],
        config_fingerprint=pending["config_fingerprint"],
    )

    assert "当前进入重跑准备" in reply
    status = RunRegistry(tmp_path).get_run_status(run_id)
    assert status["state"] == "retrying"
    assert status["state_revision"] == pending["state_revision"] + 1
    assert status["extra"]["pending_action"]["action_id"] == action["action_id"]
    args, kwargs = spawned[0]
    assert args[:3] == ["python3", "run_pipeline.py", "orca"]
    assert args[-2:] == ["--resume-pending-action", action["action_id"]]
    assert kwargs["pass_fds"] == (19,)
    assert "已失效" in frontend_api.confirm_pending_action(action["action_id"], run_id)
    assert len(spawned) == 1


def test_confirm_pending_action_restores_waiting_state_when_runner_cannot_start(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_id = "md__202608030002"
    run_dir = tmp_path / "md_run" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps({
        "residues": {"Li": 1},
        "molecules": {"Li": {"charge": 0, "spin": 1}},
        "md": default_md_config(),
    }))
    registry = RunRegistry(tmp_path)
    registry.register_run(run_dir, backend="g16", total_steps=10)
    action = create_eq_pending_action(run_dir, proposal={
        "adjustments": [{"field": "dt", "after": 0.0005}],
    })
    registry.record_status(run_dir, {
        "state": "awaiting_confirmation", "step": 9,
        "step_label": "GROMACS 三点式退火平衡", "layer": "simulation",
        "error": "EQ 验收未通过", "error_kind": "equilibration_failed",
        "activity": {}, "done_steps": list(range(1, 9)),
        "extra": {"pending_action": {
            "action_id": action["action_id"], "state": "pending",
            "step_label": "GROMACS 三点式退火平衡", "restart_step": 9,
            "summary": action["summary"], "adjustments": [],
        }},
    }, "run_awaiting_confirmation")

    class Reservation:
        fd = 19
        token = "reservation-token"
        released = False

        def __init__(self):
            self.run_dir = run_dir

        def release(self):
            self.released = True

    reservation = Reservation()
    monkeypatch.setattr(frontend_api, "reserve_existing_run_launch", lambda *_args: reservation)
    monkeypatch.setattr(
        frontend_api.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("launcher unavailable")),
    )

    reply = frontend_api.confirm_pending_action(action["action_id"], run_id)

    status = RunRegistry(tmp_path).get_run_status(run_id)
    assert "启动失败" in reply
    assert reservation.released is True
    assert status["state"] == "awaiting_confirmation"
    assert status["extra"]["pending_action"]["action_id"] == action["action_id"]


def test_run_status_cas_rejects_stale_revision_and_terminal_regression(tmp_path):
    from willy.run_registry import RunRegistryError, RunStateConflict

    run_id = "md__202608030003"
    run_dir = tmp_path / "md_run" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text("{}")
    registry = RunRegistry(tmp_path)
    registry.register_run(run_dir, backend="g16", total_steps=10)
    registry.record_status(run_dir, {
        "state": "done", "step": 11, "total_steps": 10,
        "activity": {}, "done_steps": list(range(1, 11)),
    }, "run_finished")
    done_status = registry.get_run_status(run_id)

    with pytest.raises(RunStateConflict):
        registry.compare_and_swap_status(
            run_dir,
            expected_revision=done_status["state_revision"] - 1,
            status={**done_status, "state": "running"},
            event_type="state_changed",
        )
    with pytest.raises(RunRegistryError, match="done -> running"):
        registry.compare_and_swap_status(
            run_dir,
            expected_revision=done_status["state_revision"],
            status={**done_status, "state": "running"},
            event_type="state_changed",
        )


def test_revising_pending_action_keeps_run_waiting_and_config_unchanged(tmp_path, monkeypatch):
    import willy.agent_config as agent_config
    from willy.agent_simulation import SimulationAgent
    from willy.simulation.pending_action import validate_pending_action_for_launch

    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_id = "md__202608040099"
    run_dir = tmp_path / "md_run" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps({
        "residues": {"Li": 1},
        "molecules": {"Li": {"charge": 0, "spin": 1}},
        "md": default_md_config(),
    }))
    config_before = (run_dir / "config.json").read_bytes()
    registry = RunRegistry(tmp_path)
    registry.register_run(run_dir, backend="g16", total_steps=10)
    original = create_eq_pending_action(run_dir, proposal={
        "adjustments": [{"field": "dt", "after": 0.0005}],
    })
    registry.record_status(run_dir, {
        "state": "awaiting_confirmation", "step": 9,
        "step_label": "GROMACS 三点式退火平衡", "layer": "simulation",
        "error": "EQ 验收未通过", "error_kind": "equilibration_failed",
        "activity": {}, "done_steps": list(range(1, 9)),
        "extra": {"pending_action": {
            "action_id": original["action_id"], "state": "pending",
            "step_label": "GROMACS 三点式退火平衡", "restart_step": 9,
            "summary": original["summary"], "adjustments": [],
        }},
    }, "run_awaiting_confirmation")
    monkeypatch.setattr(agent_config, "_DS", object())
    monkeypatch.setattr(
        SimulationAgent,
        "propose_revised_eq_recovery",
        lambda *_args, **_kwargs: {
            "summary": "延长最终保温段后重新验收 EQ。",
            "adjustments": [{
                "field": "eq_segment.hold_target", "after": 8.0,
                "purpose": "增加目标温度采样",
            }],
        },
    )

    reply = frontend_api.revise_pending_action(
        original["action_id"], "将最终保温段改为 8 ns", run_id,
    )
    revised = frontend_api.get_pending_action(run_id)

    assert "已按你的要求更新" in reply
    assert revised is not None
    assert revised["action_id"] != original["action_id"]
    revised_status = RunRegistry(tmp_path).get_run_status(run_id)
    assert revised_status["state"] == "awaiting_confirmation"
    assert revised_status["state_revision"] == 2
    assert (run_dir / "config.json").read_bytes() == config_before
    with pytest.raises(PendingActionError, match="已失效"):
        validate_pending_action_for_launch(run_dir, original["action_id"])
    assert "pending_action_revised" in (run_dir / "events.jsonl").read_text()


def test_invalid_revised_proposal_preserves_original_waiting_action(tmp_path, monkeypatch):
    import willy.agent_config as agent_config
    from willy.agent_simulation import SimulationAgent

    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_id = "md__202608040098"
    run_dir = tmp_path / "md_run" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps({
        "residues": {"Li": 1},
        "molecules": {"Li": {"charge": 0, "spin": 1}},
        "md": default_md_config(),
    }))
    registry = RunRegistry(tmp_path)
    registry.register_run(run_dir, backend="g16", total_steps=10)
    original = create_eq_pending_action(run_dir, proposal={
        "adjustments": [{"field": "dt", "after": 0.0005}],
    })
    registry.record_status(run_dir, {
        "state": "awaiting_confirmation", "step": 9,
        "step_label": "GROMACS 三点式退火平衡", "layer": "simulation",
        "error": "EQ 验收未通过", "error_kind": "equilibration_failed",
        "activity": {}, "done_steps": list(range(1, 9)),
        "extra": {"pending_action": {
            "action_id": original["action_id"], "state": "pending",
            "step_label": "GROMACS 三点式退火平衡", "restart_step": 9,
            "summary": original["summary"], "adjustments": [],
        }},
    }, "run_awaiting_confirmation")
    monkeypatch.setattr(agent_config, "_DS", object())
    monkeypatch.setattr(SimulationAgent, "propose_revised_eq_recovery", lambda *_args, **_kwargs: {})

    reply = frontend_api.revise_pending_action(
        original["action_id"], "把方案改为更长的保温段", run_id,
    )

    assert "未通过校验：调整项格式无效：需要 adjustments 数组" in reply
    assert frontend_api.get_pending_action(run_id)["action_id"] == original["action_id"]
    assert RunRegistry(tmp_path).get_run_status(run_id)["state"] == "awaiting_confirmation"
    assert '"result": "rejected_validation"' in (run_dir / "decision_trace.jsonl").read_text()


def test_pipeline_launch_receipt_does_not_repeat_the_proposed_plan(tmp_path, monkeypatch):
    import willy.agent_config as agent_config

    class FakeProcess:
        pid = 1234

        def wait(self):
            return 0

    class ImmediateThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(agent_config, "ROOT", tmp_path)
    (tmp_path / "struct").mkdir()
    (tmp_path / "struct" / "Li.gjf").write_text("#p b3lyp/6-31g\n\nLi\n\n1 1\nLi 0 0 0\n")
    config = {
        "backend": "g16",
        "residues": {"Li": 100},
        "molecules": {"Li": {"charge": 1, "spin": 1}},
        "non_neutral_confirmed": True,
    }
    monkeypatch.setattr(agent_config, "validate_config", lambda config: [])
    monkeypatch.setattr(agent_config, "apply_config", lambda config: tmp_path / "config.json")
    monkeypatch.setattr(agent_config.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(agent_config.threading, "Thread", ImmediateThread)

    receipt = agent_config.launch_pipeline(config)

    assert "流水线已启动" in receipt
    assert "模拟方案确认" not in receipt
    assert "组成:" not in receipt


def test_run_assistant_defaults_to_the_latest_run(tmp_path, monkeypatch):
    import willy.agent_config as agent_config
    import willy.agent_run as agent_run
    from willy.run_registry import RunRegistry

    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_id = "md_demo_202608010001"
    run_dir = tmp_path / "md_run" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text("{}")
    registry = RunRegistry(tmp_path)
    registry.register_run(run_dir, backend="g16", total_steps=10)
    registry.record_status(run_dir, {
        "state": "running", "step": 9, "step_label": "GROMACS NPT退火", "layer": "simulation",
        "error": "", "error_kind": "", "total_steps": 10, "done_steps": list(range(1, 9)),
        "activity": {
            "tool": "GROMACS", "operation": "运行模拟",
            "target_type": "stage", "target": "eq", "current": 2, "total": 2,
        },
    }, "step_started")

    selected_run_ids = []

    class FakeRunAssistant:
        def __init__(self, client, registry, model):
            pass

        def answer(self, message, selected_run_id, history):
            selected_run_ids.append(selected_run_id)
            return "NPT退火正在运行。"

    monkeypatch.setattr(agent_config, "_DS", object())
    monkeypatch.setattr(agent_run, "RunAssistant", FakeRunAssistant)

    assert frontend_api.latest_run_id() == run_id
    assert f"运行 {run_id}" in frontend_api.get_run_summary_markdown(None)
    assert frontend_api.chat_run_assistant(None, "检查当前运行进度") == "NPT退火正在运行。"
    assert selected_run_ids == [run_id]


def test_run_assistant_fast_status_query_does_not_require_llm(tmp_path, monkeypatch):
    import willy.agent_config as agent_config

    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_dir = _record_run_status(tmp_path, "md_fast_202608020001", {
        "state": "running", "step": 9, "step_label": "GROMACS NPT退火", "layer": "simulation",
        "error": "", "error_kind": "", "total_steps": 10, "done_steps": list(range(1, 9)),
        "activity": {
            "tool": "GROMACS", "operation": "运行模拟",
            "target_type": "stage", "target": "eq", "current": 2, "total": 2,
        },
    })
    monkeypatch.setattr(agent_config, "_DS", None)

    answer = frontend_api.chat_run_assistant(run_dir.name, "现在到哪一步？")

    assert "状态：运行中" in answer
    assert "当前工序：GROMACS 运行模拟" in answer


def test_run_assistant_pending_message_distinguishes_fact_and_explanation_paths():
    assert frontend_api.run_assistant_pending_message("现在到哪一步？") == "正在读取当前运行事实..."
    assert frontend_api.run_assistant_pending_message("为什么 EQ 会失败？") == "正在读取运行事实并生成解释..."


def test_llm_config_is_persisted_locally_without_leaking_to_ui(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("OTHER_SETTING=keep\nDEEPSEEK_API_KEY=old\n")

    message = frontend_api.save_llm_config(
        "new-secret-key", "http://localhost:8000/v1", "local-model",
    )

    assert message == "OpenAI-compatible LLM 配置已保存到本机 .env。"
    assert "new-secret-key" not in message
    assert (tmp_path / ".env").read_text() == (
        "OTHER_SETTING=keep\n"
        "WILLY_LLM_API_KEY=new-secret-key\n"
        "WILLY_LLM_BASE_URL=http://localhost:8000/v1\n"
        "WILLY_LLM_MODEL=local-model\n"
    )
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600
    assert frontend_api.get_llm_config_status() == "OpenAI-compatible LLM 已通过本机 .env配置，模型：local-model。"


def test_llm_config_rejects_blank_or_multiline_values(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)

    assert frontend_api.save_llm_config("\n", "https://example.test", "model") == "LLM 配置无效：API Key 不能为空且不能包含换行。"
    assert frontend_api.save_llm_config("key", "https://example.test", "model\nOTHER=value") == "LLM 配置无效：Model 不能为空且不能包含换行。"
    assert not (tmp_path / ".env").exists()


def test_llm_config_notice_shows_the_loaded_model_or_a_safe_fallback(monkeypatch):
    loaded = SimpleNamespace(model="provider-model-v1")
    monkeypatch.setattr(frontend_api, "load_llm_settings", lambda _root: loaded)

    assert frontend_api.get_llm_config_notice() == (
        "Agent制作者不会以任何方式获取您的API-key。"
        "您提供的LLM只会在您电脑本地的.env配置。"
        "当前模型：*provider-model-v1*。"
    )

    monkeypatch.setattr(frontend_api, "load_llm_settings", lambda _root: None)
    assert frontend_api.get_llm_config_notice().endswith("当前模型：*当前无可用模型*。")


def test_managed_mode_is_blocked_without_writing_or_refreshing(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    refresh_calls = []
    monkeypatch.setattr(frontend_api, "_refresh_agent_llm_client", lambda: refresh_calls.append(True))
    (tmp_path / ".env").write_text("OTHER_SETTING=keep\nWILLY_LLM_API_KEY=byok-secret\n")

    message = frontend_api.save_llm_mode("managed")

    saved = (tmp_path / ".env").read_text(encoding="utf-8")
    assert message == "当前版本仅支持本地 API Key，托管网关已冻结。"
    assert "WILLY_LLM_MODE" not in saved
    assert "byok-secret" in saved
    assert "gateway.example" not in saved
    assert "invite" not in saved.lower()

    assert refresh_calls == []


def test_managed_status_registration_and_connection_are_frozen_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    monkeypatch.setattr(frontend_api, "_refresh_agent_llm_client", lambda: None)
    monkeypatch.setattr(frontend_api, "load_managed_gateway_profile", lambda *_: (_ for _ in ()).throw(AssertionError("frozen path must not read profile")))
    monkeypatch.setattr(frontend_api, "managed_gateway_usage_snapshot", lambda *_: (_ for _ in ()).throw(AssertionError("frozen path must not access gateway")))
    assert frontend_api.get_managed_gateway_status()["code"] == "managed_gateway_frozen"
    assert frontend_api.request_managed_gateway_registration() == "当前版本仅支持本地 API Key，托管网关已冻结。"
    assert frontend_api.test_managed_gateway_connection()["code"] == "managed_gateway_frozen"


def test_managed_status_compatibility_response_is_frozen(monkeypatch):
    status = frontend_api.get_managed_gateway_status()
    assert status == {
        "ok": False,
        "code": "managed_gateway_frozen",
        "message": "当前版本仅支持本地 API Key，托管网关已冻结。",
    }

    managed = SimpleNamespace(model="willy-default", provider_mode="managed")
    monkeypatch.setattr(frontend_api, "load_llm_settings", lambda _root: managed)
    assert frontend_api.get_llm_config_status() == "当前版本仅支持本地 API Key，托管网关已冻结。"
    notice = frontend_api.get_llm_config_notice()
    assert "当前模型：*willy-default*。" in notice
    assert "设备私钥" not in notice


def test_legacy_managed_config_notice_directs_user_to_byok(monkeypatch):
    monkeypatch.setattr(
        frontend_api,
        "load_llm_settings",
        lambda _root: (_ for _ in ()).throw(
            frontend_api.LLMConfigError("当前版本仅支持本地 API Key，托管网关已冻结")
        ),
    )

    notice = frontend_api.get_llm_config_notice()

    assert "历史托管网关设置" in notice
    assert "保存用户自配的 API Key" in notice


def test_managed_connection_check_is_frozen_without_network(monkeypatch):
    result = frontend_api.test_managed_gateway_connection()
    assert result["ok"] is False
    assert result["code"] == "managed_gateway_frozen"


class _ConnectionError(Exception):
    def __init__(self, *, status_code=None, content_type=None, text=""):
        super().__init__(text)
        self.status_code = status_code
        self.response = (
            SimpleNamespace(headers={"content-type": content_type})
            if content_type is not None else None
        )


class _ConnectionTimeout(Exception):
    pass


class _ConnectionFailure(Exception):
    pass


def _tool_call_response(tool_name="willy_connection_check"):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(
                tool_calls=[SimpleNamespace(function=SimpleNamespace(name=tool_name))],
            ),
        )],
    )


def _connection_client(*, response=None, error=None):
    client = MagicMock()
    if error is not None:
        client.chat.completions.create.side_effect = error
    else:
        client.chat.completions.create.return_value = response
    return client


def test_llm_connection_uses_transient_form_values_and_requires_an_automatic_tool_call(tmp_path, monkeypatch):
    secret = "test-connection-secret"
    client = _connection_client(response=_tool_call_response())
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("EXISTING=value\n")
    monkeypatch.setattr(frontend_api, "create_openai_client", lambda settings: client)

    result = frontend_api.test_llm_connection(
        secret, "https://api.forza0310.cn/v1", "compatible-model",
    )

    assert result == {
        "ok": True,
        "code": "ok",
        "message": "连接与工具调用可用",
        "suggestion": "测试仅使用当前表单值，未保存任何配置。",
    }
    assert (tmp_path / ".env").read_text() == "EXISTING=value\n"
    call = client.chat.completions.create.call_args.kwargs
    assert call["model"] == "compatible-model"
    assert call["timeout"] == frontend_api.LLM_CONNECTION_TIMEOUT_S
    assert call["tool_choice"] == "auto"
    assert call["tools"][0]["function"]["name"] == "willy_connection_check"
    assert secret not in str(result)


def test_llm_connection_does_not_append_v1_to_the_entered_url(monkeypatch):
    captured = {}

    def fake_client(settings):
        captured["base_url"] = settings.base_url
        return _connection_client(response=_tool_call_response())

    monkeypatch.setattr(frontend_api, "create_openai_client", fake_client)

    result = frontend_api.test_llm_connection(
        "test-key", "https://api.forza0310.cn", "compatible-model",
    )

    assert result["ok"] is True
    assert captured["base_url"] == "https://api.forza0310.cn"


def test_llm_connection_rejects_invalid_url_without_constructing_a_client(monkeypatch):
    monkeypatch.setattr(
        frontend_api,
        "create_openai_client",
        lambda _settings: (_ for _ in ()).throw(AssertionError("must not construct client")),
    )

    result = frontend_api.test_llm_connection("test-key", "not-a-url", "test-model")

    assert result == {
        "ok": False,
        "code": "invalid_url",
        "message": "Base URL 格式无效",
        "suggestion": "使用完整 http(s) 地址，不含查询参数。",
    }


def test_llm_connection_explains_missing_transient_key_and_v1_paths(monkeypatch):
    monkeypatch.setattr(
        frontend_api,
        "create_openai_client",
        lambda _settings: (_ for _ in ()).throw(AssertionError("must not construct client")),
    )

    result = frontend_api.test_llm_connection(
        "", "https://api.forza0310.cn", "compatible-model",
    )

    assert result == {
        "ok": False,
        "code": "invalid_test_api_key",
        "message": "本次连接测试的 API Key 为空或格式无效",
        "suggestion": "测试只使用当前表单值，已保存的 Key 不会回填。请重新粘贴 Key；部分兼容服务还要求 Base URL 以 /v1 结尾，请以服务商文档为准。",
    }


@pytest.mark.parametrize(
    ("error", "code", "message", "suggestion"),
    [
        (
            _ConnectionError(content_type="text/html; charset=utf-8"),
            "non_json",
            "服务未返回 OpenAI API 响应",
            "检查 Base URL；常见修复是在末尾追加 /v1。",
        ),
        (
            _ConnectionError(status_code=401),
            "authentication",
            "API Key 无效或无权访问该服务",
            "重新复制 Key，确认账户与模型权限。",
        ),
        (
            _ConnectionError(status_code=403),
            "authentication",
            "API Key 无效或无权访问该服务",
            "重新复制 Key，确认账户与模型权限。",
        ),
        (
            _ConnectionError(status_code=404),
            "not_found",
            "未找到 API 端点或模型",
            "检查 /v1 路径和 Model 名称。",
        ),
        (
            _ConnectionError(status_code=429),
            "rate_limited",
            "服务限流或余额不足",
            "稍后重试，检查配额、并发和账户余额。",
        ),
        (
            _ConnectionTimeout(),
            "network",
            "无法连接 LLM 服务",
            "检查网络、代理、防火墙和服务可达性。",
        ),
        (
            _ConnectionFailure(),
            "network",
            "无法连接 LLM 服务",
            "检查网络、代理、防火墙和服务可达性。",
        ),
    ],
    ids=[
        "html_response",
        "status_401",
        "status_403",
        "status_404",
        "status_429",
        "timeout",
        "network_failure",
    ],
)
def test_llm_connection_maps_network_and_http_failures_to_public_results(
    monkeypatch, error, code, message, suggestion,
):
    client = _connection_client(error=error)
    monkeypatch.setattr(frontend_api, "create_openai_client", lambda _settings: client)

    result = frontend_api.test_llm_connection("test-key", "https://example.test/v1", "test-model")

    assert result == {
        "ok": False,
        "code": code,
        "message": message,
        "suggestion": suggestion,
    }


def test_llm_connection_maps_missing_choices_to_protocol_error(monkeypatch):
    client = _connection_client(response=SimpleNamespace(choices=[]))
    monkeypatch.setattr(frontend_api, "create_openai_client", lambda _settings: client)

    result = frontend_api.test_llm_connection("test-key", "https://example.test/v1", "test-model")

    assert result["code"] == "protocol"
    assert result["message"] == "服务不兼容 OpenAI Chat Completions"


def test_llm_connection_maps_missing_tool_call_and_hides_exception_details(monkeypatch):
    secret = "connection-secret-that-must-not-leak"
    client = _connection_client(response=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[]))],
    ))
    monkeypatch.setattr(frontend_api, "create_openai_client", lambda _settings: client)

    no_tool_result = frontend_api.test_llm_connection(
        secret, "https://example.test/v1", "test-model",
    )
    assert no_tool_result["code"] == "tool_call_unsupported"
    assert secret not in str(no_tool_result)

    client = _connection_client(error=Exception(f"untrusted upstream detail: {secret}"))
    monkeypatch.setattr(frontend_api, "create_openai_client", lambda _settings: client)
    protocol_result = frontend_api.test_llm_connection(
        secret, "https://example.test/v1", "test-model",
    )
    assert protocol_result["code"] == "protocol"
    assert secret not in str(protocol_result)


@pytest.mark.llm_connection
def test_real_llm_connection_is_explicitly_opt_in():
    """Exercise the configured endpoint only under --run-llm-connection."""
    from willy.llm_config import load_llm_settings

    settings = load_llm_settings(frontend_api.ROOT)
    if settings is None:
        pytest.skip("requires configured WILLY_LLM_API_KEY or DEEPSEEK_API_KEY")

    result = frontend_api.test_llm_connection(
        settings.api_key,
        settings.base_url,
        settings.model,
    )

    assert result["ok"], result["message"]


def test_visualization_scans_only_pdb_and_mol2_from_current_run_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_id = "md_demo_202608020001"
    run_dir = _record_run_status(tmp_path, run_id, {"state": "running", "step": 2})
    mol2 = run_dir / "Li.mol2"
    pdb = run_dir / "model.pdb"
    log = run_dir / "engine.log"
    nested_pdb = run_dir / "equilibration" / "frame.PDB"
    mol2.write_text("@<TRIPOS>MOLECULE\nLi\n")
    pdb.write_text("ATOM      1  LI  LI  A   1       0.000   0.000   0.000\n")
    log.write_text("internal log")
    nested_pdb.parent.mkdir()
    nested_pdb.write_text("ATOM      1  XX  XX  A   1       0.000   0.000   0.000\n")
    outside_pdb = tmp_path / "outside.pdb"
    outside_pdb.write_text("ATOM      1  YY  YY  A   1       0.000   0.000   0.000\n")
    (run_dir / "linked-outside.pdb").symlink_to(outside_pdb)
    monkeypatch.setattr(frontend_api, "get_active_run_id", lambda: run_id)

    assert frontend_api.get_run_visualization_choices() == [
        "Li.mol2",
        "equilibration/frame.PDB",
        "model.pdb",
    ]
    html = frontend_api.render_run_visualization_html("Li.mol2")
    assert "&quot;mol2&quot;" in html
    assert "Li.mol2" in html
    assert 'class="structure-viewer-frame"' in html
    assert "height:43.2rem" in html
    assert '<div class="structure-viewer-name">Li.mol2</div>' in html
    assert str(run_dir) not in html
    assert "engine.log" not in str(frontend_api.get_run_visualization_choices())
    assert "linked-outside.pdb" not in frontend_api.get_run_visualization_choices()

    monkeypatch.setattr(frontend_api, "get_active_run_id", lambda: None)
    assert frontend_api.get_run_visualization_choices() == [
        "Li.mol2",
        "equilibration/frame.PDB",
        "model.pdb",
    ]


def test_visualization_prefers_active_lock_then_newest_numbered_run_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    oldest = tmp_path / "md_run" / "md__202608040002"
    newest = tmp_path / "md_run" / "md__202608040010"
    oldest.mkdir(parents=True)
    newest.mkdir(parents=True)
    (oldest / "oldest.pdb").write_text("ATOM      1  LI  LI  A   1       0.000   0.000   0.000\n")
    (newest / "newest.mol2").write_text("@<TRIPOS>MOLECULE\nNEW\n")

    monkeypatch.setattr(frontend_api, "get_active_run_id", lambda: None)
    assert frontend_api.get_run_visualization_choices() == ["newest.mol2"]

    monkeypatch.setattr(frontend_api, "get_active_run_id", lambda: "md__202608040002")
    assert frontend_api.get_run_visualization_choices() == ["oldest.pdb"]


def test_visualization_run_and_file_choices_are_scoped_to_the_selected_run(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    older = tmp_path / "md_run" / "md__202608040002"
    newer = tmp_path / "md_run" / "md__202608040010"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    (older / "eq.pdb").write_text("ATOM      1  LI  LI  A   1       0.000   0.000   0.000\n")
    (older / "model.mol2").write_text("@<TRIPOS>MOLECULE\nOLDER\n")
    (newer / "prod.pdb").write_text("ATOM      1  NA  NA  A   1       0.000   0.000   0.000\n")

    monkeypatch.setattr(frontend_api, "get_active_run_id", lambda: None)
    assert frontend_api.get_run_visualization_run_choices() == [
        "md__202608040010", "md__202608040002",
    ]
    assert frontend_api.get_run_visualization_file_choices("md__202608040002") == [
        "eq.pdb", "model.mol2",
    ]
    assert frontend_api.get_run_visualization_file_choices("md__202608040010") == ["prod.pdb"]

    older_data = frontend_api.get_run_visualization_data(
        "model.mol2", "md__202608040002"
    )
    assert older_data is not None
    assert "OLDER" in older_data["content"]
    assert frontend_api.get_run_visualization_data("model.mol2", "md__202608040010") is None


def test_visualization_style_uses_balanced_defaults_and_clamps_user_values():
    pdb_data = (
        "ATOM      1  H4  MOL A   1       0.000   0.000   0.000  1.00  0.00              \n"
        "ATOM      2  F10 MOL A   1       0.000   0.000   1.000  1.00  0.00              \n"
    )
    normalized_pdb = frontend_api._pdb_with_explicit_elements(pdb_data)
    assert normalized_pdb.splitlines()[0][12:16].strip() == "H"
    assert normalized_pdb.splitlines()[1][12:16].strip() == "F"
    assert normalized_pdb.splitlines()[0][76:78] == " H"
    assert normalized_pdb.splitlines()[1][76:78] == " F"

    conflicting_pdb = (
        "ATOM      1  H4  MOL A   1       0.000   0.000   0.000  1.00  0.00           F  \n"
        "ATOM      2  Li100MOL A   1       0.000   0.000   1.000  1.00  0.00           O  \n"
    )
    normalized_conflicting = frontend_api._pdb_with_explicit_elements(conflicting_pdb)
    assert normalized_conflicting.splitlines()[0][76:78] == " H"
    assert normalized_conflicting.splitlines()[1][76:78] == "Li"

    html = frontend_api._render_viewer_html(
        "ATOM      1  LI  LI  A   1       0.000   0.000   0.000\n",
        "pdb",
        1,
        "Li",
    )
    viewer_source = unescape(html)
    assert "radius:0.22" in html
    assert f"scale:{frontend_api.DEFAULT_SPHERE_SCALE * 0.5:.2f}" in html
    assert 'v.addModel(' in viewer_source
    assert '{keepH:true}' in viewer_source
    assert 'var elementColors={' in viewer_source
    assert 'stick:{radius:0.22,color:color}' in viewer_source
    assert 'var metalCations=["Li","Na","Mg","Ca","Zn"]' in viewer_source
    assert 'model.selectedAtoms({}).forEach(function(atom){' in viewer_source
    assert (
        'v.setStyle({serial:atom.serial},{sphere:{scale:0.17,color:color||"#909090"}});'
        in viewer_source
    )

    html = frontend_api._render_viewer_html(
        "ATOM      1  LI  LI  A   1       0.000   0.000   0.000\n",
        "pdb",
        1,
        "Li",
        sphere_scale=9,
        stick_radius=-1,
    )
    viewer_source = unescape(html)
    assert "radius:0.08" in html
    assert f"scale:{frontend_api.VIEWER_SPHERE_SCALE_RANGE[1] * 0.5:.2f}" in html
    assert 'v.setStyle({serial:atom.serial},{sphere:{scale:0.33,color:color||"#909090"}});' in viewer_source


def test_visualization_legend_lists_only_elements_present_in_pdb_and_mol2():
    pdb_data = (
        "ATOM      1  Li1 MOL A   1       0.000   0.000   0.000  1.00  0.00              \n"
        "ATOM      2  O1  MOL A   1       0.000   0.000   1.000  1.00  0.00              \n"
    )
    mol2_data = (
        "@<TRIPOS>MOLECULE\nDemo\n"
        "@<TRIPOS>ATOM\n"
        "1 Na1 0.0 0.0 0.0 Na 1 MOL\n"
        "2 C1 1.0 0.0 0.0 C.3 1 MOL\n"
        "@<TRIPOS>BOND\n"
    )

    assert frontend_api._viewer_legend_elements(pdb_data, "pdb") == ["O", "Li"]
    assert frontend_api._viewer_legend_elements(mol2_data, "mol2") == ["C", "Na"]
    html = frontend_api._viewer_legend_html(pdb_data, "pdb")
    assert 'aria-label="原子颜色图例"' in html
    assert "O 氧" in html
    assert "Li 锂" in html
    assert "C 碳" not in html
    assert "原子颜色：" not in html
    assert "justify-content:center" in html
    assert "radial-gradient(circle at 30% 28%" in html


def test_run_assistant_status_renders_only_public_chinese_activity(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_id = "md_demo_202608010001"
    _record_run_status(tmp_path, run_id, {
        "state": "running",
        "activity": {
            "tool": "G16", "operation": "结构优化",
            "target_type": "molecule", "target": "NO3", "current": 1, "total": 4,
        },
        "error": "",
    })

    markdown = frontend_api.get_run_summary_markdown(run_id)

    assert "正在使用 G16 进行结构优化" in markdown
    assert "当前进度：NO3（1/4）" in markdown
    assert "Agent" not in markdown
    assert "stderr" not in markdown
    assert "pipeline-status-indicator--running" in markdown


def test_run_summary_renders_local_mdrun_heartbeat_without_inventing_eta(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_id = "md_demo_202608020006"
    run_dir = _record_run_status(tmp_path, run_id, {
        "state": "running", "step": 9, "layer": "simulation", "error": "",
        "activity": {
            "tool": "GROMACS", "operation": "运行模拟",
            "target_type": "stage", "target": "eq", "current": 2, "total": 2,
        },
    })
    (run_dir / "mdrun_eta.json").write_text(json.dumps({
        "schema_version": 2, "stage": "eq", "status": "waiting",
        "observed_at": "2026-08-02T13:37:00+00:00",
        "last_progress_at": "2026-08-02T13:36:55+00:00",
        "last_progress_step": 1553500,
        "process_alive": True, "source": "stage_artifacts",
    }))

    markdown = frontend_api.get_run_summary_markdown(run_id)

    assert "GROMACS 正在运行，暂未获得 ETA。最近心跳：" in markdown
    assert "本地时间（UTC" in markdown
    assert "预计结束：" not in markdown


def test_run_summary_uses_compact_current_stage_eta_label(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_id = "md_demo_202608020007"
    run_dir = _record_run_status(tmp_path, run_id, {
        "state": "running", "step": 9, "layer": "simulation", "error": "",
        "activity": {
            "tool": "GROMACS", "operation": "运行模拟",
            "target_type": "stage", "target": "eq", "current": 2, "total": 2,
        },
    })
    (run_dir / "mdrun_eta.json").write_text(json.dumps({
        "schema_version": 2, "stage": "eq", "status": "available",
        "observed_at": "2026-08-02T13:37:00+00:00",
        "eta_observed_at": "2026-08-02T13:32:41+00:00",
        "estimated_end_at": "2026-08-02T13:53:56+00:00",
        "process_alive": True, "source": "gromacs_log",
        "step": 100, "remaining_seconds": 120,
    }))

    markdown = frontend_api.get_run_summary_markdown(run_id)

    assert "当前步骤预计结束：2026-08-02 21:53:56 本地时间（UTC+08:00）" in markdown
    assert "GROMACS 预计结束：" not in markdown
    assert "预测）" not in markdown


def test_run_assistant_status_animates_completion_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_id = "md_demo_202608010002"
    _record_run_status(tmp_path, run_id, {
        "state": "done",
        "activity": {
            "tool": "GROMACS", "operation": "运行模拟",
            "target_type": "stage", "target": "prod", "current": 2, "total": 2,
        },
        "error": "",
    })

    markdown = frontend_api.get_run_summary_markdown(run_id)

    assert "pipeline-status-indicator--done" in markdown
    assert "pipeline-status-spinner" in markdown
    assert "pipeline-status-check" in markdown
    assert "全流程完成" in markdown


def test_run_assistant_status_shows_public_repair_attempt_and_adjustments(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_id = "md_demo_202608010005"
    _record_run_status(tmp_path, run_id, {
        "state": "retrying",
        "retry_n": 2,
        "retry_max": 3,
        "adjustments": [
            {"name": "恒温耦合时间", "before": "0.5 ps", "after": "2 ps"},
            {"name": "EQ 压强耦合时间", "before": "1 ps", "after": "2 ps"},
        ],
        "activity": {
            "tool": "GROMACS", "operation": "运行模拟",
            "target_type": "stage", "target": "eq", "current": 2, "total": 2,
        },
        "error": "GROMACS 运行模拟失败：eq\n原因：平衡验收未通过",
    })

    markdown = frontend_api.get_run_summary_markdown(run_id)

    assert "自动修复：第 2/3 次" in markdown
    assert "已调整：恒温耦合时间 0.5 ps → 2 ps" in markdown
    assert "已调整：EQ 压强耦合时间 1 ps → 2 ps" in markdown
    assert "raw_output" not in markdown


def test_run_assistant_status_uses_only_public_error_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_id = "md_demo_202608010003"
    _record_run_status(tmp_path, run_id, {
        "state": "aborted",
        "activity": {
            "tool": "G16", "operation": "结构优化",
            "target_type": "molecule", "target": "NO3", "current": 1, "total": 4,
        },
        "error": "G16 结构优化失败：NO3\n原因：SCF 未收敛",
    })

    markdown = frontend_api.get_run_summary_markdown(run_id)

    assert "G16 结构优化失败：NO3" in markdown
    assert "当前工序" not in markdown
    assert "/tmp/" not in markdown
    assert "raw_output" not in markdown


def test_run_assistant_status_ignores_an_orphan_root_status(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_id = "md_demo_202608010004"
    _record_run_status(tmp_path, run_id, {
        "state": "running",
        "activity": {
            "tool": "GROMACS", "operation": "运行模拟",
            "target_type": "stage", "target": "eq", "current": 2, "total": 2,
        },
        "error": "",
    })
    (tmp_path / "status.json").write_text(json.dumps({
        "state": "aborted", "step": 0, "error": "不应显示的根状态错误",
    }))

    markdown = frontend_api.get_run_summary_markdown(run_id)

    assert "正在使用 GROMACS 进行运行模拟" in markdown
    assert "不应显示的根状态错误" not in markdown


def test_execution_profile_snapshot_degrades_without_remote_registry(monkeypatch):
    imports = []
    monkeypatch.setattr(
        frontend_api.importlib,
        "import_module",
        lambda name: imports.append(name) or (_ for _ in ()).throw(ModuleNotFoundError(name)),
    )
    monkeypatch.setattr(
        frontend_api.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not start a process")),
    )

    snapshot = frontend_api.get_execution_profile_snapshot("ssh", None)
    context = frontend_api.get_execution_profile_proposal_context("ssh", None)

    assert imports == [
        "willy.remote_registry",
        "willy.remote_registry",
    ]
    assert snapshot["registry_state"] == "unavailable"
    assert snapshot["selected_mode"] == "ssh"
    assert snapshot["selected_profile_id"] == "ssh-not-configured"
    assert [profile["mode"] for profile in snapshot["profiles"]] == ["local", "ssh", "slurm"]
    assert context["available"] is False
    assert "不会发起连接或提交任务" in context["summary"]


def test_execution_profile_snapshot_redacts_remote_registry_fields(monkeypatch):
    raw_host = "compute-01.internal.example"
    raw_path = "/srv/cluster/private-key"
    raw_command = "sbatch --account=secret"
    module = SimpleNamespace(
        get_public_execution_snapshot=lambda: {
            "profiles": [
                {
                    "profile_id": "research-cluster",
                    "mode": "slurm",
                    "available": True,
                    "connection_state": "ready",
                    "host": raw_host,
                    "key_path": raw_path,
                    "command": raw_command,
                    "label": raw_host,
                },
            ],
        },
    )
    monkeypatch.setattr(frontend_api.importlib, "import_module", lambda _name: module)

    snapshot = frontend_api.get_execution_profile_snapshot("slurm", "research-cluster")
    rendered = str(snapshot)

    assert snapshot["registry_state"] == "ready"
    assert snapshot["selected_mode"] == "slurm"
    assert snapshot["selected_profile_id"] == "research-cluster"
    assert snapshot["profiles"][0] == {
        "profile_id": "research-cluster",
        "mode": "slurm",
        "label": "Slurm 调度",
        "available": True,
        "connection_state": "unknown",
    }
    assert raw_host not in rendered
    assert raw_path not in rendered
    assert raw_command not in rendered


def test_execution_profile_snapshot_adapts_initial_capability_map(monkeypatch):
    raw_host = "gpu-login.internal.example"
    module = SimpleNamespace(
        public_remote_capabilities=lambda: {
            "available": True,
            "profiles": {
                "Lab_GPU": {
                    "profile": "Lab_GPU",
                    "launcher": "direct",
                    "transfer": "rsync",
                    "gpu_policy": "required",
                    "host": raw_host,
                },
            },
        },
    )
    monkeypatch.setattr(frontend_api.importlib, "import_module", lambda _name: module)

    snapshot = frontend_api.get_execution_profile_snapshot("ssh", "Lab_GPU")

    assert snapshot["selected_profile_id"] == "Lab_GPU"
    assert snapshot["profiles"] == [
        {
            "profile_id": "Lab_GPU",
            "mode": "ssh",
            "label": "SSH 执行",
            "available": True,
            "connection_state": "unknown",
        },
        {
            "profile_id": "local-default",
            "mode": "local",
            "label": "本机执行",
            "available": True,
            "connection_state": "ready",
        },
        {
            "profile_id": "slurm-not-configured",
            "mode": "slurm",
            "label": "Slurm 调度",
            "available": False,
            "connection_state": "not_configured",
        },
    ]
    assert "已在本机登记，尚未进行连接预检" in snapshot["summary"]
    assert raw_host not in str(snapshot)


def test_execution_profile_context_is_non_persistent_and_selection_scoped(monkeypatch):
    module = SimpleNamespace(
        get_public_execution_snapshot=lambda: {
            "profiles": [
                {"profile_id": "ssh-default", "mode": "ssh", "available": True},
            ],
        },
    )
    monkeypatch.setattr(frontend_api.importlib, "import_module", lambda _name: module)

    context = frontend_api.get_execution_profile_proposal_context("ssh", "ssh-default")

    assert context == {
        "execution_mode": "ssh",
        "execution_profile_id": "ssh-default",
        "available": True,
        "summary": "已选择SSH 执行配置，已在本机登记，尚未进行连接预检。连接详情已隐藏；此页面不会发起连接或提交任务。",
    }
    assert not hasattr(frontend_api, "set_execution_profile")
