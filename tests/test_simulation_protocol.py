"""Schema-v2 MD protocol, manifest permissions, and box contract tests."""

from __future__ import annotations

import json
import subprocess

import pytest

from willy.errors import ErrorKind
from willy.run_metadata import create_run_manifest, load_run_manifest
from willy.simulation.box import (
    Component, InpConfig, InpGenerator, estimate_box_size,
    estimate_box_size_from_mass,
)
from willy.simulation.manifest import (
    initialize_manifest,
    invalidate_stages_from,
    load_manifest,
    manifest_path,
    prepare_stage_attempt,
    record_stage_result,
    record_box_attempt,
    record_box_execution,
    stage_can_resume,
    stage_contract,
)
from willy.simulation.mdp import build_all
from willy.simulation.protocol import default_md_config, validate_md_config


def _write_common_inputs(tmp_path):
    (tmp_path / "topol.top").write_text('#include "solute.itp"\n\n[ molecules ]\nSOL 1\n')
    (tmp_path / "solute.itp").write_text("[ moleculetype ]\nSOL 3\n")
    (tmp_path / "model.pdb").write_text("ATOM      1  C   SOL A   1       0.000   0.000   0.000\nEND\n")


def _write_v2_config(tmp_path, md=None):
    values = default_md_config() if md is None else md
    values["run_seed"] = 12345
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"md": values}))
    return path


def test_default_eq_mdp_has_six_segment_cumulative_points_and_actual_time(tmp_path):
    config = _write_v2_config(tmp_path)
    result = build_all(str(config), str(tmp_path))

    assert result.success is True
    eq_mdp = (tmp_path / "eq.mdp").read_text()
    assert "annealing_npoints = 7" in eq_mdp
    assert "annealing_time = 0 2000 3000 5000 6000 8000 10000" in eq_mdp
    assert "annealing_temp = 298 500 500 400 400 298 298" in eq_mdp
    metadata = json.loads((tmp_path / "mdp_metadata.json").read_text())
    assert metadata["stages"]["eq"]["nsteps"] == 10_000_000
    assert metadata["stages"]["eq"]["actual_ns"] == pytest.approx(10.0)
    assert metadata["stages"]["eq"]["segments"]["hold_target"]["actual_ns"] == pytest.approx(2.0)
    assert 'tau_p = 2.0' in eq_mdp
    assert metadata["stages"]["eq"]["acceptance"]["max_potential_relative_slope_per_ns"] == pytest.approx(0.01)


def test_run_mdp_metadata_is_embedded_in_md_manifest(tmp_path):
    config = _write_v2_config(tmp_path)
    initialize_manifest(
        tmp_path,
        config,
        random_seed=12345,
        versions={"gromacs": "test", "packmol": "test"},
    )

    result = build_all(str(config), str(tmp_path))

    assert result.success is True
    assert "metadata" not in result.outputs
    assert not (tmp_path / "mdp_metadata.json").exists()
    metadata = load_manifest(tmp_path)["protocol"]["mdp"]
    assert metadata["stages"]["eq"]["actual_ns"] == pytest.approx(10.0)
    assert metadata["protocol_controls"]["pcoupl"] == "C-rescale"


def test_unified_run_manifest_keeps_md_evidence_and_protocol_in_private_sections(tmp_path):
    config = _write_v2_config(tmp_path)
    create_run_manifest(tmp_path)

    initialize_manifest(
        tmp_path,
        config,
        random_seed=12345,
        versions={"gromacs": "test", "packmol": "test"},
    )
    assert build_all(str(config), str(tmp_path), stages=("eq",)).success
    record_box_attempt(tmp_path, {"target_mass_density_g_cm3": 0.7})
    record_box_execution(tmp_path, {
        "output_name": "model.pdb",
        "failure_stage": "preflight",
        "output_before": {"exists": True, "kind": "file", "size_bytes": 12},
        "output_removed_before_run": False,
        "requested_box_vectors_angstrom": [],
        "returncode": None,
    })
    record_stage_result(tmp_path, "eq", success=False, contract={}, error_kind="fixture")

    root = load_run_manifest(tmp_path)
    simulation = root["sections"]["simulation"]["data"]
    protocol = root["sections"]["protocol"]["data"]
    assert not (tmp_path / "md_manifest.json").exists()
    assert simulation["stages"]["eq"]["status"] == "failed"
    assert simulation["box_attempts"][-1]["target_mass_density_g_cm3"] == pytest.approx(0.7)
    assert simulation["box_executions"][-1]["failure_stage"] == "preflight"
    assert simulation["box_executions"][-1]["output_before"]["exists"] is True
    assert "protocol" not in simulation
    assert protocol["mdp"]["stages"]["eq"]["actual_ns"] == pytest.approx(10.0)
    assert load_manifest(tmp_path)["protocol"]["mdp"] == protocol["mdp"]


def test_partial_mdp_regeneration_preserves_other_stage_metadata(tmp_path):
    config = _write_v2_config(tmp_path)
    assert build_all(str(config), str(tmp_path)).success
    assert build_all(str(config), str(tmp_path), stages=("prod",)).success

    metadata = json.loads((tmp_path / "mdp_metadata.json").read_text())
    assert set(metadata["stages"]) == {"em", "eq", "prod"}
    assert metadata["protocol_controls"]["pcoupl"] == "C-rescale"
    assert metadata["protocol_controls"]["dispersion_correction"] == "EnerPres"


@pytest.mark.parametrize("total", [7.0, 100.0])
def test_eq_duration_boundaries_are_valid(total):
    md = default_md_config()
    share = total / 6.0
    md["eq"]["segments_ns"] = {
        "heat": share,
        "hold_high": share,
        "cool_transition": share,
        "hold_transition": share,
        "cool_target": share,
        "hold_target": share,
    }
    validation = validate_md_config(md)
    assert validation.valid


@pytest.mark.parametrize("total", [6.999, 100.001])
def test_eq_duration_outside_boundaries_is_rejected(total):
    md = default_md_config()
    md["eq"]["segments_ns"] = {name: total / 6.0 for name in md["eq"]["segments_ns"]}
    validation = validate_md_config(md)
    assert any("7-100" in issue for issue in validation.issues)


@pytest.mark.parametrize("duration", [2.0, 200.0])
def test_prod_duration_boundaries_are_valid(duration):
    md = default_md_config()
    md["prod"]["duration_ns"] = duration
    assert validate_md_config(md).valid


@pytest.mark.parametrize("duration", [1.999, 200.001])
def test_prod_duration_outside_boundaries_is_rejected(duration):
    md = default_md_config()
    md["prod"]["duration_ns"] = duration
    assert any("prod.duration_ns" in issue for issue in validate_md_config(md).issues)


def test_short_final_hold_warns_and_disables_auto_acceptance(tmp_path):
    md = default_md_config()
    md["eq"]["segments_ns"]["hold_target"] = 0.5
    validation = validate_md_config(md)
    assert validation.valid
    assert any("禁止自动" in warning for warning in validation.warnings)
    config = _write_v2_config(tmp_path, md)
    result = build_all(str(config), str(tmp_path), stages=("eq",))
    assert result.success is True
    assert any("禁止自动" in warning for warning in result.extra["warnings"])


def test_potential_slope_acceptance_threshold_is_validated():
    md = default_md_config()
    md["eq"]["acceptance"]["max_potential_relative_slope_per_ns"] = 0.02
    assert validate_md_config(md).valid

    md["eq"]["acceptance"]["max_potential_relative_slope_per_ns"] = 0.0
    assert any(
        "max_potential_relative_slope_per_ns" in issue
        for issue in validate_md_config(md).issues
    )


def test_eq_cannot_run_without_accepted_em(tmp_path):
    from willy.simulation.eq import run_eq

    _write_common_inputs(tmp_path)
    config = _write_v2_config(tmp_path)
    build_all(str(config), str(tmp_path))
    (tmp_path / "em.gro").write_text("fixture\n1\n    1SOL      C    1   0.000   0.000   0.000\n1 1 1\n")
    initialize_manifest(tmp_path, config, random_seed=12345, versions={"gromacs": "mock", "packmol": "mock"})

    result = run_eq(str(tmp_path))

    assert result.success is False
    assert result.error.kind is ErrorKind.INPUT_CONTRACT
    assert "已验收" in result.error.message


def test_prod_requires_the_accepted_eq_checkpoint(tmp_path):
    from willy.simulation._gmx_utils import prepare_stage_execution

    _write_common_inputs(tmp_path)
    config = _write_v2_config(tmp_path)
    build_all(str(config), str(tmp_path))
    gro = "fixture\n1\n    1SOL      C    1   0.000   0.000   0.000\n1 1 1\n"
    (tmp_path / "em.gro").write_text(gro)
    (tmp_path / "eq.gro").write_text(gro)
    (tmp_path / "eq.cpt").write_text("eq checkpoint")
    initialize_manifest(tmp_path, config, random_seed=12345, versions={"gromacs": "mock", "packmol": "mock"})
    payload = load_manifest(tmp_path)
    payload["stages"]["eq"] = {"status": "accepted"}
    manifest_path(tmp_path).write_text(json.dumps(payload))

    prod = prepare_stage_execution("prod", tmp_path)

    assert prod.inputs.coordinates == tmp_path / "eq.gro"
    assert prod.continuation_checkpoint == tmp_path / "eq.cpt"


def test_stage_manifest_keeps_mdrun_evidence_private_to_the_run(tmp_path):
    config = _write_v2_config(tmp_path)
    initialize_manifest(tmp_path, config, random_seed=12345, versions={"gromacs": "mock", "packmol": "mock"})

    record_stage_result(
        tmp_path,
        "eq",
        success=False,
        contract={},
        error_kind="mdrun_failed",
        error_message="eq: mdrun 失败",
        error_evidence={
            "returncode": -15,
            "signal": "SIGTERM",
            "raw_output_tail": "Fatal error: domain decomposition failed",
        },
    )

    stage = load_manifest(tmp_path)["stages"]["eq"]
    assert stage["error"]["kind"] == "mdrun_failed"
    assert stage["error_evidence"]["returncode"] == -15
    assert stage["error_evidence"]["signal"] == "SIGTERM"
    assert "domain decomposition" in stage["error_evidence"]["raw_output_tail"]


def test_prod_append_requires_matching_manifest_contract_and_checkpoint(tmp_path):
    _write_common_inputs(tmp_path)
    config = _write_v2_config(tmp_path)
    build_all(str(config), str(tmp_path))
    (tmp_path / "eq.gro").write_text("fixture\n1\n    1SOL      C    1   0.000   0.000   0.000\n1 1 1\n")
    (tmp_path / "eq.cpt").write_text("eq checkpoint")
    initialize_manifest(tmp_path, config, random_seed=12345, versions={"gromacs": "mock", "packmol": "mock"})
    payload = load_manifest(tmp_path)
    payload["stages"]["eq"] = {"status": "accepted"}
    manifest_path(tmp_path).write_text(json.dumps(payload))
    contract = stage_contract(
        tmp_path,
        "prod",
        config_path=config,
        topol=tmp_path / "topol.top",
        itps=[tmp_path / "solute.itp"],
        mdp=tmp_path / "prod.mdp",
        coordinates=tmp_path / "eq.gro",
        parent_checkpoint=tmp_path / "eq.cpt",
    )
    payload = load_manifest(tmp_path)
    payload["stages"]["prod"] = {"status": "failed", "contract": contract}
    manifest_path(tmp_path).write_text(json.dumps(payload))
    (tmp_path / "prod.cpt").write_text("prod checkpoint")

    assert stage_can_resume(tmp_path, "prod", contract) is True
    (tmp_path / "prod.mdp").write_text("changed protocol\n")
    changed = stage_contract(
        tmp_path,
        "prod",
        config_path=config,
        topol=tmp_path / "topol.top",
        itps=[tmp_path / "solute.itp"],
        mdp=tmp_path / "prod.mdp",
        coordinates=tmp_path / "eq.gro",
        parent_checkpoint=tmp_path / "eq.cpt",
    )
    assert stage_can_resume(tmp_path, "prod", changed) is False


def test_upstream_protocol_change_invalidates_downstream_acceptance(tmp_path):
    _write_common_inputs(tmp_path)
    config = _write_v2_config(tmp_path)
    build_all(str(config), str(tmp_path))
    (tmp_path / "em.gro").write_text("em structure")
    (tmp_path / "eq.gro").write_text("eq structure")
    (tmp_path / "eq.cpt").write_text("eq checkpoint")
    for name in ("em", "eq", "prod"):
        for suffix in ("tpr", "gro", "xtc", "edr", "cpt"):
            (tmp_path / f"{name}.{suffix}").write_text(f"{name}-{suffix}")
    initialize_manifest(tmp_path, config, random_seed=12345, versions={"gromacs": "mock", "packmol": "mock"})

    contracts = {}
    coordinates = {"em": tmp_path / "model.pdb", "eq": tmp_path / "em.gro", "prod": tmp_path / "eq.gro"}
    mdps = {stage: tmp_path / f"{stage}.mdp" for stage in ("em", "eq", "prod")}
    for stage in ("em", "eq", "prod"):
        contracts[stage] = stage_contract(
            tmp_path, stage, config_path=config, topol=tmp_path / "topol.top",
            itps=[tmp_path / "solute.itp"], mdp=mdps[stage], coordinates=coordinates[stage],
            parent_checkpoint=(tmp_path / "eq.cpt" if stage == "prod" else None),
        )
        prepare_stage_attempt(tmp_path, stage, contracts[stage])
        record_stage_result(tmp_path, stage, success=True, contract=contracts[stage])

    (tmp_path / "em.mdp").write_text("changed em protocol\n")
    changed = stage_contract(
        tmp_path, "em", config_path=config, topol=tmp_path / "topol.top",
        itps=[tmp_path / "solute.itp"], mdp=tmp_path / "em.mdp", coordinates=tmp_path / "model.pdb",
    )
    prepare_stage_attempt(tmp_path, "em", changed)

    manifest = load_manifest(tmp_path)
    assert manifest["stages"]["eq"]["status"] == "invalidated"
    assert manifest["stages"]["prod"]["status"] == "invalidated"
    assert not (tmp_path / "eq.xtc").exists()
    assert not (tmp_path / "prod.xtc").exists()


def test_config_edit_invalidates_changed_stage_and_all_downstream(tmp_path):
    _write_common_inputs(tmp_path)
    config = _write_v2_config(tmp_path)
    build_all(str(config), str(tmp_path))
    (tmp_path / "em.gro").write_text("em structure")
    (tmp_path / "eq.gro").write_text("eq structure")
    (tmp_path / "eq.cpt").write_text("eq checkpoint")
    for stage in ("em", "eq", "prod"):
        for suffix in ("tpr", "gro", "xtc", "edr", "cpt"):
            (tmp_path / f"{stage}.{suffix}").write_text(f"{stage}-{suffix}")
    initialize_manifest(tmp_path, config, random_seed=12345, versions={"gromacs": "mock", "packmol": "mock"})
    coordinates = {"em": tmp_path / "model.pdb", "eq": tmp_path / "em.gro", "prod": tmp_path / "eq.gro"}
    for stage in ("em", "eq", "prod"):
        contract = stage_contract(
            tmp_path, stage, config_path=config, topol=tmp_path / "topol.top",
            itps=[tmp_path / "solute.itp"], mdp=tmp_path / f"{stage}.mdp",
            coordinates=coordinates[stage],
            parent_checkpoint=(tmp_path / "eq.cpt" if stage == "prod" else None),
        )
        prepare_stage_attempt(tmp_path, stage, contract)
        record_stage_result(tmp_path, stage, success=True, contract=contract)

    invalidated = invalidate_stages_from(tmp_path, "eq", reason="EQ 温度已更改")

    manifest = load_manifest(tmp_path)
    assert invalidated == ["eq", "prod"]
    assert manifest["stages"]["em"]["status"] == "accepted"
    assert manifest["stages"]["eq"]["status"] == "invalidated"
    assert manifest["stages"]["prod"]["status"] == "invalidated"
    assert not (tmp_path / "eq.xtc").exists()
    assert not (tmp_path / "prod.xtc").exists()


def test_manifest_archives_each_config_revision_used_by_a_stage(tmp_path):
    _write_common_inputs(tmp_path)
    config = _write_v2_config(tmp_path)
    original = config.read_text()
    build_all(str(config), str(tmp_path))
    initialize_manifest(tmp_path, config, random_seed=12345, versions={"gromacs": "mock", "packmol": "mock"})

    updated = json.loads(config.read_text())
    updated["md"]["run_seed"] = 54321
    config.write_text(json.dumps(updated))
    contract = stage_contract(
        tmp_path, "em", config_path=config, topol=tmp_path / "topol.top",
        itps=[tmp_path / "solute.itp"], mdp=tmp_path / "em.mdp", coordinates=tmp_path / "model.pdb",
    )
    prepare_stage_attempt(tmp_path, "em", contract)

    revisions = load_manifest(tmp_path)["config_revisions"]
    assert len(revisions) == 2
    assert (tmp_path / revisions[0]["archive"]).read_text() == original


def test_packing_number_density_changes_packmol_box_size_and_input():
    components = [Component(pdb="one.pdb", count=216)]
    side_at_six = estimate_box_size(components, 6.0)
    side_at_three = estimate_box_size(components, 3.0)
    assert side_at_three > side_at_six
    content = InpGenerator(InpConfig(
        components=components,
        output_dir=".",
        target_mass_density_g_cm3=None,
        packing_number_density_nm3=3.0,
    )).build()
    assert f"pbc {side_at_three:.3f} {side_at_three:.3f} {side_at_three:.3f}" in content
    assert f"inside cube 0. 0. 0. {side_at_three:.3f}" in content


def test_mass_density_box_uses_topology_mass_and_explicit_pbc():
    components = [Component(pdb="one.pdb", count=2, residue_name="ONE", molecular_mass_amu=50.0)]
    expected_side = estimate_box_size_from_mass(100.0, 0.7)

    generator = InpGenerator(InpConfig(components=components, output_dir="."))
    plan = generator.box_plan()
    content = generator.build()

    assert plan.source == "target_mass_density"
    assert plan.total_mass_amu == 100.0
    assert plan.box_size_angstrom == expected_side
    assert f"pbc {expected_side:.3f} {expected_side:.3f} {expected_side:.3f}" in content
    assert "add_box_sides" not in content


def test_auto_box_config_derives_mass_from_run_local_itp(tmp_path):
    from willy.simulation.box import auto_from_config

    (tmp_path / "A.pdb").write_text("ATOM      1  C   A   A   1       0.000   0.000   0.000\nEND\n")
    (tmp_path / "A.itp").write_text(
        "[ moleculetype ]\nA 3\n\n[ atoms ]\n"
        "1 CA 1 A C1 1 0.0 12.0\n2 HA 1 A H1 1 0.0 1.0\n"
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "residues": {"A": 2},
        "box": {"target_mass_density_g_cm3": 1.0},
    }))

    config = auto_from_config(
        config_path=str(config_path), gro_dir=str(tmp_path), pdb_dir=str(tmp_path), output_dir=str(tmp_path),
    )

    assert config.components[0].molecular_mass_amu == 13.0
    assert config.components[0].residue_name == "A"
    assert InpGenerator(config).box_plan().total_mass_amu == 26.0


def test_packmol_uses_seekable_input_file(tmp_path, monkeypatch):
    import willy.simulation.box as box_module

    component = tmp_path / "AR.pdb"
    component.write_text("ATOM      1  AR  AR  A   1       0.000   0.000   0.000\nEND\n")
    config = InpConfig(
        components=[Component(pdb=str(component), count=1)],
        output_dir=str(tmp_path),
        box_size=10.0,
        packmol_bin="packmol-test",
    )
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        (tmp_path / "model.pdb").write_text(
            "CRYST1   10.000   10.000   10.000  90.00  90.00  90.00 P 1           1\n"
            + component.read_text()
        )
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(box_module.subprocess, "run", fake_run)
    result = InpGenerator(config).run()

    assert result.success
    assert calls[0][0] == ["packmol-test", "-i", str(tmp_path / "model.inp")]
    assert "input" not in calls[0][1]
    assert result.extra["box_parameters"]["actual_box_vectors_angstrom"] == [10.0, 10.0, 10.0]


def test_packmol_failure_removes_stale_output_and_classifies_process_exit(tmp_path, monkeypatch):
    import willy.simulation.box as box_module

    component = tmp_path / "AR.pdb"
    component.write_text("ATOM      1  AR  AR  A   1       0.000   0.000   0.000\nEND\n")
    stale = tmp_path / "model.pdb"
    stale.write_text(
        "CRYST1   10.000   10.000   10.000  90.00  90.00  90.00 P 1           1\n"
        + component.read_text()
    )
    config = InpConfig(
        components=[Component(pdb=str(component), count=1)],
        output_dir=str(tmp_path),
        box_size=10.0,
        packmol_bin="packmol-test",
    )

    def fake_run(args, **kwargs):
        assert not stale.exists(), "a stale output must not satisfy this invocation"
        return subprocess.CompletedProcess(args, 17, "", "packing failed")

    monkeypatch.setattr(box_module.subprocess, "run", fake_run)
    result = InpGenerator(config).run()

    assert not result.success
    assert result.error.kind is ErrorKind.PACKMOL_FAILED
    evidence = result.extra["box_execution"]
    assert evidence["failure_stage"] == "process_exit"
    assert evidence["returncode"] == 17
    assert evidence["output_before"]["exists"] is True
    assert evidence["output_removed_before_run"] is True
    assert evidence["output_after"]["exists"] is False


def test_box_preflight_reports_missing_inputs_as_structured_evidence(tmp_path):
    from willy.simulation.box import validate_box_preflight

    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "residues": {"AR": 1},
        "molecules": {},
        "box": {"target_mass_density_g_cm3": 0.7},
    }))
    (tmp_path / "topol.top").write_text("[ molecules ]\nAR 1\n")

    result = validate_box_preflight(config, tmp_path)

    assert not result.success
    assert result.error.kind is ErrorKind.INPUT_CONTRACT
    evidence = result.extra["box_execution"]
    assert evidence["failure_stage"] == "preflight"
    assert "molecules.AR" in evidence["preflight_issues"]
    assert "AR.pdb/.gro" in evidence["preflight_issues"]


def test_grompp_default_does_not_force_warning_bypass(tmp_path, monkeypatch):
    from willy.simulation._gmx_utils import grompp_and_mdrun
    import willy.simulation._gmx_utils as gmx_utils

    _write_common_inputs(tmp_path)
    (tmp_path / "em.mdp").write_text("integrator = cg\n")
    calls = []

    def fake_gmx(args, cwd, timeout=None, input_text=None):
        calls.append(args)
        if args[0] == "grompp":
            (cwd / "em.tpr").write_text("tpr")
        else:
            for suffix in ("gro", "xtc", "edr", "log", "cpt"):
                (cwd / f"em.{suffix}").write_text("output")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(gmx_utils, "run_gmx", fake_gmx)
    result = grompp_and_mdrun("em", tmp_path)
    assert result.success
    assert "-maxwarn" not in calls[0]


def test_em_zero_step_convergence_gets_a_one_frame_xtc(tmp_path, monkeypatch):
    from willy.simulation._gmx_utils import grompp_and_mdrun
    import willy.simulation._gmx_utils as gmx_utils

    _write_common_inputs(tmp_path)
    (tmp_path / "em.mdp").write_text("integrator = cg\n")
    calls = []

    def fake_gmx(args, cwd, timeout=None, input_text=None):
        calls.append(args)
        if args[0] == "grompp":
            (cwd / "em.tpr").write_text("tpr")
        elif args[0] == "mdrun":
            for suffix in ("gro", "edr", "log", "cpt"):
                (cwd / f"em.{suffix}").write_text("output")
        elif args[0] == "trjconv":
            assert input_text == "0\n"
            (cwd / "em.xtc").write_text("one frame")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(gmx_utils, "run_gmx", fake_gmx)
    result = grompp_and_mdrun("em", tmp_path)

    assert result.success
    assert any(call[0] == "trjconv" for call in calls)
    assert (tmp_path / "em.xtc").is_file()


def test_prod_end_time_prefers_physical_progress_over_wall_clock(tmp_path):
    from willy.simulation.prod import _normal_end_time

    log = tmp_path / "prod.log"
    log.write_text(
        "           Step           Time\n"
        "              0        0.00000\n"
        "           Step           Time\n"
        "         400000     2000.00000\n"
        "Time:        1.010        1.010      100.0\n"
        "Finished mdrun on rank 0\n"
    )

    assert _normal_end_time(log) == pytest.approx(2000.0)


def test_prod_frame_count_uses_gmx_check_summary(tmp_path, monkeypatch):
    from willy.simulation.prod import _trajectory_frames
    import willy.simulation.prod as prod

    (tmp_path / "prod.xtc").write_text("trajectory")

    def fake_gmx(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="Item        #frames Timestep (ps)\nStep           401    5\n",
            stderr="",
        )

    monkeypatch.setattr(prod, "run_gmx", fake_gmx)
    frames, error = _trajectory_frames(tmp_path)

    assert frames == 401
    assert error == ""
