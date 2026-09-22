"""Fault exits and human intervention never imply automatic scientific execution."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from willy import run_faults
from willy.pipeline_state import State, validate_state_transition
from willy.run_registry import RunRegistry, RunStateConflict
from tests.test_resume_admission import _inputs


def _fault(tmp_path, *, step=8, phase="execution", kind="unexpected_exception", state="running"):
    registry, directory, status = _inputs(tmp_path, step)
    registry.record_status(directory, {**status, "state": state, "error_kind": "mdrun_failed"}, "fixture_state")
    status = run_faults.record_fault(tmp_path, directory.name, phase=phase, error_kind=kind)
    return registry, directory, status


@pytest.mark.parametrize("state", ["idle", "running", "retrying", "awaiting_confirmation", "stopping", "aborted", "escalated"])
def test_noncompleted_states_have_fault_exit_with_origin(tmp_path, state):
    registry, directory, status = _fault(tmp_path, state=state)
    validate_state_transition(state, State.ESCALATED)
    assert status["state"] == "escalated"
    assert status["fault"]["state_at_error"] == state
    assert status["fault"]["step"] == 8
    assert status["fault"]["cause_error_kind"] == "mdrun_failed"
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    assert events[-1]["details"]["fault"]["fault_id"] == status["fault"]["fault_id"]
    assert registry.get_run_status(directory.name, reconcile=False)["state"] == "escalated"


def test_done_is_not_regressed_by_later_ui_or_child_failure(tmp_path):
    registry, directory, status = _inputs(tmp_path, 8)
    registry.record_status(directory, {**status, "state": "done"}, "fixture_done")
    before = (directory / "status.json").read_bytes()
    run_faults.record_fault(tmp_path, directory.name, phase="launch")
    assert (directory / "status.json").read_bytes() == before


def test_refresh_does_not_claim_unknown_error_was_fixed(tmp_path):
    registry, directory, status = _fault(tmp_path)
    before = (directory / "status.json").read_bytes()
    for _attempt in range(3):
        review = run_faults.review_fault(tmp_path, directory.name)
        assert review["status"] == "blocked"
        assert review["manual_completion_allowed"] is True
    assert (directory / "status.json").read_bytes() == before
    assert registry.get_run_status(directory.name, reconcile=False)["state_revision"] == status["state_revision"]


def test_declared_manual_completion_is_atomic_idempotent_and_does_not_run(tmp_path):
    registry, directory, status = _fault(tmp_path)
    before_config = (directory / "config.json").read_bytes()
    declaration = {"state_revision": status["state_revision"], "fault_id": status["fault"]["fault_id"]}
    review = run_faults.review_fault(tmp_path, directory.name, declaration=declaration)
    assert review["status"] == "resolved"
    updated = registry.get_run_status(directory.name, reconcile=False)
    assert updated["state"] == "aborted"
    assert updated["fault"]["resolution_basis"] == "manual_declaration"
    assert updated["error_kind"] == ""
    assert updated["fault"]["error_kind"] == status["error_kind"]
    assert updated["done_steps"] == status["done_steps"]
    assert (directory / "config.json").read_bytes() == before_config
    assert run_faults.review_fault(tmp_path, directory.name) == {}
    with pytest.raises(RunStateConflict):
        run_faults.review_fault(tmp_path, directory.name, declaration=declaration)
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    assert len([event for event in events if event["event_type"] == "run_fault_resolved"]) == 1
    assert not any(event["event_type"] == "controlled_retry_started" for event in events)


@pytest.mark.parametrize("declaration", [{"state_revision": True}, {"state_revision": 0}, {"state_revision": "2"}])
def test_invalid_manual_revision_is_rejected(tmp_path, declaration):
    _registry, directory, _status = _fault(tmp_path)
    with pytest.raises(RunStateConflict):
        run_faults.review_fault(tmp_path, directory.name, declaration=declaration)


@pytest.mark.parametrize("kind", ["dependency_missing", "dependency_no_exec", "runtime_unavailable"])
def test_environment_intervention_is_detected_on_refresh(tmp_path, monkeypatch, kind):
    import willy.env_checker as checker
    registry, directory, status = _fault(tmp_path, kind=kind, phase="preflight")
    available = [False]
    monkeypatch.setattr(checker, "check_module", lambda module: SimpleNamespace(results=[object()], is_ok=lambda _: available[0]))
    assert run_faults.review_fault(tmp_path, directory.name)["status"] == "blocked"
    available[0] = True
    assert run_faults.review_fault(tmp_path, directory.name)["status"] == "resolved"
    updated = registry.get_run_status(directory.name, reconcile=False)
    assert updated["state"] == "aborted"
    assert updated["fault"]["fault_id"] == status["fault"]["fault_id"]


def test_input_repair_requires_original_hash_or_declared_adoption(tmp_path):
    registry, directory, _status = _fault(tmp_path, kind="input_contract")
    path = directory / "model.pdb"
    original = path.read_bytes()
    path.write_text("changed without input declaration")
    import willy.frontend_api as api
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(api, "ROOT", tmp_path)
        assert api.refresh_run_fault(directory.name)["status"] == "blocked"
        assert registry.get_run_status(directory.name, reconcile=False)["state"] == "escalated"
        path.write_bytes(original)
        assert api.refresh_run_fault(directory.name)["status"] == "resolved"


@pytest.mark.parametrize("guard", ["launch", "md", "heartbeat", "process"])
def test_live_or_uncertain_execution_blocks_fault_resolution(tmp_path, monkeypatch, guard):
    from willy.pipeline_launch import reserve_existing_run_launch
    from willy.simulation.manifest import RunLock
    registry, directory, _status = _fault(tmp_path, phase="stopping", kind="stop_timeout")
    reservation = reserve_existing_run_launch(tmp_path, directory.name) if guard == "launch" else None
    md_lock = RunLock(directory) if guard == "md" else None
    if md_lock:
        md_lock.acquire()
    if guard == "heartbeat":
        monkeypatch.setattr(RunRegistry, "get_live_stage_evidence", lambda *_args: {"active": True})
    if guard == "process":
        monkeypatch.setattr(run_faults, "_remaining_processes", lambda _: True)
    try:
        review = run_faults.review_fault(tmp_path, directory.name)
        assert review["reason"] == "process_exit_unconfirmed"
        assert registry.get_run_status(directory.name, reconcile=False)["state"] == "escalated"
    finally:
        if reservation:
            reservation.release()
        if md_lock:
            md_lock.release()


def test_stop_timeout_can_escalate_then_refresh_after_process_exit(tmp_path, monkeypatch):
    registry, directory, status = _inputs(tmp_path, 8)
    registry.record_status(directory, {**status, "state": "stopping"}, "fixture_stop")
    marker = directory / "stop.request"
    marker.write_text("stop")
    os.utime(marker, (1, 1))
    alive = [True]
    monkeypatch.setattr(run_faults, "_remaining_processes", lambda _: alive[0])
    assert run_faults.review_fault(tmp_path, directory.name)["status"] == "blocked"
    failed = registry.get_run_status(directory.name, reconcile=False)
    assert failed["state"] == "escalated"
    assert failed["fault"]["phase"] == "stopping"
    assert failed["fault"]["error_kind"] == "stop_timeout"
    alive[0] = False
    assert run_faults.review_fault(tmp_path, directory.name)["status"] == "resolved"
    assert registry.get_run_status(directory.name, reconcile=False)["state"] == "aborted"


def test_recheck_cas_cannot_overwrite_a_new_fault(tmp_path, monkeypatch):
    registry, directory, _status = _fault(tmp_path, phase="cleanup", kind="cleanup_failed")

    def change_before_commit(*_args):
        run_faults.record_fault(tmp_path, directory.name, phase="execution")
        return "process_exit"

    monkeypatch.setattr(run_faults, "_resolution_basis", change_before_commit)
    with pytest.raises(RunStateConflict):
        run_faults.review_fault(tmp_path, directory.name)
    assert registry.get_run_status(directory.name, reconcile=False)["state"] == "escalated"


@pytest.mark.parametrize("step", range(1, 11))
def test_unknown_exception_at_each_step_records_exact_origin(tmp_path, monkeypatch, step):
    from willy.pipeline_orchestrator import PipelineOrchestrator
    import willy.pipeline_orchestrator as module

    registry, directory, status = _inputs(tmp_path, step)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    orchestrator = PipelineOrchestrator(backend="g16", use_llm=False)
    orchestrator._run_dir = directory
    registry.record_status(directory, {**status, "state": "running"}, "fixture_execute")

    def unexpected(_directory):
        orchestrator._fault_phase = "execution"
        raise RuntimeError("private-secret-value")

    monkeypatch.setattr(orchestrator, "_run", unexpected)
    assert orchestrator.run(directory) is False
    failed = registry.get_run_status(directory.name, reconcile=False)
    assert failed["state"] == "escalated"
    assert failed["fault"]["step"] == step
    assert "private-secret-value" not in (directory / "status.json").read_text()
    assert "private-secret-value" not in (directory / "events.jsonl").read_text()
    diagnostic = json.loads((directory / ".fault_diagnostics.jsonl").read_text())
    assert diagnostic["frames"][-1]["function"] == "unexpected"


def test_process_identity_survives_runner_exit_and_blocks_new_launch(tmp_path, monkeypatch):
    from willy.pipeline_launch import PipelineLockConflict, reserve_existing_run_launch, reserve_pipeline_launch
    from willy.run_processes import track_process
    import willy.pipeline_launch as launch

    _registry, directory, _status = _fault(tmp_path, phase="stopping")
    alive = [True]
    monkeypatch.setattr(launch, "_pid_start_ticks", lambda _: 77)
    monkeypatch.setattr(launch, "_process_group_is_alive", lambda pid: pid == 1234567 and alive[0])
    track_process(directory, SimpleNamespace(pid=1234567))
    for reserve in (lambda: reserve_existing_run_launch(tmp_path, directory.name), lambda: reserve_pipeline_launch(tmp_path)):
        with pytest.raises(PipelineLockConflict):
            reserve()
    assert run_faults.review_fault(tmp_path, directory.name)["status"] == "blocked"
    alive[0] = False
    assert run_faults.review_fault(tmp_path, directory.name)["status"] == "resolved"


def test_fork_archives_but_does_not_reuse_fault_authority(tmp_path):
    from willy.branching import copy_run_context
    _registry, directory, _status = _fault(tmp_path)
    (directory / ".process_owners.json").write_text('{"1":{"pid":1}}')
    child = directory.with_name("md__202609100002")
    copy_run_context(directory, child, json.loads((directory / "config.json").read_text()))
    assert not (child / ".process_owners.json").exists()
    assert not (child / run_faults.FAULT_CONTEXT).exists()
    assert (directory / ".process_owners.json").exists()


def test_snapshot_refresh_and_manual_completion_update_same_run_only(tmp_path, monkeypatch):
    import app
    import willy.frontend_api as api
    registry, directory, status = _fault(tmp_path)
    monkeypatch.setattr(api, "ROOT", tmp_path)
    snapshot = app.run_snapshot(directory.name)
    assert snapshot["state"] == "escalated"
    assert snapshot["fault_review"]["manual_completion_allowed"]
    payload = {"fault_id": snapshot["fault"]["fault_id"], "state_revision": snapshot["state_revision"]}
    response = app.complete_run_fault(directory.name, payload)
    assert response["run_id"] == directory.name
    assert response["snapshot"]["state"] == "aborted"
    assert response["snapshot"]["fault"]["resolution"] == "resolved"
    assert "未启动计算" in response["text"]
    assert registry.get_run_status(directory.name, reconcile=False)["done_steps"] == status["done_steps"]
    with pytest.raises(app.HTTPException) as conflict:
        app.complete_run_fault(directory.name, payload)
    assert conflict.value.status_code == 409


def test_page_refresh_detects_repaired_environment_without_confirmation(tmp_path, monkeypatch):
    import app
    import willy.frontend_api as api
    import willy.env_checker as checker
    _registry, directory, _status = _fault(tmp_path, phase="preflight", kind="dependency_missing")
    monkeypatch.setattr(api, "ROOT", tmp_path)
    available = [False]
    monkeypatch.setattr(checker, "check_module", lambda _: SimpleNamespace(results=[object()], is_ok=lambda _: available[0]))
    assert app.run_snapshot(directory.name)["state"] == "escalated"
    available[0] = True
    snapshot = app.run_snapshot(directory.name)
    assert snapshot["state"] == "aborted"
    assert "人工处理结果已通过复查" in snapshot["summary"]


def test_stop_signal_failure_preserves_pid_and_records_control_fault(tmp_path, monkeypatch):
    import willy.frontend_api as api
    registry, directory, status = _inputs(tmp_path, 3)
    registry.record_status(directory, {**status, "state": "running"}, "fixture_running")
    pid_file = tmp_path / ".pipeline.pid"
    pid_file.write_text("7654321")
    monkeypatch.setattr(api, "ROOT", tmp_path)
    monkeypatch.setattr(api, "get_active_run_id", lambda: directory.name)

    def signal_failure(*_args):
        raise PermissionError("private-signal-details")

    monkeypatch.setattr(api.os, "killpg", signal_failure)
    reply = api.stop_pipeline()
    assert "停止操作失败" in reply
    assert pid_file.exists()
    failed = registry.get_run_status(directory.name, reconcile=False)
    assert failed["state"] == "escalated"
    assert failed["fault"]["phase"] == "stopping"
    assert failed["fault"]["error_kind"] == "stop_failed"
    assert "private-signal-details" not in json.dumps(failed)


def test_new_fault_after_stop_keeps_original_failure_link(tmp_path):
    registry, directory, status = _inputs(tmp_path, 8)
    registry.record_status(directory, {**status, "state": "running", "error_kind": "mdrun_failed"}, "fixture_failure")
    stopped = registry.request_stop(directory.name)
    failed = run_faults.record_fault(tmp_path, directory.name, phase="stopping", error_kind="stop_failed")
    assert failed["fault"]["cause_fault_id"] == stopped["fault"]["fault_id"]
    assert failed["fault"]["cause_error_kind"] == "mdrun_failed"


def test_refresh_waits_for_actual_owned_process_to_exit(tmp_path):
    from willy.run_processes import track_process
    registry, directory, _status = _fault(tmp_path, phase="stopping")
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"], start_new_session=True)
    try:
        track_process(directory, process)
        review = run_faults.review_fault(tmp_path, directory.name)
        assert review["reason"] == "process_exit_unconfirmed"
        assert registry.get_run_status(directory.name, reconcile=False)["state"] == "escalated"
        process.terminate()
        process.wait(timeout=3)
        assert run_faults.review_fault(tmp_path, directory.name)["status"] == "resolved"
        assert registry.get_run_status(directory.name, reconcile=False)["state"] == "aborted"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)


def test_legacy_pipeline_pid_blocks_review_until_gone(tmp_path, monkeypatch):
    import willy.pipeline_launch as launch
    _registry, directory, _status = _fault(tmp_path, phase="stopping")
    (tmp_path / ".pipeline.pid").write_text("7654321")
    alive = [True]
    monkeypatch.setattr(launch, "_pid_alive", lambda pid: pid == 7654321 and alive[0])
    monkeypatch.setattr(launch, "_process_group_is_alive", lambda _: False)
    assert run_faults.review_fault(tmp_path, directory.name)["reason"] == "process_exit_unconfirmed"
    alive[0] = False
    assert run_faults.review_fault(tmp_path, directory.name)["status"] == "resolved"


@pytest.mark.parametrize("content", ["[]", "{broken", '{"1":null}'])
def test_bad_process_register_is_not_overwritten_and_new_process_is_reaped(tmp_path, content):
    from willy.run_processes import OWNERS_FILE, has_live_processes, track_process
    _registry, directory, _status = _fault(tmp_path, phase="stopping")
    path = directory / OWNERS_FILE
    path.write_text(content)
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"], start_new_session=True)
    try:
        with pytest.raises(ValueError, match="进程登记损坏"):
            track_process(directory, process)
        assert process.poll() is not None
        assert path.read_text() == content
        assert has_live_processes(directory)
        assert run_faults.review_fault(tmp_path, directory.name)["reason"] == "process_exit_unconfirmed"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)


@pytest.mark.parametrize("phase", ["initialization", "stopping", "cleanup"])
def test_runner_control_exceptions_record_fault_instead_of_leaving_transient_state(tmp_path, monkeypatch, phase):
    import run_pipeline as runner
    import willy.pipeline_orchestrator as orchestrator_module
    registry, directory, status = _inputs(tmp_path, 8)
    registry.record_status(directory, {**status, "state": "running"}, "fixture_running")

    def execute(**_kwargs):
        if phase == "stopping":
            raise KeyboardInterrupt
        if phase == "initialization":
            raise RuntimeError("private-runner-details")
        return False

    def failed_stop():
        raise RuntimeError("private-stop-details")

    def release():
        if phase == "cleanup":
            raise OSError("private-release-details")

    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(runner, "reserve_pipeline_launch", lambda _root: SimpleNamespace(
        run_dir=directory, mark_runner_started=lambda _pid: None, release=release,
    ))
    monkeypatch.setattr(orchestrator_module, "PipelineOrchestrator", lambda **_kwargs: SimpleNamespace(
        run=execute, _run_dir=directory, abort_from_signal=failed_stop,
    ))
    assert runner.main(["--no-llm"]) == 1
    failed = registry.get_run_status(directory.name, reconcile=False)
    assert failed["state"] == "escalated"
    assert failed["fault"]["phase"] == phase
    assert failed["fault"]["step"] == 8
    assert "private-" not in json.dumps(failed)
