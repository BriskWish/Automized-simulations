"""Gaussian SMD catalog, rendering, and configuration boundary tests."""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest

from willy.quantum.smd_solvents import (
    SMDSolventError,
    apply_scrf_snapshot,
    lookup_solvent,
    input_has_scrf,
    load_solvents,
    register_manual_solvent,
    resolve_exact,
    solvent_directory,
    validate_custom_values,
)


def test_one_time_gaussian_catalog_contains_expected_builtin_records():
    result = resolve_exact("acetone")
    assert result is not None
    assert result.source == "builtin"
    assert result.epsilon == "20.493"
    assert result.epsinf is None


def test_builtin_route_replaces_existing_scrf():
    route, trailer, record = apply_scrf_snapshot(
        "#p B3LYP/6-31G(d) Opt SCRF=(PCM,Solvent=Water)",
        "Acetone",
        None,
    )
    assert route.count("SCRF") == 1
    assert "Solvent=Acetone" in route
    assert "PCM" not in route
    assert trailer == ""
    assert record["source"] == "builtin"


def test_manual_solvent_requires_exactly_new_name_and_physical_order(tmp_path):
    record = register_manual_solvent("MyMix", 20, 1.8, project_root=tmp_path)
    assert record.source == "manual"
    assert resolve_exact("mymix", tmp_path).epsinf == "1.8"
    with pytest.raises(SMDSolventError, match="大小写不敏感"):
        register_manual_solvent("mYmIx", 21, 1.9, project_root=tmp_path)
    with pytest.raises(SMDSolventError, match="epsilon >= epsinf >= 1"):
        register_manual_solvent("bad", 1.5, 2, project_root=tmp_path)


def test_default_name_skips_existing_manual_entry(tmp_path):
    register_manual_solvent("default_1", 2, 1.1, project_root=tmp_path)
    record = register_manual_solvent(None, 3, 1.2, project_root=tmp_path)
    assert record.name == "default_2"


def test_manual_route_writes_generic_parameters(tmp_path):
    record = register_manual_solvent("CustomEC", 20, 1.8, project_root=tmp_path)
    route, trailer, snapshot = apply_scrf_snapshot(
        "#p B3LYP/6-31G(d) Opt SCRF(SMD,Solvent=Water)",
        record.name,
        record.as_dict(),
        tmp_path,
    )
    assert route.endswith("SCRF=(SMD,Solvent=Generic,Read)")
    assert trailer == "Eps=20\nEpsInf=1.8\n"
    assert snapshot["manual"] is True


def test_lookup_unknown_returns_vector_candidates():
    result = lookup_solvent("acet")
    assert result["match"] is None
    assert result["candidates"]


def test_builtin_catalog_is_separate_from_manual_catalog():
    builtin_path = solvent_directory() / "gaussian_builtin.json"
    manual_path = solvent_directory() / "gaussian_manual.json"
    builtin = json.loads(builtin_path.read_text())
    manual = json.loads(manual_path.read_text())
    assert len(builtin["solvents"]) == 184
    assert not {name.casefold() for name in builtin["solvents"]} & {name.casefold() for name in manual["solvents"]}
    for record in manual["solvents"].values():
        validate_custom_values(record["epsilon"], record["epsinf"])


def test_g16_generic_acceptance_records_only_the_verified_scope():
    path = Path(__file__).resolve().parent / "reports" / "audits" / "gaussian_smd_generic_20260921.json"
    report = json.loads(path.read_text())
    assert report["passed"] and all(report["checks"].values())
    assert "not full SMD parameter calibration" in report["scope"]
    assert "SCRF=(SMD,Solvent=Generic,Read)" in report["input"]
    assert report["input"].endswith("\n\nEps=20\nEpsInf=1.8\n\n")
    assert len(report["log_sha256"]) == 64
    assert any("Optimization completed." in line for line in report["evidence"])


@pytest.mark.parametrize("epsilon,epsinf", [
    (True, 1), (None, 1), ("nan", 1), ("inf", 1), (1, 0.9),
    ("1e1000000000", 1), ([], 1), (2, {}),
])
def test_invalid_custom_dielectrics_are_rejected(epsilon, epsinf):
    with pytest.raises(SMDSolventError):
        validate_custom_values(epsilon, epsinf)


@pytest.mark.parametrize("name", ["gas", "a\x00b", "a\nb", " ", 42, {}])
def test_invalid_manual_names_are_rejected(tmp_path, name):
    with pytest.raises(SMDSolventError):
        register_manual_solvent(name, 20, 1.8, project_root=tmp_path)


def test_builtin_name_collision_is_rejected_in_isolated_project(tmp_path):
    with pytest.raises(SMDSolventError, match="大小写不敏感"):
        register_manual_solvent("aCeToNe", 20, 1.8, project_root=tmp_path)


def test_concurrent_registration_preserves_all_default_names(tmp_path):
    def register(_index):
        return register_manual_solvent(None, 20, 1.8, project_root=tmp_path).name

    with ThreadPoolExecutor(max_workers=6) as executor:
        names = list(executor.map(register, range(12)))
    assert len(set(names)) == 12
    _, manual = load_solvents(tmp_path)
    assert set(manual) == set(names)
    payload = json.loads((solvent_directory(tmp_path) / "gaussian_manual.json").read_text())
    assert all(record["manual"] is True for record in payload["solvents"].values())


def test_manual_snapshot_remains_renderable_without_catalog_entry(tmp_path):
    snapshot = {"name": "Custom", "source": "manual", "epsilon": "20", "epsinf": "1.8"}
    route, trailer, _ = apply_scrf_snapshot("# B3LYP/6-31G(d) Opt", "Custom", snapshot, tmp_path)
    assert "Generic,Read" in route
    assert "EpsInf=1.8" in trailer


@pytest.mark.parametrize("snapshot", [
    {}, {"name": "Water", "source": "builtin"},
    {"name": "Acetone", "source": "none"},
    {"name": "Acetone", "source": "invalid"},
])
def test_mismatched_or_incomplete_snapshot_is_rejected(snapshot):
    with pytest.raises(SMDSolventError):
        apply_scrf_snapshot("# B3LYP/6-31G(d) Opt", "Acetone", snapshot)


def test_bare_scrf_removal_preserves_next_keyword():
    route, _, _ = apply_scrf_snapshot("# B3LYP/6-31G(d) SCRF Opt Freq", "gas", None)
    assert route == "# B3LYP/6-31G(d) Opt Freq"


@pytest.mark.parametrize("backend", ["g16", "g09"])
@pytest.mark.parametrize("solvent", ["Acetone", "Custom", "gas"])
def test_multiline_route_is_parsed_and_scrf_replaced(tmp_path, backend, solvent):
    module = import_module(f"willy.quantum.struct_{backend}")
    source = tmp_path / "EC.gjf"
    source.write_text(
        "%chk=original.chk\n# B3LYP/6-31G(d) Opt\n"
        " SCRF=(SMD,Solvent=Generic,Read)\n SCF=XQC\n\n"
        "EC title\n\n0 1\nC 0 0 0\n\nEps=99\nEpsInf=9\n\n"
    )
    assert input_has_scrf(source)
    parsed = module._parse_gjf(source)
    assert parsed["title"] == "EC title"
    assert "SCF=XQC" in parsed["route"]
    reference = None
    if solvent == "Custom":
        reference = register_manual_solvent(solvent, 20, 1.8, project_root=tmp_path).as_dict()
    result = module._build_gjf(
        parsed, "B3LYP/6-31G(d)", "1GB", 1, "EC",
        solvent=solvent, solvent_ref=reference, project_root=tmp_path,
    )
    assert "Eps=99" not in result
    assert result.count("SCRF=") == (0 if solvent == "gas" else 1)
    assert "SCF=XQC" in result
    assert "EC title\n\n0 1\nC 0 0 0" in result
    if solvent == "Custom":
        assert result.endswith("\n\nEps=20\nEpsInf=1.8\n\n")


@pytest.mark.parametrize("backend", ["g16", "g09"])
@pytest.mark.parametrize("solvent", ["Acetone", "Custom", "gas"])
def test_singlepoint_uses_bound_solvent_and_generic_tail(tmp_path, monkeypatch, backend, solvent):
    module = import_module(f"willy.quantum.singlepoint_{backend}")
    fchk = tmp_path / "EC.fchk"
    fchk.write_text("wavefunction")
    xyz = tmp_path / "EC.xyz"
    xyz.write_text("1\nEC\nC 0 0 0\n")
    monkeypatch.setattr(module, "extract_xyz", lambda *_args: str(xyz))
    monkeypatch.setattr(module, "require_tool", lambda name: SimpleNamespace(executable=name))
    monkeypatch.setattr(module, "build_tool_env", lambda *_args: {})
    captured = []

    def execute(command, **kwargs):
        if kwargs.get("input_text"):
            captured.append(kwargs["input_text"])
            (tmp_path / "EC_opt.chk").write_text("checkpoint")
        else:
            Path(command[-1]).write_text("formatted checkpoint")
        return subprocess.CompletedProcess(command, 0, "Normal termination", "")

    monkeypatch.setattr(module, "run_managed_command", execute)
    reference = None
    if solvent == "Custom":
        reference = register_manual_solvent(solvent, 20, 1.8, project_root=tmp_path).as_dict()
    result = module.run(str(fchk), 0, 1, solvent=solvent, solvent_ref=reference)
    assert result.success
    assert len(captured) == 1
    if solvent == "Custom":
        assert "SCRF=(SMD,Solvent=Generic,Read)" in captured[0]
        assert captured[0].endswith("\n\nEps=20\nEpsInf=1.8\n\n")
    elif solvent == "gas":
        assert "SCRF" not in captured[0]
    else:
        assert "SCRF=(SMD,Solvent=Acetone)" in captured[0]
        assert "Eps=" not in captured[0]
