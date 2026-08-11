"""Simulation execution contracts without requiring a local GROMACS install."""

from __future__ import annotations

import json
import subprocess

from willy.errors import ErrorKind, StepError, StepResult
from willy.simulation._gmx_utils import grompp_and_mdrun
from willy.simulation.eq import detect_vacuum_region
from willy.simulation.mdp import build_all


def _write_stage_inputs(tmp_path, stage: str = "em"):
    (tmp_path / "topol.top").write_text('#include "solute.itp"\n')
    (tmp_path / "solute.itp").write_text("[ moleculetype ]\nSOL 3\n")
    (tmp_path / f"{stage}.mdp").write_text("integrator = md\n")
    coordinate = {"em": "model.pdb", "eq": "em.gro", "prod": "eq.gro"}[stage]
    (tmp_path / coordinate).write_text("coordinates\n")


def _fake_gmx_with_outputs(args, cwd, timeout=None, input_text=None):
    if args[0] == "grompp":
        output = args[args.index("-o") + 1]
        (cwd / output.split("/")[-1]).write_text("tpr")
    elif args[0] == "mdrun":
        name = args[args.index("-deffnm") + 1]
        for suffix in ("gro", "xtc", "edr", "log", "cpt"):
            (cwd / f"{name}.{suffix}").write_text("output")
    return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")


def test_grompp_and_mdrun_returns_required_stage_artifacts(tmp_path, monkeypatch):
    import willy.simulation._gmx_utils as gmx_utils

    _write_stage_inputs(tmp_path)
    monkeypatch.setattr(gmx_utils, "run_gmx", _fake_gmx_with_outputs)

    result = grompp_and_mdrun("em", tmp_path)

    assert result.success is True
    assert {"tpr", "gro", "xtc", "edr"} <= set(result.outputs)
    assert all((tmp_path / f"em.{suffix}").exists() for suffix in ("tpr", "gro", "xtc", "edr"))


def test_grompp_allows_only_small_single_net_charge_warning(tmp_path, monkeypatch):
    import willy.simulation._gmx_utils as gmx_utils

    _write_stage_inputs(tmp_path)
    calls = []
    warning = (
        "WARNING 1 [file topol.top, line 1]:\n"
        "System has non-zero total charge: 0.080015\n"
        "You are using Ewald electrostatics in a system with net charge.\n"
        "There was 1 WARNING\n"
    )

    def fake_gmx(args, cwd, **kwargs):
        calls.append(args)
        if args[0] == "grompp" and "-maxwarn" not in args:
            return subprocess.CompletedProcess(args, 1, "", warning)
        return _fake_gmx_with_outputs(args, cwd, **kwargs)

    monkeypatch.setattr(gmx_utils, "run_gmx", fake_gmx)
    result = gmx_utils.grompp_and_mdrun("em", tmp_path)

    assert result.success is True
    assert [call[0] for call in calls] == ["grompp", "grompp", "mdrun"]
    assert calls[1][-2:] == ["-maxwarn", "1"]
    assert result.extra["grompp_warning_policy"] == {
        "name": "net_charge_rounding",
        "total_charge_e": 0.080015,
        "tolerance_e": 0.15,
        "warning_count": 1,
        "maxwarn": 1,
    }


def test_grompp_charge_warning_above_tolerance_remains_fatal(tmp_path, monkeypatch):
    import willy.simulation._gmx_utils as gmx_utils

    _write_stage_inputs(tmp_path)
    calls = []
    warning = (
        "WARNING 1 [file topol.top, line 1]:\n"
        "System has non-zero total charge: 0.150001\n"
        "You are using Ewald electrostatics in a system with net charge.\n"
        "There was 1 WARNING\n"
    )

    def fake_gmx(args, cwd, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, "", warning)

    monkeypatch.setattr(gmx_utils, "run_gmx", fake_gmx)
    result = gmx_utils.grompp_and_mdrun("em", tmp_path)

    assert result.success is False
    assert len(calls) == 1
    assert "-maxwarn" not in calls[0]


def test_em_result_preserves_grompp_warning_policy_evidence():
    from willy.simulation.em import EMResult, _em_extra

    gmx_result = StepResult(
        "md_em", 8, True,
        extra={"grompp_warning_policy": {"name": "net_charge_rounding"}},
    )
    merged = _em_extra(gmx_result, EMResult(converged=True))

    assert merged["grompp_warning_policy"] == {"name": "net_charge_rounding"}
    assert merged["em_result"].converged is True


def test_grompp_and_mdrun_uses_run_config_cpu_default(tmp_path, monkeypatch):
    import willy.simulation._gmx_utils as gmx_utils

    _write_stage_inputs(tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps({"defaults": {"nproc": 3}}), encoding="utf-8",
    )
    calls = []

    def fake_gmx(args, cwd, **kwargs):
        calls.append(args)
        return _fake_gmx_with_outputs(args, cwd, **kwargs)

    monkeypatch.setattr(gmx_utils, "run_gmx", fake_gmx)

    result = grompp_and_mdrun("em", tmp_path)

    assert result.success is True
    mdrun_args = next(args for args in calls if args[0] == "mdrun")
    assert mdrun_args[mdrun_args.index("-nt") + 1] == "3"


def test_gromacs_reports_preprocess_and_run_as_public_activities(tmp_path, monkeypatch):
    import willy.simulation._gmx_utils as gmx_utils

    _write_stage_inputs(tmp_path, "eq")
    monkeypatch.setattr(gmx_utils, "run_gmx", _fake_gmx_with_outputs)
    activities = []

    result = grompp_and_mdrun("eq", tmp_path, on_progress=activities.append)

    assert result.success is True
    assert activities == [
        {
            "tool": "GROMACS", "operation": "输入预处理",
            "target_type": "stage", "target": "eq", "current": 1, "total": 2,
        },
        {
            "tool": "GROMACS", "operation": "运行模拟",
            "target_type": "stage", "target": "eq", "current": 2, "total": 2,
        },
    ]


def test_gromacs_routes_liveness_heartbeats_without_redefining_activity(tmp_path, monkeypatch):
    import willy.simulation._gmx_utils as gmx_utils

    _write_stage_inputs(tmp_path, "eq")
    heartbeats = []

    def fake_gmx(args, cwd, timeout=None, input_text=None, **kwargs):
        if args[0] == "mdrun":
            kwargs["on_mdrun_heartbeat"]({
                "stage": "eq", "status": "waiting", "process_alive": True,
            })
        return _fake_gmx_with_outputs(args, cwd, timeout, input_text)

    monkeypatch.setattr(gmx_utils, "run_gmx", fake_gmx)

    result = grompp_and_mdrun("eq", tmp_path, on_heartbeat=heartbeats.append)

    assert result.success is True
    assert heartbeats == [{"stage": "eq", "status": "waiting", "process_alive": True}]


def test_mdp_reports_each_stage_as_public_progress(tmp_path):
    config_path = tmp_path / "config.json"
    from willy.simulation.protocol import default_md_config
    config_path.write_text(json.dumps({"md": default_md_config()}))
    activities = []

    result = build_all(config_path=str(config_path), output_dir=str(tmp_path), on_progress=activities.append)

    assert result.success is True
    assert [item["target"] for item in activities] == ["em", "eq", "prod"]
    assert [(item["current"], item["total"]) for item in activities] == [(1, 3), (2, 3), (3, 3)]
    assert all(item["tool"] == "MDP" and item["operation"] == "参数生成" for item in activities)


def test_grompp_and_mdrun_passes_custom_tpr_to_mdrun(tmp_path, monkeypatch):
    import willy.simulation._gmx_utils as gmx_utils

    _write_stage_inputs(tmp_path)
    calls = []

    def fake_gmx(args, cwd, timeout=None, input_text=None):
        calls.append(args)
        return _fake_gmx_with_outputs(args, cwd, timeout, input_text)

    monkeypatch.setattr(gmx_utils, "run_gmx", fake_gmx)
    custom_tpr = tmp_path / "prepared-input.tpr"

    result = grompp_and_mdrun("em", tmp_path, tpr=custom_tpr)

    assert result.success is True
    mdrun_args = next(args for args in calls if args[0] == "mdrun")
    assert mdrun_args[mdrun_args.index("-s") + 1] == str(custom_tpr)


def test_grompp_and_mdrun_rejects_missing_required_input(tmp_path):
    (tmp_path / "topol.top").write_text("")

    result = grompp_and_mdrun("em", tmp_path)

    assert result.success is False
    assert result.error.kind is ErrorKind.FILE_NOT_FOUND


def test_grompp_and_mdrun_rejects_success_without_required_output(tmp_path, monkeypatch):
    import willy.simulation._gmx_utils as gmx_utils

    _write_stage_inputs(tmp_path)

    def fake_without_xtc(args, cwd, timeout=None, input_text=None):
        if args[0] == "grompp":
            (cwd / "em.tpr").write_text("tpr")
        elif args[0] == "mdrun":
            for suffix in ("gro", "edr"):
                (cwd / f"em.{suffix}").write_text("output")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(gmx_utils, "run_gmx", fake_without_xtc)
    result = grompp_and_mdrun("em", tmp_path)

    assert result.success is False
    assert result.error.kind is ErrorKind.MDRUN_FAILED
    assert "xtc" in result.error.message


def test_mdrun_failure_has_private_process_evidence_and_mdrun_kind(tmp_path, monkeypatch):
    import willy.simulation._gmx_utils as gmx_utils

    _write_stage_inputs(tmp_path, "eq")

    def failing_gmx(args, cwd, timeout=None, input_text=None):
        if args[0] == "grompp":
            (cwd / "eq.tpr").write_text("tpr")
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            args=args,
            returncode=-15,
            stdout="",
            stderr="Fatal error: domain decomposition failed",
        )

    monkeypatch.setattr(gmx_utils, "run_gmx", failing_gmx)
    result = grompp_and_mdrun("eq", tmp_path)

    assert result.success is False
    assert result.error.kind is ErrorKind.MDRUN_FAILED
    assert result.extra["execution"] == {
        "returncode": -15,
        "signal": "SIGTERM",
        "raw_output_tail": "Fatal error: domain decomposition failed",
    }


def _write_gro(path, x_coordinates):
    lines = ["fixture", str(len(x_coordinates))]
    for index, x in enumerate(x_coordinates, 1):
        lines.append(f"{1:5d}{'SOL':<5}{'C':>5}{index:5d}{x:8.3f}{0.5:8.3f}{0.5:8.3f}")
    lines.append("3.00000 3.00000 3.00000")
    path.write_text("\n".join(lines) + "\n")


def _write_xvg(path, values):
    path.write_text("\n".join(
        f"{index * 250.0:.1f} {value:.8f}"
        for index, value in enumerate(values)
    ) + "\n")


def test_detect_vacuum_region_from_final_eq_structure(tmp_path):
    gro = tmp_path / "eq.gro"
    _write_gro(gro, [0.1 + (index % 2) * 0.05 for index in range(24)])

    result = detect_vacuum_region(gro)

    assert result["detected"] is True
    assert result["axis"] == "x"


def test_eq_acceptance_uses_temperature_and_potential_slope_only(tmp_path, monkeypatch):
    import willy.simulation.eq as eq_module
    from willy.simulation.protocol import default_md_config

    values = {
        "Density": [900.0, 1050.0, 1200.0, 1350.0, 1500.0],
        "Temperature": [297.0, 298.0, 299.0, 298.0, 298.0],
        "Pressure": [-500.0, 800.0, -700.0, 600.0, -900.0],
        "Potential": [-1000.0, -1000.5, -1000.0, -999.5, -1000.0],
    }

    def fake_extract(_edr, term, output):
        _write_xvg(output, values[term])
        return True, ""

    monkeypatch.setattr(eq_module, "extract_energy_xvg", fake_extract)
    details, issues = eq_module._acceptance_details(
        tmp_path,
        hold_ns=1.0,
        acceptance=default_md_config()["eq"]["acceptance"],
        target_temperature=298.0,
    )

    assert issues == []
    assert details["series"]["density"]["relative_slope_per_ns"] > 0.01
    assert details["series"]["pressure"]["relative_slope_per_ns"] > 0.01


def test_eq_acceptance_rejects_excessive_potential_slope(tmp_path, monkeypatch):
    import willy.simulation.eq as eq_module
    from willy.simulation.protocol import default_md_config

    values = {
        "Density": [1000.0] * 5,
        "Temperature": [298.0] * 5,
        "Pressure": [1.0] * 5,
        "Potential": [-1000.0, -995.0, -990.0, -985.0, -980.0],
    }

    def fake_extract(_edr, term, output):
        _write_xvg(output, values[term])
        return True, ""

    monkeypatch.setattr(eq_module, "extract_energy_xvg", fake_extract)
    _, issues = eq_module._acceptance_details(
        tmp_path,
        hold_ns=1.0,
        acceptance=default_md_config()["eq"]["acceptance"],
        target_temperature=298.0,
    )

    assert issues == ["最终势能线性斜率超过稳定阈值"]


def test_eq_vacuum_and_density_are_observations_not_blockers(tmp_path, monkeypatch):
    import willy.simulation.eq as eq_module
    from willy.simulation.manifest import initialize_manifest, load_manifest, manifest_path
    from willy.simulation.mdp import build_all
    from willy.simulation.protocol import default_md_config

    (tmp_path / "topol.top").write_text('#include "solute.itp"\n')
    (tmp_path / "solute.itp").write_text("[ moleculetype ]\nSOL 3\n")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"md": default_md_config() | {"run_seed": 12345}}))
    assert build_all(str(config), str(tmp_path)).success
    _write_gro(tmp_path / "em.gro", [0.1 + (index % 2) * 0.05 for index in range(24)])
    initialize_manifest(tmp_path, config, random_seed=12345, versions={"gromacs": "mock"})
    manifest = load_manifest(tmp_path)
    manifest["stages"]["em"] = {"status": "accepted"}
    manifest_path(tmp_path).write_text(json.dumps(manifest))

    def fake_gmx_result(*_args, **_kwargs):
        _write_gro(tmp_path / "eq.gro", [0.1 + (index % 2) * 0.05 for index in range(24)])
        (tmp_path / "eq.cpt").write_text("checkpoint")
        return StepResult("eq", 9, True, outputs={"tpr": str(tmp_path / "eq.tpr")})

    values = {
        "Density": [900.0, 1100.0, 1300.0, 1500.0, 1700.0],
        "Temperature": [298.0] * 5,
        "Pressure": [1.0] * 5,
        "Potential": [-1000.0] * 5,
    }

    def fake_extract(_edr, term, output):
        _write_xvg(output, values[term])
        return True, ""

    monkeypatch.setattr(eq_module, "grompp_and_mdrun", fake_gmx_result)
    monkeypatch.setattr(eq_module, "extract_energy_xvg", fake_extract)
    result = eq_module.run_eq(str(tmp_path))

    assert result.success is True
    assert result.extra["eq_result"].details["vacuum"]["detected"] is True
    assert result.extra["eq_result"].details["series"]["density"]["relative_slope_per_ns"] > 0.01
