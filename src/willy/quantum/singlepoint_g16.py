"""
singlepoint_g16.py
==================
G16 两步法 Step 2: 高精度单点能 → *_opt.fchk。

.fchk (DZ优化) → 提取坐标 → *_opt.gjf → g16 SP(def2TZVP) → formchk → *_opt.fchk
"""

import subprocess
import time as _time
from pathlib import Path

from willy.errors import StepResult, StepError, ErrorKind
from willy.env_registry import EnvironmentRegistryError, build_tool_env, require_tool
from willy.process_lifecycle import run_managed_command
from willy.quantum._orca_utils import extract_xyz

SP_BASIS = "b3lyp/def2TZVP"


def run(
    fchk_path: str,
    charge: int,
    spin: int,
    workdir: str = None,
    name: str = None,
    mem: str = "5GB",
    nproc: int = 8,
) -> StepResult:
    """G16 single-point at b3lyp/def2TZVP → *_opt.fchk.

    Args:
        fchk_path: Step 1 产出的 .fchk（优化后几何）。
        charge: 净电荷。
        spin: 自旋多重度。
        workdir: 工作目录，默认与 fchk 同目录。
        name: 分子名，默认从 fchk 文件名推导。
        mem: G16 内存。
        nproc: CPU 核数。

    Returns:
        StepResult: outputs["fchk"] = *_opt.fchk 路径。
    """
    _start = _time.time()
    mp = Path(fchk_path)
    if workdir is None:
        workdir = str(mp.parent)
    if name is None:
        name = mp.stem

    opt_name = f"{name}_opt"
    opt_fchk = Path(workdir) / f"{opt_name}.fchk"

    if opt_fchk.exists():
        print(f"[sp_g16] ⏭ {name}: {opt_fchk.name} 已存在，跳过 G16 SP")
        return StepResult(
            step_name="sp_g16", step_index=2, success=True,
            outputs={"fchk": str(opt_fchk)},
            artifacts=[str(opt_fchk)], duration_s=0.0,
        )

    # ── Extract coordinates from optimization fchk ──
    try:
        xyz_path = extract_xyz(fchk_path, workdir)
    except (FileNotFoundError, RuntimeError) as e:
        return StepResult(
            step_name="sp_g16", step_index=2, success=False,
            error=StepError(kind=ErrorKind.RESP_FAILED,
                            message=f"{name}: 坐标提取失败 — {e}"),
            duration_s=_time.time() - _start,
        )

    # ── Build *_opt.gjf ──
    xyz_content = Path(xyz_path).read_text()
    xyz_lines = xyz_content.strip().split("\n")
    coords = "\n".join(xyz_lines[2:]) if len(xyz_lines) > 2 else ""

    gjf_content = f"""%mem={mem}
%nprocshared={nproc}
%chk={opt_name}.chk
# {SP_BASIS} SP

{name}_opt

{charge} {spin}
{coords}

"""

    gjf_path = Path(workdir) / f"{opt_name}.gjf"
    gjf_path.write_text(gjf_content)
    print(f"[sp_g16] {name}: *_opt.gjf 已生成  (basis={SP_BASIS})")

    try:
        g16 = require_tool("g16")
        formchk = require_tool("formchk")
        g16_env = build_tool_env("g16")
        formchk_env = build_tool_env("formchk")
    except EnvironmentRegistryError as exc:
        return StepResult(
            step_name="sp_g16", step_index=2, success=False,
            error=StepError(ErrorKind.DEPENDENCY_MISSING, str(exc)),
            duration_s=_time.time() - _start,
        )

    # ── Run G16 SP ──
    try:
        result = run_managed_command(
            [str(g16.executable)],
            input_text=gjf_content,
            cwd=workdir,
            timeout=3600,
            env=g16_env,
            run_dir=workdir,
        )
    except subprocess.TimeoutExpired:
        return StepResult(
            step_name="sp_g16", step_index=2, success=False,
            error=StepError(kind=ErrorKind.TIMEOUT,
                            message=f"{name}: G16 SP 超时 (3600s)"),
            duration_s=_time.time() - _start,
        )

    (Path(workdir) / f"{opt_name}.log").write_text(result.stdout)

    if "Normal termination" not in result.stdout:
        return StepResult(
            step_name="sp_g16", step_index=2, success=False,
            error=StepError(kind=ErrorKind.GAUSSIAN_CRASH,
                            message=f"{name}: G16 SP 未正常终止",
                            raw_output=result.stdout[-500:]),
            duration_s=_time.time() - _start,
        )
    print(f"[sp_g16] {name}: ✅ G16 SP 完成")

    # ── formchk → *_opt.fchk ──
    chk_path = Path(workdir) / f"{opt_name}.chk"
    if not chk_path.exists():
        return StepResult(
            step_name="sp_g16", step_index=2, success=False,
            error=StepError(kind=ErrorKind.GAUSSIAN_CRASH,
                            message=f"{name}: G16 SP .chk 未生成"),
            duration_s=_time.time() - _start,
        )

    try:
        run_managed_command(
            [str(formchk.executable), str(chk_path.resolve()), str(opt_fchk.resolve())],
            cwd=workdir,
            timeout=60,
            env=formchk_env,
            run_dir=workdir,
        )
    except subprocess.TimeoutExpired:
        return StepResult(
            step_name="sp_g16", step_index=2, success=False,
            error=StepError(kind=ErrorKind.TIMEOUT,
                            message=f"{name}: formchk 超时 (60s)"),
            duration_s=_time.time() - _start,
        )

    if not opt_fchk.exists():
        return StepResult(
            step_name="sp_g16", step_index=2, success=False,
            error=StepError(kind=ErrorKind.FORMCHK_FAILED,
                            message=f"{name}: formchk 失败"),
            duration_s=_time.time() - _start,
        )

    # ── Cleanup ──
    Path(xyz_path).unlink(missing_ok=True)
    gjf_path.unlink(missing_ok=True)

    print(f"[sp_g16] {name}: ✅ {opt_fchk.name}  ({opt_fchk.stat().st_size} bytes)")

    return StepResult(
        step_name="sp_g16", step_index=2, success=True,
        outputs={"fchk": str(opt_fchk)},
        artifacts=[str(opt_fchk)],
        duration_s=_time.time() - _start,
    )
