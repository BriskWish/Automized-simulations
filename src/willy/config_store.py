"""Atomic, locked JSON persistence for active and run-local configurations."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping
import fcntl
import json
import os
import tempfile


def _lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lock")


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    """Serialize writers for one configuration without locking its JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(_lock_path(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _atomic_write_bytes_unlocked(path: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_json(path: str | Path, payload: Any) -> Path:
    """Atomically replace one JSON file while holding its writer lock."""
    target = Path(path)
    with _exclusive_lock(target):
        _atomic_write_bytes_unlocked(target, _json_bytes(payload))
    return target


def write_text(path: str | Path, content: str) -> Path:
    """Atomically replace a UTF-8 text file while holding its writer lock."""
    target = Path(path)
    with _exclusive_lock(target):
        _atomic_write_bytes_unlocked(target, content.encode("utf-8"))
    return target


def copy_file(path: str | Path, destination: str | Path) -> Path:
    """Copy a file through an atomic replacement at the destination."""
    source = Path(path)
    target = Path(destination)
    content = source.read_bytes()
    with _exclusive_lock(target):
        _atomic_write_bytes_unlocked(target, content)
    return target


def replace_json_with_backup(
    path: str | Path,
    payload: Mapping[str, Any],
    *,
    backup_path: str | Path | None = None,
) -> Path:
    """Atomically refresh a config and, when requested, its durable backup.

    The active file is written last.  A backup-write failure therefore leaves
    the last active configuration intact.
    """
    target = Path(path)
    backup = Path(backup_path) if backup_path is not None else None
    with _exclusive_lock(target):
        if backup is not None and target.is_file():
            existing = target.read_bytes()
            with _exclusive_lock(backup):
                _atomic_write_bytes_unlocked(backup, existing)
        _atomic_write_bytes_unlocked(target, _json_bytes(payload))
    return target
