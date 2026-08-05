"""Atomic launch ownership and run-local state tests."""

from __future__ import annotations

import json
import os

import pytest
from unittest.mock import MagicMock

from willy.pipeline_launch import (
    PipelineLockConflict,
    active_pipeline_run_id,
    read_startup_audit,
    reserve_pipeline_launch,
    write_startup_audit,
)
from willy.pipeline_state import PipelineStateMachine, State


def test_reservation_is_atomic_and_allocates_a_run_id(tmp_path):
    reservation = reserve_pipeline_launch(tmp_path)
    try:
        assert reservation.run_dir.is_dir()
        assert reservation.run_id.startswith("md__")
        assert active_pipeline_run_id(tmp_path) == reservation.run_id
        with pytest.raises(PipelineLockConflict) as conflict:
            reserve_pipeline_launch(tmp_path)
        assert conflict.value.run_id == reservation.run_id
    finally:
        reservation.release()

    assert not (tmp_path / ".pipeline.lock").exists()


def test_reservation_derives_the_next_sequence_from_existing_run_directories(tmp_path):
    from willy import pipeline_launch

    today = pipeline_launch.datetime.now().strftime("%Y%m%d")
    (tmp_path / "md_run" / f"md__{today}0003").mkdir(parents=True)

    reservation = reserve_pipeline_launch(tmp_path)
    try:
        assert reservation.run_id == f"md__{today}0004"
        assert not (tmp_path / ".md_counter").exists()
    finally:
        reservation.release()


def test_legacy_live_lock_remains_authoritative_until_its_owner_exits(tmp_path):
    (tmp_path / ".pipeline.lock").write_text(f"{os.getpid()}\n")

    with pytest.raises(PipelineLockConflict):
        reserve_pipeline_launch(tmp_path)


def test_frontend_recovers_the_current_run_from_a_live_legacy_lock(tmp_path, monkeypatch):
    import willy.frontend_api as frontend_api
    from willy.run_registry import RunRegistry

    run_id = "md_demo_202608010001"
    run_dir = tmp_path / "md_run" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text("{}")
    registry = RunRegistry(tmp_path)
    registry.register_run(run_dir, backend="g16", total_steps=10)
    registry.record_status(run_dir, {
        "state": "running", "step": 9, "activity": {}, "error": "", "error_kind": "",
    }, "step_started")
    (tmp_path / ".pipeline.lock").write_text(f"{os.getpid()}\n")
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)

    assert frontend_api.get_active_run_id() == run_id


def test_unbound_startup_audit_has_only_public_messages(tmp_path):
    write_startup_audit(tmp_path, "lock_conflict")
    assert read_startup_audit(tmp_path) == {
        "state": "lock_conflict", "message": "已有任务运行",
    }

    write_startup_audit(tmp_path, "failed")
    payload = (tmp_path / "startup_audit.json").read_text()
    assert "启动失败" in payload
    assert "raw_output" not in payload
    assert "/tmp/" not in payload


def test_frontend_launch_conflict_does_not_spawn_a_second_child(tmp_path, monkeypatch):
    import willy.agent_config as agent_config

    monkeypatch.setattr(agent_config, "ROOT", tmp_path)
    config = {"backend": "g16", "residues": {"Li": 1}}
    monkeypatch.setattr(agent_config, "validate_config", lambda config: [])
    popen = MagicMock()
    monkeypatch.setattr(agent_config.subprocess, "Popen", popen)

    active = reserve_pipeline_launch(tmp_path)
    try:
        receipt = agent_config.start_pipeline(config)
    finally:
        active.release()

    assert receipt.state == "lock_conflict"
    assert receipt.run_id == active.run_id
    assert receipt.message == "已有任务运行，未启动第二个子进程。"
    popen.assert_not_called()


def test_deferred_state_machine_does_not_write_root_before_run_binding(tmp_path, monkeypatch):
    import willy.pipeline_state as pstate

    monkeypatch.setattr(pstate, "get_project_root", lambda: tmp_path)
    state = PipelineStateMachine(total_steps=10, defer_writes=True)
    state.transition(State.RUNNING)
    state.set_step(1, "G16 结构优化", "quantum")

    assert not (tmp_path / "status.json").exists()

    run_status = tmp_path / "md_run" / "md_demo_202608010001" / "status.json"
    state.bind_status_path(run_status)
    state.set_extra(run_id="md_demo_202608010001")

    assert not (tmp_path / "status.json").exists()
    payload = json.loads(run_status.read_text())
    assert payload["extra"]["run_id"] == "md_demo_202608010001"
    assert payload["step"] == 1


def test_orchestrator_binds_state_to_run_before_any_status_write(tmp_path, monkeypatch):
    import willy.pipeline_orchestrator as orchestrator_module
    import willy.pipeline_state as pstate
    from willy.pipeline_orchestrator import PipelineOrchestrator
    from willy.simulation.protocol import default_md_config

    (tmp_path / "struct").mkdir()
    (tmp_path / "struct" / "Li.gjf").write_text("Li input")
    (tmp_path / "config.json").write_text(json.dumps({
        "molecules": {"Li": {"charge": 0}},
        "residues": {"Li": 1},
        "md": default_md_config(),
    }))
    monkeypatch.setattr(orchestrator_module, "ROOT", tmp_path)
    monkeypatch.setattr(pstate, "get_project_root", lambda: tmp_path)

    run_dir = tmp_path / "md_run" / "md_demo_202608010002"
    orchestrator = PipelineOrchestrator(use_llm=False)
    orchestrator._prepare_run_directory(run_dir)

    assert not (tmp_path / "status.json").exists()
    status = json.loads((run_dir / "status.json").read_text())
    assert status["run_id"] == run_dir.name
