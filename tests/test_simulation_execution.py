"""Simulation execution contracts without requiring a local GROMACS install."""

from __future__ import annotations

import json
import subprocess

import pytest

from willy.errors import ErrorKind, StepError, StepResult
from willy.simulation._gmx_utils import grompp_and_mdrun
from willy.simulation.eq import detect_vacuum_region
from willy.simulation.mdp import build_all
from tests.test_eq_acceptance import make_window, write_block_values, write_completion


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


@pytest.mark.parametrize("total_charge", [-0.15, -0.149999, -0.080015, 0.080015, 0.149999, 0.15])
def test_grompp_allows_only_small_single_net_charge_warning(tmp_path, monkeypatch, total_charge):
    import willy.simulation._gmx_utils as gmx_utils

    _write_stage_inputs(tmp_path)
    calls = []
    warning = (
        "WARNING 1 [file topol.top, line 1]:\n"
        f"System has non-zero total charge: {total_charge}\n"
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
        "total_charge_e": total_charge,
        "tolerance_e": 0.15,
        "warning_count": 1,
        "maxwarn": 1,
    }


@pytest.mark.parametrize(
    "total_charge, additional_warning",
    [(0.150001, False), (-0.150001, False), (0.080015, True), (-0.080015, True)],
    ids=["positive-over-limit", "negative-over-limit", "positive-extra-warning", "negative-extra-warning"],
)
def test_grompp_rejects_warnings_outside_charge_tolerance_policy(
    tmp_path, monkeypatch, total_charge, additional_warning,
):
    import willy.simulation._gmx_utils as gmx_utils

    _write_stage_inputs(tmp_path)
    calls = []
    warning = (
        "WARNING 1 [file topol.top, line 1]:\n"
        f"System has non-zero total charge: {total_charge}\n"
        "You are using Ewald electrostatics in a system with net charge.\n"
    )
    if additional_warning:
        warning += "WARNING 2 [file em.mdp, line 1]:\nUnrelated protocol warning.\n"
    warning += f"There were {1 + int(additional_warning)} WARNINGS\n"

    def fake_gmx(args, cwd, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, "", warning)

    monkeypatch.setattr(gmx_utils, "run_gmx", fake_gmx)
    result = gmx_utils.grompp_and_mdrun("em", tmp_path)

    assert result.success is False
    assert result.error.kind == ErrorKind.INPUT_CONTRACT
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


def test_grompp_and_mdrun_forwards_an_explicit_mdrun_timeout(tmp_path, monkeypatch):
    import willy.simulation._gmx_utils as gmx_utils

    _write_stage_inputs(tmp_path)
    timeouts = []

    def fake_gmx(args, cwd, timeout=None, input_text=None, **kwargs):
        if args[0] == "mdrun":
            timeouts.append(timeout)
        return _fake_gmx_with_outputs(args, cwd, timeout, input_text)

    monkeypatch.setattr(gmx_utils, "run_gmx", fake_gmx)

    result = grompp_and_mdrun("em", tmp_path, mdrun_timeout_s=42)

    assert result.success is True
    assert timeouts == [42]


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


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "negative-inf"])
@pytest.mark.parametrize("column", ["time", "value"])
def test_final_window_rejects_non_finite_samples(tmp_path, column, value):
    from willy.simulation._gmx_utils import analyze_final_window

    rows = [[index * 250.0, 298.0] for index in range(5)]
    rows[2][0 if column == "time" else 1] = value
    path = tmp_path / "energy.xvg"
    path.write_text("".join(f"{time_value} {sample}\n" for time_value, sample in rows))

    result = analyze_final_window(path, 1000.0)

    assert result["ok"] is False
    assert result["reason_code"] == "non_finite"
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("window_ps", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "negative-inf"])
def test_final_window_rejects_non_finite_duration(tmp_path, window_ps):
    from willy.simulation._gmx_utils import analyze_final_window

    path = tmp_path / "energy.xvg"
    _write_xvg(path, [298.0] * 5)

    result = analyze_final_window(path, window_ps)

    assert result["ok"] is False
    assert result["reason_code"] == "non_finite"
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "times, values, window_ps",
    [
        ([0.0, 250.0, 500.0, 750.0], [1e308] * 4, 1000.0),
        ([0.0, 250.0, 500.0, 750.0], [1e200, -1e200, 1e200, -1e200], 1000.0),
        ([-3e154, -2e154, -1e154, 0.0], [298.0] * 4, 3e154),
    ],
    ids=["mean-overflow", "value-variance-overflow", "time-variance-overflow"],
)
def test_final_window_rejects_non_finite_statistics(tmp_path, times, values, window_ps):
    from willy.simulation._gmx_utils import analyze_final_window

    path = tmp_path / "energy.xvg"
    path.write_text("".join(
        f"{time_value} {sample}\n" for time_value, sample in zip(times, values)
    ))

    result = analyze_final_window(path, window_ps)

    assert result["ok"] is False
    assert result["reason_code"] == "non_finite"
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("hold_ns", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "negative-inf"])
def test_eq_acceptance_rejects_non_finite_hold_metadata(tmp_path, monkeypatch, hold_ns):
    import willy.simulation.eq as eq_module
    from willy.simulation.protocol import default_md_config

    def unexpected_extract(*args, **kwargs):
        pytest.fail("Invalid acceptance metadata must not trigger energy extraction")

    monkeypatch.setattr(eq_module, "extract_energy_xvg", unexpected_extract)

    details, issues = eq_module._acceptance_details(
        tmp_path, hold_ns, default_md_config()["eq"]["acceptance"], 298.0,
    )

    assert details["auto_acceptance"] is False
    assert issues and "有限" in issues[0]
    json.dumps(details, allow_nan=False)


def test_detect_vacuum_region_from_final_eq_structure(tmp_path):
    gro = tmp_path / "eq.gro"
    _write_gro(gro, [0.1 + (index % 2) * 0.05 for index in range(24)])

    result = detect_vacuum_region(gro)

    assert result["detected"] is True
    assert result["axis"] == "x"


def test_eq_acceptance_uses_temperature_and_potential_slope_only(tmp_path, monkeypatch):
    import willy.simulation.eq as eq_module
    from willy.simulation.protocol import default_md_config
    window = make_window(tmp_path)

    values = {
        "Density": [900.0, 1050.0, 1200.0, 1350.0, 1500.0],
        "Temperature": [297.0, 298.0, 299.0, 298.0, 298.0],
        "Pressure": [-500.0, 800.0, -700.0, 600.0, -900.0],
        "Potential": [-1000.0, -1000.5, -1000.0, -999.5, -1000.0],
    }

    def fake_extract(_edr, term, output):
        write_block_values(output, window, values[term])
        return True, ""

    monkeypatch.setattr(eq_module, "extract_energy_xvg", fake_extract)
    details, issues = eq_module._acceptance_details(
        tmp_path,
        hold_ns=1.0,
        acceptance=default_md_config()["eq"]["acceptance"],
        target_temperature=298.0,
        window=window,
    )

    assert issues == []
    assert details["series"]["density"]["relative_slope_per_ns"] > 0.01
    assert details["series"]["pressure"]["relative_slope_per_ns"] > 0.01


def test_eq_acceptance_rejects_excessive_potential_slope(tmp_path, monkeypatch):
    import willy.simulation.eq as eq_module
    from willy.simulation.protocol import default_md_config
    window = make_window(tmp_path)

    values = {
        "Density": [1000.0] * 5,
        "Temperature": [298.0] * 5,
        "Pressure": [1.0] * 5,
        "Potential": [-1000.0, -995.0, -990.0, -985.0, -980.0],
    }

    def fake_extract(_edr, term, output):
        write_block_values(output, window, values[term])
        return True, ""

    monkeypatch.setattr(eq_module, "extract_energy_xvg", fake_extract)
    _, issues = eq_module._acceptance_details(
        tmp_path,
        hold_ns=1.0,
        acceptance=default_md_config()["eq"]["acceptance"],
        target_temperature=298.0,
        window=window,
    )

    assert issues == ["最终势能线性斜率超过稳定阈值"]


def test_eq_vacuum_and_density_are_observations_not_blockers(tmp_path, monkeypatch):
    import willy.simulation.eq as eq_module
    from willy.simulation.manifest import initialize_manifest, load_manifest, manifest_path
    from willy.simulation.mdp import build_all
    from willy.simulation.protocol import default_md_config
    window = make_window(tmp_path)

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
        write_completion(tmp_path / "eq.log", window)
        return StepResult("eq", 9, True, outputs={"tpr": str(tmp_path / "eq.tpr")})

    values = {
        "Density": [900.0, 1100.0, 1300.0, 1500.0, 1700.0],
        "Temperature": [298.0] * 5,
        "Pressure": [1.0] * 5,
        "Potential": [-1000.0] * 5,
    }

    def fake_extract(_edr, term, output):
        write_block_values(output, window, values[term])
        return True, ""

    monkeypatch.setattr(eq_module, "grompp_and_mdrun", fake_gmx_result)
    monkeypatch.setattr(eq_module, "extract_energy_xvg", fake_extract)
    result = eq_module.run_eq(str(tmp_path))

    assert result.success is True
    assert result.extra["eq_result"].details["vacuum"]["detected"] is True
    assert result.extra["eq_result"].details["series"]["density"]["relative_slope_per_ns"] > 0.01


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "negative-inf"])
@pytest.mark.parametrize("term", ["Temperature", "Potential", "Density", "Pressure"])
def test_eq_non_finite_energy_cannot_grant_prod_permission(tmp_path, monkeypatch, term, value):
    import willy.simulation.eq as eq_module
    from willy.simulation.manifest import (
        ManifestError, initialize_manifest, load_manifest, manifest_path, require_prior_stage,
    )
    from willy.simulation.protocol import default_md_config
    window = make_window(tmp_path)

    (tmp_path / "topol.top").write_text('#include "solute.itp"\n')
    (tmp_path / "solute.itp").write_text("[ moleculetype ]\nSOL 3\n")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"md": default_md_config() | {"run_seed": 12345}}))
    assert build_all(str(config), str(tmp_path)).success
    _write_gro(tmp_path / "em.gro", [0.1] * 24)
    initialize_manifest(tmp_path, config, random_seed=12345, versions={"gromacs": "mock"})
    manifest = load_manifest(tmp_path)
    manifest["stages"]["em"] = {"status": "accepted"}
    manifest_path(tmp_path).write_text(json.dumps(manifest))

    def fake_execution(*args, **kwargs):
        _write_gro(tmp_path / "eq.gro", [0.1] * 24)
        (tmp_path / "eq.cpt").write_text("checkpoint")
        (tmp_path / "eq.tpr").write_text("tpr")
        write_completion(tmp_path / "eq.log", window)
        return StepResult("eq", 9, True, outputs={"tpr": str(tmp_path / "eq.tpr")})

    def fake_extract(_edr, selected_term, output):
        normal = {"Temperature": 298.0, "Potential": -1000.0, "Density": 1000.0, "Pressure": 1.0}
        values = [normal[selected_term]] * 5
        if selected_term == term:
            values[2] = value
        write_block_values(output, window, values)
        return True, ""

    monkeypatch.setattr(eq_module, "grompp_and_mdrun", fake_execution)
    monkeypatch.setattr(eq_module, "extract_energy_xvg", fake_extract)

    result = eq_module.run_eq(str(tmp_path))

    assert result.success is False
    assert result.error.kind == ErrorKind.EQUILIBRATION_FAILED
    assert "非有限" in result.error.message
    manifest = load_manifest(tmp_path)
    assert manifest["stages"]["eq"]["status"] == "failed"
    json.dumps(manifest, allow_nan=False)
    with pytest.raises(ManifestError):
        require_prior_stage(tmp_path, "prod")


def test_eq_missing_optional_observations_remain_non_blocking(tmp_path, monkeypatch):
    import willy.simulation.eq as eq_module
    from willy.simulation.protocol import default_md_config
    window = make_window(tmp_path)

    def fake_extract(_edr, term, output):
        if term in {"Density", "Pressure"}:
            return False, "optional term unavailable"
        write_block_values(output, window, [298.0 if term == "Temperature" else -1000.0] * 5)
        return True, ""

    monkeypatch.setattr(eq_module, "extract_energy_xvg", fake_extract)

    details, issues = eq_module._acceptance_details(
        tmp_path, 2.0, default_md_config()["eq"]["acceptance"], 298.0,
        window=window,
    )

    assert issues == []
    assert details["series"]["density"]["ok"] is False
    assert details["series"]["pressure"]["ok"] is False
