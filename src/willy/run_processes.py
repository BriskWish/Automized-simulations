"""Persist process-group identities independently of the pipeline wrapper."""

from pathlib import Path
import json
import os
import signal
import subprocess
import time

from willy.run_store import run_transaction


OWNERS_FILE = ".process_owners.json"


def _active(record: dict) -> bool:
    from willy.pipeline_launch import _pid_start_ticks, _process_group_is_alive

    pid = record.get("pid")
    if type(pid) is not int or pid <= 0:
        return True
    current = _pid_start_ticks(pid)
    expected = record.get("started_ticks")
    if expected is not None and current is not None and current != expected:
        return False
    return _process_group_is_alive(pid)


def track_process(directory: Path, process) -> None:
    from willy.pipeline_launch import _pid_start_ticks

    pid = getattr(process, "pid", None)
    if type(pid) is not int or pid <= 0:
        return
    try:
        with run_transaction(directory) as store:
            records = store.read_json(OWNERS_FILE) if (directory / OWNERS_FILE).exists() else {}
            if not isinstance(records, dict) or any(not isinstance(value, dict) for value in records.values()):
                raise ValueError("进程登记损坏，禁止覆盖原记录")
            records = {key: value for key, value in records.items() if _active(value)}
            records[str(pid)] = {"pid": pid, "started_ticks": _pid_start_ticks(pid)}
            store.write_json(OWNERS_FILE, records)
    except (OSError, ValueError):
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise


def has_live_processes(directory: Path) -> bool:
    path = directory / OWNERS_FILE
    try:
        if not path.exists():
            return False
        records = json.loads(path.read_text())
        return not isinstance(records, dict) or any(not isinstance(record, dict) or _active(record) for record in records.values())
    except (OSError, ValueError):
        return True


def terminate_tracked_process_groups(directory: Path, *, term_grace_s: float = 0.5) -> list[int]:
    """Terminate only still-matching process groups recorded for one run."""
    path = directory / OWNERS_FILE
    try:
        records = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("进程登记无法读取") from exc
    if not isinstance(records, dict) or any(not isinstance(record, dict) for record in records.values()):
        raise ValueError("进程登记损坏")

    terminated: list[int] = []
    for signal_value in (signal.SIGTERM, signal.SIGKILL):
        live_groups = [
            record["pid"]
            for record in records.values()
            if type(record.get("pid")) is int and record["pid"] > 0 and _active(record)
        ]
        for process_group in live_groups:
            try:
                os.killpg(process_group, signal_value)
                if process_group not in terminated:
                    terminated.append(process_group)
            except ProcessLookupError:
                continue
        if signal_value == signal.SIGTERM and live_groups:
            time.sleep(max(0.0, term_grace_s))
    return terminated


def active_process_run(root: Path) -> str | None:
    for path in (root / "md_run").glob("md__*/" + OWNERS_FILE):
        if path.parent.is_symlink() or path.is_symlink() or has_live_processes(path.parent):
            return path.parent.name
    return None
