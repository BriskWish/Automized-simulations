"""Simulation execution contracts without requiring a local GROMACS install."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock

from willy.errors import ErrorKind, StepError, StepResult
from willy.pipeline_orchestrator import PipelineOrchestrator
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


def test_detect_vacuum_region_from_final_eq_structure(tmp_path):
    gro = tmp_path / "eq.gro"
    _write_gro(gro, [0.1 + (index % 2) * 0.05 for index in range(24)])

    result = detect_vacuum_region(gro)

    assert result["detected"] is True
    assert result["axis"] == "x"


def test_eq_vacuum_waits_for_user_confirmation_before_packmol_changes(tmp_path, monkeypatch):
    import willy.pipeline_orchestrator as orchestrator_module
    import willy.pipeline_state as state_module

    monkeypatch.setattr(orchestrator_module, "ROOT", tmp_path)
    monkeypatch.setattr(state_module, "get_project_root", lambda: tmp_path)
    run_dir = tmp_path / "md_run" / "run-1"
    run_dir.mkdir(parents=True)
    config_path = run_dir / "config.json"
    from willy.simulation.protocol import default_md_config
    config_path.write_text(json.dumps({
        "residues": {"Li": 1},
        "molecules": {"Li": {"charge": 0, "spin": 1}},
        "md": default_md_config() | {"max_box_rollbacks": 1},
        "box": {"packing_number_density_nm3": 6.0},
    }))
    for stage in ("em", "eq", "prod"):
        (run_dir / f"{stage}.gro").touch()
        (run_dir / f"{stage}.xtc").touch()

    orchestrator = PipelineOrchestrator(use_llm=False)
    orchestrator._run_config_path = config_path
    simulation_agent = MagicMock()
    simulation_agent.propose_eq_recovery.return_value = {
        "summary": "重新建盒后从 EQ 重新验收。",
        "adjustments": [{"field": "dt", "after": 0.0005}],
    }
    orchestrator._agents[3] = simulation_agent
    failure = StepResult(
        "eq", 9, False,
        error=StepError(ErrorKind.EQ_NOT_CONVERGED, "检测到真空区"),
        extra={
            "rollback_to_step": 7,
            "rollback_reason": "EQ 真空区",
            "box_density_multiplier": 1.1,
        },
    )

    assert orchestrator._handle_single_result(failure, "GROMACS NPT 平衡", run_dir, {}, 3, 9) is False
    assert orchestrator._rollback_to_step is None
    assert (run_dir / "eq.gro").exists()
    assert (run_dir / "prod.xtc").exists()
    saved = json.loads(config_path.read_text())
    assert saved["box"]["packing_number_density_nm3"] == 6.0
    assert orchestrator._sm._status.state == "awaiting_confirmation"
    assert orchestrator._sm._status.extra["pending_action"]["restart_step"] == 7
