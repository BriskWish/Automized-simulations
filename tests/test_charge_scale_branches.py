"""Charge-scale forks preserve quantum work without reusing old charge authority."""

import json

import pytest

import willy.frontend_api as frontend_api
from willy.branch_controls import create_branch
from willy.branching import copy_run_context
from willy.config_store import write_json
from willy.errors import StepResult
from willy.quantum.charge_files import publish_charge_files, validate_charge_record
from willy.resume_admission import ResumeAdmissionError, validate_resume_admission
from willy.run_control import (
    RunControlError, apply_fork_changes, parameter_owner_step,
    parse_run_control_command, validate_fork_changes,
)
from willy.run_registry import RunRegistry
from willy.simulation.protocol import canonical_json_fingerprint
from willy.step_contracts import (
    CONTRACT_FILENAME, StepContractError, archive_files, begin_step,
    fingerprint, finish_step, load_contracts, step_output_paths, validate_step_inputs,
)
from tests.run_contract_fixture import seed_contracts
from tests.test_resume_admission import _inputs, _launch_stub
from tests.test_run_control import _IdleThread, _Process, _Reservation, _config


pytestmark = pytest.mark.integration


def _snapshot(directory):
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in directory.rglob("*") if path.is_file()
    }


def _charged_parent(tmp_path, *, step=9, scale=None, metadata=True):
    registry, directory, status = _inputs(tmp_path, step)
    config = json.loads((directory / "config.json").read_text())
    config["molecules"]["Li"]["charge"] = 1
    config["non_neutral_confirmed"] = True
    if scale is not None:
        config["ion_charge_scale"] = scale
    write_json(directory / "config.json", config)
    (directory / "Li_opt.fchk").write_text("Number of atoms                            I                1\n")
    raw_text = "Li 0.0 0.0 0.0 1.000000\n"
    if metadata:
        publish_charge_files(directory, "Li", directory / "Li_opt.fchk", 1, 1, scale or 1.0, raw_text)
    else:
        (directory / "Li.chg").write_text(raw_text)
    seed_contracts(directory)
    payload = load_contracts(directory)
    for current in range(1, step):
        outputs = {}
        for path in step_output_paths(directory, current):
            if path.is_file():
                observed = fingerprint(directory, path)
                outputs[observed["path"]] = observed
                payload["artifacts"][observed["path"]] = {
                    **observed, "producer_step": current, "source": "fixture", "accepted": True,
                }
        payload["steps"][str(current)] = {
            "status": "accepted", "attempt_id": f"parent-{current}",
            "inputs": {"config.json": fingerprint(directory, "config.json")}, "outputs": outputs,
        }
    write_json(directory / CONTRACT_FILENAME, payload)
    return registry, directory, status


def _fork_stub(monkeypatch, tmp_path, parent):
    child = parent.with_name("md__202609190002")
    reservation = _Reservation(child)
    spawned = []
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    monkeypatch.setattr(frontend_api, "reserve_pipeline_launch", lambda *_args: reservation)
    monkeypatch.setattr(frontend_api.threading, "Thread", _IdleThread)
    monkeypatch.setattr(
        frontend_api, "_spawn_controlled_run",
        lambda *args, **kwargs: spawned.append((args, kwargs)) or _Process(),
    )
    return child, reservation, spawned


@pytest.mark.parametrize("value", [amount / 100 for amount in range(60, 101)])
def test_fork_accepts_hundredths_and_owns_step_three(value):
    config = _config()
    config["ion_charge_scale"] = 0.6 if value == 1.0 else 1.0
    before = json.dumps(config, sort_keys=True)
    command = parse_run_control_command(f"/fork ion_charge_scale={value:.2f}")
    plan = validate_fork_changes(config, command.changes, stopped_at=10)
    changed = apply_fork_changes(config, command.changes)
    assert parameter_owner_step(("ion_charge_scale",)) == 3
    assert plan.restart_step == 3
    assert plan.parameter_paths == ("ion_charge_scale",)
    assert changed["ion_charge_scale"] == value
    assert type(changed["ion_charge_scale"]) is float
    assert json.dumps(config, sort_keys=True) == before


@pytest.mark.parametrize("value", [True, False, None, "0.80", [], {}, 0.59, 1.01, 0.805, float("nan"), float("inf"), -float("inf")])
def test_fork_rejects_invalid_scale_without_coercion(value):
    changes = {("ion_charge_scale",): value}
    with pytest.raises(RunControlError, match="ion_charge_scale"):
        validate_fork_changes(_config(), changes, stopped_at=9)
    with pytest.raises(RunControlError, match="ion_charge_scale"):
        apply_fork_changes(_config(), changes)


@pytest.mark.parametrize("original,value", [(None, 1.0), (None, 1), (1, 1.0), (0.8, 0.80)])
def test_missing_default_and_equal_values_are_noops(original, value):
    config = _config()
    if original is not None:
        config["ion_charge_scale"] = original
    with pytest.raises(RunControlError, match="未发生变化"):
        validate_fork_changes(config, {("ion_charge_scale",): value}, stopped_at=9)


def test_json_fork_mixed_changes_and_legacy_default():
    config = _config()
    command = parse_run_control_command('/fork {"ion_charge_scale": 0.80, "md": {"eq": {"tau_p": 3}}}')
    assert validate_fork_changes(config, command.changes, stopped_at=9).restart_step == 3
    changed = apply_fork_changes(config, command.changes)
    assert changed["ion_charge_scale"] == 0.8
    assert changed["md"]["eq"]["tau_p"] == 3
    assert "ion_charge_scale" not in config
    assert config["md"]["eq"]["tau_p"] == 2.0
    with pytest.raises(RunControlError):
        parameter_owner_step(("ion_charge_scale", "value"))
    with pytest.raises(RunControlError, match="/resume 不接受参数"):
        parse_run_control_command("/resume ion_charge_scale=0.80")


@pytest.mark.parametrize("scale,metadata", [(None, False), (1.0, True), (0.6, True)])
def test_scale_fork_preserves_prefix_and_archives_downstream(tmp_path, monkeypatch, scale, metadata):
    registry, parent, _status = _charged_parent(tmp_path, scale=scale, metadata=metadata)
    child, reservation, spawned = _fork_stub(monkeypatch, tmp_path, parent)
    original = json.loads((parent / "config.json").read_text())
    before = _snapshot(parent)
    prefix = load_contracts(parent)

    reply = frontend_api.run_assistant_control_command(parent.name, "/fork ion_charge_scale=0.80")

    assert "已创建 fork" in reply
    assert "第 3 步" in reply
    assert reservation.detached
    assert spawned[0][1]["restart_step"] == 3
    assert json.loads((child / "config.json").read_text()) == {**original, "ion_charge_scale": 0.8}
    status = registry.get_run_status(child.name, reconcile=False)
    assert status["step"] == 3
    assert status["done_steps"] == [1, 2]
    assert validate_resume_admission(child, backend="g16", status=status, restart_step=3).restart_step == 3
    contracts = load_contracts(child)
    assert contracts["config_signature"] == canonical_json_fingerprint({**original, "ion_charge_scale": 0.8})
    for step in (1, 2):
        assert contracts["steps"][str(step)] == prefix["steps"][str(step)]
        validate_step_inputs(child, step)
    for name in ("Li.fchk", "Li_opt.fchk", "Li.mol2"):
        assert contracts["artifacts"][name] == prefix["artifacts"][name]
        assert (child / name).read_bytes() == before[name]
    for name, record in contracts["artifacts"].items():
        if record.get("producer_step", 0) >= 3:
            assert record["accepted"] is False, name
    with pytest.raises(StepContractError):
        validate_step_inputs(child, 4)

    downstream = {
        str(path.relative_to(child)): path.read_bytes()
        for step in range(3, 11) for path in step_output_paths(child, step) if path.is_file()
    }
    attempt = begin_step(child, 3)
    for name, content in downstream.items():
        assert not (child / name).exists()
        assert (child / "old" / attempt / "files" / name).read_bytes() == content
    assert (child / "Li_opt.fchk").read_bytes() == before["Li_opt.fchk"]
    assert (child / "Li.mol2").read_bytes() == before["Li.mol2"]
    publish_charge_files(child, "Li", child / "Li_opt.fchk", 1, 1, 0.8, "Li 0.0 0.0 0.0 1.000000\n")
    finish_step(child, 3, attempt, StepResult("chg", 3, True))
    validate_step_inputs(child, 4)
    assert validate_charge_record(child, "Li", 1, 1, 0.8)["effective_total_charge"] == 0.8
    assert _snapshot(parent) == before


def test_changed_factor_cannot_retain_old_charge_adoption(tmp_path):
    _registry, parent, _status = _charged_parent(tmp_path, metadata=False)
    payload = load_contracts(parent)
    payload["artifacts"]["Li.chg"]["producer_step"] = 0
    payload["adoptions"]["Li.chg"] = {**fingerprint(parent, "Li.chg"), "steps": [4]}
    payload["adoptions"]["Li.mol2"] = {**fingerprint(parent, "Li.mol2"), "steps": [3, 4]}
    write_json(parent / CONTRACT_FILENAME, payload)
    before = _snapshot(parent)
    child = parent.with_name("md__202609190002")
    config = json.loads((parent / "config.json").read_text())
    copy_run_context(parent, child, {**config, "ion_charge_scale": 0.8}, restart_step=3)
    contracts = load_contracts(child)
    assert contracts["artifacts"]["Li.chg"]["accepted"] is False
    assert "Li.chg" not in contracts["adoptions"]
    assert contracts["adoptions"]["Li.mol2"] == payload["adoptions"]["Li.mol2"]
    assert all(contracts["steps"][str(step)]["status"] == "invalidated" for step in range(3, 9))
    validate_step_inputs(child, 3)
    with pytest.raises(StepContractError):
        validate_step_inputs(child, 4)
    assert _snapshot(parent) == before


def test_archived_quantum_inputs_are_restored_only_in_child(tmp_path):
    _registry, parent, _status = _charged_parent(tmp_path)
    archive_files(parent, {parent / "Li_opt.fchk", parent / "Li.mol2"}, "quantum-cleanup", step=10)
    before = _snapshot(parent)
    child = parent.with_name("md__202609190002")
    config = json.loads((parent / "config.json").read_text())
    copy_run_context(parent, child, {**config, "ion_charge_scale": 0.8}, restart_step=3)
    validate_step_inputs(child, 3)
    assert (child / "Li_opt.fchk").is_file()
    assert (child / "Li.mol2").is_file()
    assert _snapshot(parent) == before


def test_direct_copy_rejects_late_restart_before_creating_child(tmp_path):
    _registry, parent, _status = _charged_parent(tmp_path)
    before = _snapshot(parent)
    child = parent.with_name("md__202609190002")
    config = json.loads((parent / "config.json").read_text())
    with pytest.raises(ValueError, match="第 3 步"):
        copy_run_context(parent, child, {**config, "ion_charge_scale": 0.8}, restart_step=6)
    assert not child.exists()
    assert _snapshot(parent) == before


@pytest.mark.parametrize("stopped", [1, 2, 3, 9, 11])
def test_branch_restart_is_earliest_of_charge_owner_and_unfinished_step(tmp_path, monkeypatch, stopped):
    registry, parent, status = _charged_parent(tmp_path, step=stopped)
    if stopped == 11:
        registry.record_status(parent, {**status, "state": "done", "step": 10}, "fixture_done")
    child, _reservation, spawned = _fork_stub(monkeypatch, tmp_path, parent)
    config = json.loads((parent / "config.json").read_text())
    before = _snapshot(parent)
    reply = create_branch(parent, {**config, "ion_charge_scale": 0.8}, restart_step=6)
    assert "已创建 fork" in reply
    restart = min(stopped, 3)
    assert spawned[0][1]["restart_step"] == restart
    child_status = registry.get_run_status(child.name, reconcile=False)
    assert child_status["done_steps"] == list(range(1, restart))
    validate_resume_admission(child, backend="g16", status=child_status, restart_step=restart)
    assert _snapshot(parent) == before


@pytest.mark.parametrize("step", [3, 4, 9])
@pytest.mark.parametrize("scale", [None, 1.0, 0.8])
def test_original_resume_preserves_scale_config_and_inputs(tmp_path, monkeypatch, step, scale):
    _registry, directory, status = _charged_parent(tmp_path, step=step, scale=scale, metadata=scale is not None)
    before = _snapshot(directory)
    validate_resume_admission(directory, backend="g16", status=status, restart_step=step)
    assert _snapshot(directory) == before
    _reservation, spawned = _launch_stub(monkeypatch, tmp_path, directory)
    reply = frontend_api.resume_aborted_run(directory.name)
    assert "已接受 /resume" in reply
    assert spawned[0][1]["restart_step"] == step
    assert (directory / "config.json").read_bytes() == before["config.json"]
    assert (directory / CONTRACT_FILENAME).read_bytes() == before[CONTRACT_FILENAME]
    for name, content in before.items():
        if name.startswith("Li"):
            assert (directory / name).read_bytes() == content


@pytest.mark.parametrize("value", [0.8, 0.805, True, "0.80"])
def test_same_run_resume_rejects_edited_scale_without_rebaselining(tmp_path, value):
    _registry, directory, status = _charged_parent(tmp_path, metadata=False)
    config = json.loads((directory / "config.json").read_text())
    write_json(directory / "config.json", {**config, "ion_charge_scale": value})
    before = _snapshot(directory)
    with pytest.raises(ResumeAdmissionError) as caught:
        validate_resume_admission(directory, backend="g16", status=status, restart_step=9)
    assert caught.value.code == ("input_contract" if value == 0.8 else "invalid_config")
    assert _snapshot(directory) == before
