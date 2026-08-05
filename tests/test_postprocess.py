"""Contracts for deterministic production-trajectory post-processing."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from willy.errors import ErrorKind
from willy.simulation.manifest import file_fingerprint
from willy.simulation.postprocess import PostprocessConfig, run_postprocess


def _write_prod_sources(workspace):
    for name in ("prod.tpr", "prod.xtc", "prod.edr"):
        (workspace / name).write_bytes(b"source")
    (workspace / "prod.mdp").write_text(
        "dt = 0.001\n"
        "nsteps = 10000\n"
        "nstxout-compressed = 1000\n"
    )
    _write_completed_prod_manifest(workspace)


def _write_completed_prod_manifest(workspace):
    outputs = {
        name: file_fingerprint(workspace / f"prod.{name}", workspace)
        for name in ("tpr", "xtc", "edr")
    }
    manifest = {
        "schema_version": 1,
        "stages": {
            "prod": {
                "status": "completed",
                "contract": {
                    "fingerprint": "accepted-production-contract",
                    "inputs": {"mdp": file_fingerprint(workspace / "prod.mdp", workspace)},
                },
                "outputs": outputs,
            },
        },
        "events": [],
    }
    (workspace / "md_manifest.json").write_text(json.dumps(manifest))


def _write_xvg(path, values):
    path.write_text(
        '@ s0 legend "value"\n'
        + "\n".join(f"{time} {value}" for time, value in values)
        + "\n"
    )


def _fake_gmx(args, cwd, timeout=None, input_text=None):
    command = args[0]
    if command == "check":
        return subprocess.CompletedProcess(
            args=args, returncode=0,
            stdout="There were 11 frames, average spacing 1\nLast frame 10 time 10.000\n",
            stderr="",
        )
    if command == "trjconv":
        output = next(value for index, value in enumerate(args) if args[index - 1] == "-o")
        output_path = Path(output)
        output_path = output_path if output_path.is_absolute() else cwd / output_path
        output_path.write_bytes(b"processed trajectory")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
    if command == "energy":
        output = next(value for index, value in enumerate(args) if args[index - 1] == "-o")
        output_path = Path(output)
        output_path = output_path if output_path.is_absolute() else cwd / output_path
        _write_xvg(output_path, [(0, 1), (2, 2), (4, 3), (6, 4), (8, 5), (10, 6)])
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
    if command == "msd":
        output = next(value for index, value in enumerate(args) if args[index - 1] == "-o")
        output_path = Path(output)
        output_path = output_path if output_path.is_absolute() else cwd / output_path
        _write_xvg(output_path, [(0, 0), (2, 0.4), (4, 0.8), (6, 1.2)])
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
    raise AssertionError(f"unexpected GROMACS command: {args}")


def test_postprocess_generates_traceable_analysis_outputs(tmp_path, monkeypatch):
    import willy.simulation.postprocess as postprocess

    _write_prod_sources(tmp_path)
    monkeypatch.setattr(postprocess, "run_gmx", _fake_gmx)
    activities = []

    result = run_postprocess(tmp_path, on_progress=activities.append)

    assert result.success is True
    assert result.step_index == 11
    analysis_dir = tmp_path / "analysis" / "standard"
    manifest = json.loads((analysis_dir / "analysis_manifest.json").read_text())
    assert manifest["trajectory_integrity"]["complete"] is True
    assert manifest["parameters"]["discard_time_ps"] == 2.0
    assert manifest["thermodynamics"]["temperature"]["statistics"]["sample_count"] == 5
    assert manifest["msd"]["preliminary_diffusion_nm2_per_ps"] == 0.033333333333
    assert manifest["operations"][-1]["source"] == "prod.xtc"
    assert manifest["operations"][-1]["pbc"] == "gmx_default"
    assert (analysis_dir / "prod_centered.xtc").is_file()
    assert (analysis_dir / "prod_nojump.xtc").is_file()
    assert not (analysis_dir / ".prod_whole.xtc").exists()
    assert [item["operation"] for item in activities] == [
        "检查生产轨迹", "处理周期性边界", "提取热力学统计", "计算均方位移", "写入分析记录",
    ]


def test_postprocess_uses_system_for_pbc_and_requested_group_for_msd(tmp_path, monkeypatch):
    import willy.simulation.postprocess as postprocess

    _write_prod_sources(tmp_path)
    calls = []

    def capture_gmx(args, cwd, timeout=None, input_text=None):
        calls.append((args, input_text))
        return _fake_gmx(args, cwd, timeout, input_text)

    monkeypatch.setattr(postprocess, "run_gmx", capture_gmx)

    result = run_postprocess(tmp_path, PostprocessConfig(analysis_id="lithium", msd_group="Li"))

    assert result.success is True
    trjconv_inputs = [input_text for args, input_text in calls if args[0] == "trjconv"]
    assert trjconv_inputs == ["System\n", "System\nSystem\n", "System\n"]
    msd_args, msd_input = next((args, input_text) for args, input_text in calls if args[0] == "msd")
    assert msd_input == "Li\n"
    assert "-nopbc" not in msd_args
    assert "-nrmpbc" not in msd_args


def test_postprocess_rejects_incomplete_trajectory(tmp_path, monkeypatch):
    import willy.simulation.postprocess as postprocess

    _write_prod_sources(tmp_path)

    def incomplete_check(args, cwd, timeout=None, input_text=None):
        assert args[0] == "check"
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="Last frame 5 time 5.000\n", stderr="")

    monkeypatch.setattr(postprocess, "run_gmx", incomplete_check)

    result = run_postprocess(tmp_path)

    assert result.success is False
    assert result.error.kind is ErrorKind.POSTPROCESS_FAILED
    assert "不完整" in result.error.message
    manifest = json.loads((tmp_path / "analysis" / "standard" / "analysis_manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["error"]["kind"] == "postprocess_failed"


def test_postprocess_refuses_to_overwrite_existing_analysis(tmp_path, monkeypatch):
    import willy.simulation.postprocess as postprocess

    _write_prod_sources(tmp_path)
    analysis_dir = tmp_path / "analysis" / "standard"
    analysis_dir.mkdir(parents=True)
    (analysis_dir / "analysis_manifest.json").write_text("{}")
    monkeypatch.setattr(postprocess, "run_gmx", _fake_gmx)

    result = run_postprocess(tmp_path, PostprocessConfig())

    assert result.success is False
    assert result.error.kind is ErrorKind.LOCK_CONFLICT
    assert "已存在" in result.error.message


def test_postprocess_rejects_invalid_discard_fraction(tmp_path):
    result = run_postprocess(tmp_path, PostprocessConfig(discard_fraction=1.0))

    assert result.success is False
    assert result.error.kind is ErrorKind.CONFIG_INVALID
    assert "discard_fraction" in result.error.message


def test_postprocess_reports_missing_production_artifacts(tmp_path):
    result = run_postprocess(tmp_path)

    assert result.success is False
    assert result.error.kind is ErrorKind.FILE_NOT_FOUND
    assert "prod.xtc" in result.error.message


def test_postprocess_rejects_sources_without_completed_prod_manifest(tmp_path):
    for name in ("prod.tpr", "prod.xtc", "prod.edr"):
        (tmp_path / name).write_bytes(b"source")
    (tmp_path / "prod.mdp").write_text("dt = 0.001\nnsteps = 10000\n")

    result = run_postprocess(tmp_path)

    assert result.success is False
    assert result.error.kind is ErrorKind.INPUT_CONTRACT
    assert "验收" in result.error.message
    assert not (tmp_path / "analysis").exists()


def test_postprocess_rejects_artifact_fingerprint_mismatch(tmp_path):
    _write_prod_sources(tmp_path)
    (tmp_path / "prod.xtc").write_bytes(b"mixed-run-trajectory")

    result = run_postprocess(tmp_path)

    assert result.success is False
    assert result.error.kind is ErrorKind.INPUT_CONTRACT
    assert "指纹不一致" in result.error.message
