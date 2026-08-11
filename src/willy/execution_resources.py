"""Small, backend-neutral helpers for local CPU and memory settings."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import json
import re


DEFAULT_NPROC = 8
DEFAULT_ORCA_MEM_MB = 5000
_MEMORY_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(kb|kib|mb|mib|gb|gib|tb|tib)?\s*$", re.IGNORECASE)


def resolve_nproc(value: object, fallback: int = DEFAULT_NPROC) -> int:
    """Return a positive CPU width, falling back for absent or malformed input."""
    try:
        nproc = int(value)
    except (TypeError, ValueError):
        return fallback
    return nproc if not isinstance(value, bool) and nproc > 0 else fallback


def nproc_from_config(config_path: str | Path, fallback: int = DEFAULT_NPROC) -> int:
    """Read the shared workflow CPU default without making config I/O fatal."""
    try:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    defaults = payload.get("defaults", {}) if isinstance(payload, Mapping) else {}
    return resolve_nproc(defaults.get("nproc") if isinstance(defaults, Mapping) else None, fallback)


def memory_to_mb(value: object, fallback: int = DEFAULT_ORCA_MEM_MB) -> int:
    """Translate the shared Gaussian-style memory value to ORCA ``%maxcore`` MB."""
    if isinstance(value, bool):
        return fallback
    match = _MEMORY_RE.fullmatch(str(value)) if value not in (None, "") else None
    if not match:
        return fallback
    amount = float(match.group(1))
    unit = (match.group(2) or "mb").lower()
    multiplier = {
        "kb": 1 / 1000,
        "kib": 1 / 1024,
        "mb": 1,
        "mib": 1.048576,
        "gb": 1000,
        "gib": 1073.741824,
        "tb": 1_000_000,
        "tib": 1_099_511.627776,
    }[unit]
    converted = int(amount * multiplier)
    return converted if converted > 0 else fallback
