"""Per-molecule structure-optimization bypass contracts."""

from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from willy.errors import StepResult


@pytest.mark.parametrize(
    ("module_name", "suffix", "step_name", "source"),
    [
        ("willy.quantum.struct_g16", ".gjf", "struct_g16", "# B3LYP Opt\n\nLi\n\n1 1\nLi 0 0 0\n"),
        ("willy.quantum.struct_g09", ".gjf", "struct_g09", "# B3LYP Opt\n\nLi\n\n1 1\nLi 0 0 0\n"),
        ("willy.quantum.struct_orca", ".inp", "struct_orca", "! B3LYP Opt\n\n* xyz 1 1\nLi 0 0 0\n*\n"),
    ],
)
def test_step_one_bypasses_optimization_for_explicitly_selected_molecule(
    tmp_path, module_name, suffix, step_name, source,
):
    module = importlib.import_module(module_name)
    source_path = tmp_path / f"Li{suffix}"
    source_path.write_text(source, encoding="utf-8")

    result = module.run_one("Li", {"skip_optimization": True}, {}, str(tmp_path))

    assert result.success is True
    assert result.step_name == step_name
    assert result.outputs == {"source_input": str(source_path)}
    assert result.extra == {"optimization_skipped": True}
    assert module.OPTIMIZATION_TIMEOUT_S == 36_000


def test_g16_singlepoint_reads_selected_raw_geometry_without_multiwfn(tmp_path, monkeypatch):
    from willy.quantum import singlepoint_g16

    source = tmp_path / "Li.gjf"
    source.write_text(
        "# B3LYP/6-31G(d) Opt\n\nLi\n\n1 1\nLi 0.0 0.0 0.0\n",
        encoding="utf-8",
    )
    inputs: list[str] = []

    monkeypatch.setattr(singlepoint_g16, "extract_xyz", lambda *_args: pytest.fail("raw input must not invoke Multiwfn"))
    monkeypatch.setattr(
        singlepoint_g16,
        "require_tool",
        lambda name: SimpleNamespace(executable=name),
    )
    monkeypatch.setattr(singlepoint_g16, "build_tool_env", lambda _name: {})

    def fake_run(command, *, cwd, input_text=None, **_kwargs):
        if command[0] == "g16":
            inputs.append(input_text)
            (Path(cwd) / "Li_opt.chk").write_text("chk", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="Normal termination", stderr="")
        Path(command[2]).write_text("fchk", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(singlepoint_g16, "run_managed_command", fake_run)

    result = singlepoint_g16.run(
        None,
        charge=1,
        spin=1,
        workdir=str(tmp_path),
        name="Li",
        source_input_path=str(source),
    )

    assert result.success is True
    assert "Li 0.0 0.0 0.0" in inputs[0]
    assert (tmp_path / "Li_opt.fchk").is_file()


def test_raw_coordinate_reader_removes_comments_before_building_singlepoint_input(tmp_path):
    from willy.quantum.input_audit import coordinates_from_quantum_input

    source = tmp_path / "Li.gjf"
    source.write_text(
        "# B3LYP/6-31G(d) Opt\n\nLi\n\n1 1\n# generated geometry 1 2 3\nLi 0.0 0.0 0.0 7\n",
        encoding="utf-8",
    )

    assert coordinates_from_quantum_input(source) == ["Li 0.0 0.0 0.0"]


@pytest.mark.parametrize(
    ("step_function", "source_name", "converter_module", "converter_name", "sp_module"),
    [
        ("_g16_sp_and_mol2", "Li.gjf", "fchk_mol2", "convert", "singlepoint_g16"),
        ("_g09_sp_and_mol2", "Li.gjf", "fchk_mol2", "convert", "singlepoint_g09"),
        ("_orca_sp_and_mol2", "Li.inp", "molden_mol2", "convert", "singlepoint_orca"),
    ],
)
def test_step_two_passes_raw_source_when_optimization_is_skipped(
    tmp_path, monkeypatch, step_function, source_name, converter_module, converter_name, sp_module,
):
    import willy.pipeline_orchestrator as orchestrator
    from willy.quantum import fchk_mol2, molden_mol2, singlepoint_g09, singlepoint_g16, singlepoint_orca

    source = "! B3LYP Opt\n\n* xyz 1 1\nLi 0 0 0\n*\n" if source_name.endswith(".inp") else (
        "# B3LYP Opt\n\nLi\n\n1 1\nLi 0 0 0\n"
    )
    (tmp_path / source_name).write_text(source, encoding="utf-8")
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "molecules": {"Li": {"charge": 1, "spin": 1, "skip_optimization": True}},
        "defaults": {"mem": "5GB", "nproc": 1},
    }), encoding="utf-8")
    opt_fchk = tmp_path / "Li_opt.fchk"
    opt_fchk.write_text("fchk", encoding="utf-8")
    calls = []

    def fake_singlepoint(*args, **kwargs):
        calls.append((args, kwargs))
        outputs = {"fchk": str(opt_fchk)}
        if source_name.endswith(".inp"):
            outputs["molden"] = str(tmp_path / "Li_opt.molden")
        return StepResult("singlepoint", 2, True, outputs=outputs)

    singlepoint_module = {"singlepoint_g16": singlepoint_g16, "singlepoint_g09": singlepoint_g09, "singlepoint_orca": singlepoint_orca}[sp_module]
    converter = {"fchk_mol2": fchk_mol2, "molden_mol2": molden_mol2}[converter_module]
    monkeypatch.setattr(singlepoint_module, "run", fake_singlepoint)
    monkeypatch.setattr(converter, converter_name, lambda _path: StepResult(
        "mol2", 2, True, outputs={"mol2": str(tmp_path / "Li.mol2")},
    ))

    result = getattr(orchestrator, step_function)(str(config_path), work_dir=tmp_path)

    assert result[0].success is True
    assert calls[0][0] == (None,)
    assert calls[0][1]["source_input_path"] == str(tmp_path / source_name)
