"""
_orca_utils.py
==============
ORCA 共享工具 —— 路径解析、环境设置、坐标格式化。

struct_orca、mol2_orca、resp_maker 均从此模块导入，消除硬编码重复。
"""

import subprocess
from pathlib import Path

from willy.env_registry import build_tool_env, require_tool
from willy.process_lifecycle import run_managed_command

ORCA_BIN_NAME = "orca"
ORCA_2MKL_BIN_NAME = "orca_2mkl"
MULTIWFN_BIN_NAME = "Multiwfn"


# ============================================================
# Path Resolution
# ============================================================

def find_orca() -> str:
    """Resolve ORCA through the centralized environment registry."""
    return str(require_tool("orca").executable)


def find_orca_2mkl() -> str:
    """Resolve the ORCA companion binary through the centralized registry."""
    return str(require_tool("orca_2mkl").executable)


def get_orca_dir() -> str:
    """Return the resolved ORCA installation directory."""
    result = require_tool("orca")
    return str(result.home or result.executable.parent)


def find_multiwfn() -> str:
    """Resolve Multiwfn through the centralized environment registry."""
    return str(require_tool("multiwfn").executable)


# ============================================================
# ORCA Environment
# ============================================================

def get_orca_env() -> dict[str, str]:
    """Build the isolated ORCA child environment."""
    return build_tool_env("orca")


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
        result = run_managed_command(
            [multiwfn, str(mp.resolve())],
            input_text=commands,
            cwd=workdir,
            timeout=60,
            env=build_tool_env("multiwfn"),
            run_dir=workdir,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{mp.name}: Multiwfn 坐标提取超时 (60s)")
    if result.returncode != 0:
        raise RuntimeError(f"{mp.name}: Multiwfn 坐标提取执行失败")

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
