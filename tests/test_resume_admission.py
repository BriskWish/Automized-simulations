"""Same-run admission requires unchanged inputs or explicit version-bound adoption."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import willy.frontend_api as frontend_api
from willy.resume_admission import RESUME_HASH_POLICY, ResumeAdmissionError, validate_resume_admission
from willy.run_metadata import update_run_manifest_section
from willy.simulation.manifest import RunLock, load_manifest, record_stage_result, request_safe_stop, stop_requested
from tests.test_run_control import _IdleThread, _Process, _Reservation, _aborted_run


def _inputs(tmp_path, step, backend="g16"):
    registry, directory = _aborted_run(tmp_path, step=step)
    config_path = directory / "config.json"
    config = json.loads(config_path.read_text())
    config["backend"] = backend
    config_path.write_text(json.dumps(config))
    for suffix in ("inp", "fchk", "molden", "mol2", "chg"):
        (directory / f"Li.{suffix}").write_text("quantum input\n")
    for suffix in ("fchk", "molden"):
        (directory / f"Li_opt.{suffix}").write_text("single point input\n")
    (directory / ".assembly_itp").mkdir()
    (directory / ".assembly_itp" / "Li.itp").write_text("assembled input\n")
    (directory / "topol.top").write_text('#include ".assembly_itp/Li.itp"\n')
    update_run_manifest_section(directory, "topology", {
        "schema_version": 1, "backend": "sobtop", "forcefield_family": "gaff",
        "components": [{
            "residue_name": "Li", "success": True, "validated": True,
            "itp": str(directory / "Li.itp"), "gro": str(directory / "Li.gro"),
        }],
    })
    status = registry.get_run_status(directory.name, reconcile=False)
    from tests.run_contract_fixture import seed_contracts
    seed_contracts(directory)
    return registry, directory, status


def _check(directory, status, step, backend="g16"):
    return validate_resume_admission(directory, backend=backend, status=status, restart_step=step)


def _launch_stub(monkeypatch, tmp_path, directory):
    reservation = _Reservation(directory)
    spawned = []
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    monkeypatch.setattr(frontend_api, "reserve_existing_run_launch", lambda *_args: reservation)
    monkeypatch.setattr(frontend_api.threading, "Thread", _IdleThread)
    monkeypatch.setattr(
        frontend_api, "_spawn_controlled_run",
        lambda *args, **kwargs: spawned.append((args, kwargs)) or _Process(),
    )
    return reservation, spawned


@pytest.mark.parametrize("step", range(1, 11))
def test_resume_accepts_complete_inputs_at_each_restart_step(tmp_path, step):
    _registry, directory, status = _inputs(tmp_path, step)
    before = (directory / "run_manifest.json").read_bytes()

    report = _check(directory, status, step)

    assert report.restart_step == step
    assert report.checked_files > 1
    assert report.public_summary()["hash_policy"] == RESUME_HASH_POLICY
    assert (directory / "run_manifest.json").read_bytes() == before


@pytest.mark.parametrize("backend", ("g09", "orca"))
@pytest.mark.parametrize("step", (1, 2, 3))
def test_quantum_resume_checks_backend_specific_input(tmp_path, backend, step):
    _registry, directory, status = _inputs(tmp_path, step, backend)
    _check(directory, status, step, backend)
    suffix = ("inp" if backend == "orca" else "gjf") if step == 1 else (
        "molden" if backend == "orca" else "fchk"
    )
    filename = "Li_opt.fchk" if step == 3 else f"Li.{suffix}"
    (directory / filename).write_bytes(b"")
    with pytest.raises(ResumeAdmissionError) as caught:
        _check(directory, status, step, backend)
    assert caught.value.code == "input_empty"


@pytest.mark.parametrize("filename", ("topol.top", "Li.itp", ".assembly_itp/Li.itp", "prod.mdp", "eq.gro", "eq.cpt"))
@pytest.mark.parametrize("change", ("same_size", "different_size"))
def test_nonempty_hash_changes_are_rejected_without_rewriting_history(tmp_path, filename, change):
    _registry, directory, status = _inputs(tmp_path, 10)
    before = (directory / "run_manifest.json").read_bytes()
    target = directory / filename
    replacement = b"X" * target.stat().st_size if change == "same_size" else b"manually updated input\n"
    target.write_bytes(replacement)

    with pytest.raises(ResumeAdmissionError, match="哈希变化"):
        _check(directory, status, 10)

    assert target.read_bytes() == replacement
    assert (directory / "run_manifest.json").read_bytes() == before


def test_valid_config_hash_change_keeps_original_snapshot_and_history(tmp_path):
    _registry, directory, status = _inputs(tmp_path, 10)
    previous = load_manifest(directory)["config"]
    config_path = directory / "config.json"
    config = json.loads(config_path.read_text())
    config["md"]["run_seed"] = 23
    config_path.write_text(json.dumps(config))
    current = config_path.read_bytes()

    with pytest.raises(ResumeAdmissionError, match="稳定配置"):
        _check(directory, status, 10)

    assert config_path.read_bytes() == current
    assert load_manifest(directory)["config"] == previous


@pytest.mark.parametrize("filename", ("topol.top", "Li.itp", ".assembly_itp/Li.itp", "em.mdp", "eq.mdp", "prod.mdp", "model.pdb"))
@pytest.mark.parametrize("change", ("missing", "empty"))
def test_missing_or_empty_reused_inputs_reject_without_launch_or_state_change(tmp_path, monkeypatch, filename, change):
    registry, directory, status = _inputs(tmp_path, 8)
    target = directory / filename
    if change == "missing":
        target.unlink()
    else:
        target.write_bytes(b"")
    reservation, spawned = _launch_stub(monkeypatch, tmp_path, directory)
    config_before = (directory / "config.json").read_bytes()

    reply = frontend_api.resume_aborted_run(directory.name)

    assert "原样续跑未启动" in reply
    assert not spawned
    assert reservation.released
    assert registry.get_run_status(directory.name, reconcile=False) == status
    assert (directory / "config.json").read_bytes() == config_before
    assert registry._read_registry_manifest(directory)["control_history"][-1]["outcome"] == "rejected"
    events = (directory / "events.jsonl").read_text()
    assert '"resume_admission_checked"' in events
    assert f'"reason": "input_{change}"' in events
    assert str(tmp_path) not in reply + events


@pytest.mark.parametrize("step,parent", ((9, "em"), (10, "eq")))
def test_files_do_not_replace_upstream_acceptance(tmp_path, step, parent):
    _registry, directory, status = _inputs(tmp_path, step)
    record_stage_result(directory, parent, success=False, contract={})
    with pytest.raises(ResumeAdmissionError) as caught:
        _check(directory, status, step)
    assert caught.value.code == "upstream_unaccepted"
    assert load_manifest(directory)["stages"][parent]["status"] == "failed"


@pytest.mark.parametrize("step,parent,suffix", (
    (9, "em", "tpr"), (9, "em", "gro"), (9, "em", "xtc"), (9, "em", "edr"),
    (10, "eq", "tpr"), (10, "eq", "gro"), (10, "eq", "xtc"), (10, "eq", "edr"), (10, "eq", "cpt"),
))
def test_upstream_acceptance_does_not_hide_empty_outputs(tmp_path, step, parent, suffix):
    _registry, directory, status = _inputs(tmp_path, step)
    (directory / f"{parent}.{suffix}").write_bytes(b"")
    with pytest.raises(ResumeAdmissionError) as caught:
        _check(directory, status, step)
    assert caught.value.code == "input_empty"
    assert load_manifest(directory)["stages"][parent]["status"] == "accepted"


@pytest.mark.parametrize("step", (5, 9, 10))
def test_absent_acceptance_section_is_not_fabricated(tmp_path, step):
    _registry, directory, status = _inputs(tmp_path, step)
    section = "topology" if step == 5 else "simulation"
    update_run_manifest_section(directory, section, {})
    before = (directory / "run_manifest.json").read_bytes()
    with pytest.raises(ResumeAdmissionError) as caught:
        _check(directory, status, step)
    assert caught.value.code == "manifest_invalid"
    assert (directory / "run_manifest.json").read_bytes() == before


@pytest.mark.parametrize("step", (9, 10))
def test_late_resume_ignores_unused_quantum_and_box_history(tmp_path, step):
    _registry, directory, status = _inputs(tmp_path, step)
    for filename in ("Li.gjf", "Li.fchk", "Li_opt.fchk", "Li.mol2", "Li.chg", "Li.gro", "model.pdb", "em.mdp"):
        (directory / filename).unlink()
    (directory / "Li_opt.molden").write_bytes(b"")
    _check(directory, status, step)


@pytest.mark.parametrize("step,filename", ((1, "Li.fchk"), (2, "Li_opt.fchk"), (3, "Li.chg")))
def test_empty_quantum_output_is_not_an_input_to_rerun(tmp_path, step, filename):
    _registry, directory, status = _inputs(tmp_path, step)
    (directory / filename).write_bytes(b"")
    _check(directory, status, step)


def test_step_six_regenerates_mdps_and_ignores_current_stage_outputs(tmp_path):
    _registry, directory, status = _inputs(tmp_path, 6)
    for filename in ("em.mdp", "eq.mdp", "prod.mdp"):
        (directory / filename).unlink()
    (directory / "em.gro").write_bytes(b"")
    _check(directory, status, 6)


def test_topology_resume_uses_selected_backend_and_molecule_id(tmp_path):
    _registry, directory, status = _inputs(tmp_path, 4)
    config_path = directory / "config.json"
    config = json.loads(config_path.read_text())
    config["topology"] = {"backend": "oplsaa", "force_field": "oplsaa"}
    config["molecules"]["Li"]["molecule_id"] = "input_Li"
    config_path.write_text(json.dumps(config))
    (directory / "Li.mol2").rename(directory / "input_Li.mol2")
    (directory / "Li.chg").unlink()
    from tests.run_contract_fixture import seed_contracts
    seed_contracts(directory)
    _check(directory, status, 4)


def test_production_without_checkpoint_can_restart_from_accepted_eq(tmp_path):
    _registry, directory, status = _inputs(tmp_path, 10)
    assert not (directory / "prod.cpt").exists()
    (directory / "prod.gro").write_bytes(b"")
    _check(directory, status, 10)


@pytest.mark.parametrize("filename", ("prod.cpt", "prod.tpr", "prod.xtc", "prod.edr", "prod.log", "prod.trr"))
def test_existing_production_checkpoint_requires_nonempty_append_bundle(tmp_path, filename):
    _registry, directory, status = _inputs(tmp_path, 10)
    config_path = directory / "config.json"
    config = json.loads(config_path.read_text())
    config["md"]["outputs"]["trr"] = True
    config_path.write_text(json.dumps(config))
    for suffix in ("cpt", "tpr", "xtc", "edr", "log", "trr"):
        (directory / f"prod.{suffix}").write_text("continuation data")
    from tests.run_contract_fixture import seed_contracts
    seed_contracts(directory)
    _check(directory, status, 10)
    (directory / filename).write_bytes(b"")
    with pytest.raises(ResumeAdmissionError) as caught:
        _check(directory, status, 10)
    assert caught.value.code == "input_empty"
    if filename != "prod.cpt":
        (directory / filename).unlink()
        with pytest.raises(ResumeAdmissionError) as caught:
            _check(directory, status, 10)
        assert caught.value.code == "input_missing"


@pytest.mark.parametrize("done", ([1, 3], [1, 1], [True], "invalid", None, list(range(1, 11))))
def test_inconsistent_progress_is_not_repaired_implicitly(tmp_path, done):
    _registry, directory, status = _inputs(tmp_path, 8)
    status["done_steps"] = done
    with pytest.raises(ResumeAdmissionError) as caught:
        _check(directory, status, 8)
    assert caught.value.code == "invalid_progress"


@pytest.mark.parametrize("step", (None, True, 0, 11, "8"))
def test_invalid_restart_step_has_controlled_reason(tmp_path, step):
    _registry, directory, status = _inputs(tmp_path, 8)
    with pytest.raises(ResumeAdmissionError) as caught:
        _check(directory, status, step)
    assert caught.value.code == "invalid_progress"


@pytest.mark.parametrize("content,reason", (("not JSON", "invalid_config"), ("[]", "invalid_config"), ("", "input_empty")))
def test_invalid_config_has_bounded_public_reason(tmp_path, content, reason):
    _registry, directory, status = _inputs(tmp_path, 8)
    (directory / "config.json").write_text(content)
    with pytest.raises(ResumeAdmissionError) as caught:
        _check(directory, status, 8)
    assert caught.value.code == reason
    assert str(directory) not in str(caught.value)


def test_backend_change_is_not_same_run_resume(tmp_path):
    _registry, directory, status = _inputs(tmp_path, 8, backend="orca")
    with pytest.raises(ResumeAdmissionError) as caught:
        _check(directory, status, 8)
    assert caught.value.code == "backend_mismatch"


@pytest.mark.parametrize("kind,reason", (("external", "unsafe_path"), ("dangling", "input_missing"), ("directory", "input_missing")))
def test_unusable_paths_are_rejected_without_exposing_target(tmp_path, kind, reason):
    _registry, directory, status = _inputs(tmp_path, 8)
    target = directory / "model.pdb"
    target.unlink()
    if kind == "external":
        external = tmp_path / "private-input"
        external.write_text("secret-file-content")
        target.symlink_to(external)
    elif kind == "dangling":
        target.symlink_to(directory / "missing-file")
    else:
        target.mkdir()
    with pytest.raises(ResumeAdmissionError) as caught:
        _check(directory, status, 8)
    assert caught.value.code == reason
    assert str(tmp_path) not in str(caught.value)


def test_unreadable_file_is_not_treated_as_valid_nonempty_input(tmp_path, monkeypatch):
    _registry, directory, status = _inputs(tmp_path, 8)
    original_open = Path.open

    def fail_input(path, *args, **kwargs):
        if path.name == "model.pdb":
            raise PermissionError("private permission details")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_input)
    with pytest.raises(ResumeAdmissionError) as caught:
        _check(directory, status, 8)
    assert caught.value.code == "input_unreadable"
    assert "private" not in str(caught.value)


def test_active_md_lock_rejects_same_run_replay(tmp_path, monkeypatch):
    _registry, directory, status = _inputs(tmp_path, 8)
    reservation, spawned = _launch_stub(monkeypatch, tmp_path, directory)
    with RunLock(directory):
        reply = frontend_api.resume_aborted_run(directory.name)
    assert "占用" in reply
    assert not spawned
    assert reservation.released


@pytest.mark.parametrize("state", ("running", "done", "stopping"))
def test_resume_requires_an_eligible_terminal_or_waiting_state(tmp_path, monkeypatch, state):
    registry, directory, status = _inputs(tmp_path, 8)
    status["state"] = state
    registry.record_status(directory, status, "fixture_state")
    _reservation, spawned = _launch_stub(monkeypatch, tmp_path, directory)
    monkeypatch.setattr(frontend_api, "reconcile_stale_pipeline_state", lambda *_args, **_kwargs: None)
    reply = frontend_api.resume_aborted_run(directory.name)
    assert "只能对" in reply
    assert not spawned


def test_resume_uses_original_parameters_without_applying_pending_action(tmp_path, monkeypatch):
    registry, directory, status = _inputs(tmp_path, 8)
    status["state"] = "awaiting_confirmation"
    status["extra"] = {"pending_action": {
        "action_id": "requires-review", "state": "pending", "restart_step": 9,
        "step_label": "EQ", "summary": "需要确认调整方案",
    }}
    registry.record_status(directory, status, "fixture_pending_action")
    _reservation, spawned = _launch_stub(monkeypatch, tmp_path, directory)
    before = (directory / "config.json").read_bytes()
    reply = frontend_api.resume_aborted_run(directory.name)
    assert "已接受 /resume" in reply
    assert len(spawned) == 1
    assert (directory / "config.json").read_bytes() == before


@pytest.mark.parametrize("state", ("aborted", "awaiting_confirmation", "escalated"))
def test_error_resume_preserves_original_run_and_legal_state_path(tmp_path, monkeypatch, state):
    registry, directory, status = _inputs(tmp_path, 8)
    status.update(state=state, error="执行失败", error_kind="execution_failed")
    registry.record_status(directory, status, "fixture_failure")
    current = registry.get_run_status(directory.name, reconcile=False)
    before = (directory / "config.json").read_bytes()
    _reservation, spawned = _launch_stub(monkeypatch, tmp_path, directory)

    reply = frontend_api.resume_aborted_run(directory.name, state_revision=current["state_revision"])

    assert "已接受 /resume" in reply
    assert len(spawned) == 1
    assert (directory / "config.json").read_bytes() == before
    assert list((tmp_path / "md_run").glob("md__*")) == [directory]
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    transitions = [event["details"]["state"] for event in events if (
        "state" in event.get("details", {})
        and event["details"].get("state_revision", 0) > current["state_revision"]
    )]
    expected = ["aborted"] if state == "escalated" else []
    expected.extend(["awaiting_confirmation", "retrying"])
    assert transitions == expected


def test_escalated_resume_admission_failure_does_not_reopen_state(tmp_path, monkeypatch):
    registry, directory, status = _inputs(tmp_path, 8)
    registry.record_status(directory, {**status, "state": "escalated"}, "fixture_failure")
    before = (directory / "status.json").read_bytes()
    (directory / "model.pdb").write_text("unapproved changed input")
    _reservation, spawned = _launch_stub(monkeypatch, tmp_path, directory)

    reply = frontend_api.resume_aborted_run(directory.name)

    assert "哈希变化" in reply
    assert not spawned
    assert (directory / "status.json").read_bytes() == before


@pytest.mark.parametrize("revision", (0, True, "1"))
def test_original_resume_rejects_stale_or_invalid_revision(tmp_path, monkeypatch, revision):
    registry, directory, _status = _inputs(tmp_path, 8)
    _reservation, spawned = _launch_stub(monkeypatch, tmp_path, directory)
    before = registry.get_run_status(directory.name, reconcile=False)

    reply = frontend_api.resume_aborted_run(directory.name, state_revision=revision)

    assert "运行状态已更新" in reply
    assert not spawned
    assert registry.get_run_status(directory.name, reconcile=False) == before


def test_resume_clears_old_stop_request_but_keeps_new_launch_stop(tmp_path, monkeypatch):
    _registry, directory, _status = _inputs(tmp_path, 8)
    request_safe_stop(directory)
    _launch_stub(monkeypatch, tmp_path, directory)

    def spawn(*_args, **_kwargs):
        assert not stop_requested(directory)
        request_safe_stop(directory)
        return _Process()

    monkeypatch.setattr(frontend_api, "_spawn_controlled_run", spawn)
    reply = frontend_api.resume_aborted_run(directory.name)
    assert "已接受 /resume" in reply
    assert stop_requested(directory)


def test_child_rechecks_inputs_without_rewriting_completed_prefix(tmp_path, monkeypatch):
    import willy.pipeline_orchestrator as orchestrator_module
    import willy.pipeline_state as state_module

    registry, directory, _status = _inputs(tmp_path, 8)
    _launch_stub(monkeypatch, tmp_path, directory)
    assert "已接受 /resume" in frontend_api.resume_aborted_run(directory.name)
    accepted_status = registry.get_run_status(directory.name, reconcile=False)
    (directory / "model.pdb").write_bytes(b"")
    monkeypatch.setattr(orchestrator_module, "ROOT", tmp_path)
    monkeypatch.setattr(state_module, "get_project_root", lambda: tmp_path)
    orchestrator = orchestrator_module.PipelineOrchestrator(
        backend="g16", use_llm=False, controlled_resume_step=8,
    )
    with pytest.raises(ResumeAdmissionError) as caught:
        orchestrator._bind_controlled_resume_run(directory)
    assert caught.value.code == "input_empty"
    assert registry.get_run_status(directory.name, reconcile=False) == accepted_status


def test_child_admission_failure_ends_retry_without_losing_progress(tmp_path, monkeypatch):
    import willy.pipeline_orchestrator as orchestrator_module
    import willy.pipeline_state as state_module

    registry, directory, _status = _inputs(tmp_path, 8)
    _launch_stub(monkeypatch, tmp_path, directory)
    assert "已接受 /resume" in frontend_api.resume_aborted_run(directory.name)
    (directory / "model.pdb").write_bytes(b"")
    monkeypatch.setattr(orchestrator_module, "ROOT", tmp_path)
    monkeypatch.setattr(state_module, "get_project_root", lambda: tmp_path)
    orchestrator = orchestrator_module.PipelineOrchestrator(
        backend="g16", use_llm=False, controlled_resume_step=8,
    )
    monkeypatch.setattr(orchestrator, "_build_steps", lambda *_args: pytest.fail("science must not start"))

    assert orchestrator.run(run_dir=directory) is False

    status = registry.get_run_status(directory.name, reconcile=False)
    assert status["state"] == "aborted"
    assert status["step"] == 8
    assert status["done_steps"] == list(range(1, 8))


def test_launch_failure_releases_reservation_and_returns_to_waiting(tmp_path, monkeypatch):
    registry, directory, _status = _inputs(tmp_path, 8)
    reservation, _spawned = _launch_stub(monkeypatch, tmp_path, directory)

    def fail_launch(*_args, **_kwargs):
        raise OSError("private launch details")

    monkeypatch.setattr(frontend_api, "_spawn_controlled_run", fail_launch)
    reply = frontend_api.resume_aborted_run(directory.name)
    assert "已回到等待状态" in reply
    assert "private" not in reply
    assert reservation.released
    assert registry.get_run_status(directory.name, reconcile=False)["state"] == "awaiting_confirmation"
