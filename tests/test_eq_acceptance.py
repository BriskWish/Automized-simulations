"""Strict EQ time coverage and production-admission regression tests."""

from __future__ import annotations

import json
import math
from unittest.mock import patch

import pytest

from willy.errors import StepResult
from willy.simulation._gmx_utils import analyze_final_window
from willy.simulation.eq_acceptance import EQCoverageError, EQWindow
from willy.simulation.manifest import (
    ManifestError, initialize_manifest, load_manifest, manifest_path, require_prior_stage,
)
from willy.simulation.mdp import build_all
from willy.simulation.protocol import default_md_config, validate_md_config


def make_window(directory, *, md=None, stride=500):
    configuration = md or default_md_config()
    config_path = directory / "config.json"
    config_path.write_text(json.dumps({"md": configuration | {"run_seed": 12345}}))
    assert build_all(str(config_path), str(directory)).success
    mdp = directory / "eq.mdp"
    mdp.write_text(mdp.read_text().replace("nstenergy = 500", f"nstenergy = {stride}"))
    metadata = json.loads((directory / "mdp_metadata.json").read_text())["stages"]["eq"]
    window = EQWindow.from_mdp(mdp, metadata, configuration["eq"]["target_temperature"])
    write_completion(directory / "eq.log", window)
    return window


def write_completion(path, window, *, final_step=None, finished=True):
    step = window.final_step if final_step is None else final_step
    time_ps = window.initial_time_ps + step * window.dt_ps
    path.write_text(f"Step Time\n{step} {time_ps:.8f}\n" + ("Finished mdrun\n" if finished else ""))


def sample_times(window):
    first_grid = math.ceil((window.start_ps - window.initial_time_ps) / window.interval_ps)
    result = [window.initial_time_ps + index * window.interval_ps for index in range(first_grid, window.final_step // window.energy_stride + 1)]
    if not result or not math.isclose(result[-1], window.end_ps, abs_tol=1e-8):
        result.append(window.end_ps)
    return result


def write_samples(path, times, values):
    path.write_text("".join(f"{time_ps:.8f} {value}\n" for time_ps, value in zip(times, values)))


def write_block_values(path, window, values):
    times = sample_times(window)
    samples = [values[min(4, int((time_ps - window.start_ps) / 200.0))] for time_ps in times]
    write_samples(path, times, samples)


def accept_eq_fixture(directory, *, transform=None, early=False, transformed_term="Temperature"):
    import willy.simulation.eq as eq_module

    window = make_window(directory)
    (directory / "topol.top").write_text('#include "solute.itp"\n')
    (directory / "solute.itp").write_text("[ moleculetype ]\nSOL 3\n")
    gro = "fixture\n1\n    1SOL      C    1   0.100   0.100   0.100\n3 3 3\n"
    (directory / "em.gro").write_text(gro)
    initialize_manifest(directory, directory / "config.json", random_seed=12345, versions={"gromacs": "mock"})
    manifest = load_manifest(directory)
    manifest["stages"]["em"] = {"status": "accepted"}
    manifest_path(directory).write_text(json.dumps(manifest))

    def fake_execution(*args, **kwargs):
        for suffix in ("tpr", "xtc", "edr", "cpt"):
            (directory / f"eq.{suffix}").write_text("fixture")
        (directory / "eq.gro").write_text(gro)
        write_completion(directory / "eq.log", window, final_step=window.final_step - (500 if early else 0))
        return StepResult("eq", 9, True, outputs={suffix: str(directory / f"eq.{suffix}") for suffix in ("tpr", "gro", "xtc", "edr", "log", "cpt")})

    def fake_extract(_edr, term, output):
        times = sample_times(window)
        if transform is not None and term == transformed_term:
            times = transform(times)
        value = {"Temperature": 298.0, "Potential": -1000.0, "Density": 1000.0, "Pressure": 1.0}[term]
        write_samples(output, times, [value] * len(times))
        return True, ""

    with patch.object(eq_module, "grompp_and_mdrun", fake_execution), patch.object(eq_module, "extract_energy_xvg", fake_extract):
        return eq_module.run_eq(str(directory))


def test_five_blocks_have_complete_nonoverlapping_coverage(tmp_path):
    window = make_window(tmp_path)
    path = tmp_path / "energy.xvg"
    write_block_values(path, window, [296.0, 297.0, 298.0, 299.0, 300.0])

    result = analyze_final_window(path, 1000.0, coverage=window)

    assert result["ok"] is True
    coverage = result["coverage"]
    assert coverage["expected_start_ps"] == 9000.0
    assert coverage["expected_end_ps"] == 10000.0
    assert [block["sample_count"] for block in coverage["blocks"]] == [400, 400, 400, 400, 401]
    assert [block["relative_end_ns"] for block in coverage["blocks"]] == [-0.8, -0.6, -0.4, -0.2, 0.0]
    assert result["sample_count"] == 2001
    assert result["mean"] == pytest.approx((400 * (296 + 297 + 298 + 299) + 401 * 300) / 2001)


@pytest.mark.parametrize("stride", [200, 750, 777])
@pytest.mark.parametrize("duration_ns", [7.0, 12.0])
def test_coverage_uses_actual_duration_and_output_grid(tmp_path, stride, duration_ns):
    configuration = default_md_config()
    configuration["eq"]["segments_ns"]["heat"] += duration_ns - 10.0
    if configuration["eq"]["segments_ns"]["heat"] <= 0:
        configuration["eq"]["segments_ns"].update(heat=1.0, cool_transition=1.0, cool_target=1.0)
    window = make_window(tmp_path, md=configuration, stride=stride)
    path = tmp_path / "energy.xvg"
    write_block_values(path, window, [298.0] * 5)

    result = analyze_final_window(path, 1000.0, coverage=window)

    assert result["ok"] is True
    assert result["coverage"]["expected_end_ps"] == duration_ns * 1000.0
    assert sum(block["sample_count"] for block in result["coverage"]["blocks"]) == result["sample_count"]


BAD_TIMES = {
    "missing-block": lambda times: [value for value in times if not 9400 <= value < 9600],
    "internal-gap": lambda times: times[:100] + times[101:],
    "early-energy-end": lambda times: times[:-1],
    "out-of-order": lambda times: times[:100] + [times[101], times[100]] + times[102:],
    "duplicate-time": lambda times: times[:100] + [times[99]] + times[101:],
    "extra-time": lambda times: times[:100] + [times[99] + 0.1] + times[100:],
    "wrong-frequency": lambda times: times[:100] + [times[100] + 0.1] + times[101:],
    "early-fragment": lambda times: [0.0, 1.0, 2.0, 3.0],
    "five-instantaneous-points": lambda times: [9200.0, 9400.0, 9600.0, 9800.0, 10000.0],
    "late-energy-end": lambda times: times + [10000.5],
    "nan-time": lambda times: times[:100] + [math.nan] + times[101:],
    "inf-time": lambda times: times[:100] + [math.inf] + times[101:],
    "negative-inf-time": lambda times: times[:100] + [-math.inf] + times[101:],
    "duplicate-before-window": lambda times: [0.0, 0.0] + times,
}


@pytest.mark.parametrize("case", list(BAD_TIMES))
def test_invalid_coverage_cannot_grant_production_permission(tmp_path, case):
    result = accept_eq_fixture(tmp_path, transform=BAD_TIMES[case])

    assert result.success is False
    assert result.error.kind.value == "equilibration_failed"
    assert load_manifest(tmp_path)["stages"]["eq"]["status"] == "failed"
    with pytest.raises(ManifestError):
        require_prior_stage(tmp_path, "prod")


def test_early_normal_exit_does_not_count_as_complete_eq(tmp_path):
    result = accept_eq_fixture(tmp_path, early=True)
    assert result.success is False
    assert "结束" in result.error.message
    with pytest.raises(ManifestError):
        require_prior_stage(tmp_path, "prod")


def test_complete_eq_grants_production_permission(tmp_path):
    result = accept_eq_fixture(tmp_path)
    assert result.success is True
    assert require_prior_stage(tmp_path, "prod")["status"] == "accepted"


def test_legacy_accepted_flag_without_coverage_is_rejected(tmp_path):
    assert accept_eq_fixture(tmp_path).success
    manifest = load_manifest(tmp_path)
    manifest["stages"]["eq"].pop("details")
    manifest_path(tmp_path).write_text(json.dumps(manifest))
    with pytest.raises(ManifestError, match="五段覆盖"):
        require_prior_stage(tmp_path, "prod")


@pytest.mark.parametrize("name", ["eq.mdp", "eq.cpt", "eq.log", "eq.edr", "eq.tpr", "eq.gro", "temp.xvg", "potential.xvg"])
def test_changed_acceptance_artifact_revokes_production_permission(tmp_path, name):
    assert accept_eq_fixture(tmp_path).success
    path = tmp_path / name
    path.write_text(path.read_text() + "changed")
    with pytest.raises(ManifestError, match="指纹变化"):
        require_prior_stage(tmp_path, "prod")


@pytest.mark.parametrize("term", ["Density", "Pressure", "Potential"])
@pytest.mark.parametrize("anomaly", ["missing-block", "out-of-order", "duplicate-time"])
def test_extracted_observations_cannot_hide_invalid_time_evidence(tmp_path, term, anomaly):
    result = accept_eq_fixture(tmp_path, transform=BAD_TIMES[anomaly], transformed_term=term)
    assert result.success is False
    assert result.error.kind.value == "equilibration_failed"
    with pytest.raises(ManifestError):
        require_prior_stage(tmp_path, "prod")


@pytest.mark.parametrize("mutation", ["missing-finish", "wrong-step", "nan-time", "inf-time", "missing-log"])
def test_completion_requires_valid_final_step_and_normal_end(tmp_path, mutation):
    window = make_window(tmp_path)
    if mutation == "missing-finish":
        write_completion(tmp_path / "eq.log", window, finished=False)
    elif mutation == "wrong-step":
        write_completion(tmp_path / "eq.log", window, final_step=window.final_step - 1)
    elif mutation == "missing-log":
        (tmp_path / "eq.log").unlink()
    else:
        value = "nan" if mutation == "nan-time" else "inf"
        (tmp_path / "eq.log").write_text(f"Step Time\n{window.final_step} {value}\nFinished mdrun\n")
    with pytest.raises(EQCoverageError):
        window.completion(tmp_path / "eq.log")


@pytest.mark.parametrize("content", ["broken row\n", "9999.5\n", "9999.5 298 1\n"])
def test_malformed_energy_rows_are_not_silently_skipped(tmp_path, content):
    window = make_window(tmp_path)
    path = tmp_path / "energy.xvg"
    write_block_values(path, window, [298.0] * 5)
    path.write_text(content + path.read_text())
    assert analyze_final_window(path, 1000.0, coverage=window)["ok"] is False


def test_final_target_hold_must_contain_the_whole_window(tmp_path):
    configuration = default_md_config()
    configuration["eq"]["segments_ns"]["hold_target"] = 0.5
    with pytest.raises(EQCoverageError, match="不足 1 ns"):
        make_window(tmp_path, md=configuration)


def test_window_configuration_cannot_shorten_the_fixed_policy():
    configuration = default_md_config()
    configuration["eq"]["acceptance"]["window_ns"] = 0.2
    assert validate_md_config(configuration).valid is False


@pytest.mark.parametrize("observation", ["Temperature", "Potential"])
def test_blocks_do_not_introduce_new_stability_thresholds(tmp_path, monkeypatch, observation):
    import willy.simulation.eq as eq_module

    window = make_window(tmp_path)
    def fake_extract(_edr, term, output):
        values = [298.0] * 5 if term == "Temperature" else [-1000.0] * 5
        if term == observation:
            values = [278.0, 318.0, 298.0, 298.0, 298.0] if observation == "Temperature" else [-1000.0, -800.0, -1200.0, -800.0, -1000.0]
        write_block_values(output, window, values)
        return True, ""
    monkeypatch.setattr(eq_module, "extract_energy_xvg", fake_extract)
    details, issues = eq_module._acceptance_details(tmp_path, 2.0, default_md_config()["eq"]["acceptance"], 298.0, window=window)
    assert issues == []
    assert details["series"]["temperature"]["mean"] == 298.0


@pytest.mark.parametrize("field,value", [
    ("dt", "nan"), ("dt", "inf"), ("dt", "0"), ("nsteps", "0"),
    ("nstenergy", "0"), ("nstenergy", "-1"), ("annealing", "periodic"),
    ("annealing_npoints", "2"), ("tinit", "inf"), ("init_step", "1000000"),
    ("annealing_temp", "600 600 600 400 400 400 400"),
    ("annealing_time", "0 2000 3000 5000 6000 8000 9999"),
    ("annealing_time", "0 2000 3000 5000 8000 8000 10000"),
])
def test_mdp_must_prove_expected_end_and_target_hold(tmp_path, field, value):
    make_window(tmp_path)
    path = tmp_path / "eq.mdp"
    lines = [line for line in path.read_text().splitlines() if line.split("=", 1)[0].strip().replace("-", "_") != field]
    path.write_text("\n".join(lines) + f"\n{field} = {value}\n")
    metadata = json.loads((tmp_path / "mdp_metadata.json").read_text())["stages"]["eq"]
    with pytest.raises(EQCoverageError):
        EQWindow.from_mdp(path, metadata, 298.0)


@pytest.mark.parametrize("mutation", ["missing-metadata", "wrong-duration", "wrong-schedule", "duplicate-mdp"])
def test_inconsistent_protocol_evidence_is_rejected(tmp_path, mutation):
    make_window(tmp_path)
    metadata = json.loads((tmp_path / "mdp_metadata.json").read_text())["stages"]["eq"]
    path = tmp_path / "eq.mdp"
    if mutation == "missing-metadata":
        metadata = {}
    elif mutation == "wrong-duration":
        metadata["actual_ns"] = 12.0
    elif mutation == "wrong-schedule":
        metadata["annealing_time_ps"][-2] = 8500.0
    else:
        path.write_text(path.read_text() + "\nnstenergy = 500\n")
    with pytest.raises(EQCoverageError):
        EQWindow.from_mdp(path, metadata, 298.0)


@pytest.mark.parametrize("mutation", ["flag", "count", "block", "mean", "completion", "policy", "missing-step", "window"])
def test_incomplete_manifest_proof_is_not_a_permission(tmp_path, mutation):
    assert accept_eq_fixture(tmp_path).success
    manifest = load_manifest(tmp_path)
    details = manifest["stages"]["eq"]["details"]["eq_result"]["details"]
    stats = details["series"]["temperature"]
    if mutation == "flag":
        details["auto_acceptance"] = False
    elif mutation == "count":
        stats["coverage"]["expected_sample_count"] += 1
    elif mutation == "block":
        stats["coverage"]["blocks"].pop()
    elif mutation == "mean":
        stats["coverage"]["blocks"][0]["mean"] = math.nan
    elif mutation == "completion":
        details["completion"]["observed_end_ps"] -= 1.0
    elif mutation == "missing-step":
        details["completion"].pop("final_step")
    elif mutation == "window":
        stats["window_ps"] = 0.2
    else:
        details["acceptance_policy"] = "legacy"
    manifest_path(tmp_path).write_text(json.dumps(manifest))
    with pytest.raises(ManifestError, match="五段覆盖"):
        require_prior_stage(tmp_path, "prod")


def test_output_frequency_cannot_reduce_blocks_to_instantaneous_points(tmp_path):
    window = make_window(tmp_path, stride=200000)
    path = tmp_path / "energy.xvg"
    write_block_values(path, window, [298.0] * 5)
    assert analyze_final_window(path, 1000.0, coverage=window)["ok"] is False
