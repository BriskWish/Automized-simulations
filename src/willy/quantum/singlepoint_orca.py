"""
singlepoint_orca.py
===================
ORCA 两步法 Step 2: 高精度单点能 → *_opt.fchk。

.molden (DZ优化) → 提取坐标 → *_opt.inp → ORCA SP(def2-TZVP)
→ orca_2mkl → *_opt.molden → Multiwfn molden→fchk → *_opt.fchk
"""

import subprocess
import time as _time
from pathlib import Path

from willy.errors import StepResult, StepError, ErrorKind
from willy.quantum._orca_utils import (
    find_orca, find_orca_2mkl, get_orca_env,
    find_multiwfn, format_orca_xyz_coords, extract_xyz,
)

SP_BASIS = "def2-TZVP"


def run(
    molden_path: str,
    charge: int,
    spin: int,
    workdir: str = None,
    name: str = None,
    nproc: int = 8,
    mem_mb: int = 5000,
) -> StepResult:
    """ORCA single-point at B3LYP def2-TZVP → *_opt.fchk。

    内部完成: 提取坐标 → ORCA SP → orca_2mkl → Multiwfn molden→fchk。
    最终输出 *_opt.fchk，与 G16 链路统一。

    Args:
        molden_path: Step 1 产出的 .molden（优化后几何+波函数）。
        charge: 净电荷。
        spin: 自旋多重度。
        workdir: 工作目录。
        name: 分子名。
        nproc: CPU 核数。
        mem_mb: 每核内存 (MB)。

    Returns:
        StepResult: outputs["fchk"] = *_opt.fchk 路径。
    """
    _start = _time.time()
    mp = Path(molden_path)
    if workdir is None:
        workdir = str(mp.parent)
    if name is None:
        name = mp.stem

    opt_name = f"{name}_opt"
    opt_fchk = Path(workdir) / f"{opt_name}.fchk"

    if opt_fchk.exists():
        print(f"[sp_orca] ⏭ {name}: {opt_fchk.name} 已存在，跳过 ORCA SP")
        return StepResult(
            step_name="sp_orca", step_index=2, success=True,
            outputs={"fchk": str(opt_fchk)},
            artifacts=[str(opt_fchk)], duration_s=0.0,
        )

    # ── Extract coordinates ──
    try:
        xyz_path = extract_xyz(molden_path, workdir)
    except (FileNotFoundError, RuntimeError) as e:
        return StepResult(
            step_name="sp_orca", step_index=2, success=False,
            error=StepError(kind=ErrorKind.RESP_FAILED,
                            message=f"{name}: 坐标提取失败 — {e}"),
            duration_s=_time.time() - _start,
        )

    # ── Build *_opt.inp ──
    xyz_content = Path(xyz_path).read_text()
    xyz_lines = xyz_content.strip().split("\n")
    xyz_block = format_orca_xyz_coords(xyz_lines[2:])

    inp_content = f"""! B3LYP {SP_BASIS} SP TightSCF
%maxcore {mem_mb}
%pal nprocs {nproc} end
* xyz {charge} {spin}
{xyz_block}
*"""

    inp_path = Path(workdir) / f"{opt_name}.inp"
    inp_path.write_text(inp_content)
    print(f"[sp_orca] {name}: *_opt.inp 已生成  (basis={SP_BASIS})")

    # ── Run ORCA SP ──
    try:
        orca_bin = find_orca()
        orca_env = get_orca_env()
    except RuntimeError as e:
        return StepResult(
            step_name="sp_orca", step_index=2, success=False,
            error=StepError(kind=ErrorKind.DEPENDENCY_MISSING, message=str(e)),
            duration_s=_time.time() - _start,
        )

    try:
        result = subprocess.run(
            [orca_bin, inp_path.name],
            capture_output=True, text=True,
            cwd=workdir, timeout=3600, env=orca_env,
        )
    except subprocess.TimeoutExpired:
        return StepResult(
            step_name="sp_orca", step_index=2, success=False,
            error=StepError(kind=ErrorKind.TIMEOUT,
                            message=f"{name}: ORCA SP 超时 (3600s)"),
            duration_s=_time.time() - _start,
        )

    gbw_path = Path(workdir) / f"{opt_name}.gbw"
    if not gbw_path.exists():
        raw = result.stderr[-500:] if result.stderr else result.stdout[-500:]
        return StepResult(
            step_name="sp_orca", step_index=2, success=False,
            error=StepError(kind=ErrorKind.ORCA_CRASH,
                            message=f"{name}: ORCA SP 失败 (.gbw 未生成)",
                            raw_output=raw),
            duration_s=_time.time() - _start,
        )
    print(f"[sp_orca] {name}: ✅ ORCA SP 完成")

    # ── orca_2mkl → *_opt.molden ──
    try:
        orca_2mkl = find_orca_2mkl()
        subprocess.run(
            [orca_2mkl, opt_name, "-molden"],
            capture_output=True, text=True,
            cwd=workdir, timeout=60, env=orca_env,
        )
    except (RuntimeError, subprocess.TimeoutExpired):
        pass

    molden_input = Path(workdir) / f"{opt_name}.molden.input"
    opt_molden = Path(workdir) / f"{opt_name}.molden"
    if molden_input.exists():
        molden_input.rename(opt_molden)

    if not opt_molden.exists():
        return StepResult(
            step_name="sp_orca", step_index=2, success=False,
            error=StepError(kind=ErrorKind.ORCA_CRASH,
                            message=f"{name}: orca_2mkl 失败 (.molden 未生成)"),
            duration_s=_time.time() - _start,
        )
    print(f"[sp_orca] {name}: ✅ {opt_molden.name}")

    # ── Multiwfn: *_opt.molden → *_opt.fchk ──
    multiwfn = find_multiwfn()
    commands = f"100\n2\n7\n{opt_fchk.resolve()}\n0\nq\n"
    try:
        subprocess.run(
            [multiwfn, str(opt_molden.resolve())],
            input=commands, capture_output=True, text=True,
            cwd=workdir, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return StepResult(
            step_name="sp_orca", step_index=2, success=False,
            error=StepError(kind=ErrorKind.TIMEOUT,
                            message=f"{name}: Multiwfn molden→fchk 超时"),
            duration_s=_time.time() - _start,
        )

    if not opt_fchk.exists():
        return StepResult(
            step_name="sp_orca", step_index=2, success=False,
            error=StepError(kind=ErrorKind.UNKNOWN,
                            message=f"{name}: Multiwfn molden→fchk 失败"),
            duration_s=_time.time() - _start,
        )

    # ── Cleanup ──
    Path(xyz_path).unlink(missing_ok=True)

    print(f"[sp_orca] {name}: ✅ {opt_fchk.name}  ({opt_fchk.stat().st_size} bytes)")

    return StepResult(
        step_name="sp_orca", step_index=2, success=True,
        outputs={"fchk": str(opt_fchk)},
        artifacts=[str(opt_fchk)],
        duration_s=_time.time() - _start,
    )
