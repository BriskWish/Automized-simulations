"""Production MD with accepted-EQ checkpoint continuity and completion checks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from willy._paths import get_project_root
from willy.errors import ErrorKind, StepError, StepResult
from willy.simulation._gmx_utils import (
    grompp_and_mdrun,
    prepare_stage_execution,
    record_stage_execution,
    run_gmx,
)
from willy.simulation.manifest import ManifestError
from willy.simulation.mdp import load_mdp_config
from willy.step_registry import PROD_STEP


ROOT = get_project_root()
DEFAULT_WORK_DIR = ROOT / "md_run"


@dataclass
class ProdResult:
    completed: bool
    tpr: str = ""
    actual_time_ps: float | None = None
    trajectory_frames: int = 0


def _normal_end_time(log_path: Path) -> float | None:
    if not log_path.is_file() or log_path.stat().st_size <= 0:
        return None
    text = log_path.read_text(errors="replace")
    if "Finished mdrun" not in text:
        return None
    # GROMACS records physical time in repeated two-column progress tables.
    # Do this before the generic ``Time:`` fallback, which otherwise matches
    # the wall-clock timing line in the final performance report.
    progress_times = re.findall(
        r"(?:^|\n)\s*Step\s+Time\s*\n\s*\d+\s+([0-9]+(?:\.[0-9]+)?)",
        text,
    )
    if progress_times:
        return float(progress_times[-1])
    candidates = re.findall(r"(?:^|\n)\s*Time\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)", text)
    if not candidates:
        candidates = re.findall(r"time\s+([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
    return float(candidates[-1]) if candidates else None


def _trajectory_frames(cwd: Path) -> tuple[int, str]:
    try:
        result = run_gmx(["check", "-f", str(cwd / "prod.xtc")], cwd, timeout=60)
    except Exception as exc:
        return 0, str(exc)
    if result.returncode != 0:
        return 0, (result.stderr or result.stdout or "gmx check 失败")[-500:]
    text = (result.stdout or "") + (result.stderr or "")
    summary = re.search(
        r"(?:^|\n)\s*Item\s+#frames\s+Timestep.*?\n\s*(?:Step|Time|Coords)\s+(\d+)\s+",
        text,
        re.DOTALL,
    )
    if summary:
        return int(summary.group(1)), ""
    last_frame = re.search(r"Last frame\s+(\d+)\s+time", text, re.IGNORECASE)
    if last_frame:
        return int(last_frame.group(1)) + 1, ""
    count = len(re.findall(r"Reading frame", text))
    if count == 0:
        match = re.search(r"([0-9]+)\s+frames", text, re.IGNORECASE)
        count = int(match.group(1)) if match else 0
    return count, ""


def run_prod(
    work_dir: str | None = None,
    mdp: str | None = None,
    conf: str | None = None,
    topol: str = "topol.top",
    itps: list[str] | None = None,
    tpr: str | None = None,
    extra_mdrun: list[str] | None = None,
    on_progress=None,
    on_heartbeat=None,
) -> StepResult:
    """Run PROD only from accepted EQ and reject incomplete artifacts."""
    import time as _time

    started_at = _time.time()
    cwd = Path(work_dir) if work_dir else DEFAULT_WORK_DIR
    if not cwd.is_absolute():
        cwd = ROOT / cwd
    if not cwd.exists():
        return StepResult("prod", PROD_STEP, False, error=StepError(ErrorKind.FILE_NOT_FOUND, f"工作目录不存在: {cwd}"))
    try:
        configuration = load_mdp_config(str(cwd / "config.json"))
        preparation = prepare_stage_execution(
            "prod", cwd, mdp=mdp, conf=conf, topol=topol, itps=itps, tpr=tpr,
        )
    except (ManifestError, OSError, ValueError) as exc:
        return StepResult(
            "prod", PROD_STEP, False,
            error=StepError(ErrorKind.RECOVERY_CONFLICT, f"PROD 启动条件不满足: {exc}"),
        )

    gmx_result = grompp_and_mdrun(
        "prod", cwd,
        mdp=preparation.inputs.mdp,
        conf=preparation.inputs.coordinates,
        topol=preparation.inputs.topol,
        itps=preparation.inputs.itps,
        tpr=preparation.inputs.tpr,
        extra_mdrun=extra_mdrun,
        continuation_checkpoint=preparation.continuation_checkpoint,
        append=preparation.append,
        on_progress=on_progress,
        on_heartbeat=on_heartbeat,
    )
    if not gmx_result.success:
        gmx_result.step_name = "prod"
        gmx_result.step_index = PROD_STEP
        record_stage_execution(
            preparation, success=False, details=gmx_result.extra, error=gmx_result.error,
        )
        return gmx_result

    required = {"log": cwd / "prod.log", "checkpoint": cwd / "prod.cpt"}
    missing = [name for name, path in required.items() if not path.is_file() or path.stat().st_size <= 0]
    end_time = _normal_end_time(required["log"])
    frames, frame_error = _trajectory_frames(cwd)
    expected_ps = float(preparation.metadata.get("actual_ns", configuration.values["prod"]["duration_ns"])) * 1000.0
    time_ok = end_time is not None and abs(end_time - expected_ps) <= max(configuration.dt, 0.01)
    if end_time is None:
        missing.append("正常结束日志或终止时间")
    elif not time_ok:
        missing.append(f"实际终止时间 {end_time:g} ps 与协议 {expected_ps:g} ps 不一致")
    if frames <= 0:
        missing.append("有效轨迹帧")
    if missing:
        result = StepResult(
            "prod", PROD_STEP, False,
            error=StepError(
                ErrorKind.MDRUN_FAILED,
                "PROD 产物验收失败: " + "; ".join(missing),
                raw_output=frame_error,
                hint="检查 prod.log、prod.xtc、prod.edr 和 prod.cpt；不完整产物不得进入分析",
            ),
            outputs=dict(gmx_result.outputs),
            artifacts=gmx_result.artifacts,
            duration_s=_time.time() - started_at,
        )
        record_stage_execution(preparation, success=False, outputs=result.outputs, error=result.error)
        return result

    prod_result = ProdResult(
        completed=True,
        tpr=gmx_result.outputs.get("tpr", ""),
        actual_time_ps=end_time,
        trajectory_frames=frames,
    )
    result = StepResult(
        "prod", PROD_STEP, True,
        outputs=gmx_result.outputs,
        artifacts=gmx_result.artifacts,
        duration_s=_time.time() - started_at,
        extra={"prod_result": prod_result},
    )
    record_stage_execution(preparation, success=True, outputs=result.outputs, details=result.extra)
    return result


if __name__ == "__main__":
    import sys

    result = run_prod(work_dir=sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_WORK_DIR))
    raise SystemExit(0 if result.success else 1)
