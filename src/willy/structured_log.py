"""Run-local, redacted structured execution logging.

The logger is deliberately a small adapter around :func:`run_transaction`.
Each event is appended to ``md_run/<run_id>/logs/structured.jsonl`` while the
run-store lock is held, so its ``sequence`` shares the run-wide sequence used
by events and decision traces.  Logging is observational: a persistence
failure never changes the caller's execution result.
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
import re
from typing import Any, Mapping

from willy.run_store import run_transaction


STRUCTURED_LOG_FILENAME = "logs/structured.jsonl"
STRUCTURED_LOG_SCHEMA_VERSION = 1
REDACTED = "[redacted]"

_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
_SENSITIVE_RE = re.compile(
    r"(?:api[_-]?key|authorization|bearer\s|password|secret|credential|"
    r"access[_-]?token|refresh[_-]?token|raw[_-]?output|raw\s+output|prompt)",
    re.IGNORECASE,
)
_COMMAND_RE = re.compile(
    r"(?:^|\s)(?:gmx(?:_mpi)?|python(?:3)?|ssh|scp|bash|sh|csh|orca|"
    r"g(?:09|16)|packmol|sobtop)(?:\s|$)|[;&|`$]"
)

def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_path_like(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if "/" in stripped or "\\" in stripped or stripped.startswith("~"):
        return True
    return bool(re.match(r"^[A-Za-z]:[\\/]", stripped))


def _redact_text(value: object) -> str | None:
    """Return a bounded safe string, or ``None`` for malformed values."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if _SENSITIVE_RE.search(text) or _is_path_like(text) or _COMMAND_RE.search(text):
        return REDACTED
    if any(ord(char) < 32 for char in text):
        return REDACTED
    return text[:160]


def _safe_scalar(value: object) -> object:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if abs(value) <= 10**12 else None
    if isinstance(value, float):
        return value if isfinite(value) and abs(value) <= 10**12 else None
    return _redact_text(value)


def _safe_identifier(value: object) -> str | None:
    text = _redact_text(value)
    if text is None or text == REDACTED or not _IDENTIFIER_RE.fullmatch(text):
        return None
    return text


def _safe_label(value: object) -> str | None:
    """Keep a bounded human-readable label without accepting free-form data."""
    text = _redact_text(value)
    if text is None or text == REDACTED:
        return None
    return text[:160]


def _safe_artifacts(value: object) -> list[str] | None:
    if not isinstance(value, (list, tuple)):
        return None
    result: list[str] = []
    for item in list(value)[:32]:
        clean = _redact_text(item)
        if clean is not None:
            result.append(clean)
    return result


class StructuredRunLogger:
    """Append redacted structured events for one ``md_run`` child directory."""

    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir)

    @property
    def path(self) -> Path:
        return self.run_dir / STRUCTURED_LOG_FILENAME

    def append(
        self,
        event_code: object,
        *,
        level: object = "INFO",
        attempt_id: object = None,
        step: object = None,
        step_name: object = None,
        layer: object = None,
        source: object = None,
        outcome: object = None,
        error_kind: object = None,
        action_id: object = None,
        policy_id: object = None,
        model_id: object = None,
        prompt_version: object = None,
        message_code: object = None,
        duration_ms: object = None,
        duration: object = None,
        artifact_refs: object = None,
        parameter_fields: object = None,
        **_ignored: object,
    ) -> dict[str, Any] | None:
        """Append one event and return the persisted record.

        Invalid optional fields are omitted.  A malformed required event code,
        an unsafe run directory, or any filesystem/locking error returns
        ``None`` rather than interrupting the scientific execution.
        """
        try:
            event_name = _safe_identifier(event_code)
            if event_name is None:
                return None
            level_name = _safe_identifier(level)
            if level_name not in _LEVELS:
                level_name = "INFO"
            payload: dict[str, Any] = {
                "schema_version": STRUCTURED_LOG_SCHEMA_VERSION,
                "timestamp": _timestamp(),
                "event_code": event_name,
                "level": level_name,
                "run_id": self.run_dir.name,
            }
            for field, value in (
                ("attempt_id", attempt_id),
                ("layer", layer),
                ("source", source),
                ("outcome", outcome),
                ("error_kind", error_kind),
                ("action_id", action_id),
                ("policy_id", policy_id),
                ("model_id", model_id),
                ("prompt_version", prompt_version),
                ("message_code", message_code),
            ):
                if value is None:
                    continue
                clean_text = _redact_text(value)
                if clean_text == REDACTED:
                    payload[field] = REDACTED
                    continue
                clean = _safe_identifier(clean_text)
                if clean is not None:
                    payload[field] = clean
            if step is not None and isinstance(step, int) and not isinstance(step, bool) and 0 <= step <= 10000:
                payload["step"] = step
            if step_name is not None:
                clean_step_name = _safe_label(step_name)
                if clean_step_name is not None:
                    payload["step_name"] = clean_step_name
            duration_value = duration_ms if duration_ms is not None else duration
            if duration_value is not None:
                clean_duration = _safe_scalar(duration_value)
                if (
                    isinstance(clean_duration, (int, float))
                    and not isinstance(clean_duration, bool)
                    and clean_duration >= 0
                ):
                    payload["duration_ms"] = clean_duration
            if artifact_refs is not None:
                clean_artifacts = _safe_artifacts(artifact_refs)
                if clean_artifacts is not None:
                    payload["artifact_refs"] = clean_artifacts
            if parameter_fields is not None:
                clean_parameters = _safe_artifacts(parameter_fields)
                if clean_parameters is not None:
                    payload["parameter_fields"] = clean_parameters

            logs_dir = self.run_dir / "logs"
            if logs_dir.is_symlink():
                return None
            with run_transaction(self.run_dir) as store:
                logs_dir.mkdir(parents=True, exist_ok=True)
                return store.append_jsonl(STRUCTURED_LOG_FILENAME, payload)
        except Exception:
            return None


def append_structured_event(
    run_dir: str | Path,
    event_code: object,
    **fields: object,
) -> dict[str, Any] | None:
    """Convenience wrapper for one-shot run-local structured events."""
    return StructuredRunLogger(run_dir).append(event_code, **fields)


def record_structured_event(
    run_dir: str | Path,
    event_code: object,
    *,
    fields: Mapping[str, object] | None = None,
    **kwargs: object,
) -> dict[str, Any] | None:
    """Compatibility wrapper accepting a mapping plus explicit fields."""
    values = dict(fields or {})
    values.update(kwargs)
    return append_structured_event(run_dir, event_code, **values)


__all__ = [
    "REDACTED",
    "STRUCTURED_LOG_FILENAME",
    "STRUCTURED_LOG_SCHEMA_VERSION",
    "StructuredRunLogger",
    "append_structured_event",
    "record_structured_event",
]
