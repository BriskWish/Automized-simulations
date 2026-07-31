"""
_gmx_utils.py
============
GROMACS MD 执行共享工具 —— grompp / mdrun / xvg 解析 / 收敛检查。
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional
import subprocess
import re

from willy.errors import StepResult, StepError, ErrorKind


def run_gmx(args: list[str], cwd: Path, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    """封装 gmx 命令调用，统一错误处理。"""
    cmd = ["gmx"] + args
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        cwd=str(cwd), timeout=timeout,
    )
    return result


def grompp_and_mdrun(stage: str,
                     cwd: Path,
                     mdp: str = None,
                     conf: str = None,
                     topol: str = "topol.top",
                     extra_grompp: list[str] = None,
                     extra_mdrun: list[str] = None,
                     ) -> StepResult:
    """
    标准两步: gmx grompp → gmx mdrun。

    Args:
        stage: 阶段名 (em / eq / prod)，用于查找 .mdp 和命名 .tpr
        cwd: 工作目录
        mdp: .mdp 文件路径，None 则用 cwd/{stage}.mdp
        conf: 起始结构，None 则自动推断 (model.pdb for em, em.gro for eq, eq.gro for prod)
        topol: 拓扑文件
        extra_grompp: grompp 额外参数
        extra_mdrun: mdrun 额外参数

    Returns:
        StepResult (success=True 时 outputs={"tpr": path})
    """
    import time as _time
    _start = _time.time()

    if mdp is None:
        mdp = str(cwd / f"{stage}.mdp")
    if conf is None:
        if stage == "em":
            conf = str(cwd / "model.pdb")
        elif stage == "eq":
            conf = str(cwd / "em.gro")
        else:
            conf = str(cwd / "eq.gro")

    tpr = cwd / f"{stage}.tpr"
    step_index = {"em": 8, "eq": 9, "prod": 10}.get(stage, 8)

    # grompp
    grompp_args = [
        "grompp", "-f", mdp, "-c", conf,
        "-p", topol, "-o", str(tpr),
        "-po", str(cwd / f"{stage}_out.mdp"),
        "-maxwarn", "1",
    ]
    if extra_grompp:
        grompp_args.extend(extra_grompp)

    cpt = cwd / f"{stage}.cpt"
    if cpt.exists():
        grompp_args.extend(["-t", str(cpt)])

    print(f"[{stage}] grompp...")
    try:
        r = run_gmx(grompp_args, cwd, timeout=60)
    except subprocess.TimeoutExpired:
        return StepResult(
            step_name=f"md_{stage}", step_index=step_index, success=False,
            error=StepError(kind=ErrorKind.GROMPP_FAILED,
                            message=f"{stage}: grompp 超时 (60s)",
                            hint="检查 .mdp 和 .top/.itp 文件是否正确"),
            duration_s=_time.time() - _start,
        )
    if r.returncode != 0:
        tail = r.stderr[-500:] if r.stderr else "(no stderr)"
        return StepResult(
            step_name=f"md_{stage}", step_index=step_index, success=False,
            error=StepError(kind=ErrorKind.GROMPP_FAILED,
                            message=f"{stage}: grompp 失败",
                            raw_output=tail,
                            hint="检查 atomtype 是否缺失、.itp 是否完整、.mdp 参数是否有误"),
            duration_s=_time.time() - _start,
        )

    # mdrun
    mdrun_args = ["mdrun", "-deffnm", stage, "-v"]
    if extra_mdrun:
        mdrun_args.extend(extra_mdrun)

    print(f"[{stage}] mdrun...")
    try:
        r = run_gmx(mdrun_args, cwd, timeout=None)
    except subprocess.TimeoutExpired:
        return StepResult(
            step_name=f"md_{stage}", step_index=step_index, success=False,
            error=StepError(kind=ErrorKind.TIMEOUT,
                            message=f"{stage}: mdrun 超时",
                            hint="减少模拟时间或增加计算资源"),
            outputs={"tpr": str(tpr)},
            duration_s=_time.time() - _start,
        )
    if r.returncode != 0:
        tail = r.stderr[-500:] if r.stderr else "(no stderr)"
        return StepResult(
            step_name=f"md_{stage}", step_index=step_index, success=False,
            error=StepError(kind=ErrorKind.MDRUN_FAILED,
                            message=f"{stage}: mdrun 失败",
                            raw_output=tail,
                            hint="检查日志中是否有 LINCS 警告、温度爆炸或原子重叠"),
            outputs={"tpr": str(tpr)},
            duration_s=_time.time() - _start,
        )

    duration = _time.time() - _start
    return StepResult(
        step_name=f"md_{stage}", step_index=step_index, success=True,
        outputs={"tpr": str(tpr)},
        artifacts=[str(tpr), str(cwd / f"{stage}.gro"),
                   str(cwd / f"{stage}.log"), str(cwd / f"{stage}.edr")],
        duration_s=duration,
    )


# ============================================================
# XVG 解析
# ============================================================

def parse_xvg(xvg_path: Path) -> tuple[list[str], list[list[float]]]:
    """
    解析 GROMACS .xvg 文件。

    Returns:
        (legends, columns): legends 是列名列表, columns 是各列数据
    """
    text = xvg_path.read_text()
    lines = text.split("\n")

    legends = []
    for line in lines:
        if line.startswith("@ s") and "legend" in line:
            # @ sN legend "Density"
            m = re.search(r'"([^"]*)"', line)
            if m:
                legends.append(m.group(1))

    data = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("@") or line.startswith("#"):
            continue
        parts = line.split()
        if parts:
            try:
                data.append([float(x) for x in parts])
            except ValueError:
                continue

    # 转置：行 → 列
    if not data:
        return legends, []
    ncols = len(data[0])
    columns = []
    for i in range(ncols):
        columns.append([row[i] for row in data])

    return legends, columns


def check_last_fraction(xvg_path: Path,
                        fraction: float = 0.2,
                        max_rel_change: float = 0.10,
                        ) -> dict:
    """
    检查 .xvg 数据最后 fraction 部分的相对变化是否在阈值内。

    Args:
        xvg_path: .xvg 文件路径
        fraction: 取最后多少比例的数据用于检查
        max_rel_change: 最大允许的相对变化 (max-min)/|mean|

    Returns:
        {col_name: {"mean": float, "rel_change": float, "ok": bool}, ...}
    """
    legends, columns = parse_xvg(xvg_path)

    # 第一列通常是时间，跳过
    results = {}
    for i, col in enumerate(columns):
        name = legends[i] if i < len(legends) else f"col_{i}"
        if name.lower() in ("time", "t"):
            continue
        if not col:
            continue

        n = max(1, int(len(col) * fraction))
        tail = col[-n:]

        mean = sum(tail) / len(tail)
        rng = max(tail) - min(tail)
        rel_change = rng / abs(mean) if abs(mean) > 1e-10 else float("inf")

        results[name] = {
            "mean": round(mean, 4),
            "min": round(min(tail), 4),
            "max": round(max(tail), 4),
            "rel_change": round(rel_change, 6),
            "ok": rel_change <= max_rel_change,
        }

    return results
