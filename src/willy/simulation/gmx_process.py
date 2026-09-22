"""Bounded execution policy for GROMACS commands other than mdrun."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence
import math
import subprocess
import time

from willy.process_lifecycle import ProcessTerminationController, record_process_lifecycle
from willy.structured_log import append_structured_event


GMX_AUX_TIMEOUT_S = 20
GMX_AUX_MAX_ATTEMPTS = 3
GMX_AUX_REAP_TIMEOUT_S = 1


def _stop_requested(directory: Path) -> bool:
    return (directory / "stop.request").is_file()


def _collect_terminated_output(process: subprocess.Popen) -> tuple[str, str]:
    try:
        return process.communicate(timeout=GMX_AUX_REAP_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        try:
            process.wait(timeout=GMX_AUX_REAP_TIMEOUT_S)
        except subprocess.TimeoutExpired as reap_error:
            raise OSError("GROMACS 辅助进程未能在清理期限内退出，禁止继续重试") from reap_error
        stdout, stderr = exc.output or "", exc.stderr or ""
        return (
            stdout.decode(errors="replace") if isinstance(stdout, bytes) else stdout,
            stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr,
        )


def run_gmx_auxiliary(
    command: Sequence[str],
    *,
    cwd: str | Path,
    timeout: float | None = None,
    input_text: str | None = None,
    env: Mapping[str, str] | None = None,
    run_dir: str | Path | None = None,
) -> subprocess.CompletedProcess:
    """Allow at most three timeout attempts, each with a 20-second ceiling."""
    requested_timeout = GMX_AUX_TIMEOUT_S if timeout is None else float(timeout)
    if not math.isfinite(requested_timeout) or requested_timeout < 0:
        raise ValueError("GROMACS 辅助命令超时必须为非负有限数")
    effective_timeout = min(requested_timeout, GMX_AUX_TIMEOUT_S)
    directory = Path(run_dir) if run_dir is not None else Path(cwd)
    arguments = [str(value) for value in command]
    if len(arguments) > 1 and arguments[1] == "mdrun":
        raise ValueError("mdrun 必须使用独立的长任务执行器")

    for attempt in range(1, GMX_AUX_MAX_ATTEMPTS + 1):
        if _stop_requested(directory):
            return subprocess.CompletedProcess(arguments, 130, "", "")
        process = subprocess.Popen(
            arguments,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if input_text is not None else None,
            text=True,
            cwd=str(cwd),
            start_new_session=True,
            env=dict(env) if env is not None else None,
        )
        started_at = time.monotonic()
        from willy.run_processes import track_process
        track_process(directory, process)
        pending_input = input_text
        termination = None
        append_structured_event(
            directory, "process_started", source="gromacs", layer="simulation",
            attempt_id=f"gmx_aux_{attempt}", outcome="started", message_code="process.started",
        )
        try:
            while True:
                remaining = effective_timeout - (time.monotonic() - started_at)
                reason = "stop_requested" if _stop_requested(directory) else (
                    "timeout" if remaining <= 0 else ""
                )
                if reason:
                    termination = ProcessTerminationController(process)
                    termination.force_kill(reason)
                    stdout, stderr = _collect_terminated_output(process)
                    break
                try:
                    stdout, stderr = process.communicate(
                        input=pending_input, timeout=min(1.0, remaining),
                    )
                except subprocess.TimeoutExpired:
                    pending_input = None
                    continue
                break
        except BaseException:
            if termination is None:
                termination = ProcessTerminationController(process)
                termination.force_kill("interrupted")
                _collect_terminated_output(process)
            raise
        finally:
            if termination is not None:
                record_process_lifecycle(
                    directory, termination, command=arguments, returncode=process.returncode,
                )
            append_structured_event(
                directory, "process_finished", source="gromacs", layer="simulation",
                attempt_id=f"gmx_aux_{attempt}",
                outcome=(
                    "timed_out" if termination is not None and termination.reason == "timeout"
                    else "stopped" if termination is not None and termination.reason == "stop_requested"
                    else "succeeded" if process.returncode == 0
                    else "failed"
                ),
                error_kind=termination.reason if termination is not None else None,
                duration_ms=max(0.0, (time.monotonic() - started_at) * 1000.0),
                message_code="process.finished",
            )
        if termination is not None and termination.reason == "timeout":
            if _stop_requested(directory):
                return subprocess.CompletedProcess(arguments, 130, stdout, stderr)
            if attempt == GMX_AUX_MAX_ATTEMPTS:
                raise subprocess.TimeoutExpired(
                    arguments, effective_timeout, output=stdout, stderr=stderr,
                )
            continue
        stopped = termination is not None and termination.reason == "stop_requested"
        return subprocess.CompletedProcess(
            arguments, (process.returncode or 130) if stopped else process.returncode,
            stdout, stderr,
        )
