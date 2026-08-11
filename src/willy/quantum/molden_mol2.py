"""ORCA Molden -> Tripos MOL2 conversion.

ORCA's FCHK export contains coordinates and wavefunction data but does not
carry the Gaussian ``MxBond/NBond/IBond/RBond`` connectivity arrays consumed by
``fchk_mol2``.  This adapter keeps the ORCA path independent: it parses the
atom order and coordinates from ``*_opt.molden`` and asks the bundled
Multiwfn runtime to evaluate the molecular connectivity.  The connectivity
index is converted to a MOL2 bond type, retaining single, aromatic, double,
and triple information when it is present.  A one-atom molecule is valid and
produces a zero-bond MOL2 file.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any

from willy.errors import ErrorKind, StepError, StepResult
from willy.process_lifecycle import run_managed_command
from willy.quantum._orca_utils import find_multiwfn
from willy.env_registry import build_tool_env


_BOHR_TO_ANGSTROM = 0.529177210903
_BOND_LINE = re.compile(
    r"^\s*(\d+)[A-Za-z]*\s+---\s+(\d+)[A-Za-z]*\s*:\s*"
    r"([-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?)\s+Nearest integer:\s*(\d+)"
)
_MAYER_LINE = re.compile(
    r"^\s*#\s*\d+\s*:\s*(\d+)\([^)]*\)\s+"
    r"(\d+)\([^)]*\)\s+([-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?)"
)


def parse_molden(molden_path: str | Path) -> dict[str, Any]:
    """Read ``[Atoms]`` while preserving the Molden atom ordering.

    ORCA writes coordinates in ``AU``.  ``[Atoms] Angs`` is accepted as well
    for hand-authored or third-party Molden files.
    """

    path = Path(molden_path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    atoms_header = None
    for index, line in enumerate(lines):
        if line.strip().lower().startswith("[atoms]"):
            atoms_header = index
            break
    if atoms_header is None:
        raise ValueError("Molden 文件缺少 [Atoms] 段")

    header = lines[atoms_header].strip().lower()
    factor = 1.0 if "angs" in header or "angstrom" in header else _BOHR_TO_ANGSTROM
    atoms: list[dict[str, Any]] = []
    for raw in lines[atoms_header + 1 :]:
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("["):
            break
        fields = stripped.split()
        if len(fields) < 6:
            continue
        try:
            index = int(fields[1])
            atomic_number = int(fields[2])
            x, y, z = (float(value) * factor for value in fields[3:6])
        except ValueError:
            continue
        symbol = re.sub(r"[^A-Za-z]", "", fields[0]) or f"X{atomic_number}"
        symbol = symbol[0].upper() + symbol[1:].lower()
        atoms.append({
            "index": index,
            "atomic_number": atomic_number,
            "symbol": symbol,
            "coords": (x, y, z),
        })

    if not atoms:
        raise ValueError("Molden [Atoms] 段为空或格式不完整")
    expected = list(range(1, len(atoms) + 1))
    actual = [atom["index"] for atom in atoms]
    if actual != expected:
        raise ValueError("Molden 原子序号不是从 1 开始的连续顺序")
    return {"atoms": atoms}


def _bond_type(connectivity: float, nearest_integer: int) -> str:
    """Map Multiwfn connectivity to a Tripos bond type."""

    # The integer is the robust fallback.  The continuous index preserves
    # aromatic/partial-bond information when Multiwfn reports it.
    if nearest_integer >= 3 or connectivity >= 2.5:
        return "3"
    if nearest_integer == 2 or connectivity >= 1.8:
        return "2"
    if 1.3 <= connectivity < 1.8:
        return "ar"
    return "1"


def _mayer_bond_type(order: float) -> str:
    """Map a Mayer bond order to a Tripos bond type.

    Mayer values are not exact integer bond orders.  The intervals preserve
    the useful chemical distinction while avoiding false double bonds for
    ordinary polar single bonds (for example ester C-O values around 1.1).
    """
    if order >= 2.35:
        return "3"
    if order >= 1.65:
        return "2"
    if order >= 1.25:
        return "ar"
    return "1"


def parse_connectivity(output: str, natoms: int) -> list[tuple[int, int, str]]:
    """Parse Multiwfn's ``100 -> 9`` connectivity report.

    Only bonds whose nearest integer connectivity is at least one are emitted;
    weak contacts (for example ``0.11965 -> 0``) remain excluded.
    """

    bonds: list[tuple[int, int, str]] = []
    seen: set[tuple[int, int]] = set()
    for line in output.splitlines():
        match = _BOND_LINE.match(line)
        if not match:
            continue
        first, second = int(match.group(1)), int(match.group(2))
        connectivity = float(match.group(3))
        nearest = int(match.group(4))
        if nearest < 1 or not (1 <= first <= natoms and 1 <= second <= natoms):
            continue
        pair = (min(first, second), max(first, second))
        if pair in seen:
            continue
        seen.add(pair)
        bonds.append((pair[0], pair[1], _bond_type(connectivity, nearest)))
    return bonds


def parse_mayer_bond_orders(output: str, natoms: int) -> dict[tuple[int, int], float]:
    """Parse Multiwfn Mayer bond-order rows keyed by atom pair."""
    orders: dict[tuple[int, int], float] = {}
    for line in output.splitlines():
        match = _MAYER_LINE.match(line)
        if not match:
            continue
        first, second = int(match.group(1)), int(match.group(2))
        if not (1 <= first <= natoms and 1 <= second <= natoms):
            continue
        pair = (min(first, second), max(first, second))
        orders[pair] = float(match.group(3))
    return orders


def _multiwfn_bond_analysis(molden_path: Path, workdir: Path) -> tuple[str, str]:
    """Run bundled Multiwfn connectivity and Mayer bond-order analyses."""

    multiwfn = find_multiwfn()
    # 100 -> 9: connectivity; 9 -> 1: Mayer bond order.  Both analyses ask
    # whether to write a matrix; ``n`` keeps the run directory clean.
    commands = "100\n9\n\nn\n0\n9\n1\nn\n0\nq\n"
    try:
        result = run_managed_command(
            [multiwfn, str(molden_path.resolve())],
            input_text=commands,
            cwd=str(workdir),
            timeout=600,
            env=build_tool_env("multiwfn"),
            run_dir=str(workdir),
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("Multiwfn Molden 连通性分析超时 (600s)") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-500:]
        raise RuntimeError(f"Multiwfn Molden 连通性分析失败{(': ' + detail) if detail else ''}")
    return result.stdout or "", result.stderr or ""


def build_mol2(data: dict[str, Any], name: str,
               bonds: list[tuple[int, int, str]]) -> str:
    """Build a Tripos MOL2 using the original Molden atom order."""

    atoms = data["atoms"]
    lines = [f"# {name}", "# Created by molden_mol2.py", "#", "",
             "@<TRIPOS>MOLECULE", name,
             f"{len(atoms)} {len(bonds)}", "SMALL", "NO_CHARGES", "",
             "@<TRIPOS>ATOM"]
    for atom in atoms:
        x, y, z = atom["coords"]
        symbol = atom["symbol"]
        lines.append(
            f"{atom['index']:>4d} {symbol}{atom['index']:<5d} "
            f"{x:12.4f} {y:12.4f} {z:12.4f} {symbol:<4s}"
        )
    lines.append("@<TRIPOS>BOND")
    for bond_index, (first, second, bond_type) in enumerate(bonds, 1):
        lines.append(f"{bond_index:>5d} {first:>5d} {second:>5d} {bond_type:>4s}")
    return "\n".join(lines) + "\n"


def convert(molden_path: str, output_path: str | None = None) -> StepResult:
    """Convert one ORCA ``*_opt.molden`` to ``.mol2`` with connectivity."""

    started = time.time()
    source = Path(molden_path)
    name = source.stem.removesuffix("_opt")
    if not source.is_file():
        return StepResult(
            step_name="molden_mol2", step_index=2, success=False,
            error=StepError(ErrorKind.FILE_NOT_FOUND, f"{source} 不存在"),
            duration_s=time.time() - started,
        )
    try:
        data = parse_molden(source)
        stdout, stderr = _multiwfn_bond_analysis(source, source.parent)
        output = "\n".join((stdout, stderr))
        bonds = parse_connectivity(output, len(data["atoms"]))
        mayer_orders = parse_mayer_bond_orders(output, len(data["atoms"]))
        bonds = [
            (first, second, _mayer_bond_type(mayer_orders[(first, second)])
             if (first, second) in mayer_orders else bond_type)
            for first, second, bond_type in bonds
        ]
        # Multiwfn returns no bond rows for a valid isolated atom.  Do not
        # reject that case: zero-bond MOL2 is required for ions such as Li.
        if len(data["atoms"]) > 1 and not bonds:
            raise ValueError("Multiwfn 未识别到任何键连接")
        content = build_mol2(data, name, bonds)
        target = Path(output_path) if output_path else source.parent / f"{name}.mol2"
        target.write_text(content, encoding="utf-8")
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        return StepResult(
            step_name="molden_mol2", step_index=2, success=False,
            error=StepError(
                ErrorKind.UNKNOWN,
                f"{name}: molden→mol2 转换失败: {exc}",
                hint="确认 ORCA *_opt.molden 完整，并检查内置 Multiwfn 连通性分析。",
            ),
            duration_s=time.time() - started,
        )
    return StepResult(
        step_name="molden_mol2", step_index=2, success=True,
        outputs={"mol2": str(target)}, artifacts=[str(target)],
        duration_s=time.time() - started,
        extra={
            "natoms": len(data["atoms"]),
            "nbonds": len(bonds),
            "mayer_bonds": len(mayer_orders),
            "source": str(source),
        },
    )
