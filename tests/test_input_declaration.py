"""LLM proposals cannot grant blanket, malformed or stale input exemptions."""

import json
from types import SimpleNamespace

import pytest

from willy.input_declaration import apply_input_declaration, decide_input_scope, input_inventory, validate_input_format
from willy.step_contracts import StepContractError, declared_input_matches, load_contracts, validate_step_inputs
from tests.test_resume_admission import _inputs, _launch_stub


def test_declared_version_only_applies_to_selected_consumers(tmp_path):
    _registry, directory, _status = _inputs(tmp_path, 9)
    (directory / "Li.itp").write_text("[ moleculetype ]\nLi 3\n")
    receipt = apply_input_declaration(directory, [{"path": "Li.itp", "steps": [9]}], input_inventory(directory))
    assert receipt["files"][0]["name"] == "Li.itp"
    assert len(receipt["files"][0]["sha256"]) == 64
    validate_step_inputs(directory, 9)
    assert not declared_input_matches(directory, 10, "Li.itp")
    with pytest.raises(StepContractError):
        validate_step_inputs(directory, 10)
    (directory / "Li.itp").write_text("[ moleculetype ]\nLi 4\n")
    assert not declared_input_matches(directory, 9, "Li.itp")


@pytest.mark.parametrize("selection", [
    [], [{"path": "Li.itp", "steps": list(range(1, 11))}],
    [{"path": "../outside.itp", "steps": [9]}],
    [{"path": "Li.itp", "steps": [True]}],
    [{"path": "Li.itp", "steps": [9], "skip_all": True}],
])
def test_bad_llm_scope_changes_no_baseline(tmp_path, selection):
    _registry, directory, _status = _inputs(tmp_path, 9)
    before = load_contracts(directory)
    with pytest.raises(StepContractError):
        apply_input_declaration(directory, selection, input_inventory(directory))
    assert load_contracts(directory) == before


@pytest.mark.parametrize("filename,content", [
    ("bad.gro", "broken"), ("bad.pdb", "broken"), ("bad.itp", "broken"),
    ("bad.mol2", "broken"), ("bad.chg", "Li nan 0 0 1"),
    ("bad.mdp", "dt = nan"), ("bad.gjf", "broken"),
    ("bad.inp", "broken"), ("bad.fchk", "broken"), ("bad.molden", "broken"),
    ("bad.pdb", ""), ("bad.unknown", "data"),
])
def test_new_inputs_need_nonempty_basic_format(tmp_path, filename, content):
    (tmp_path / filename).write_text(content)
    with pytest.raises(StepContractError):
        validate_input_format(tmp_path, filename)


def test_modified_file_after_llm_inventory_requires_new_declaration(tmp_path):
    _registry, directory, _status = _inputs(tmp_path, 9)
    inventory = input_inventory(directory)
    (directory / "Li.itp").write_text("[ moleculetype ]\nLi 3\n")
    with pytest.raises(StepContractError, match="LLM 决策后"):
        apply_input_declaration(directory, [{"path": "Li.itp", "steps": [9]}], inventory)
    assert load_contracts(directory)["adoptions"] == {}


def test_empty_unrelated_output_does_not_hide_valid_candidates(tmp_path):
    _registry, directory, _status = _inputs(tmp_path, 8)
    (directory / "eq.gro").write_bytes(b"")
    inventory = input_inventory(directory)
    assert any(item["path"] == "model.pdb" for item in inventory)
    assert not any(item["path"] == "eq.gro" for item in inventory)


def test_llm_unavailable_never_falls_back_to_blanket_acceptance():
    with pytest.raises(StepContractError, match="LLM 当前不可用"):
        decide_input_scope(None, "test-model", "new model.pdb", [])


def test_declared_gro_archives_derived_pdb_before_box_reuses_it(tmp_path):
    _registry, directory, _status = _inputs(tmp_path, 7)
    (directory / "Li.gro").write_text("Li\n1\n    1LI      Li    1   0.000   0.000   0.000\n1.0 1.0 1.0\n")
    (directory / "Li.pdb").write_text("stale derived PDB")
    apply_input_declaration(directory, [{"path": "Li.gro", "steps": [7]}], input_inventory(directory))
    assert not (directory / "Li.pdb").exists()
    assert list((directory / "old").glob("input-*/files/Li.pdb"))
    validate_step_inputs(directory, 7)


def test_legacy_adoption_creates_only_explicit_file_baselines(tmp_path):
    from willy.step_contracts import CONTRACT_FILENAME
    from tests.test_run_control import _aborted_run

    _registry, directory = _aborted_run(tmp_path)
    (directory / CONTRACT_FILENAME).unlink()
    (directory / "Li.itp").write_text("[ moleculetype ]\nLi 3\n")
    apply_input_declaration(directory, [{"path": "Li.itp", "steps": [8]}], input_inventory(directory))
    payload = load_contracts(directory)
    assert set(payload["artifacts"]) == {"config.json"}
    assert set(payload["adoptions"]) == {"Li.itp"}
    with pytest.raises(StepContractError):
        validate_step_inputs(directory, 8)


def test_explicit_command_queries_inventory_and_registers_scoped_inputs(tmp_path, monkeypatch):
    import willy.frontend_api as api
    import willy.agent_config as agent_config

    _registry, directory, _status = _inputs(tmp_path, 9)
    _reservation, spawned = _launch_stub(monkeypatch, tmp_path, directory)
    (directory / "Li.itp").write_text("[ moleculetype ]\nLi 3\n")
    messages = []

    def choose(**kwargs):
        messages.extend(kwargs["messages"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
            "files": [{"path": "Li.itp", "steps": [9]}],
        })))])

    monkeypatch.setattr(agent_config, "_DS", SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=choose))))
    monkeypatch.setattr(agent_config, "_LLM_SETTINGS", None)
    listing = api.run_assistant_control_command(directory.name, "/inputs")
    assert "Li.itp" in listing and "SHA256=" in listing
    reply = api.run_assistant_control_command(directory.name, "/inputs 新的 Li.itp 用于第9步")
    assert "已登记新输入" in reply and "步骤=[9]" in reply
    assert messages and "directory_inputs" in messages[-1]["content"]
    assert not spawned
    assert declared_input_matches(directory, 9, "Li.itp")
