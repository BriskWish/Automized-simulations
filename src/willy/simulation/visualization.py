"""GROMACS stage structures for the run visualization panel.

The PDB files in ``visualization/`` are derived display artifacts.  They are
best-effort for EM/EQ.  The final PROD PDB is synchronously verified by the
orchestrator before the run may enter its terminal ``done`` state.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterator
import fcntl
import logging
import os
import secrets
import subprocess
import threading

from willy.env_registry import EnvironmentRegistryError, build_tool_env, require_tool
from willy.process_lifecycle import run_managed_command


VIEWER_DIRECTORY = "visualization"
VISUALIZATION_STAGES = frozenset({"em", "eq", "prod"})
_CONVERSION_TIMEOUT_S = 60
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class VisualizationConversionResult:
    """The bounded outcome of a non-critical GRO-to-PDB conversion."""

    stage: str
    success: bool
    output: Path | None = None
    reason: str = ""


def stage_visualization_path(run_dir: str | Path, stage: str) -> Path:
    """Return the fixed, run-local viewer artifact location for one stage."""
    _require_stage(stage)
    return Path(run_dir) / VIEWER_DIRECTORY / f"{stage}.pdb"


def clear_stage_visualization_artifact(run_dir: str | Path, stage: str) -> list[str]:
    """Remove only display artifacts for one regenerated MD stage."""
    directory = Path(run_dir)
    output = stage_visualization_path(directory, stage)
    token = _token_path(directory, stage)
    removed: list[str] = []
    with _artifact_lock(directory, stage):
        for path in (output, token):
            if path.is_file():
                path.unlink()
                removed.append(str(path.relative_to(directory)))
        for temporary in output.parent.glob(f".{stage}.*.pdb"):
            if temporary.is_file():
                temporary.unlink()
                removed.append(str(temporary.relative_to(directory)))
    return removed


def schedule_accepted_stage_visualization(run_dir: str | Path, stage: str) -> None:
    """Start a daemon conversion after a stage has passed its primary contract.

    The token is removed whenever the stage is prepared again.  A worker that
    outlives its source stage therefore cannot publish an obsolete PDB file.
    """
    _require_stage(stage)
    directory = Path(run_dir)
    source = directory / f"{stage}.gro"
    if not source.is_file() or source.stat().st_size <= 0:
        return
    try:
        source_digest = _fingerprint(source)
        token = secrets.token_hex(16)
        output = stage_visualization_path(directory, stage)
        with _artifact_lock(directory, stage):
            output.parent.mkdir(parents=True, exist_ok=True)
            _token_path(directory, stage).write_text(token, encoding="ascii")
    except OSError:
        return

    def convert() -> None:
        result = convert_stage_gro_to_pdb(
            directory,
            stage,
            expected_source_digest=source_digest,
            token=token,
        )
        if not result.success and result.reason not in {"stale", "stopped"}:
            _LOGGER.warning("%s PDB 转换未完成: %s", stage, result.reason)

    thread = threading.Thread(
        target=convert,
        name=f"willy-viewer-{stage}",
        daemon=True,
    )
    try:
        thread.start()
    except RuntimeError:
        _remove_matching_token(directory, stage, token)


def convert_stage_gro_to_pdb(
    run_dir: str | Path,
    stage: str,
    *,
    expected_source_digest: str | None = None,
    token: str | None = None,
) -> VisualizationConversionResult:
    """Convert one accepted GRO artifact without affecting the MD workflow."""
    _require_stage(stage)
    directory = Path(run_dir)
    source = directory / f"{stage}.gro"
    output = stage_visualization_path(directory, stage)
    temporary = output.with_name(f".{stage}.{secrets.token_hex(8)}.pdb")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        if not source.is_file() or source.stat().st_size <= 0:
            return VisualizationConversionResult(stage, False, reason="source_missing")
        source_digest = _fingerprint(source)
        if expected_source_digest and source_digest != expected_source_digest:
            return VisualizationConversionResult(stage, False, reason="stale")
        if token is not None and not _token_matches(directory, stage, token):
            return VisualizationConversionResult(stage, False, reason="stale")
        try:
            gmx = require_tool("gmx")
            environment = build_tool_env("gmx")
        except EnvironmentRegistryError:
            return VisualizationConversionResult(stage, False, reason="gmx_unavailable")

        result = run_managed_command(
            [str(gmx.executable), "editconf", "-f", str(source), "-o", str(temporary)],
            cwd=directory,
            timeout=_CONVERSION_TIMEOUT_S,
            env=environment,
            run_dir=directory,
        )
        if result.returncode != 0:
            return VisualizationConversionResult(stage, False, reason="editconf_failed")
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            return VisualizationConversionResult(stage, False, reason="output_missing")
        if _gro_atom_count(source) != _pdb_atom_count(temporary):
            return VisualizationConversionResult(stage, False, reason="atom_count_mismatch")
        with _artifact_lock(directory, stage):
            if not source.is_file() or _fingerprint(source) != source_digest:
                return VisualizationConversionResult(stage, False, reason="stale")
            if token is not None and not _token_matches(directory, stage, token):
                return VisualizationConversionResult(stage, False, reason="stale")
            os.replace(temporary, output)
        return VisualizationConversionResult(stage, True, output=output)
    except subprocess.TimeoutExpired:
        return VisualizationConversionResult(stage, False, reason="timeout")
    except OSError:
        return VisualizationConversionResult(stage, False, reason="io_error")
    finally:
        temporary.unlink(missing_ok=True)


def _fingerprint(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _gro_atom_count(path: Path) -> int:
    try:
        return int(path.read_text(errors="replace").splitlines()[1].strip())
    except (IndexError, OSError, ValueError):
        return -1


def _pdb_atom_count(path: Path) -> int:
    try:
        return sum(
            1 for line in path.read_text(errors="replace").splitlines()
            if line.startswith(("ATOM  ", "HETATM"))
        )
    except OSError:
        return -1


def _token_path(run_dir: Path, stage: str) -> Path:
    return run_dir / VIEWER_DIRECTORY / f".{stage}.token"


@contextmanager
def _artifact_lock(run_dir: Path, stage: str) -> Iterator[None]:
    """Serialize token invalidation and atomic publication for one stage."""
    directory = run_dir / VIEWER_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / f".{stage}.lock"
    with lock_path.open("a+", encoding="ascii") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _token_matches(run_dir: Path, stage: str, expected: str) -> bool:
    try:
        return _token_path(run_dir, stage).read_text(encoding="ascii") == expected
    except OSError:
        return False


def _remove_matching_token(run_dir: Path, stage: str, expected: str) -> None:
    path = _token_path(run_dir, stage)
    try:
        if path.read_text(encoding="ascii") == expected:
            path.unlink()
    except OSError:
        pass


def _require_stage(stage: str) -> None:
    if stage not in VISUALIZATION_STAGES:
        raise ValueError(f"不支持生成可视化 PDB 的阶段: {stage}")
