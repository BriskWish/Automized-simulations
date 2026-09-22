"""Bounded auxiliary GROMACS requests without running scientific software."""

from __future__ import annotations

import io
import json
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from willy.simulation import _gmx_utils, gmx_process
from willy.simulation.manifest import RunLock


def _install_processes(monkeypatch, tmp_path, outcomes, *, cleanup_stuck=False):
    clock = SimpleNamespace(value=0.0)
    processes = []

    class Process:
        def __init__(self, outcome, command):
            self.outcome = outcome
            self.command = command
            self.pid = 10000 + len(processes)
            self.returncode = None
            self.inputs = []
            self.waits = []
            self.stdin = io.StringIO()
            self.stdout = io.StringIO()
            self.stderr = io.StringIO()

        def communicate(self, *, input=None, timeout=None):
            self.inputs.append(input)
            self.waits.append(timeout)
            if self.returncode is not None:
                if cleanup_stuck:
                    raise subprocess.TimeoutExpired(self.command, timeout, output=b"partial")
                return "partial", "timeout evidence"
            if self.outcome in {"success", "failure"}:
                self.returncode = 0 if self.outcome == "success" else 1
                return "output", ""
            if self.outcome == "stop":
                (tmp_path / "stop.request").write_text("stop")
            clock.value += timeout
            raise subprocess.TimeoutExpired(self.command, timeout)

        def wait(self, *, timeout=None):
            if cleanup_stuck:
                raise subprocess.TimeoutExpired(self.command, timeout)
            return self.returncode

    def popen(command, **kwargs):
        assert all(process.returncode is not None for process in processes)
        assert kwargs["start_new_session"] is True
        process = Process(outcomes[len(processes)], command)
        processes.append(process)
        return process

    def send_signal(process, requested_signal):
        assert requested_signal == signal.SIGKILL
        process.returncode = -signal.SIGKILL
        return True

    monkeypatch.setattr(gmx_process, "time", SimpleNamespace(monotonic=lambda: clock.value))
    monkeypatch.setattr(gmx_process.subprocess, "Popen", popen)
    monkeypatch.setattr("willy.process_lifecycle._send_group_signal", send_signal)
    monkeypatch.setattr(_gmx_utils, "require_tool", lambda *_args: SimpleNamespace(executable="gmx"))
    monkeypatch.setattr(_gmx_utils, "build_tool_env", lambda *_args: {})
    return clock, processes


@pytest.mark.parametrize("requested_timeout", [None, 20, 60, 300])
def test_auxiliary_timeout_is_capped_and_attempts_stop_at_three(tmp_path, monkeypatch, requested_timeout):
    clock, processes = _install_processes(monkeypatch, tmp_path, ["timeout"] * 3)

    with pytest.raises(subprocess.TimeoutExpired) as failure:
        _gmx_utils.run_gmx(["grompp"], tmp_path, timeout=requested_timeout)

    assert failure.value.timeout == 20
    assert failure.value.output == "partial"
    assert len(processes) == 3
    assert clock.value == 60
    with RunLock(tmp_path):
        pass
    lifecycle = [json.loads(line) for line in (tmp_path / "process_lifecycle.jsonl").read_text().splitlines()]
    assert len(lifecycle) == 3
    assert all(record["signals"] == ["SIGKILL"] for record in lifecycle)
    events = [json.loads(line) for line in (tmp_path / "logs/structured.jsonl").read_text().splitlines()]
    assert [event["event_code"] for event in events] == ["process_started", "process_finished"] * 3
    assert all(event["outcome"] == "timed_out" for event in events[1::2])


def test_shorter_timeout_is_preserved(tmp_path, monkeypatch):
    clock, processes = _install_processes(monkeypatch, tmp_path, ["timeout"] * 3)

    with pytest.raises(subprocess.TimeoutExpired) as failure:
        _gmx_utils.run_gmx(["energy"], tmp_path, timeout=2)

    assert failure.value.timeout == 2
    assert len(processes) == 3
    assert clock.value == 6


def test_timeout_retry_can_succeed_and_replays_stdin_once_per_process(tmp_path, monkeypatch):
    clock, processes = _install_processes(monkeypatch, tmp_path, ["timeout", "success"])

    result = _gmx_utils.run_gmx(["energy"], tmp_path, input_text="Temperature\n0\n")

    assert result.returncode == 0
    assert len(processes) == 2
    assert clock.value == 20
    assert processes[0].inputs[0] == processes[1].inputs[0] == "Temperature\n0\n"
    assert all(value is None for value in processes[0].inputs[1:])


@pytest.mark.parametrize("outcome,returncode", [("success", 0), ("failure", 1)])
def test_completed_requests_are_not_retried(tmp_path, monkeypatch, outcome, returncode):
    _, processes = _install_processes(monkeypatch, tmp_path, [outcome])

    result = _gmx_utils.run_gmx(["grompp"], tmp_path)

    assert result.returncode == returncode
    assert len(processes) == 1


def test_user_stop_terminates_without_retry(tmp_path, monkeypatch):
    _, processes = _install_processes(monkeypatch, tmp_path, ["stop"])

    result = _gmx_utils.run_gmx(["trjconv"], tmp_path)

    assert result.returncode != 0
    assert len(processes) == 1
    lifecycle = json.loads((tmp_path / "process_lifecycle.jsonl").read_text())
    assert lifecycle["reason"] == "stop_requested"


def test_existing_stop_request_prevents_process_launch(tmp_path, monkeypatch):
    _, processes = _install_processes(monkeypatch, tmp_path, [])
    (tmp_path / "stop.request").write_text("stop")

    result = _gmx_utils.run_gmx(["editconf"], tmp_path)

    assert result.returncode == 130
    assert not processes


def test_unreaped_child_blocks_further_attempts(tmp_path, monkeypatch):
    _, processes = _install_processes(monkeypatch, tmp_path, ["timeout"], cleanup_stuck=True)

    with pytest.raises(OSError, match="禁止继续重试"):
        _gmx_utils.run_gmx(["check"], tmp_path, timeout=0)

    assert len(processes) == 1
    assert processes[0].stdout.closed
    assert processes[0].stderr.closed


@pytest.mark.parametrize("timeout", [-1, float("nan"), float("inf")])
def test_invalid_timeouts_never_start_processes(tmp_path, monkeypatch, timeout):
    _, processes = _install_processes(monkeypatch, tmp_path, [])

    with pytest.raises(ValueError):
        _gmx_utils.run_gmx(["check"], tmp_path, timeout=timeout)

    assert not processes


def test_mdrun_keeps_its_original_timeout_and_executor(tmp_path, monkeypatch):
    _, processes = _install_processes(monkeypatch, tmp_path, [])
    calls = []

    def run_mdrun(command, cwd, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(_gmx_utils, "_run_mdrun_with_live_eta", run_mdrun)
    result = _gmx_utils.run_gmx(["mdrun", "-deffnm", "eq"], tmp_path, timeout=7200)

    assert result.returncode == 0
    assert calls[0]["timeout"] == 7200
    assert not processes


def test_real_auxiliary_process_timeout_is_bounded_and_reaped(tmp_path):
    started_at = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        gmx_process.run_gmx_auxiliary(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path, timeout=0.05,
        )

    assert time.monotonic() - started_at < 5
    records = [json.loads(line) for line in (tmp_path / "process_lifecycle.jsonl").read_text().splitlines()]
    assert len(records) == 3
    assert all(record["returncode"] == -signal.SIGKILL for record in records)


def test_box_conversion_uses_shared_twenty_second_policy(tmp_path, monkeypatch):
    from willy.simulation import box

    source = tmp_path / "solute.gro"
    source.write_text("fixture\n1\natom\n3 3 3\n")
    output = tmp_path / "solute.pdb"
    calls = []

    def convert(command, **kwargs):
        calls.append(kwargs)
        output.write_text("ATOM      1  C   SOL A   1       1.000   1.000   1.000\nEND\n")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(box, "require_tool", lambda *_args: SimpleNamespace(executable="gmx"))
    monkeypatch.setattr(box, "build_tool_env", lambda *_args: {})
    monkeypatch.setattr(box, "run_gmx_auxiliary", convert)

    assert box._gro_to_pdb(str(source), str(output)) == output
    assert calls[0]["timeout"] == 20
    assert calls[0]["run_dir"] == tmp_path


def test_grompp_warning_retry_timeout_remains_a_step_failure(tmp_path, monkeypatch):
    from tests.test_simulation_execution import _write_stage_inputs

    _write_stage_inputs(tmp_path)
    calls = []

    def run_gmx(args, cwd, **kwargs):
        calls.append(args)
        assert kwargs["timeout"] == 20
        if "-maxwarn" in args:
            raise subprocess.TimeoutExpired(args, 20)
        return subprocess.CompletedProcess(args, 1, "", (
            "WARNING 1 [file topol.top, line 1]:\n"
            "System has non-zero total charge: 0.080015\n"
            "You are using Ewald electrostatics in a system with net charge.\n"
            "There was 1 WARNING\n"
        ))

    monkeypatch.setattr(_gmx_utils, "run_gmx", run_gmx)
    result = _gmx_utils.grompp_and_mdrun("em", tmp_path)

    assert not result.success
    assert "20s" in result.error.message
    assert len(calls) == 2
    assert all(command[0] == "grompp" for command in calls)
