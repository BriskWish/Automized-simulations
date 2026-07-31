"""
_orca_utils.py
==============
ORCA 共享工具 —— 路径解析、环境设置、坐标格式化。

struct_orca、mol2_orca、resp_maker 均从此模块导入，消除硬编码重复。
"""

import os
import shutil
import subprocess
from pathlib import Path

ORCA_BIN_NAME = "orca"
ORCA_2MKL_BIN_NAME = "orca_2mkl"
MULTIWFN_BIN_NAME = "Multiwfn"


# ============================================================
# Path Resolution
# ============================================================

def find_orca() -> str:
    """Resolve ORCA binary path.

    Priority: ORCA_DIR env var → shutil.which("orca") → error.
    """
    orca_dir = os.environ.get("ORCA_DIR", "")
    if orca_dir:
        p = Path(orca_dir) / "orca"
        if p.exists():
            return str(p)
    found = shutil.which(ORCA_BIN_NAME)
    if found:
        return found
    raise RuntimeError(
        "找不到 ORCA 可执行文件。请设置 ORCA_DIR 环境变量 "
        "(如 export ORCA_DIR=/path/to/orca) 或将 orca 加入 PATH。"
    )


def find_orca_2mkl() -> str:
    """Resolve orca_2mkl binary path (bundled with ORCA)."""
    orca_dir = os.environ.get("ORCA_DIR", "")
    if orca_dir:
        p = Path(orca_dir) / "orca_2mkl"
        if p.exists():
            return str(p)
    orca_path = shutil.which(ORCA_BIN_NAME)
    if orca_path:
        candidate = str(Path(orca_path).parent / "orca_2mkl")
        if Path(candidate).exists():
            return candidate
    raise RuntimeError(
        "找不到 orca_2mkl。请设置 ORCA_DIR 环境变量 "
        "或确保它与 ORCA 可执行文件在同一目录。"
    )


def get_orca_dir() -> str:
    """Get ORCA installation directory (for LD_LIBRARY_PATH)."""
    orca_dir = os.environ.get("ORCA_DIR", "")
    if orca_dir:
        return orca_dir
    orca_path = shutil.which(ORCA_BIN_NAME)
    if orca_path:
        return str(Path(orca_path).parent)
    raise RuntimeError("无法确定 ORCA 安装目录。")


def find_multiwfn() -> str:
    """Resolve Multiwfn binary path.

    Priority: MULTIWFN_BIN env var → shutil.which("Multiwfn") → error.
    """
    env_bin = os.environ.get("MULTIWFN_BIN", "")
    if env_bin and Path(env_bin).exists():
        return env_bin
    found = shutil.which(MULTIWFN_BIN_NAME)
    if found:
        return found
    raise RuntimeError(
        "找不到 Multiwfn。请设置 MULTIWFN_BIN 环境变量 "
        "或将 Multiwfn 加入 PATH。"
    )


# ============================================================
# ORCA Environment
# ============================================================

def get_orca_env() -> dict[str, str]:
    """Build environment dict with ORCA library path.

    Must be called after find_orca() succeeds (uses get_orca_dir() internally).
    """
    orca_dir = get_orca_dir()
    return {
        **os.environ,
        "LD_LIBRARY_PATH": f"{orca_dir}:{os.environ.get('LD_LIBRARY_PATH', '')}",
    }


# ============================================================
# Coordinate Formatting
# ============================================================

def extract_xyz(struct_path: str, workdir: str) -> str:
    """Extract molecular geometry as XYZ via Multiwfn (100→2→2).

    Works for .fchk, .molden, .gjf — any format Multiwfn can read.

    Returns path to the generated .xyz file.
    """
    mp = Path(struct_path)
    if not mp.exists():
        raise FileNotFoundError(f"结构文件不存在: {struct_path}")

    multiwfn = find_multiwfn()
    tmp_xyz = Path(workdir) / f"_tmp_{mp.stem}.xyz"

    commands = f"100\n2\n2\n{tmp_xyz.resolve()}\n0\nq\n"

    try:
        subprocess.run(
            [multiwfn, str(mp.resolve())],
            input=commands, capture_output=True, text=True,
            cwd=workdir, timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{mp.name}: Multiwfn 坐标提取超时 (60s)")

    if not tmp_xyz.exists():
        raise RuntimeError(
            f"{mp.name}: Multiwfn 无法提取 XYZ 坐标。请检查结构文件是否有效。"
        )

    return str(tmp_xyz)


def format_orca_xyz_coords(lines: list[str]) -> str:
    """Format atomic coordinate lines for ORCA * xyz block.

    Extracts atom symbol and x/y/z from each line, formats to ORCA convention:
      {symbol:<3s} {x:14.8f} {y:14.8f} {z:14.8f}

    Args:
        lines: Raw coordinate lines from .gjf or .xyz files.
               Expected format: "Symbol  x  y  z" per line.

    Returns:
        Newline-joined formatted coordinate block.
    """
    formatted = []
    for line in lines:
        parts = line.strip().split()
        if len(parts) >= 4:
            formatted.append(
                f"  {parts[0]:<3s} {float(parts[1]):14.8f} "
                f"{float(parts[2]):14.8f} {float(parts[3]):14.8f}"
            )
    return "\n".join(formatted)
