"""Atomic launch reservation and public startup audit for one pipeline run."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import secrets
import tempfile
import re

from willy.python_runtime import parent_python_executable


LOCK_FILENAME = ".pipeline.lock"
STARTUP_AUDIT_FILENAME = "startup_audit.json"
_RUN_DIR_RE = re.compile(r"^md__(?P<date>\d{8})(?P<counter>\d{4})$")


class PipelineLockConflict(RuntimeError):
    """Raised when another pipeline launch already owns the project lock."""

    def __init__(self, run_id: str | None = None):
        super().__init__("已有任务运行")
        self.run_id = run_id


class PipelineLaunchError(RuntimeError):
    """Raised when a child cannot adopt a reserved launch lock."""


def pipeline_command(root: str | Path, *arguments: str) -> list[str]:
    """Use the Web/CLI interpreter for every managed pipeline launch."""
    return [parent_python_executable(), str(Path(root).absolute() / "run_pipeline.py"), *arguments]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lock_path(root: Path) -> Path:
    return root / LOCK_FILENAME


def _read_lock_fd(fd: int) -> dict:
    os.lseek(fd, 0, os.SEEK_SET)
    raw = os.read(fd, 8192).decode("utf-8", errors="replace").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        first_line = raw.splitlines()[0].strip()
        try:
            return {"runner_pid": int(first_line), "legacy": True}
        except ValueError:
            return {}
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, int):
        return {"runner_pid": payload, "legacy": True}
    return {}


def _read_lock_path(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        try:
            return {"runner_pid": int(raw.splitlines()[0].strip()), "legacy": True}
        except (IndexError, ValueError):
            return {}
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, int):
        return {"runner_pid": payload, "legacy": True}
    return {}


def _write_lock_fd(fd: int, payload: dict) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    offset = 0
    while offset < len(encoded):
        offset += os.write(fd, encoded[offset:])
    os.fsync(fd)


def _pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_start_ticks(pid: object) -> int | None:
    """Return Linux process start ticks when available to reject PID reuse."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = raw.rsplit(")", 1)[1].split()
        return int(fields[19])
    except (OSError, IndexError, ValueError):
        return None


def _process_group_is_alive(pgid: object) -> bool:
    """Return whether a managed process group still has signalable members."""
    if not isinstance(pgid, int) or pgid <= 0:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _recorded_pid_is_alive(record: dict, prefix: str) -> bool:
    pid_key = f"{prefix}_pid" if prefix else "pid"
    ticks_key = f"{prefix}_started_ticks" if prefix else "started_ticks"
    pid = record.get(pid_key)
    if not _pid_alive(pid):
        return False
    expected_ticks = record.get(ticks_key)
    if expected_ticks is None:
        return True  # Legacy lock records cannot provide a stronger identity.
    return _pid_start_ticks(pid) == expected_ticks


def _record_is_active(record: dict) -> bool:
    state = record.get("state")
    if state == "reserved":
        return _recorded_pid_is_alive(record, "launcher")
    if _recorded_pid_is_alive(record, "runner") or _recorded_pid_is_alive(record, ""):
        return True
    # Current launches use start_new_session=True, so the runner PID is also
    # the process-group ID.  A surviving GROMACS child keeps the reservation
    # active even if the Python wrapper is being reaped.
    return _process_group_is_alive(record.get("runner_pgid"))


def _allocate_run_dir(root: Path) -> Path:
    """Allocate a run directory while the caller owns the launch lock."""
    runs_dir = root / "md_run"
    runs_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    counter = 1
    for child in runs_dir.iterdir():
        if not child.is_dir():
            continue
        match = _RUN_DIR_RE.fullmatch(child.name)
        if match and match.group("date") == today:
            counter = max(counter, int(match.group("counter")) + 1)

    while True:
        run_dir = runs_dir / f"md__{today}{counter:04d}"
        try:
            run_dir.mkdir()
            break
        except FileExistsError:
            counter += 1

    return run_dir


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


@dataclass
class PipelineLaunchReservation:
    """An exclusively locked run directory handed from UI to child process."""

    root: Path
    run_dir: Path
    fd: int
    token: str

    @property
    def run_id(self) -> str:
        return self.run_dir.name

    def mark_runner_started(self, pid: int) -> None:
        try:
            pgid = os.getpgid(pid)
        except OSError:
            pgid = int(pid)
        _write_lock_fd(self.fd, {
            "version": 1,
            "state": "running",
            "run_id": self.run_id,
            "token": self.token,
            "runner_pid": int(pid),
            "runner_started_ticks": _pid_start_ticks(pid),
            "runner_pgid": int(pgid),
            "updated_at": _now(),
        })

    def detach_parent(self) -> None:
        """Close the parent's descriptor after the child inherited the lock."""
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def release(self) -> None:
        """Release and remove only the lock owned by this reservation."""
        if self.fd < 0:
            return
        try:
            record = _read_lock_fd(self.fd)
            if record.get("token") == self.token:
                _lock_path(self.root).unlink(missing_ok=True)
        finally:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = -1


def reserve_pipeline_launch(root: str | Path) -> PipelineLaunchReservation:
    """Atomically reserve one run and retain its lock until a child adopts it."""
    project_root = Path(root).resolve()
    lock_path = _lock_path(project_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PipelineLockConflict(active_pipeline_run_id(project_root))

        previous = _read_lock_fd(fd)
        # Existing pre-flock lock files from older versions remain authoritative
        # while their recorded owner is alive.
        from willy.run_processes import active_process_run
        remaining_run = active_process_run(project_root)
        if _record_is_active(previous) or remaining_run:
            raise PipelineLockConflict(previous.get("run_id") or remaining_run)

        run_dir = _allocate_run_dir(project_root)
        token = secrets.token_urlsafe(18)
        _write_lock_fd(fd, {
            "version": 1,
            "state": "reserved",
            "run_id": run_dir.name,
            "token": token,
            "launcher_pid": os.getpid(),
            "launcher_started_ticks": _pid_start_ticks(os.getpid()),
            "updated_at": _now(),
        })
        clear_startup_audit(project_root)
        return PipelineLaunchReservation(project_root, run_dir, fd, token)
    except BaseException:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        raise


def reserve_existing_run_launch(root: str | Path, run_id: str) -> PipelineLaunchReservation:
    """Reserve the launch lock for one existing, validated run directory."""
    project_root = Path(root).resolve()
    if not isinstance(run_id, str) or _RUN_DIR_RE.fullmatch(run_id) is None:
        raise PipelineLaunchError("恢复运行标识无效")
    run_dir = (project_root / "md_run" / run_id).resolve()
    if run_dir.parent != (project_root / "md_run").resolve() or not run_dir.is_dir():
        raise PipelineLaunchError("恢复运行目录不存在")

    lock_path = _lock_path(project_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PipelineLockConflict(active_pipeline_run_id(project_root))
        previous = _read_lock_fd(fd)
        from willy.run_processes import active_process_run
        remaining_run = active_process_run(project_root)
        if _record_is_active(previous) or remaining_run:
            raise PipelineLockConflict(previous.get("run_id") or remaining_run)
        token = secrets.token_urlsafe(18)
        _write_lock_fd(fd, {
            "version": 1,
            "state": "reserved",
            "run_id": run_dir.name,
            "token": token,
            "launcher_pid": os.getpid(),
            "launcher_started_ticks": _pid_start_ticks(os.getpid()),
            "updated_at": _now(),
        })
        clear_startup_audit(project_root)
        return PipelineLaunchReservation(project_root, run_dir, fd, token)
    except BaseException:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        raise


def adopt_pipeline_launch(root: str | Path, run_dir: str | Path, fd: int, token: str) -> PipelineLaunchReservation:
    """Validate a lock inherited by the child runner before executing work."""
    project_root = Path(root).resolve()
    directory = Path(run_dir).resolve()
    try:
        record = _read_lock_fd(fd)
    except OSError as exc:
        raise PipelineLaunchError("启动锁不可用") from exc
    if (
        record.get("token") != token
        or record.get("run_id") != directory.name
        or directory.parent != (project_root / "md_run").resolve()
    ):
        raise PipelineLaunchError("启动锁不属于当前运行")
    reservation = PipelineLaunchReservation(project_root, directory, fd, token)
    reservation.mark_runner_started(os.getpid())
    return reservation


def active_pipeline_run_id(root: str | Path) -> str | None:
    """Return the active run ID from the launch lock without exposing process details."""
    project_root = Path(root).resolve()
    record = _read_lock_path(_lock_path(project_root))
    if not _record_is_active(record):
        from willy.run_processes import active_process_run
        return active_process_run(project_root)
    run_id = record.get("run_id")
    return run_id if isinstance(run_id, str) and run_id else None


def pipeline_launch_is_active(root: str | Path) -> bool:
    """Check lock ownership even for a legacy lock without a run ID."""
    project_root = Path(root).resolve()
    from willy.run_processes import active_process_run
    return _record_is_active(_read_lock_path(_lock_path(project_root))) or active_process_run(project_root) is not None


def cleanup_finished_launch(root: str | Path, run_id: str, token: str) -> None:
    """Remove a completed reservation without touching a newer launch lock."""
    project_root = Path(root).resolve()
    lock_path = _lock_path(project_root)
    try:
        fd = os.open(lock_path, os.O_RDWR)
    except OSError:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        record = _read_lock_fd(fd)
        if record.get("run_id") == run_id and record.get("token") == token:
            lock_path.unlink(missing_ok=True)
    except OSError:
        return
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def write_startup_audit(root: str | Path, state: str) -> None:
    """Persist only a public startup outcome, never engine or command output."""
    message = "已有任务运行" if state == "lock_conflict" else "启动失败"
    _atomic_write_json(Path(root).resolve() / STARTUP_AUDIT_FILENAME, {
        "state": state if state in {"lock_conflict", "failed"} else "failed",
        "message": message,
        "updated_at": _now(),
    })


def read_startup_audit(root: str | Path) -> dict:
    """Read the concise public startup outcome for an unbound launch."""
    try:
        payload = json.loads((Path(root).resolve() / STARTUP_AUDIT_FILENAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    state = payload.get("state")
    if state not in {"lock_conflict", "failed"}:
        return {}
    return {"state": state, "message": "已有任务运行" if state == "lock_conflict" else "启动失败"}


def clear_startup_audit(root: str | Path) -> None:
    try:
        (Path(root).resolve() / STARTUP_AUDIT_FILENAME).unlink(missing_ok=True)
    except OSError:
        return
