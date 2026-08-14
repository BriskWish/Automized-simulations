"""Regression coverage for explicit Run Assistant /resume and /fork controls."""

from __future__ import annotations

import json

import pytest

import willy.frontend_api as frontend_api
from willy.run_control import (
    RunControlError,
    parse_run_control_command,
    validate_fork_changes,
)
from willy.run_registry import RunRegistry
from willy.simulation.protocol import default_md_config


def _config() -> dict:
    return {
        "backend": "g16",
        "residues": {"Li": 1},
        "molecules": {"Li": {"charge": 0, "spin": 1}},
        "md": default_md_config(),
        "box": {"target_mass_density_g_cm3": 0.7, "box_size": None, "tolerance": 2.0},
    }


def _aborted_run(tmp_path, *, run_id: str = "md__202608140001", step: int = 8):
    directory = tmp_path / "md_run" / run_id
    directory.mkdir(parents=True)
    (directory / "config.json").write_text(json.dumps(_config()), encoding="utf-8")
    (directory / "Li.gjf").write_text("#p test\n\nLi\n\n0 1\nLi 0 0 0\n", encoding="utf-8")
    (directory / "topol.top").write_text("; parent upstream output\n", encoding="utf-8")
    registry = RunRegistry(tmp_path)
    registry.register_run(directory, backend="g16", total_steps=10)
    registry.record_status(directory, {
        "state": "aborted", "step": step,
        "step_label": "GROMACS 能量最小化", "layer": "simulation",
        "activity": {}, "done_steps": list(range(1, step)),
    }, "run_aborted")
    return registry, directory


class _Reservation:
    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.fd = 19
        self.token = "control-token"
        self.released = False
        self.started_pid = None
        self.detached = False

    def mark_runner_started(self, pid):
        self.started_pid = pid

    def detach_parent(self):
        self.detached = True

    def release(self):
        self.released = True


class _Process:
    pid = 44321

    def wait(self):
        return 0


class _IdleThread:
    def __init__(self, target, daemon):
        self.target = target

    def start(self):
        return None


def test_only_explicit_slash_commands_are_recognized():
    assert parse_run_control_command("请帮我 resume 这个运行") is None
    assert parse_run_control_command("fork 后会怎样？") is None
    assert parse_run_control_command("/resume").kind == "resume"
    fork = parse_run_control_command("/fork md.eq.tau_p=3 md.prod.duration_ns=4")
    assert fork is not None
    assert fork.kind == "fork"
    assert fork.changes[("md", "eq", "tau_p")] == 3


def test_fork_parameter_must_belong_to_stopped_or_later_step():
    command = parse_run_control_command("/fork md.eq.tau_p=3")
    assert command is not None
    accepted = validate_fork_changes(_config(), command.changes, stopped_at=8)
    assert accepted.restart_step == 6
    assert accepted.parameter_paths == ("md.eq.tau_p",)

    too_early = parse_run_control_command("/fork md.dt=0.002")
    assert too_early is not None
    with pytest.raises(RunControlError, match="早于上次停止"):
        validate_fork_changes(_config(), too_early.changes, stopped_at=8)


def test_resume_reuses_same_run_from_last_safe_step_and_audits(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    _registry, directory = _aborted_run(tmp_path)
    reservation = _Reservation(directory)
    spawned = []
    monkeypatch.setattr(frontend_api, "reserve_existing_run_launch", lambda *_args: reservation)
    monkeypatch.setattr(
        frontend_api.subprocess, "Popen",
        lambda args, **kwargs: spawned.append((args, kwargs)) or _Process(),
    )
    monkeypatch.setattr(frontend_api.threading, "Thread", _IdleThread)

    reply = frontend_api.run_assistant_control_command(directory.name, "/resume")

    assert "原 config.json" in reply
    assert reservation.run_dir == directory
    status = RunRegistry(tmp_path).get_run_status(directory.name, reconcile=False)
    assert status["state"] == "retrying"
    assert status["step"] == 8
    assert status["done_steps"] == list(range(1, 8))
    args, kwargs = spawned[0]
    assert args[-2:] == ["--resume-from-step", "8"]
    assert kwargs["pass_fds"] == (19,)
    manifest = RunRegistry(tmp_path)._read_registry_manifest(directory)
    assert manifest["control_history"][-1]["outcome"] == "launched"
    events = (directory / "events.jsonl").read_text(encoding="utf-8")
    assert "resume_intent_accepted" in events
    assert "controlled_retry_started" in events
    assert "resume_launched" in events


def test_orchestrator_binds_controlled_resume_without_reinitializing_workspace(tmp_path, monkeypatch):
    import willy.pipeline_orchestrator as orchestrator_module
    import willy.pipeline_state as state_module

    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    monkeypatch.setattr(orchestrator_module, "ROOT", tmp_path)
    monkeypatch.setattr(state_module, "get_project_root", lambda: tmp_path)
    registry, directory = _aborted_run(tmp_path)
    status = registry.get_run_status(directory.name, reconcile=False)
    status["state"] = "awaiting_confirmation"
    awaiting = registry.compare_and_swap_status(
        directory, expected_revision=status["state_revision"], status=status,
        event_type="resume_intent_accepted",
    )
    status = dict(awaiting)
    status["state"] = "retrying"
    status["step"] = 8
    registry.compare_and_swap_status(
        directory, expected_revision=awaiting["state_revision"], status=status,
        event_type="controlled_retry_started",
    )

    orchestrator = orchestrator_module.PipelineOrchestrator(
        backend="g16", use_llm=False, controlled_resume_step=8,
    )
    config_path = orchestrator._bind_controlled_resume_run(directory)

    assert config_path == directory / "config.json"
    assert orchestrator._run_dir == directory
    assert orchestrator._sm._status.state == "retrying"
    assert orchestrator._sm._status.done_steps == list(range(1, 8))


def test_fork_creates_isolated_child_with_parent_audit_and_modified_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    _registry, parent = _aborted_run(tmp_path)
    child = tmp_path / "md_run" / "md__202608140002"
    child.mkdir(parents=True)
    reservation = _Reservation(child)
    spawned = []
    monkeypatch.setattr(frontend_api, "reserve_pipeline_launch", lambda *_args: reservation)
    monkeypatch.setattr(
        frontend_api.subprocess, "Popen",
        lambda args, **kwargs: spawned.append((args, kwargs)) or _Process(),
    )
    monkeypatch.setattr(frontend_api.threading, "Thread", _IdleThread)

    reply = frontend_api.run_assistant_control_command(parent.name, "/fork md.eq.tau_p=3")

    assert "已创建 fork" in reply
    assert json.loads((parent / "config.json").read_text())["md"]["eq"]["tau_p"] == 2.0
    assert json.loads((child / "config.json").read_text())["md"]["eq"]["tau_p"] == 3
    assert (child / "topol.top").read_text() == "; parent upstream output\n"
    child_manifest = RunRegistry(tmp_path)._read_registry_manifest(child)
    assert child_manifest["parent_run_id"] == parent.name
    assert child_manifest["control_history"][-1]["outcome"] == "launched"
    assert not (child / "events.jsonl").read_text().startswith((parent / "events.jsonl").read_text())
    child_status = RunRegistry(tmp_path).get_run_status(child.name, reconcile=False)
    assert child_status["state"] == "retrying"
    assert child_status["step"] == 6
    assert "fork_intent_accepted" in (child / "events.jsonl").read_text()
    assert "controlled_retry_started" in (child / "events.jsonl").read_text()
    args, _kwargs = spawned[0]
    assert args[-2:] == ["--resume-from-step", "6"]
    parent_manifest = RunRegistry(tmp_path)._read_registry_manifest(parent)
    assert parent_manifest["control_history"][-1]["action"] == "fork"
    assert "fork_accepted" in (parent / "events.jsonl").read_text()


def test_rejected_early_fork_returns_parent_to_awaiting_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    _registry, directory = _aborted_run(tmp_path)

    reply = frontend_api.run_assistant_control_command(directory.name, "/fork md.dt=0.002")

    assert "被拒绝" in reply
    status = RunRegistry(tmp_path).get_run_status(directory.name, reconcile=False)
    assert status["state"] == "awaiting_confirmation"
    assert status["done_steps"] == list(range(1, 8))
    manifest = RunRegistry(tmp_path)._read_registry_manifest(directory)
    assert manifest["control_history"][-1]["outcome"] == "rejected"
    assert "fork_rejected_awaiting" in (directory / "events.jsonl").read_text()
