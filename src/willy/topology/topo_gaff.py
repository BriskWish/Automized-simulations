"""Sobtop GAFF+UFF execution backend.

Sobtop writes to its own installation directory.  This module serializes that
shared workspace and copies only files produced and validated by the current
process into the caller's run directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import fcntl
import os
import shutil
import subprocess
import time

from willy._paths import get_project_root
from willy.errors import ErrorKind, StepError, StepResult
from willy.process_lifecycle import run_managed_command
from willy.topology.validation import validate_topology_files, validate_topology_output_name


SOBTOP_DIR = get_project_root() / "vendor" / "sobtop"
SOBTOP_BIN = SOBTOP_DIR / "sobtop"
_LOCK_PATH = SOBTOP_DIR / ".willy-sobtop.lock"
# Some supported filesystems expose output mtimes at coarser precision than
# ``time.time_ns()``.  The workspace lock and pre-launch cleanup remain the
# primary current-output boundary; this tolerance avoids rejecting files that
# were created by the active process but rounded down by the filesystem.
_OUTPUT_MTIME_TOLERANCE_NS = 2_000_000_000


def check_sobtop_ready() -> list[str]:
    """Delegate Sobtop availability checks to the central environment checker."""
    from willy.env_checker import check_module

    return check_module("topo_gaff").failed_strs()


@dataclass(frozen=True)
class SobtopInput:
    """The sole supported Sobtop mode: GAFF, then UFF for missing atom types."""

    mol2: str
    chg: str
    output_name: str


# Compatibility alias for callers that used the old public input name.
TopMakerInput = SobtopInput


@dataclass(frozen=True)
class SobtopInputBuilder:
    """Build the frozen interactive sequence for Sobtop 2026.1.16.

    The verified menu actions are ``7 -> 10 -> chg -> 0 -> 1 -> 2 -> 4``:
    load charges, return, generate topology, assign GAFF then UFF, and use
    prebuilt bonded parameters with guessed gaps.  Explicit paths then avoid
    Sobtop's implicit input-stem output names; ``2 -> gro -> 0`` emits GRO and
    exits the program.
    """

    inp: SobtopInput
    work_dir: Path | None = None

    def expected_outputs(self) -> dict[str, Path]:
        _validate_output_name(self.inp.output_name)
        work_dir = self.work_dir or SOBTOP_DIR
        return {
            "top": work_dir / f"{self.inp.output_name}.top",
            "itp": work_dir / f"{self.inp.output_name}.itp",
            "gro": work_dir / f"{self.inp.output_name}.gro",
        }

    def build(self) -> str:
        outputs = self.expected_outputs()
        lines = [
            str(Path(self.inp.mol2).resolve()),
            "7",
            "10",
            str(Path(self.inp.chg).resolve()),
            "0",
            "1",
            "2",
            "4",
            str(outputs["top"]),
            str(outputs["itp"]),
            "2",
            str(outputs["gro"]),
            "0",
        ]
        return "\n".join(lines) + "\n"


class SobtopWorkspaceLock:
    """A process-wide advisory lock around Sobtop's shared vendor directory."""

    def __enter__(self) -> "SobtopWorkspaceLock":
        _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._handle = _LOCK_PATH.open("a+")
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()


def _validate_output_name(output_name: str) -> None:
    validate_topology_output_name(output_name)


def _cleanup_expected_outputs(paths: Iterable[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _raw_output(result: subprocess.CompletedProcess[str]) -> str:
    return ((result.stdout or "") + "\n" + (result.stderr or ""))[-1000:].strip()


def _failure(
    inp: SobtopInput,
    start: float,
    message: str,
    *,
    raw_output: str = "",
    kind: ErrorKind = ErrorKind.SOBTOP_FAILED,
) -> StepResult:
    return StepResult(
        step_name="topo_gaff",
        step_index=4,
        success=False,
        error=StepError(
            kind=kind,
            message=f"{inp.output_name}: {message}",
            raw_output=raw_output[-1000:],
            hint="检查本次运行目录内的 .mol2/.chg，或重试 Sobtop GAFF+UFF 后端。",
        ),
        duration_s=time.monotonic() - start,
    )


def make_itp_gro(inp: SobtopInput, output_dir: str | None = None) -> StepResult:
    """Run Sobtop and return only validated current-process ITP/GRO artifacts."""
    start = time.monotonic()
    if not output_dir:
        return _failure(
            inp, start, "必须提供当前 run 的 output_dir",
            kind=ErrorKind.INPUT_CONTRACT,
        )
    if not Path(inp.mol2).is_file() or not Path(inp.chg).is_file():
        missing = [path for path in (inp.mol2, inp.chg) if not Path(path).is_file()]
        return _failure(inp, start, f"缺少输入文件: {', '.join(missing)}", kind=ErrorKind.FILE_NOT_FOUND)

    issues = check_sobtop_ready()
    if issues:
        return _failure(inp, start, "Sobtop 环境未就绪", raw_output="\n".join(issues),
                        kind=ErrorKind.DEPENDENCY_MISSING)

    try:
        builder = SobtopInputBuilder(inp)
        vendor_outputs = builder.expected_outputs()
        stdin_text = builder.build()
    except ValueError as exc:
        return _failure(inp, start, str(exc), kind=ErrorKind.CONFIG_INVALID)

    run_dir = Path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    run_outputs = {kind: run_dir / path.name for kind, path in vendor_outputs.items()}

    with SobtopWorkspaceLock():
        _cleanup_expected_outputs(vendor_outputs.values())
        _cleanup_expected_outputs(run_outputs.values())
        try:
            try:
                started_ns = time.time_ns()
                result = run_managed_command(
                    [str(SOBTOP_BIN)],
                    input_text=stdin_text,
                    cwd=SOBTOP_DIR,
                    timeout=120,
                    env={**os.environ, "OMP_NUM_THREADS": "1"},
                    run_dir=run_dir,
                )
            except subprocess.TimeoutExpired:
                return _failure(inp, start, "Sobtop 超时 (120s)", kind=ErrorKind.TIMEOUT)
            except OSError as exc:
                return _failure(
                    inp,
                    start,
                    f"无法启动 Sobtop: {exc}",
                    kind=ErrorKind.DEPENDENCY_MISSING,
                )

            if result.returncode not in {0, 24}:
                return _failure(inp, start, f"Sobtop 退出码={result.returncode}", raw_output=_raw_output(result))

            not_current = [
                str(path) for path in vendor_outputs.values()
                if not path.is_file()
                or path.stat().st_mtime_ns + _OUTPUT_MTIME_TOLERANCE_NS < started_ns
            ]
            if not_current:
                return _failure(
                    inp,
                    start,
                    "未生成本次运行所需的临时输出: " + ", ".join(not_current),
                    raw_output=_raw_output(result),
                )

            validation = validate_topology_files(
                vendor_outputs["itp"], vendor_outputs["gro"],
                step_name="topo_gaff", error_kind=ErrorKind.SOBTOP_FAILED,
                expected_moleculetype=inp.output_name,
            )
            if not validation.success:
                validation.duration_s = time.monotonic() - start
                return validation

            if Path(inp.chg).with_suffix(".charge_scaling.json").is_file():
                from willy.quantum.charge_files import validate_itp_charge_transfer

                try:
                    validate_itp_charge_transfer(Path(inp.chg), vendor_outputs["itp"])
                except (OSError, ValueError) as exc:
                    return _failure(inp, start, str(exc), kind=ErrorKind.INPUT_CONTRACT)

            shutil.copy2(vendor_outputs["itp"], run_outputs["itp"])
            shutil.copy2(vendor_outputs["gro"], run_outputs["gro"])
            if result.returncode == 24:
                accepted_rc24 = True
            else:
                accepted_rc24 = False
            return StepResult(
                step_name="topo_gaff",
                step_index=4,
                success=True,
                outputs={"itp": str(run_outputs["itp"]), "gro": str(run_outputs["gro"])},
                artifacts=[str(run_outputs["itp"]), str(run_outputs["gro"])],
                duration_s=time.monotonic() - start,
                extra={
                    "returncode": result.returncode,
                    "accepted_rc24": accepted_rc24,
                    **validation.extra,
                },
            )
        finally:
            _cleanup_expected_outputs(vendor_outputs.values())


def batch_make_topo(inputs: Iterable[SobtopInput], output_dir: str) -> list[StepResult]:
    """Parameterize explicit plan inputs; this function never scans directories."""
    return [make_itp_gro(inp, output_dir=output_dir) for inp in inputs]
