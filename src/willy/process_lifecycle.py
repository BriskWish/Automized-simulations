"""Managed process-group termination and run-local lifecycle evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
import os
import signal
import subprocess
import time

from willy.run_store import run_transaction
from willy.structured_log import append_structured_event


PROCESS_LIFECYCLE_FILENAME = "process_lifecycle.jsonl"
PROCESS_LIFECYCLE_SCHEMA_VERSION = 1
DEFAULT_INTERRUPT_GRACE_S = 30.0
DEFAULT_TERMINATE_GRACE_S = 10.0
DEFAULT_POLL_INTERVAL_S = 1.0
STRUCTURED_HEARTBEAT_INTERVAL_S = 15.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_command(command: Sequence[object]) -> list[str]:
    """Keep executable and subcommand only; paths and user values stay private."""
    values = [str(item) for item in command[:2]]
    return [Path(value).name if index == 0 else value[:80] for index, value in enumerate(values)]


def _send_group_signal(process: Any, sig: signal.Signals) -> bool:
    """Signal the dedicated child process group, with a test-friendly fallback."""
    try:
        os.killpg(int(process.pid), sig)
        return True
    except (AttributeError, ProcessLookupError, PermissionError, OSError, ValueError):
        try:
            process.send_signal(sig)
            return True
        except (AttributeError, ProcessLookupError, OSError):
            return False


@dataclass
class ProcessTerminationController:
    """Escalate one managed process group without blocking its output reader."""

    process: Any
    interrupt_grace_s: float = DEFAULT_INTERRUPT_GRACE_S
    terminate_grace_s: float = DEFAULT_TERMINATE_GRACE_S
    reason: str = ""
    phase: str = "running"
    requested_at: float | None = None
    phase_started_at: float | None = None
    signals: list[str] = field(default_factory=list)

    def request(self, reason: str, *, now: float | None = None) -> None:
        if self.phase != "running":
            return
        self.reason = str(reason)[:80]
        moment = time.monotonic() if now is None else now
        self.requested_at = moment
        self.phase_started_at = moment
        self._signal(signal.SIGINT, "interrupt")

    def tick(self, *, now: float | None = None) -> None:
        if self.phase in {"running", "killed", "finished"}:
            return
        if self._is_finished():
            self.phase = "finished"
            return
        moment = time.monotonic() if now is None else now
        elapsed = moment - (self.phase_started_at if self.phase_started_at is not None else moment)
        if self.phase == "interrupt" and elapsed >= self.interrupt_grace_s:
            self.phase_started_at = moment
            self._signal(signal.SIGTERM, "terminate")
        elif self.phase == "terminate" and elapsed >= self.terminate_grace_s:
            self._signal(signal.SIGKILL, "killed")

    def finish(self) -> None:
        if self.phase not in {"running", "killed"}:
            self.phase = "finished"

    def force_kill(self, reason: str) -> None:
        """Enforce a hard deadline even when only group members remain."""
        self.reason = str(reason)[:80]
        self.requested_at = time.monotonic()
        self.phase_started_at = self.requested_at
        self._signal(signal.SIGKILL, "killed")

    def _is_finished(self) -> bool:
        try:
            return self.process.poll() is not None
        except AttributeError:
            return False

    def _signal(self, sig: signal.Signals, phase: str) -> None:
        _send_group_signal(self.process, sig)
        self.phase = phase
        self.signals.append(sig.name)

    def to_dict(self, *, command: Sequence[object], returncode: int | None, checkpoint_exists: bool | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": PROCESS_LIFECYCLE_SCHEMA_VERSION,
            "timestamp": _now(),
            "command": _safe_command(command),
            "pid": int(self.process.pid) if isinstance(getattr(self.process, "pid", None), int) else None,
            "reason": self.reason,
            "phase": self.phase,
            "signals": list(self.signals),
            "returncode": returncode,
        }
        if checkpoint_exists is not None:
            payload["checkpoint_exists"] = checkpoint_exists
        return payload


def record_process_lifecycle(
    run_dir: str | Path,
    controller: ProcessTerminationController,
    *,
    command: Sequence[object],
    returncode: int | None,
    checkpoint_exists: bool | None = None,
) -> None:
    """Append one termination fact without exposing paths, input or raw output."""
    directory = Path(run_dir)
    payload = controller.to_dict(
        command=command,
        returncode=returncode,
        checkpoint_exists=checkpoint_exists,
    )
    with run_transaction(directory) as store:
        store.append_jsonl(PROCESS_LIFECYCLE_FILENAME, payload)


def _default_stop_check(run_dir: Path) -> bool:
    """Read the existing run-local stop request without importing it globally."""
    try:
        from willy.simulation.manifest import stop_requested

        return stop_requested(run_dir)
    except (ImportError, OSError):
        return False


def run_managed_command(
    command: Sequence[str | Path],
    *,
    cwd: str | Path | None = None,
    timeout: float | None = None,
    input_text: str | None = None,
    env: Mapping[str, str] | None = None,
    run_dir: str | Path | None = None,
    stop_check: Callable[[], bool] | None = None,
    interrupt_grace_s: float = DEFAULT_INTERRUPT_GRACE_S,
    terminate_grace_s: float = DEFAULT_TERMINATE_GRACE_S,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
) -> subprocess.CompletedProcess[str]:
    """Run one external command under the shared stop and audit contract.

    The caller keeps the familiar :class:`subprocess.CompletedProcess` and
    :class:`subprocess.TimeoutExpired` interface.  Unlike ``subprocess.run``,
    the command owns a dedicated process group, so a timeout or user stop also
    reaches child processes started by Gaussian, ORCA, Sobtop or BOSS.
    """
    normalized_command = [str(value) for value in command]
    audit_dir = Path(run_dir).resolve() if run_dir is not None else None
    process = subprocess.Popen(
        normalized_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE if input_text is not None else None,
        text=True,
        cwd=str(cwd) if cwd is not None else None,
        start_new_session=True,
        env=dict(env) if env is not None else None,
    )
    started_at = time.monotonic()
    last_heartbeat_at = started_at
    if audit_dir is not None:
        from willy.run_processes import track_process
        track_process(audit_dir, process)
        append_structured_event(
            audit_dir,
            "process_started",
            source="process_lifecycle",
            outcome="started",
            message_code="process.started",
        )
    pending_input = input_text
    termination: ProcessTerminationController | None = None
    timed_out = False

    while True:
        now = time.monotonic()
        if timeout is not None and now - started_at >= timeout and termination is None:
            timed_out = True
            termination = ProcessTerminationController(
                process,
                interrupt_grace_s=interrupt_grace_s,
                terminate_grace_s=terminate_grace_s,
            )
            termination.request("timeout", now=now)
        elif termination is None:
            should_stop = stop_check() if stop_check is not None else (
                _default_stop_check(audit_dir) if audit_dir is not None else False
            )
            if should_stop:
                termination = ProcessTerminationController(
                    process,
                    interrupt_grace_s=interrupt_grace_s,
                    terminate_grace_s=terminate_grace_s,
                )
                termination.request("stop_requested", now=now)

        try:
            stdout, stderr = process.communicate(
                input=pending_input,
                timeout=max(0.01, poll_interval_s),
            )
        except subprocess.TimeoutExpired:
            pending_input = None
            now = time.monotonic()
            if audit_dir is not None and now - last_heartbeat_at >= STRUCTURED_HEARTBEAT_INTERVAL_S:
                append_structured_event(
                    audit_dir,
                    "process_heartbeat",
                    source="process_lifecycle",
                    outcome="running",
                    duration_ms=max(0.0, (now - started_at) * 1000.0),
                    message_code="process.heartbeat",
                )
                last_heartbeat_at = now
            if termination is not None:
                termination.tick(now=now)
            continue

        result = subprocess.CompletedProcess(
            args=normalized_command,
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
        )
        if termination is not None:
            termination.finish()
            if audit_dir is not None:
                record_process_lifecycle(
                    audit_dir,
                    termination,
                    command=normalized_command,
                    returncode=result.returncode,
                )
        if audit_dir is not None:
            append_structured_event(
                audit_dir,
                "process_finished",
                source="process_lifecycle",
                outcome=(
                    "timed_out" if timed_out
                    else "succeeded" if result.returncode == 0
                    else "failed"
                ),
                error_kind=termination.reason if termination is not None else None,
                duration_ms=max(0.0, (time.monotonic() - started_at) * 1000.0),
                message_code="process.finished",
            )
        if timed_out:
            raise subprocess.TimeoutExpired(
                normalized_command,
                timeout,
                output=stdout,
                stderr=stderr,
            )
        return result
