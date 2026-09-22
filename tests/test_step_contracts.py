"""Whole-pipeline input admission and output-generation isolation."""

import json
from pathlib import Path

import pytest

from willy.config_store import write_json
from willy.errors import StepResult
from willy.step_contracts import (
    CONTRACT_FILENAME, StepContractError, archive_files, begin_step, execute_step,
    finish_step, load_contracts, recover_archives, step_input_paths, step_output_paths,
    validate_step_inputs,
)
from tests.test_resume_admission import _inputs


@pytest.mark.parametrize("step", range(1, 11))
def test_every_step_rejects_changed_inputs_before_execution(tmp_path, step):
    _registry, directory, _status = _inputs(tmp_path, step)
    path = next(iter(step_input_paths(directory, step)))
    path.write_text(path.read_text() + " ")
    calls = []
    result = execute_step(directory, step, lambda: calls.append(True))
    assert not result.success
    assert calls == []
    assert not (directory / "old").exists()


@pytest.mark.parametrize("step", range(1, 11))
def test_each_step_archives_old_outputs_before_work_and_commits_hashes(tmp_path, step):
    _registry, directory, _status = _inputs(tmp_path, step)
    for path in step_output_paths(directory, step):
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text("old output")
    inputs = {path: path.read_bytes() for path in step_input_paths(directory, step)}

    def work():
        for path in step_output_paths(directory, step):
            assert not path.exists()
            path.write_text("new output")
        return StepResult("fixture", step, True)

    result = execute_step(directory, step, work)
    assert result.success, result.error
    payload = load_contracts(directory)
    assert payload["steps"][str(step)]["status"] == "accepted"
    for path in step_output_paths(directory, step):
        record = payload["artifacts"][str(path.relative_to(directory))]
        assert record["accepted"] is True
        assert record["producer_step"] == step
        assert list((directory / "old").glob(f"*/files/{path.relative_to(directory)}"))
    assert all(path.read_bytes() == content for path, content in inputs.items())


def test_noop_success_cannot_reuse_old_em_acceptance_files(tmp_path):
    _registry, directory, _status = _inputs(tmp_path, 8)
    for path in step_output_paths(directory, 8):
        path.write_text("old EM output")
    result = execute_step(directory, 8, lambda: StepResult("em", 8, True))
    assert not result.success
    assert load_contracts(directory)["steps"]["8"]["status"] == "failed"
    assert not (directory / "em.gro").exists()
    assert list((directory / "old").glob("*/files/em.gro"))


def test_partial_archive_is_recovered_before_workspace_work(tmp_path, monkeypatch):
    _registry, directory, _status = _inputs(tmp_path, 8)
    for path in step_output_paths(directory, 8):
        path.write_text("old EM output")
    original = Path.replace

    def fail_one(path, target):
        if path == directory / "em.gro":
            raise OSError("interrupted archive")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", fail_one)
    calls = []
    assert not execute_step(directory, 8, lambda: calls.append(True)).success
    assert calls == []
    monkeypatch.setattr(Path, "replace", original)
    recover_archives(directory)
    journals = list((directory / "old").glob("*/archive.json"))
    assert journals
    assert all(json.loads(path.read_text())["state"] == "complete" for path in journals)
    assert not (directory / "em.gro").exists()
    begin_step(directory, 8)


def test_archive_conflict_never_overwrites_either_copy(tmp_path):
    archive = tmp_path / "old" / "interrupted"
    (archive / "files").mkdir(parents=True)
    (tmp_path / "em.gro").write_text("new user data")
    (archive / "files" / "em.gro").write_text("old data")
    write_json(archive / "archive.json", {"state": "moving", "files": ["em.gro"]})
    with pytest.raises(StepContractError, match="冲突"):
        recover_archives(tmp_path)
    assert (tmp_path / "em.gro").read_text() == "new user data"
    assert (archive / "files" / "em.gro").read_text() == "old data"


def test_output_alias_cannot_archive_an_input(tmp_path):
    _registry, directory, _status = _inputs(tmp_path, 8)
    target = directory / "em.gro"
    target.unlink(missing_ok=True)
    target.symlink_to(directory / "config.json")
    before = (directory / "config.json").read_bytes()
    with pytest.raises(StepContractError, match="符号链接"):
        begin_step(directory, 8)
    assert (directory / "config.json").read_bytes() == before


def test_missing_baseline_is_never_fabricated_by_resume(tmp_path):
    _registry, directory, _status = _inputs(tmp_path, 8)
    (directory / CONTRACT_FILENAME).unlink()
    with pytest.raises(StepContractError, match="基线"):
        validate_step_inputs(directory, 8)
    assert not (directory / CONTRACT_FILENAME).exists()


@pytest.mark.parametrize("override", [{"conf": "em.gro"}, {"tpr": "unbound.tpr"}])
def test_native_stage_cannot_bypass_bound_paths(tmp_path, override):
    from willy.simulation._gmx_utils import prepare_stage_execution

    _registry, directory, _status = _inputs(tmp_path, 9)
    before = (directory / "em.gro").read_bytes()
    with pytest.raises(StepContractError, match="未绑定"):
        prepare_stage_execution("em", directory, **override)
    assert (directory / "em.gro").read_bytes() == before


def test_failed_attempt_does_not_authorize_downstream(tmp_path):
    _registry, directory, _status = _inputs(tmp_path, 8)
    attempt = begin_step(directory, 8)
    (directory / "em.gro").write_text("partial result")
    finish_step(directory, 8, attempt, StepResult("em", 8, False))
    assert load_contracts(directory)["artifacts"]["em.gro"]["accepted"] is False
    with pytest.raises(StepContractError):
        validate_step_inputs(directory, 9)


def test_production_cleanup_preserves_archived_intermediates(tmp_path):
    from willy.pipeline_orchestrator import PipelineOrchestrator

    _registry, directory, _status = _inputs(tmp_path, 10)
    before = (directory / "Li.chg").read_bytes()
    assert PipelineOrchestrator._prod_intermediate_cleanup_contract_failure(10, directory) is None
    assert not (directory / "Li.chg").exists()
    archived = list((directory / "old").glob("prod-cleanup-*/files/Li.chg"))
    assert archived[0].read_bytes() == before


def test_component_pdb_remains_usable_after_md_only_replay(tmp_path):
    _registry, directory, _status = _inputs(tmp_path, 7)

    def work():
        (directory / "Li.pdb").write_text("converted component")
        (directory / "model.pdb").write_text("new box")
        return StepResult("box", 7, True)

    assert execute_step(directory, 7, work).success
    assert load_contracts(directory)["artifacts"]["Li.pdb"]["producer_step"] == 4
    begin_step(directory, 6)
    validate_step_inputs(directory, 7)


@pytest.mark.parametrize("changed", [False, True])
def test_production_append_requires_recorded_checkpoint_bundle_hashes(tmp_path, changed):
    from willy.simulation._gmx_utils import build_stage_inputs
    from willy.simulation.manifest import record_stage_result, stage_contract

    _registry, directory, _status = _inputs(tmp_path, 10)
    inputs = build_stage_inputs(directory, "prod")
    contract = stage_contract(
        directory, "prod", config_path=directory / "config.json", topol=inputs.topol,
        itps=inputs.itps, mdp=inputs.mdp, coordinates=inputs.coordinates,
        parent_checkpoint=directory / "eq.cpt",
    )
    outputs = {}
    for suffix in ("cpt", "tpr", "xtc", "edr", "log"):
        path = directory / f"prod.{suffix}"
        path.write_text("interrupted data")
        outputs[suffix] = str(path)
    record_stage_result(directory, "prod", success=False, contract=contract, outputs=outputs)
    if changed:
        (directory / "prod.xtc").write_text("manually changed")
        with pytest.raises(StepContractError, match="续接文件哈希"):
            begin_step(directory, 10)
    else:
        begin_step(directory, 10)
        assert load_contracts(directory)["steps"]["10"]["append"] is True
    assert all(Path(path).exists() for path in outputs.values())
