import json
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from willy.process_lifecycle import (
    ProcessTerminationController,
    record_process_lifecycle,
    run_managed_command,
)


class _Process:
    pid = 1234

    def __init__(self):
        self.returncode = None
        self.fallback_signals = []

    def poll(self):
        return self.returncode

    def send_signal(self, value):
        self.fallback_signals.append(value)


def test_termination_escalates_the_process_group(monkeypatch):
    sent = []
    monkeypatch.setattr("willy.process_lifecycle.os.killpg", lambda pid, sig: sent.append((pid, sig)))
    process = _Process()
    controller = ProcessTerminationController(process, interrupt_grace_s=3, terminate_grace_s=2)

    controller.request("timeout", now=0)
    controller.tick(now=3)
    controller.tick(now=5)

    assert sent == [(1234, signal.SIGINT), (1234, signal.SIGTERM), (1234, signal.SIGKILL)]
    assert controller.phase == "killed"
    assert controller.reason == "timeout"


def test_lifecycle_record_excludes_command_paths(tmp_path, monkeypatch):
    monkeypatch.setattr("willy.process_lifecycle.os.killpg", lambda *_args: None)
    process = _Process()
    controller = ProcessTerminationController(process)
    controller.request("stop_requested", now=0)
    process.returncode = 130
    controller.finish()
    record_process_lifecycle(
        tmp_path,
        controller,
        command=["/private/gmx/bin/gmx", "mdrun", "/private/run/topol.top"],
        returncode=130,
        checkpoint_exists=True,
    )
    payload = json.loads((tmp_path / "process_lifecycle.jsonl").read_text().splitlines()[0])
    assert payload["command"] == ["gmx", "mdrun"]
    assert payload["checkpoint_exists"] is True


def test_managed_command_honors_stop_request_and_records_audit(tmp_path):
    result = run_managed_command(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        run_dir=tmp_path,
        stop_check=lambda: True,
        interrupt_grace_s=0.01,
        terminate_grace_s=0.01,
        poll_interval_s=0.01,
    )

    assert result.returncode != 0
    payload = json.loads((tmp_path / "process_lifecycle.jsonl").read_text().splitlines()[0])
    assert payload["reason"] == "stop_requested"
    assert payload["signals"] == ["SIGINT"]
    assert payload["command"] == [Path(sys.executable).name, "-c"]
    structured = [
        json.loads(line)
        for line in (tmp_path / "logs" / "structured.jsonl").read_text().splitlines()
    ]
    assert [row["event_code"] for row in structured] == [
        "process_started", "process_finished",
    ]
    assert structured[-1]["outcome"] == "failed"


def test_managed_command_timeout_terminates_and_records_audit(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        run_managed_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            run_dir=tmp_path,
            timeout=0.01,
            interrupt_grace_s=0.01,
            terminate_grace_s=0.01,
            poll_interval_s=0.01,
        )

    payload = json.loads((tmp_path / "process_lifecycle.jsonl").read_text().splitlines()[0])
    assert payload["reason"] == "timeout"
    assert payload["signals"] == ["SIGINT"]
    assert payload["returncode"] != 0
    structured = [
        json.loads(line)
        for line in (tmp_path / "logs" / "structured.jsonl").read_text().splitlines()
    ]
    assert structured[-1]["event_code"] == "process_finished"
    assert structured[-1]["outcome"] == "timed_out"


def test_managed_command_emits_low_frequency_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr("willy.process_lifecycle.STRUCTURED_HEARTBEAT_INTERVAL_S", 0.0)
    with pytest.raises(subprocess.TimeoutExpired):
        run_managed_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            run_dir=tmp_path,
            timeout=0.03,
            interrupt_grace_s=0.01,
            terminate_grace_s=0.01,
            poll_interval_s=0.01,
        )

    structured = [
        json.loads(line)
        for line in (tmp_path / "logs" / "structured.jsonl").read_text().splitlines()
    ]
    assert structured[0]["event_code"] == "process_started"
    assert any(row["event_code"] == "process_heartbeat" for row in structured)
    assert structured[-1]["event_code"] == "process_finished"
