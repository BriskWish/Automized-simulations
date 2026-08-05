"""Public, run-local ETA and liveness snapshots for ``gmx mdrun``."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import json
import os
import re
import tempfile
import time


MDRUN_ETA_FILENAME = "mdrun_eta.json"
MDRUN_ETA_SCHEMA_VERSION = 2
MDRUN_HEARTBEAT_INTERVAL_S = 15.0

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_DURATION_TOKEN = r"\d+(?:\.\d+)?\s*(?:d(?:ays?)?|h(?:ours?)?|m(?:in(?:utes?)?)?|s(?:ec(?:onds?)?)?)"
_DURATION = rf"(?:(?:{_DURATION_TOKEN})\s*)+|(?:\d+:){{1,2}}\d+(?:\.\d+)?"
_ETA_PATTERNS = (
    re.compile(
        rf"\bstep\s*[=:]?\s*(?P<step>\d+)\b[^\r\n]*?"
        rf"(?:remaining\s+(?:wall\s*clock|wallclock)\s*time|"
        rf"remaining\s+time|time\s+remaining)\s*[:=]?\s*(?P<duration>{_DURATION})",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bstep\s*[=:]?\s*(?P<step>\d+)\b[^\r\n]*?"
        rf"(?:estimated\s+remaining|estimated\s+time\s+left)\s*[:=]?\s*(?P<duration>{_DURATION})",
        re.IGNORECASE,
    ),
)
_WILL_FINISH_PATTERN = re.compile(
    r"\bstep\s*[=:]?\s*(?P<step>\d+)\b[^\r\n]*?"
    r"\bwill\s+finish\s+(?P<finish>"
    r"(?:mon|tue|wed|thu|fri|sat|sun)\s+"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+"
    r"\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})",
    re.IGNORECASE,
)
_LOG_PROGRESS_PATTERN = re.compile(
    r"\bStep\s+Time\s*\r?\n\s*(?P<step>\d+)\s+[-+]?\d+(?:\.\d+)?",
    re.IGNORECASE,
)
_STAGE_PROGRESS_SUFFIXES = ("log", "edr", "xtc", "cpt")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | None = None) -> str:
    moment = value or _now()
    return moment.astimezone(timezone.utc).isoformat()


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _path(work_dir: str | Path) -> Path:
    return Path(work_dir) / MDRUN_ETA_FILENAME


def _read_snapshot(work_dir: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(_path(work_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _as_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _newer_timestamp(first: datetime | None, second: datetime | None) -> datetime | None:
    if first is None:
        return second
    if second is None:
        return first
    return max(first, second)


def _duration_seconds(value: str) -> float | None:
    """Parse GROMACS' compact or clock-formatted remaining-time values."""
    compact = " ".join(value.lower().split())
    if ":" in compact and re.fullmatch(r"\d+(?::\d+){1,2}(?:\.\d+)?", compact):
        parts = [float(part) for part in compact.split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
    units = {
        "d": 86_400.0, "day": 86_400.0, "days": 86_400.0,
        "h": 3600.0, "hour": 3600.0, "hours": 3600.0,
        "m": 60.0, "min": 60.0, "minute": 60.0, "minutes": 60.0,
        "s": 1.0, "sec": 1.0, "second": 1.0, "seconds": 1.0,
    }
    total = 0.0
    matched = False
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*([a-z]+)", compact):
        unit = match.group(2)
        factor = units.get(unit)
        if factor is None:
            return None
        total += float(match.group(1)) * factor
        matched = True
    return total if matched else None


def _parse_gromacs_finish_time(value: str) -> datetime | None:
    """Convert GROMACS' locale-local ``ctime`` prediction to UTC.

    GROMACS 2025 changes from a remaining duration to ``will finish Tue Aug
    4 01:00:54 2026`` for predictions of at least five minutes.  ``ctime``
    has no offset, so ``mktime`` intentionally interprets it in the same host
    local time zone that GROMACS used when it emitted the text.
    """
    try:
        local_finish = datetime.strptime(" ".join(value.split()), "%a %b %d %H:%M:%S %Y")
        epoch = time.mktime(local_finish.timetuple())
    except (OverflowError, ValueError):
        return None
    return datetime.fromtimestamp(epoch, timezone.utc)


def _extract_verbose_eta(text: str, observed_at: datetime) -> tuple[int, float, datetime] | None:
    """Return GROMACS-provided ETA facts, preferring the latest stream entry."""
    cleaned = _ANSI_ESCAPE.sub("", text)
    candidates: list[tuple[int, int, float, datetime]] = []
    for pattern in _ETA_PATTERNS:
        for match in pattern.finditer(cleaned):
            seconds = _duration_seconds(match.group("duration"))
            if seconds is None or not 0 <= seconds <= 31_536_000:
                continue
            candidates.append((match.start(), int(match.group("step")), seconds, observed_at + timedelta(seconds=seconds)))
    for match in _WILL_FINISH_PATTERN.finditer(cleaned):
        finish_at = _parse_gromacs_finish_time(match.group("finish"))
        if finish_at is None:
            continue
        seconds = (finish_at - observed_at).total_seconds()
        if 0 <= seconds <= 31_536_000:
            candidates.append((match.start(), int(match.group("step")), seconds, finish_at))
    if not candidates:
        return None
    _, step, seconds, finish_at = max(candidates, key=lambda item: item[0])
    return step, seconds, finish_at


def _latest_log_step(path: Path) -> int | None:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 65_536))
            tail = handle.read().decode(errors="replace")
    except OSError:
        return None
    matches = list(_LOG_PROGRESS_PATTERN.finditer(tail))
    return int(matches[-1].group("step")) if matches else None


def _stage_progress(work_dir: str | Path, stage: str) -> tuple[datetime | None, int | None]:
    """Return only safe liveness facts from the active stage's written artifacts."""
    directory = Path(work_dir)
    latest: datetime | None = None
    log_path = directory / f"{stage}.log"
    for suffix in _STAGE_PROGRESS_SUFFIXES:
        path = directory / f"{stage}.{suffix}"
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError:
            continue
        latest = _newer_timestamp(latest, modified)
    return latest, _latest_log_step(log_path)


def begin_mdrun_eta(work_dir: str | Path, stage: str) -> None:
    """Mark that GROMACS has started but has not yet emitted an ETA."""
    now = _now()
    _atomic_write(_path(work_dir), {
        "schema_version": MDRUN_ETA_SCHEMA_VERSION,
        "stage": stage,
        "status": "waiting",
        "observed_at": _timestamp(now),
        "process_alive": True,
        "source": "startup",
    })


def heartbeat_mdrun_eta(
    work_dir: str | Path,
    stage: str,
    *,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Refresh liveness without fabricating an ETA from simulation progress.

    ``observed_at`` is the current process observation. ``eta_observed_at`` is
    present only when GROMACS itself emitted a remaining-time prediction.
    """
    moment = observed_at or _now()
    existing = _read_snapshot(work_dir)
    status = existing.get("status") if existing.get("status") in {"waiting", "available"} else "waiting"
    progress_at, progress_step = _stage_progress(work_dir, stage)
    last_progress_at = _newer_timestamp(_as_timestamp(existing.get("last_progress_at")), progress_at)
    payload: dict[str, Any] = {
        "schema_version": MDRUN_ETA_SCHEMA_VERSION,
        "stage": stage,
        "status": status,
        "observed_at": _timestamp(moment),
        "process_alive": True,
    }
    if last_progress_at is not None:
        payload["last_progress_at"] = _timestamp(last_progress_at)
    if isinstance(progress_step, int) and progress_step >= 0:
        payload["last_progress_step"] = progress_step
    elif isinstance(existing.get("last_progress_step"), int) and existing["last_progress_step"] >= 0:
        payload["last_progress_step"] = existing["last_progress_step"]
    if status == "available":
        for key in ("step", "remaining_seconds", "eta_observed_at", "estimated_end_at"):
            if key in existing:
                payload[key] = existing[key]
        payload["source"] = "gmx_verbose"
    else:
        payload["source"] = "stage_artifacts" if progress_at is not None else "mdrun_process"
    _atomic_write(_path(work_dir), payload)
    return payload


def consume_mdrun_verbose_output(
    work_dir: str | Path,
    stage: str,
    text: str,
    *,
    carry: str = "",
    observed_at: datetime | None = None,
) -> str:
    """Store the latest GROMACS ETA and retain a bounded partial record.

    GROMACS refreshes verbose progress using carriage returns, so this accepts
    arbitrary stream chunks instead of requiring newline-delimited output.
    """
    combined = carry + text
    moment = observed_at or _now()
    parsed = _extract_verbose_eta(combined, moment)
    if parsed is not None:
        step, seconds, finish_at = parsed
        _atomic_write(_path(work_dir), {
            "schema_version": MDRUN_ETA_SCHEMA_VERSION,
            "stage": stage,
            "status": "available",
            "step": step,
            "remaining_seconds": round(seconds, 3),
            "observed_at": _timestamp(moment),
            "eta_observed_at": _timestamp(moment),
            "estimated_end_at": _timestamp(finish_at),
            "last_progress_at": _timestamp(moment),
            "last_progress_step": step,
            "process_alive": True,
            "source": "gmx_verbose",
        })
    return combined[-512:]


def finish_mdrun_eta(work_dir: str | Path, stage: str, *, success: bool) -> None:
    """Close the live estimate once the mdrun process has ended."""
    moment = _now()
    existing = _read_snapshot(work_dir)
    progress_at, progress_step = _stage_progress(work_dir, stage)
    last_progress_at = _newer_timestamp(_as_timestamp(existing.get("last_progress_at")), progress_at)
    finished_at = _timestamp(moment)
    payload: dict[str, Any] = {
        "schema_version": MDRUN_ETA_SCHEMA_VERSION,
        "stage": stage,
        "status": "finished" if success else "unavailable",
        "observed_at": finished_at,
        "finished_at": finished_at,
        "process_alive": False,
        "source": existing.get("source") if isinstance(existing.get("source"), str) else "mdrun_process",
    }
    if last_progress_at is not None:
        payload["last_progress_at"] = _timestamp(last_progress_at)
    if isinstance(progress_step, int) and progress_step >= 0:
        payload["last_progress_step"] = progress_step
    elif isinstance(existing.get("last_progress_step"), int) and existing["last_progress_step"] >= 0:
        payload["last_progress_step"] = existing["last_progress_step"]
    _atomic_write(_path(work_dir), payload)
