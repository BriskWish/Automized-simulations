"""A start gate separates process creation from scientific execution."""

from __future__ import annotations

import os
from pathlib import Path
import select
import signal
import subprocess
from typing import Callable


LAUNCH_GATE_ENV = "WILLY_LAUNCH_READY_FD"
_UNREAPED: dict[int, tuple[object, object]] = {}


class LaunchCleanupError(RuntimeError):
    """The reservation must remain held until its child has been reclaimed."""


def spawn_gated_process(command: list[str], *, cwd: Path, lock_fd: int) -> subprocess.Popen:
    gate = os.pipe()
    try:
        process = subprocess.Popen(
            command, cwd=str(cwd), start_new_session=True, pass_fds=(lock_fd, gate[0]),
            env={**os.environ, LAUNCH_GATE_ENV: str(gate[0])},
        )
    except BaseException:
        for descriptor in gate:
            os.close(descriptor)
        raise
    process._willy_launch_gate = gate
    return process


def wait_for_launch_permission() -> None:
    value = os.environ.pop(LAUNCH_GATE_ENV, None)
    if value is None:
        return
    descriptor = int(value)
    try:
        readable, _, _ = select.select([descriptor], [], [], 30.0)
        if not readable or os.read(descriptor, 1) != b"1":
            raise OSError("启动交接未完成")
    finally:
        os.close(descriptor)


def close_launch_gate(process: object) -> None:
    for descriptor in getattr(process, "_willy_launch_gate", ()):
        try:
            os.close(descriptor)
        except OSError:
            pass
    setattr(process, "_willy_launch_gate", ())


def cancel_unstarted_process(process: subprocess.Popen) -> bool:
    close_launch_gate(process)
    try:
        process.wait(timeout=1.0)
        return True
    except subprocess.TimeoutExpired:
        try:
            if os.getpgid(process.pid) == process.pid:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        except OSError:
            return False
        try:
            process.wait(timeout=2.0)
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False
    except OSError:
        return False


def handoff_controlled_process(
    process: subprocess.Popen,
    reservation: object,
    root: Path,
    start_cleanup: Callable[[], None],
) -> None:
    try:
        reservation.mark_runner_started(process.pid)
        (root / ".pipeline.pid").write_text(str(process.pid), encoding="utf-8")
        start_cleanup()
        reservation.detach_parent()
        gate = getattr(process, "_willy_launch_gate", ())
        if gate:
            os.write(gate[1], b"1")
    except BaseException as exc:
        if not cancel_unstarted_process(process):
            _UNREAPED[process.pid] = (reservation, process)
            raise LaunchCleanupError("子进程尚未退出，启动锁必须保留") from exc
        try:
            pid_path = root / ".pipeline.pid"
            if pid_path.read_text(encoding="utf-8").strip() == str(process.pid):
                pid_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    finally:
        close_launch_gate(process)
