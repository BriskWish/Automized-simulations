"""
em.py
========
能量最小化执行器 —— grompp + mdrun + log 收敛检查。

用法:
  CLI:  python3 -m willy.simulation.em [work_dir]
  API:  from willy.simulation.em import run_em, EMResult
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re

from willy._paths import get_project_root
from willy.simulation._gmx_utils import (
    grompp_and_mdrun,
    prepare_stage_execution,
    record_stage_execution,
)
from willy.simulation.manifest import ManifestError
from willy.simulation.mdp import load_mdp_config
from willy.simulation.protocol import MDConfigError
from willy.errors import StepResult, StepError, ErrorKind
from willy.step_registry import EM_STEP, PACKMOL_STEP

ROOT = get_project_root()
DEFAULT_WORK_DIR = ROOT / "md_run"


@dataclass
class EMResult:
    converged: bool
    fmax: float = 0.0
    steps: int = 0
    tpr: str = ""
    log_tail: str = ""


def _em_extra(gmx_result: StepResult, em_result: EMResult, **fields: object) -> dict[str, object]:
    """Keep common grompp/`mdrun` policy evidence with the EM result."""
    extra = dict(gmx_result.extra)
    extra["em_result"] = em_result
    extra.update(fields)
    return extra


def run_em(work_dir: str = None,
           mdp: str = None,
           conf: str = None,
           topol: str = "topol.top",
           itps: list[str] | None = None,
           tpr: str | None = None,
           on_progress=None,
           on_heartbeat=None,
           ) -> StepResult:
    """执行能量最小化并检查收敛。返回 StepResult 包装 EMResult。"""
    import time as _time
    _start = _time.time()

    cwd = Path(work_dir) if work_dir else DEFAULT_WORK_DIR
    if not cwd.is_absolute():
        cwd = ROOT / work_dir
    if not cwd.exists():
        return StepResult(
            step_name="em", step_index=EM_STEP, success=False,
            error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                            message=f"工作目录不存在: {cwd}",
                            hint="确保 setup 复制文件成功"),
        )

    try:
        # Even a caller-provided em.mdp belongs to a versioned run protocol.
        load_mdp_config(str(cwd / "config.json"))
        preparation = prepare_stage_execution(
            "em", cwd, mdp=mdp, conf=conf, topol=topol, itps=itps, tpr=tpr,
        )
    except (ManifestError, MDConfigError, OSError, ValueError) as exc:
        return StepResult(
            step_name="em", step_index=EM_STEP, success=False,
            error=StepError(ErrorKind.INPUT_CONTRACT, f"EM 输入契约不满足: {exc}"),
        )

    gmx_result = grompp_and_mdrun(
        stage="em", cwd=cwd,
        mdp=preparation.inputs.mdp,
        conf=preparation.inputs.coordinates,
        topol=preparation.inputs.topol,
        itps=preparation.inputs.itps,
        tpr=preparation.inputs.tpr,
        continuation_checkpoint=preparation.continuation_checkpoint,
        append=preparation.append,
        on_progress=on_progress,
        on_heartbeat=on_heartbeat,
    )
    if not gmx_result.success:
        gmx_result.step_name = "em"
        gmx_result.step_index = EM_STEP
        record_stage_execution(
            preparation, success=False, details=gmx_result.extra, error=gmx_result.error,
        )
        return gmx_result

    tpr = gmx_result.outputs.get("tpr", "")

    # ── 检查收敛 ──
    log_path = cwd / "em.log"
    log_text = log_path.read_text() if log_path.exists() else ""

    m = re.search(r"converged to Fmax\s*<\s*([\d.]+)", log_text)
    if m:
        fmax = float(m.group(1))
        steps_m = re.search(r"converged in\s+(\d+)\s+steps", log_text)
        nsteps = int(steps_m.group(1)) if steps_m else 0
        duration = _time.time() - _start
        outputs = dict(gmx_result.outputs)
        outputs["log"] = str(cwd / "em.log")
        result = StepResult(
            step_name="em", step_index=EM_STEP, success=True,
            outputs=outputs,
            artifacts=gmx_result.artifacts,
            duration_s=duration,
            extra=_em_extra(
                gmx_result,
                EMResult(converged=True, fmax=fmax, steps=nsteps, tpr=tpr),
            ),
        )
        record_stage_execution(preparation, success=True, outputs=outputs, details=result.extra)
        return result

    if "Energy minimization has converged" in log_text:
        duration = _time.time() - _start
        outputs = dict(gmx_result.outputs)
        outputs["log"] = str(cwd / "em.log")
        result = StepResult(
            step_name="em", step_index=EM_STEP, success=True,
            outputs=outputs,
            artifacts=gmx_result.artifacts,
            duration_s=duration,
            extra=_em_extra(
                gmx_result,
                EMResult(converged=True, fmax=0.0, steps=0, tpr=tpr),
            ),
        )
        record_stage_execution(preparation, success=True, outputs=outputs, details=result.extra)
        return result

    tail = "\n".join(log_text.split("\n")[-10:]) if log_text else "(log not found)"
    duration = _time.time() - _start
    result = StepResult(
        step_name="em", step_index=EM_STEP, success=False,
        error=StepError(kind=ErrorKind.EM_NOT_CONVERGED,
                        message="能量最小化未收敛",
                        raw_output=tail,
                        hint="增加 nsteps、增大 emtol、或重建 Packmol 盒子（增大 tolerance）"),
        outputs={"tpr": tpr},
        artifacts=gmx_result.artifacts,
        duration_s=duration,
        extra=_em_extra(
            gmx_result,
            EMResult(converged=False, log_tail=tail, tpr=tpr),
            rollback_to_step=PACKMOL_STEP,
            rollback_reason="EM 未收敛，需要从 Packmol 初始盒子重新开始",
        ),
    )
    record_stage_execution(preparation, success=False, outputs=result.outputs, details=result.extra, error=result.error)
    return result


if __name__ == "__main__":
    import sys
    wd = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_WORK_DIR)
    sr = run_em(work_dir=wd)
    if sr.success:
        em = sr.extra.get("em_result")
        if em:
            print(f"✅ EM 收敛 (Fmax < {em.fmax}, {em.steps} steps)")
        else:
            print("✅ EM 收敛")
    else:
        print(f"❌ EM 未收敛: {sr.error.message if sr.error else 'unknown'}")
        if sr.error:
            print(sr.error.raw_output)
        exit(1)
