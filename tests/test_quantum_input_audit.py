"""Backend-specific raw quantum input audit regression tests."""

from __future__ import annotations

import json
from types import SimpleNamespace

from willy.quantum.input_audit import (
    apply_audited_quantum_properties,
    audit_quantum_inputs,
    quantum_input_contract_issues,
)
from willy.simulation.protocol import default_md_config
from willy.run_metadata import load_run_manifest
from willy.structure_uploads import load_uploaded_structures


def _bundled_struct_paths(suffix: str):
    """Return versioned profiles without mutable user-uploaded structures."""
    from pathlib import Path

    struct_dir = Path(__file__).resolve().parents[1] / "struct"
    uploaded_names = {entry.name for entry in load_uploaded_structures(struct_dir.parent)}
    return [
        path for path in sorted(struct_dir.glob(f"*{suffix}"))
        if path.stem not in uploaded_names
    ]


def _gjf(charge: int, spin: int = 1) -> str:
    return f"#p b3lyp/6-31g\n\ninput\n\n{charge} {spin}\nH 0 0 0\n\n"


def _inp(charge: int, spin: int = 1, *, optimize: bool = True) -> str:
    keyword = "! B3LYP def2-SVP Opt" if optimize else "! B3LYP def2-SVP SP"
    return f"{keyword}\n* xyz {charge} {spin}\nH 0 0 0\n*\n"


def test_g16_audit_reads_charge_and_spin_from_raw_input(tmp_path):
    (tmp_path / "Li.gjf").write_text(_gjf(1), encoding="utf-8")
    (tmp_path / "TFSI.gjf").write_text(_gjf(-1), encoding="utf-8")

    audit = audit_quantum_inputs("g16", {"Li": 2, "TFSI": 2}, struct_dir=tmp_path)

    assert audit["ok"] is True
    assert audit["expected_suffix"] == ".gjf"
    assert audit["charge_balance"] == "balanced"
    assert audit["net_charge"] == 0
    assert [(item["name"], item["charge"], item["spin"]) for item in audit["components"]] == [
        ("Li", 1, 1), ("TFSI", -1, 1),
    ]


def test_g09_audit_uses_the_same_gjf_contract_without_falling_back_to_inp(tmp_path):
    (tmp_path / "Li.gjf").write_text(_gjf(1), encoding="utf-8")
    (tmp_path / "Li.inp").write_text(_inp(0), encoding="utf-8")

    audit = audit_quantum_inputs("g09", {"Li": 1}, struct_dir=tmp_path)

    assert audit["ok"] is True
    assert audit["backend"] == "g09"
    assert audit["expected_suffix"] == ".gjf"
    assert audit["components"][0]["charge"] == 1


def test_imported_ionic_gjf_inputs_match_the_registered_filename_charges():
    """迁入 struct 的带电文件必须以文件名电荷写入 GJF 头部。"""
    from pathlib import Path

    struct_dir = Path(__file__).resolve().parents[1] / "struct"
    audit = audit_quantum_inputs(
        "g16",
        {"Na": 1, "AsF6": 1, "BCN4": 1, "FTFSI": 1},
        struct_dir=struct_dir,
    )

    assert audit["ok"] is True
    assert [(item["name"], item["charge"], item["spin"]) for item in audit["components"]] == [
        ("Na", 1, 1), ("AsF6", -1, 1), ("BCN4", -1, 1), ("FTFSI", -1, 2),
    ]


def test_struct_gjf_headers_are_canonical_and_dmaa_is_removed():
    """每个内置 GJF 都必须有相对 checkpoint、同名标题和 charge 行。"""
    import re
    from pathlib import Path

    struct_dir = Path(__file__).resolve().parents[1] / "struct"
    paths = _bundled_struct_paths(".gjf")

    assert paths
    assert not (struct_dir / "DMAA.gjf").exists()
    for path in paths:
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines[0].strip() == f"%chk={path.stem}.chk"
        assert any(line.strip().startswith("#") for line in lines)
        assert path.stem in lines[:8]
        assert any(re.fullmatch(r"\s*[+-]?\d+\s+\d+\s*", line) for line in lines)


def test_every_retained_gjf_has_a_native_orca_input():
    """每个内置 GJF 都应有可被 ORCA 原生审计器读取的同名 INP。"""
    from pathlib import Path

    struct_dir = Path(__file__).resolve().parents[1] / "struct"
    names = sorted(path.stem for path in _bundled_struct_paths(".gjf"))
    inp_names = sorted(path.stem for path in _bundled_struct_paths(".inp"))
    audit = audit_quantum_inputs("orca", {name: 1 for name in inp_names}, struct_dir=struct_dir)

    assert names == inp_names
    assert "DMAA" not in names
    assert audit["ok"] is True


def test_orca_audit_requires_native_optimized_inp_and_never_falls_back_to_gjf(tmp_path):
    (tmp_path / "EC.gjf").write_text(_gjf(0), encoding="utf-8")
    (tmp_path / "EC.inp").write_text(_inp(0, optimize=False), encoding="utf-8")

    orca = audit_quantum_inputs("orca", {"EC": 1}, struct_dir=tmp_path)
    g16 = audit_quantum_inputs("g16", {"Missing": 1}, struct_dir=tmp_path)

    assert orca["ok"] is False
    assert any("Opt" in issue for issue in orca["issues"])
    assert g16["ok"] is False
    assert g16["components"][0]["status"] == "missing"
    assert ".gjf" in g16["issues"][0]


def test_audit_properties_replace_an_untrusted_neutral_default(tmp_path):
    (tmp_path / "Li.gjf").write_text(_gjf(1), encoding="utf-8")
    raw_config = {
        "backend": "g16",
        "residues": {"Li": 1},
        "molecules": {"Li": {"charge": 0, "spin": 1}},
        "non_neutral_confirmed": True,
    }
    audit = audit_quantum_inputs("g16", raw_config["residues"], struct_dir=tmp_path)

    corrected = apply_audited_quantum_properties(raw_config, audit)

    assert corrected["molecules"]["Li"]["charge"] == 1
    assert quantum_input_contract_issues(raw_config, audit)
    assert quantum_input_contract_issues(corrected, audit) == []


def test_orca_workspace_requires_inp_and_snapshots_it(tmp_path, monkeypatch):
    import willy.pipeline_orchestrator as po
    from willy.pipeline_orchestrator import PipelineOrchestrator

    root = tmp_path / "project"
    struct_dir = root / "struct"
    struct_dir.mkdir(parents=True)
    (struct_dir / "anion.inp").write_text(_inp(-1), encoding="utf-8")
    (struct_dir / "anion.gjf").write_text(_gjf(0), encoding="utf-8")
    (root / "config.json").write_text(json.dumps({
        "backend": "orca",
        "residues": {"anion": 1},
        "molecules": {"anion": {"charge": -1, "spin": 1}},
        "md": default_md_config(),
        "box": {"target_mass_density_g_cm3": 0.7, "box_size": None, "tolerance": 2.0},
        "non_neutral_confirmed": True,
    }), encoding="utf-8")
    monkeypatch.setattr(po, "ROOT", root)

    run_dir = root / "md_run" / "md__audit_orca"
    snapshot = PipelineOrchestrator(backend="orca", use_llm=False)._prepare_run_directory(run_dir)

    assert (run_dir / "anion.inp").read_text(encoding="utf-8") == _inp(-1)
    assert not (run_dir / "anion.gjf").exists()
    assert json.loads(snapshot.read_text(encoding="utf-8"))["molecules"]["anion"]["charge"] == -1
    manifest = load_run_manifest(run_dir)["sections"]["registry"]["data"]
    assert [item["path"] for item in manifest["input_files"]] == ["anion.inp"]


def test_g09_workspace_requires_gjf_and_snapshots_it(tmp_path, monkeypatch):
    import willy.pipeline_orchestrator as po
    from willy.pipeline_orchestrator import PipelineOrchestrator

    root = tmp_path / "project"
    struct_dir = root / "struct"
    struct_dir.mkdir(parents=True)
    (struct_dir / "cation.gjf").write_text(_gjf(1), encoding="utf-8")
    (struct_dir / "cation.inp").write_text(_inp(0), encoding="utf-8")
    (root / "config.json").write_text(json.dumps({
        "backend": "g09",
        "residues": {"cation": 1},
        "molecules": {"cation": {"charge": 1, "spin": 1}},
        "md": default_md_config(),
        "box": {"target_mass_density_g_cm3": 0.7, "box_size": None, "tolerance": 2.0},
        "non_neutral_confirmed": True,
    }), encoding="utf-8")
    monkeypatch.setattr(po, "ROOT", root)

    run_dir = root / "md_run" / "md__audit_g09"
    snapshot = PipelineOrchestrator(backend="g09", use_llm=False)._prepare_run_directory(run_dir)

    assert (run_dir / "cation.gjf").read_text(encoding="utf-8") == _gjf(1)
    assert not (run_dir / "cation.inp").exists()
    assert json.loads(snapshot.read_text(encoding="utf-8"))["molecules"]["cation"]["charge"] == 1


def test_start_pipeline_rejects_input_changed_after_plan_freeze(tmp_path, monkeypatch):
    import willy.agent_config as agent_config

    (tmp_path / "struct").mkdir()
    (tmp_path / "struct" / "Li.gjf").write_text(_gjf(1), encoding="utf-8")
    reserved: list[bool] = []
    monkeypatch.setattr(agent_config, "ROOT", tmp_path)
    monkeypatch.setattr(agent_config, "write_startup_audit", lambda *_args: None)
    monkeypatch.setattr(agent_config, "reserve_pipeline_launch", lambda *_args: reserved.append(True))

    receipt = agent_config.start_pipeline({
        "backend": "g16",
        "residues": {"Li": 1},
        "molecules": {"Li": {"charge": 0, "spin": 1}},
        "non_neutral_confirmed": True,
    })

    assert receipt.state == "failed"
    assert reserved == []


def test_config_agent_requires_an_input_audit_and_freezes_audited_charge(tmp_path, monkeypatch):
    import willy.agent_config as agent_config
    import willy.toolist_global as toolist_global

    (tmp_path / "struct").mkdir()
    (tmp_path / "struct" / "Li.gjf").write_text(_gjf(1), encoding="utf-8")
    config = {
        "backend": "g16",
        "residues": {"Li": 1},
        "molecules": {"Li": {"charge": 0, "spin": 1}},
        "md": default_md_config(),
        "non_neutral_confirmed": True,
    }
    tool_call = SimpleNamespace(
        id="audit-1",
        function=SimpleNamespace(
            name="tools_inspect_quantum_inputs",
            arguments=json.dumps({"backend": "g16", "components": [{"name": "Li", "count": 1}]}),
        ),
    )
    responses = iter([
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            tool_calls=None,
            content=json.dumps({"backend": "g16", "residues": {"Li": 1}}),
        ))]),
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[tool_call], content=""))]),
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=None, content=json.dumps(config)))]),
    ])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: next(responses))))
    monkeypatch.setattr(agent_config, "ROOT", tmp_path)
    monkeypatch.setattr(agent_config, "_available_residues", lambda: {"Li": {}})
    monkeypatch.setattr(
        toolist_global,
        "audit_quantum_inputs",
        lambda backend, components: audit_quantum_inputs(backend, components, struct_dir=tmp_path / "struct"),
    )
    monkeypatch.setattr(agent_config, "_DS", client)

    updates = list(agent_config.chat("Li 1", []))
    frozen = updates[-1][3]

    assert frozen is not None
    assert frozen["config"]["molecules"]["Li"]["charge"] == 1
    assert "模拟方案确认" in updates[-1][1][-1]["content"]
