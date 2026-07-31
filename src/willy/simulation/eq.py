"""
eq.py
========
NPT 平衡执行器 —— grompp + mdrun + 密度/温度收敛后检查。

检查逻辑: 取最后 20% 轨迹，密度和温度的 (max-min)/|mean| 均 < 0.10。

用法:
  CLI:  python3 -m willy.simulation.eq [work_dir]
  API:  from willy.simulation.eq import run_eq, EQResult
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import subprocess

from willy._paths import get_project_root
from willy.simulation._gmx_utils import grompp_and_mdrun, check_last_fraction
from willy.errors import StepResult, StepError, ErrorKind

ROOT = get_project_root()
DEFAULT_WORK_DIR = ROOT / "md_run"
MAX_REL_CHANGE = 0.10
CHECK_FRACTION = 0.20


@dataclass
class EQResult:
    converged: bool
    density_ok: bool = False
    temp_ok: bool = False
    density_mean: float = 0.0
    density_rel_change: float = 0.0
    temp_mean: float = 0.0
    temp_rel_change: float = 0.0
    tpr: str = ""
    details: dict = field(default_factory=dict)


def _extract_energy(edr: Path, prop_num: str, prop_name: str, out: Path) -> bool:
    """gmx energy 提取指定属性到 .xvg。"""
    cmd = f"echo '{prop_num} 0' | gmx energy -f {edr} -o {out}"
    r = subprocess.run(
        cmd, shell=True, capture_output=True, text=True,
        cwd=str(edr.parent), timeout=30,
    )
    ok = r.returncode == 0 and out.exists()
    if not ok:
        print(f"[eq] ⚠  gmx energy {prop_name} (#{prop_num}) 提取失败")
    return ok


def run_eq(work_dir: str = None,
           mdp: str = None,
           conf: str = None,
           topol: str = "topol.top",
           ) -> StepResult:
    """执行 NPT 平衡并检查密度/温度稳定性。返回 StepResult 包装 EQResult。"""
    import time as _time
    _start = _time.time()

    cwd = Path(work_dir) if work_dir else DEFAULT_WORK_DIR
    if not cwd.is_absolute():
        cwd = ROOT / work_dir
    if not cwd.exists():
        return StepResult(
            step_name="eq", step_index=9, success=False,
            error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                            message=f"工作目录不存在: {cwd}"),
        )

    gmx_result = grompp_and_mdrun(
        stage="eq", cwd=cwd,
        mdp=mdp, conf=conf, topol=topol,
    )
    if not gmx_result.success:
        gmx_result.step_name = "eq"
        gmx_result.step_index = 9
        return gmx_result

    tpr = gmx_result.outputs.get("tpr", "")

    # ── 提取密度 (#22) 和温度 (#15) ──
    edr = cwd / "eq.edr"
    if not edr.exists():
        return StepResult(
            step_name="eq", step_index=9, success=False,
            error=StepError(kind=ErrorKind.MDRUN_FAILED,
                            message=f"{edr} 不存在，EQ 运行可能失败",
                            hint="检查 mdrun 是否正常完成"),
            outputs={"tpr": tpr},
            artifacts=gmx_result.artifacts,
            duration_s=_time.time() - _start,
        )

    density_xvg = cwd / "density.xvg"
    temp_xvg = cwd / "temp.xvg"
    _extract_energy(edr, "22", "Density", density_xvg)
    _extract_energy(edr, "15", "Temperature", temp_xvg)

    # ── 检查后 20% 收敛 ──
    d_check = check_last_fraction(density_xvg, CHECK_FRACTION, MAX_REL_CHANGE)
    t_check = check_last_fraction(temp_xvg, CHECK_FRACTION, MAX_REL_CHANGE)

    d_info = d_check.get("Density", {})
    t_info = t_check.get("Temperature", {})
    d_ok = d_info.get("ok", False)
    t_ok = t_info.get("ok", False)
    converged = d_ok and t_ok

    eq_result = EQResult(
        converged=converged,
        density_ok=d_ok, temp_ok=t_ok,
        density_mean=d_info.get("mean", 0.0),
        density_rel_change=d_info.get("rel_change", 0.0),
        temp_mean=t_info.get("mean", 0.0),
        temp_rel_change=t_info.get("rel_change", 0.0),
        tpr=str(tpr),
        details={"density": d_info, "temperature": t_info},
    )

    duration = _time.time() - _start
    if converged:
        return StepResult(
            step_name="eq", step_index=9, success=True,
            outputs={"tpr": tpr, "gro": str(cwd / "eq.gro"), "edr": str(edr)},
            artifacts=gmx_result.artifacts + [str(density_xvg), str(temp_xvg)],
            duration_s=duration,
            extra={"eq_result": eq_result},
        )

    # 未收敛 — 构建诊断信息
    issues = []
    if not d_ok:
        issues.append(f"密度不稳定 (mean={d_info.get('mean',0):.4f}, rel_change={d_info.get('rel_change',0):.4f})")
    if not t_ok:
        issues.append(f"温度不稳定 (mean={t_info.get('mean',0):.4f}, rel_change={t_info.get('rel_change',0):.4f})")
    hint = "调整 tau_p/tau_t、延长 eq_ns、或重建盒子（调整密度）"

    return StepResult(
        step_name="eq", step_index=9, success=False,
        error=StepError(kind=ErrorKind.EQ_NOT_CONVERGED,
                        message="NPT 平衡未达标: " + "; ".join(issues),
                        hint=hint),
        outputs={"tpr": tpr},
        artifacts=gmx_result.artifacts,
        duration_s=duration,
        extra={"eq_result": eq_result},
    )


if __name__ == "__main__":
    import sys
    wd = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_WORK_DIR)
    sr = run_eq(work_dir=wd)
    if sr.success:
        eq = sr.extra.get("eq_result")
        if eq:
            print(f"  密度: mean={eq.density_mean:.4f}  rel_change={eq.density_rel_change:.4f}  ✅")
            print(f"  温度: mean={eq.temp_mean:.4f}  rel_change={eq.temp_rel_change:.4f}  ✅")
        print("✅ EQ 平衡 (密度+温度稳定)")
    else:
        print(f"❌ EQ 未达标: {sr.error.message if sr.error else 'unknown'}")
        exit(1)
