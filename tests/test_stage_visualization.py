"""Non-critical GRO-to-PDB artifacts for the run visualization panel."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from willy.errors import ErrorKind, StepError, StepResult
from willy.pipeline_orchestrator import PipelineOrchestrator
from willy.simulation.manifest import clear_stage_outputs
from willy.simulation.visualization import (
    clear_stage_visualization_artifact,
    convert_stage_gro_to_pdb,
    schedule_accepted_stage_visualization,
    stage_visualization_path,
)
from willy.step_registry import EM_STEP, MDP_STEP, PROD_STEP


def _write_gro(path, *, coordinates=(0.1, 0.2)):
    lines = ["viewer fixture", str(len(coordinates))]
    for index, coordinate in enumerate(coordinates, 1):
        lines.append(
            f"{1:5d}{'SOL':<5}{'C':>5}{index:5d}"
            f"{coordinate:8.3f}{0.5:8.3f}{0.5:8.3f}"
        )
    lines.append("3.00000 3.00000 3.00000")
    path.write_text("\n".join(lines) + "\n")


def _install_editconf_stub(monkeypatch, *, mutate_source=False):
    import willy.simulation.visualization as visualization

    monkeypatch.setattr(
        visualization,
        "require_tool",
        lambda _tool_id: SimpleNamespace(executable="gmx"),
    )
    monkeypatch.setattr(visualization, "build_tool_env", lambda _tool_id: {})

    def fake_run(command, *, cwd, timeout, env, run_dir):
        assert timeout == 20
        assert command[1:3] == ["editconf", "-f"]
        source = command[3]
        output = command[command.index("-o") + 1]
        if mutate_source:
            _write_gro(type(cwd)(source), coordinates=(0.7, 0.8))
        type(cwd)(output).write_text(
            "ATOM      1  C   SOL A   1       1.000   1.000   1.000\n"
            "ATOM      2  C   SOL A   1       2.000   2.000   2.000\n"
            "END\n"
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(visualization, "run_gmx_auxiliary", fake_run)


def test_stage_visualization_converts_gro_after_acceptance(tmp_path, monkeypatch):
    _write_gro(tmp_path / "em.gro")
    _install_editconf_stub(monkeypatch)

    result = convert_stage_gro_to_pdb(tmp_path, "em")

    output = stage_visualization_path(tmp_path, "em")
    assert result.success is True
    assert result.output == output
    assert output.is_file()
    assert output.read_text().count("ATOM") == 2
    assert not list(output.parent.glob(".em.*.pdb"))


def test_stale_conversion_cannot_publish_after_stage_source_changes(tmp_path, monkeypatch):
    import willy.simulation.visualization as visualization

    source = tmp_path / "eq.gro"
    _write_gro(source)
    expected_digest = visualization._fingerprint(source)
    _install_editconf_stub(monkeypatch, mutate_source=True)

    result = convert_stage_gro_to_pdb(
        tmp_path,
        "eq",
        expected_source_digest=expected_digest,
    )

    assert result.success is False
    assert result.reason == "stale"
    assert not stage_visualization_path(tmp_path, "eq").exists()


def test_schedule_runs_a_daemon_conversion_without_blocking_pipeline(tmp_path, monkeypatch):
    import willy.simulation.visualization as visualization

    _write_gro(tmp_path / "prod.gro")
    _install_editconf_stub(monkeypatch)
    created = {}

    class ImmediateThread:
        def __init__(self, *, target, name, daemon):
            created.update(name=name, daemon=daemon)
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(visualization.threading, "Thread", ImmediateThread)

    schedule_accepted_stage_visualization(tmp_path, "prod")

    assert created == {"name": "willy-viewer-prod", "daemon": True}
    assert stage_visualization_path(tmp_path, "prod").is_file()


def test_stage_regeneration_removes_its_derived_viewer_artifact(tmp_path):
    output = stage_visualization_path(tmp_path, "em")
    output.parent.mkdir()
    output.write_text("ATOM\n")
    (output.parent / ".em.token").write_text("token")

    removed = clear_stage_visualization_artifact(tmp_path, "em")

    assert sorted(removed) == ["visualization/.em.token", "visualization/em.pdb"]
    assert not output.exists()

    output.write_text("ATOM\n")
    removed = clear_stage_outputs(tmp_path, "em")
    assert "visualization/em.pdb" in removed
    assert not output.exists()


def test_orchestrator_schedules_only_md_stage_visualizations(tmp_path, monkeypatch):
    import willy.simulation.visualization as visualization

    scheduled = []
    monkeypatch.setattr(
        visualization,
        "schedule_accepted_stage_visualization",
        lambda run_dir, stage: scheduled.append((run_dir, stage)),
    )

    PipelineOrchestrator._schedule_stage_visualization(tmp_path, MDP_STEP)
    PipelineOrchestrator._schedule_stage_visualization(tmp_path, EM_STEP)

    assert scheduled == [(tmp_path, "em")]


def test_prod_visualization_is_a_finalization_contract(tmp_path, monkeypatch):
    import willy.simulation.visualization as visualization

    calls = []

    def fake_convert(run_dir, stage):
        calls.append((run_dir, stage))
        return visualization.VisualizationConversionResult(
            stage, True, stage_visualization_path(run_dir, stage), ""
        )

    monkeypatch.setattr(visualization, "convert_stage_gro_to_pdb", fake_convert)

    failure = PipelineOrchestrator._prod_visualization_contract_failure(PROD_STEP, tmp_path)

    assert failure is None
    assert calls == [(tmp_path, "prod")]


def test_prod_visualization_failure_blocks_terminal_completion(tmp_path, monkeypatch):
    import willy.simulation.visualization as visualization

    monkeypatch.setattr(
        visualization,
        "convert_stage_gro_to_pdb",
        lambda *_args, **_kwargs: visualization.VisualizationConversionResult(
            "prod", False, reason="editconf_failed"
        ),
    )

    failure = PipelineOrchestrator._prod_visualization_contract_failure(PROD_STEP, tmp_path)

    assert failure is not None
    assert failure.success is False
    assert failure.error.kind.value == "input_contract"
    assert failure.target == "prod"


def test_orchestrator_does_not_enter_done_without_prod_pdb(tmp_path, monkeypatch):
    run_dir = tmp_path / "md_run" / "md_prod_pdb_required"
    run_dir.mkdir(parents=True)
    config_path = run_dir / "config.json"
    config_path.write_text("{}")
    orchestrator = PipelineOrchestrator(use_llm=False)
    monkeypatch.setattr(orchestrator, "_prepare_run_directory", lambda _run_dir: config_path)
    monkeypatch.setattr(
        orchestrator,
        "_build_steps",
        lambda _run_dir: [
            (
                f"step-{step_index}",
                lambda step_index=step_index: StepResult(
                    f"step-{step_index}", step_index, True
                ),
                None,
                False,
                3,
            )
            for step_index in range(1, 11)
        ],
    )
    monkeypatch.setattr(orchestrator, "_completion_contract_failure", lambda *_args: None)
    monkeypatch.setattr(
        orchestrator,
        "_prod_visualization_contract_failure",
        lambda step_index, _run_dir: StepResult(
            "prod",
            step_index,
            False,
            error=StepError(ErrorKind.INPUT_CONTRACT, "PROD 可视化产物验收失败"),
            target_type="stage",
            target="prod",
        ) if step_index == PROD_STEP else None,
    )

    assert orchestrator.run(run_dir=run_dir) is False
    assert orchestrator._sm._status.state == "aborted"
    assert PROD_STEP not in orchestrator._sm._status.done_steps


def test_prod_final_cleanup_removes_only_requested_intermediates(tmp_path):
    run_dir = tmp_path / "md_prod_cleanup"
    run_dir.mkdir()
    (run_dir / "config.json").write_text('{"molecules": {"Li": {}, "DMM": {}}}')
    removable = [
        "Li.chg", "Li.chk", "Li.gro", "Li.log", "Li_opt.chk", "Li_opt.log",
        "DMM.chg", "DMM.chk", "DMM.gro", "DMM.log", "DMM_opt.chk", "DMM_opt.log",
        "em_out.mdp", "eq_out.mdp", "prod_out.mdp",
    ]
    retained = [
        "Li.fchk", "Li_opt.fchk", "DMM.fchk", "DMM_opt.fchk",
        "em.gro", "eq.gro", "prod.gro", "prod.log", "prod.mdp", "prod.xtc",
    ]
    for name in removable + retained:
        (run_dir / name).write_text(name)

    failure = PipelineOrchestrator._prod_intermediate_cleanup_contract_failure(
        PROD_STEP, run_dir
    )

    assert failure is None
    assert not any((run_dir / name).exists() for name in removable)
    assert all((run_dir / name).is_file() for name in retained)


def test_prod_final_cleanup_rejects_unsafe_molecule_names(tmp_path):
    run_dir = tmp_path / "md_prod_cleanup_invalid"
    run_dir.mkdir()
    (run_dir / "config.json").write_text('{"molecules": {"../outside": {}}}')

    failure = PipelineOrchestrator._prod_intermediate_cleanup_contract_failure(
        PROD_STEP, run_dir
    )

    assert failure is not None
    assert failure.error.kind.value == "input_contract"
