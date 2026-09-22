"""Real tiny subprocesses prove the scientific start gate is fail closed."""

import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from willy.controlled_launch import (
    LAUNCH_GATE_ENV, cancel_unstarted_process, handoff_controlled_process,
)


def _gated_child(tmp_path):
    read_fd, write_fd = os.pipe()
    marker = tmp_path / "science-started"
    command = (
        "from pathlib import Path; "
        "from willy.controlled_launch import wait_for_launch_permission; "
        "wait_for_launch_permission(); "
        f"Path({str(marker)!r}).write_text('started')"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", command], pass_fds=(read_fd,), start_new_session=True,
        env={**os.environ, LAUNCH_GATE_ENV: str(read_fd), "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    process._willy_launch_gate = (read_fd, write_fd)
    return process, marker


def test_work_cannot_start_before_bookkeeping_commit(tmp_path):
    process, marker = _gated_child(tmp_path)
    calls = []

    def record(name):
        assert not marker.exists()
        calls.append(name)

    reservation = SimpleNamespace(
        mark_runner_started=lambda _pid: record("mark"), detach_parent=lambda: record("detach"),
    )
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=0.1)
        handoff_controlled_process(process, reservation, tmp_path, lambda: record("watcher"))
        assert process.wait(timeout=5) == 0
        assert marker.read_text() == "started"
        assert calls == ["mark", "watcher", "detach"]
    finally:
        cancel_unstarted_process(process)


@pytest.mark.parametrize("failure", ["mark", "pid", "watcher", "detach"])
def test_post_spawn_failure_reaps_child_without_scientific_work(tmp_path, failure):
    process, marker = _gated_child(tmp_path)

    def fail_at(name):
        if failure == name:
            raise OSError("injected bookkeeping failure")

    if failure == "pid":
        (tmp_path / ".pipeline.pid").mkdir()
    reservation = SimpleNamespace(
        mark_runner_started=lambda _pid: fail_at("mark"), detach_parent=lambda: fail_at("detach"),
    )
    try:
        with pytest.raises(OSError):
            handoff_controlled_process(process, reservation, tmp_path, lambda: fail_at("watcher"))
        assert process.poll() is not None
        assert not marker.exists()
        assert process._willy_launch_gate == ()
    finally:
        cancel_unstarted_process(process)
