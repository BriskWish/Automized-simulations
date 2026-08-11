"""Three-point annealing EQ with manifest permission and final-hold acceptance."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from willy._paths import get_project_root
from willy.errors import ErrorKind, StepError, StepResult
from willy.simulation._gmx_utils import (
    analyze_final_window,
    extract_energy_xvg,
    grompp_and_mdrun,
    prepare_stage_execution,
    record_stage_execution,
)
from willy.simulation.manifest import ManifestError
from willy.simulation.mdp import load_mdp_config
from willy.step_registry import EQ_STEP


ROOT = get_project_root()
DEFAULT_WORK_DIR = ROOT / "md_run"


@dataclass
class EQResult:
    converged: bool
    tpr: str = ""
    details: dict = field(default_factory=dict)


def detect_vacuum_region(
    gro_path: Path,
    *,
    bins: int = 12,
    max_empty_fraction: float = 0.25,
) -> dict:
    """Detect a macroscopic empty slab in a final GRO structure."""
    try:
        lines = gro_path.read_text(errors="replace").splitlines()
        atom_count = int(lines[1].strip())
        atom_lines = lines[2:2 + atom_count]
        box_values = [float(value) for value in lines[2 + atom_count].split()]
    except (IndexError, ValueError, OSError):
        return {"detected": False, "reason": "无法解析 eq.gro"}
    if atom_count < 20 or len(box_values) < 3:
        return {"detected": False, "reason": "原子数过少或盒子向量无效"}
    coordinates: list[tuple[float, float, float]] = []
    for line in atom_lines:
        try:
            coordinates.append((
                float(line[20:28]), float(line[28:36]), float(line[36:44]),
            ))
        except ValueError:
            continue
    if len(coordinates) < 20:
        return {"detected": False, "reason": "无法读取足够坐标"}
    for axis, box_length in enumerate(box_values[:3]):
        if box_length <= 0:
            continue
        occupied = {
            min(bins - 1, max(0, int((coord[axis] % box_length) / box_length * bins)))
            for coord in coordinates
        }
        empty = [index not in occupied for index in range(bins)]
        longest = _longest_circular_empty_run(empty)
        fraction = longest / bins
        if fraction >= max_empty_fraction:
            return {
                "detected": True,
                "axis": "xyz"[axis],
                "empty_fraction": round(fraction, 4),
                "bins": bins,
            }
    return {"detected": False, "reason": "未检测到宏观真空区"}


def _longest_circular_empty_run(empty: list[bool]) -> int:
    if not empty or not any(empty):
        return 0
    if all(empty):
        return len(empty)
    longest = current = 0
    for value in empty * 2:
        current = min(current + 1, len(empty)) if value else 0
        longest = max(longest, current)
    return longest


def _contract_failure(message: str) -> StepResult:
    return StepResult(
        step_name="eq", step_index=EQ_STEP, success=False,
        error=StepError(ErrorKind.INPUT_CONTRACT, message),
    )


def _acceptance_details(cwd: Path, hold_ns: float, acceptance: dict, target_temperature: float) -> tuple[dict, list[str]]:
    window_ns = float(acceptance["window_ns"])
    if hold_ns < window_ns:
        return {
            "auto_acceptance": False,
            "reason": "最终目标温度保持段短于验收窗口",
            "hold_target_ns": hold_ns,
            "acceptance_window_ns": window_ns,
        }, ["最终 298 K 保温段短于 EQ 验收窗口，禁止自动判定已平衡"]

    series = {
        "density": ("Density", cwd / "density.xvg"),
        "temperature": ("Temperature", cwd / "temp.xvg"),
        "pressure": ("Pressure", cwd / "pressure.xvg"),
        "potential": ("Potential", cwd / "potential.xvg"),
    }
    details = {
        "auto_acceptance": True,
        "hold_target_ns": hold_ns,
        "acceptance_window_ns": window_ns,
        "series": {},
    }
    issues: list[str] = []
    for key, (term, output) in series.items():
        ok, raw = extract_energy_xvg(cwd / "eq.edr", term, output)
        if not ok:
            details["series"][key] = {"ok": False, "reason": raw[-500:]}
            if key in {"temperature", "potential"}:
                issues.append(f"无法提取 {term} 能量项")
            continue
        stats = analyze_final_window(output, window_ns * 1000.0)
        details["series"][key] = stats
        if not stats.get("ok"):
            if key in {"temperature", "potential"}:
                issues.append(f"{term} 的最终窗口采样不足")
            continue
        if key == "potential" and stats["relative_slope_per_ns"] > float(
            acceptance["max_potential_relative_slope_per_ns"]
        ):
            issues.append("最终势能线性斜率超过稳定阈值")
    temperature = details["series"].get("temperature", {})
    if temperature.get("ok") and abs(
        float(temperature["mean"]) - target_temperature
    ) > float(acceptance["temperature_abs_tolerance_k"]):
        issues.append("最终温度均值偏离目标温度")
    return details, issues


def run_eq(
    work_dir: str | None = None,
    mdp: str | None = None,
    conf: str | None = None,
    topol: str = "topol.top",
    itps: list[str] | None = None,
    tpr: str | None = None,
    vacuum_max_fraction: float = 0.25,
    on_progress=None,
    on_heartbeat=None,
) -> StepResult:
    """Run EQ only after accepted EM and accept only the final target hold."""
    import time as _time

    started_at = _time.time()
    cwd = Path(work_dir) if work_dir else DEFAULT_WORK_DIR
    if not cwd.is_absolute():
        cwd = ROOT / cwd
    if not cwd.exists():
        return StepResult("eq", EQ_STEP, False, error=StepError(ErrorKind.FILE_NOT_FOUND, f"工作目录不存在: {cwd}"))
    try:
        configuration = load_mdp_config(str(cwd / "config.json"))
        preparation = prepare_stage_execution(
            "eq", cwd, mdp=mdp, conf=conf, topol=topol, itps=itps, tpr=tpr,
        )
    except (ManifestError, OSError, ValueError) as exc:
        return _contract_failure(f"EQ 输入契约不满足: {exc}")

    gmx_result = grompp_and_mdrun(
        "eq", cwd,
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
        gmx_result.step_name = "eq"
        gmx_result.step_index = EQ_STEP
        record_stage_execution(
            preparation, success=False, details=gmx_result.extra, error=gmx_result.error,
        )
        return gmx_result
    checkpoint = cwd / "eq.cpt"
    if not checkpoint.is_file() or checkpoint.stat().st_size <= 0:
        result = StepResult(
            "eq", EQ_STEP, False,
            error=StepError(ErrorKind.MDRUN_FAILED, "EQ 未生成非空 eq.cpt，不能安全连续进入 PROD"),
            outputs=dict(gmx_result.outputs), artifacts=gmx_result.artifacts,
            duration_s=_time.time() - started_at,
        )
        record_stage_execution(preparation, success=False, outputs=result.outputs, error=result.error)
        return result

    eq = configuration.values["eq"]
    hold_ns = float(preparation.metadata.get("segments", {}).get("hold_target", {}).get("actual_ns", eq["segments_ns"]["hold_target"]))
    details, issues = _acceptance_details(
        cwd,
        hold_ns,
        eq["acceptance"],
        float(eq["target_temperature"]),
    )
    outputs = dict(gmx_result.outputs)
    artifacts = list(gmx_result.artifacts)
    for name in ("density", "temperature", "pressure", "potential"):
        path = cwd / f"{'temp' if name == 'temperature' else name}.xvg"
        if path.is_file() and path.stat().st_size > 0:
            outputs[f"{name}_xvg"] = str(path)
            artifacts.append(str(path))
    # Structural occupancy and density are retained as diagnostic evidence.
    # They deliberately do not decide whether EQ can advance to PROD.
    details["vacuum"] = detect_vacuum_region(
        cwd / "eq.gro", max_empty_fraction=vacuum_max_fraction,
    )
    eq_result = EQResult(converged=not issues, tpr=gmx_result.outputs.get("tpr", ""), details=details)
    if not issues:
        result = StepResult(
            "eq", EQ_STEP, True,
            outputs=outputs, artifacts=artifacts,
            duration_s=_time.time() - started_at,
            extra={"eq_result": eq_result},
        )
        record_stage_execution(preparation, success=True, outputs=outputs, details=result.extra)
        return result

    result = StepResult(
        "eq", EQ_STEP, False,
        error=StepError(
            ErrorKind.EQUILIBRATION_FAILED,
            "EQ 验收未通过: " + "; ".join(issues),
            hint="检查最终目标温度均值和势能线性斜率",
        ),
        outputs=outputs,
        artifacts=artifacts,
        duration_s=_time.time() - started_at,
        extra={
            "eq_result": eq_result,
        },
    )
    record_stage_execution(preparation, success=False, outputs=outputs, details=result.extra, error=result.error)
    return result


if __name__ == "__main__":
    import sys

    result = run_eq(work_dir=sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_WORK_DIR))
    raise SystemExit(0 if result.success else 1)
