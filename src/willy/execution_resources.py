"""Small, backend-neutral helpers for local CPU and memory settings."""

from __future__ import annotations

from collections.abc import Mapping
import copy
from dataclasses import dataclass
from pathlib import Path
import json
import os
import re


DEFAULT_NPROC = 8
DEFAULT_ORCA_MEM_MB = 5000
_MEMORY_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(kb|kib|mb|mib|gb|gib|tb|tib)?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class NprocNormalization:
    """A bounded local CPU configuration and its public advisory warnings."""

    config: dict[str, object]
    cpu_count: int
    warnings: tuple[str, ...]


def local_cpu_count() -> int:
    """Return CPUs available to this process, then fall back to host count."""
    try:
        affinity = os.sched_getaffinity(0)
    except (AttributeError, OSError):
        affinity = ()
    if affinity:
        return len(affinity)
    detected = os.cpu_count()
    return detected if isinstance(detected, int) and detected > 0 else 1


def default_nproc(cpu_count: int | None = None) -> int:
    """Return the conservative default width for an otherwise unspecified run."""
    capacity = local_cpu_count() if cpu_count is None else max(1, int(cpu_count))
    return min(DEFAULT_NPROC, capacity)


def normalize_config_nproc(
    config: Mapping[str, object],
    *,
    cpu_count: int | None = None,
) -> NprocNormalization:
    """Bound configured CPU widths to the local machine without blocking launch.

    Missing ``defaults.nproc`` becomes ``min(8, local CPU count)``.  Explicit
    global and molecule-level requests above the detected capacity are reduced
    and described through public warnings.  Malformed values are deliberately
    retained so the workflow schema can reject them instead of silently fixing
    invalid user input.
    """
    capacity = local_cpu_count() if cpu_count is None else max(1, int(cpu_count))
    normalized = copy.deepcopy(dict(config))
    warnings: list[str] = []

    defaults = normalized.get("defaults")
    if defaults is None:
        defaults = {}
        normalized["defaults"] = defaults
    if isinstance(defaults, dict):
        requested = defaults.get("nproc")
        if "nproc" not in defaults:
            defaults["nproc"] = default_nproc(capacity)
        elif _is_positive_int(requested) and requested > capacity:
            defaults["nproc"] = capacity
            warnings.append(
                f"已请求默认使用 {requested} 核；当前系统检测到 {capacity} 核，"
                f"本次将以 {capacity} 核运行。"
            )

    molecules = normalized.get("molecules")
    if isinstance(molecules, dict):
        for name, molecule in molecules.items():
            if not isinstance(molecule, dict):
                continue
            requested = molecule.get("nproc")
            if _is_positive_int(requested) and requested > capacity:
                molecule["nproc"] = capacity
                warnings.append(
                    f"分子 {name} 已请求使用 {requested} 核；当前系统检测到 {capacity} 核，"
                    f"本次将以 {capacity} 核运行。"
                )

    return NprocNormalization(normalized, capacity, tuple(warnings))


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def resolve_nproc(value: object, fallback: int = DEFAULT_NPROC) -> int:
    """Return a positive CPU width bounded by the current local capacity."""
    try:
        nproc = int(value)
    except (TypeError, ValueError):
        nproc = fallback
    if isinstance(value, bool) or nproc <= 0:
        nproc = fallback
    return min(max(1, nproc), local_cpu_count())


def nproc_from_config(config_path: str | Path, fallback: int = DEFAULT_NPROC) -> int:
    """Read the shared workflow CPU default without making config I/O fatal."""
    try:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return resolve_nproc(None, fallback)
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
