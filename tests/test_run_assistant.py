"""Run Assistant Phase A contract tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import json
import os
from datetime import datetime, timedelta, timezone
import pytest

from willy.errors import ErrorKind, StepError, StepResult
from willy.pipeline_state import PipelineStateMachine, State
from willy.run_registry import RunRegistry, RunRegistryError
from willy.simulation.mdrun_eta import MDRUN_ETA_FILENAME
from willy.toolist_run import RUN_TOOLS, TOOL_META, handle_run_tool_call
from willy.simulation.protocol import default_md_config


def _make_run(tmp_path: Path) -> tuple[RunRegistry, Path]:
    run_dir = tmp_path / "md_run" / "md_demo_202607310001"
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps({"backend": "g16", "residues": {"Li": 1}}))
    (run_dir / "Li.gjf").write_text("# test\n\nLi\n\n0 1\nLi 0 0 0\n")
    registry = RunRegistry(tmp_path)
    registry.register_run(run_dir, backend="g16", total_steps=7)
    return registry, run_dir


def _status() -> dict:
    return {
        "state": "running", "step": 2, "step_label": "SP + mol2", "layer": "quantum",
        "error": "", "error_kind": "", "agent": "", "retry_n": 0, "retry_max": 0,
        "actions": [], "escalation": {}, "started_at": "2026-07-31T00:00:00+00:00",
        "updated_at": "2026-07-31T00:01:00+00:00", "total_steps": 7, "done_steps": [1],
        "activity": {
            "tool": "G16", "operation": "单点计算与 mol2 转换",
            "target_type": "molecule", "target": "Li", "current": 1, "total": 1,
        },
        "extra": {"run_id": "md_demo_202607310001"},
    }


class TestRunRegistry:
    def test_register_run_hashes_reused_quantum_intermediate(self, tmp_path):
        run_dir = tmp_path / "md_run" / "md_reuse_202608010001"
        run_dir.mkdir(parents=True)
        (run_dir / "config.json").write_text(json.dumps({"backend": "g16"}))
        (run_dir / "Li.fchk").write_text("optimized geometry")

        registry = RunRegistry(tmp_path)
        registry.register_run(run_dir, backend="g16", total_steps=10)

        manifest = json.loads((run_dir / "manifest.json").read_text())
        assert manifest["input_files"] == [
            {"path": "Li.fchk", "sha256": manifest["input_files"][0]["sha256"]},
        ]

    def test_register_status_result_and_artifact_contract(self, tmp_path):
        registry, run_dir = _make_run(tmp_path)
        artifact = run_dir / "Li.mol2"
        artifact.write_text("@<TRIPOS>MOLECULE\nLi\n")

        registry.record_status(run_dir, _status(), "step_started")
        registry.record_step_result(
            run_dir,
            StepResult("sp_g16", 2, True, outputs={"mol2": str(artifact)}),
            label="SP + mol2",
        )

        manifest = json.loads((run_dir / "manifest.json").read_text())
        assert manifest["run_id"] == run_dir.name
        assert manifest["config_sha256"]
        assert manifest["input_files"] == [{"path": "Li.gjf", "sha256": manifest["input_files"][0]["sha256"]}]
        assert manifest["artifacts"][0]["path"] == "Li.mol2"
        assert registry.get_run_status(run_dir.name)["step"] == 2
        assert registry.list_runs()[0]["run_id"] == run_dir.name
        report = registry.get_step_report(run_dir.name, 2)
        assert report["completed"] is False
        assert "events" not in report

    def test_registry_rejects_path_traversal_and_log_secrets_are_redacted(self, tmp_path):
        registry, run_dir = _make_run(tmp_path)
        (run_dir / "engine.log").write_text("API_KEY=super-secret\ncomplete\n")

        with pytest.raises(RunRegistryError):
            registry.resolve_run_id("../outside")
        with pytest.raises(RunRegistryError):
            registry.tail_log(run_dir.name, "../secret.log")
        log = registry.tail_log(run_dir.name, "engine.log")
        assert "super-secret" not in log["content"]
        assert "[REDACTED]" in log["content"]

    def test_box_parameters_expose_only_audited_geometry(self, tmp_path):
        registry, run_dir = _make_run(tmp_path)
        (run_dir / "md_manifest.json").write_text(json.dumps({
            "box_attempts": [{
                "time": "2026-08-04T00:00:00+00:00",
                "box_strategy": "target_mass_density",
                "target_mass_density_g_cm3": 1.0,
                "actual_box_vectors_angstrom": [20.0, 20.0, 20.0],
                "box_volume_nm3": 8.0,
                "actual_mass_density_g_cm3": 1.0,
                "private_path": "/not/exposed",
            }],
        }))

        record = registry.get_box_parameters(run_dir.name)

        assert record["status"] == "available"
        assert record["box"]["actual_box_vectors_angstrom"] == [20.0, 20.0, 20.0]
        assert "private_path" not in record["box"]

    def test_status_observer_is_best_effort_and_event_typed(self, tmp_path, monkeypatch):
        import willy.pipeline_state as pstate

        monkeypatch.setattr(pstate, "get_project_root", lambda: tmp_path)
        observed = []
        state = PipelineStateMachine(on_update=lambda status, event: observed.append((status.state, event)))
        state.transition(State.RUNNING)
        state.set_step(1, "Structure", "quantum")
        state.mark_done(1)

        assert observed[-2:] == [("running", "step_started"), ("running", "step_succeeded")]

    def test_public_status_events_and_reports_never_include_engine_output(self, tmp_path):
        registry, run_dir = _make_run(tmp_path)
        status = _status()
        status.update({
            "error": "G16 结构优化失败：Li\n原因：SCF 未收敛",
            "error_kind": ErrorKind.SCF_NOT_CONVERGED.value,
        })
        registry.record_status(run_dir, status, "step_failed")
        registry.record_step_result(
            run_dir,
            StepResult(
                "struct_g16", 1, False,
                error=StepError(
                    ErrorKind.SCF_NOT_CONVERGED,
                    "Li: /tmp/run/Li.log failed: g16 --bad",
                    raw_output="stderr /tmp/run/Li.log --bad-command",
                ),
                target_type="molecule", target="Li",
            ),
            label="G16 结构优化",
            activity={
                "tool": "G16", "operation": "结构优化",
                "target_type": "molecule", "target": "Li", "current": 1, "total": 2,
            },
        )

        persisted = (run_dir / "status.json").read_text()
        events = (run_dir / "events.jsonl").read_text()
        report = registry.get_step_report(run_dir.name, 1)
        explanation = registry.explain_error(run_dir.name)
        for payload in (persisted, events, json.dumps(report), json.dumps(explanation)):
            assert "stderr" not in payload
            assert "/tmp/run" not in payload
            assert "--bad-command" not in payload
        assert report["activity"]["target"] == "Li"
        assert report["error"] == "G16 结构优化失败：Li\n原因：SCF 未收敛"

    def test_active_eq_manifest_reconciles_a_status_that_wrongly_entered_prod(self, tmp_path):
        """Only public state changes when a live EQ contradicts a PROD status."""
        registry, run_dir = _make_run(tmp_path)
        eq_log = run_dir / "eq.log"
        eq_log.write_text("EQ is still running\n")
        (run_dir / "md_manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "stages": {"eq": {"status": "running", "attempt": 2}},
        }))
        registry.record_status(run_dir, {
            **_status(),
            "state": "retrying",
            "step": 10,
            "step_label": "GROMACS 生产模拟",
            "layer": "simulation",
            "error": "GROMACS 输入预处理失败：prod\n原因：恢复条件与原运行不一致",
            "error_kind": "recovery_conflict",
            "done_steps": list(range(1, 10)),
            "activity": {
                "tool": "GROMACS", "operation": "输入预处理",
                "target_type": "stage", "target": "prod", "current": 0, "total": 2,
            },
        }, "step_failed")
        before_revision = json.loads((run_dir / "status.json").read_text())["state_revision"]

        status = registry.get_run_status(run_dir.name)

        assert status["state"] == "retrying"
        assert status["step"] == 9
        assert status["done_steps"] == list(range(1, 9))
        assert status["activity"]["target"] == "eq"
        assert status["error"] == ""
        assert status["state_revision"] == before_revision + 1
        assert eq_log.read_text() == "EQ is still running\n"
        events = (run_dir / "events.jsonl").read_text()
        assert "stage_status_reconciled" in events

    def test_stale_eq_manifest_does_not_reopen_a_stopped_run_as_live(self, tmp_path):
        """A stale manifest alone cannot overwrite the public terminal path."""
        registry, run_dir = _make_run(tmp_path)
        eq_log = run_dir / "eq.log"
        eq_log.write_text("old EQ output\n")
        stale = (datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp()
        os.utime(eq_log, (stale, stale))
        (run_dir / "md_manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "stages": {"eq": {"status": "running", "attempt": 2}},
        }))
        registry.record_status(run_dir, {
            **_status(),
            "state": "retrying",
            "step": 10,
            "done_steps": list(range(1, 10)),
        }, "step_failed")

        status = registry.get_run_status(run_dir.name)

        assert status["step"] == 10
        assert status["done_steps"] == list(range(1, 10))
        assert "stage_status_reconciled" not in (run_dir / "events.jsonl").read_text()


class TestRunTools:
    def test_tools_are_all_read_only_and_server_binds_run_id(self, tmp_path):
        registry, run_dir = _make_run(tmp_path)
        registry.record_status(run_dir, _status(), "step_started")

        assert {tool["function"]["name"] for tool in RUN_TOOLS} == set(TOOL_META)
        assert all(not meta["mutating"] for meta in TOOL_META.values())
        response = json.loads(handle_run_tool_call(
            "tools_get_status_run", {}, selected_run_id=run_dir.name, registry=registry,
        ))
        assert response["ok"] is True
        assert response["status"]["run_id"] == run_dir.name

        names = {tool["function"]["name"] for tool in RUN_TOOLS}
        assert "tools_tail_log_run" not in names

    def test_environment_tool_returns_only_redacted_capabilities(self, tmp_path):
        registry, run_dir = _make_run(tmp_path)
        registry.record_environment_report(run_dir, {
            "gmx": {
                "tool_id": "gmx", "label": "GROMACS", "status": "available",
                "source": "willy_env", "reason": "", "version": None,
            },
        })

        response = json.loads(handle_run_tool_call(
            "tools_get_environment_run", {}, selected_run_id=run_dir.name, registry=registry,
        ))

        assert response == {
            "ok": True,
            "environment": {
                "run_id": run_dir.name,
                "capabilities": {"gmx": {"status": "available", "source": "willy_env"}},
            },
        }

    def test_box_tool_returns_audited_geometry(self, tmp_path):
        registry, run_dir = _make_run(tmp_path)
        (run_dir / "md_manifest.json").write_text(json.dumps({
            "box_attempts": [{
                "actual_box_vectors_angstrom": [20.0, 20.0, 20.0],
                "actual_mass_density_g_cm3": 1.0,
            }],
        }))

        response = json.loads(handle_run_tool_call(
            "tools_get_box_parameters_run", {}, selected_run_id=run_dir.name, registry=registry,
        ))

        assert response["ok"] is True
        assert response["box_parameters"]["box"]["actual_mass_density_g_cm3"] == 1.0

    def test_md_eta_tool_returns_only_validated_gromacs_prediction(self, tmp_path):
        registry, run_dir = _make_run(tmp_path)
        (run_dir / MDRUN_ETA_FILENAME).write_text(json.dumps({
            "schema_version": 1,
            "stage": "prod",
            "status": "available",
            "step": 420000,
            "remaining_seconds": 123.5,
            "observed_at": "2026-08-02T08:00:00+00:00",
            "estimated_end_at": "2026-08-02T08:02:03.500000+00:00",
            "raw_output": "/private/run/prod.log API_KEY=not-public",
        }))

        response = json.loads(handle_run_tool_call(
            "tools_get_md_eta_run", {}, selected_run_id=run_dir.name, registry=registry,
        ))

        eta = response["md_eta"]
        assert response["ok"] is True
        assert {key: eta[key] for key in (
            "run_id", "status", "stage", "step", "remaining_seconds",
            "observed_at", "eta_observed_at", "estimated_end_at",
        )} == {
            "run_id": run_dir.name,
            "status": "available",
            "stage": "prod",
            "step": 420000,
            "remaining_seconds": 123.5,
            "observed_at": "2026-08-02T08:00:00+00:00",
            "eta_observed_at": "2026-08-02T08:00:00+00:00",
            "estimated_end_at": "2026-08-02T08:02:03.500000+00:00",
        }
        assert all("本地时间（UTC" in eta[key] for key in (
            "observed_at_local", "eta_observed_at_local", "estimated_end_at_local",
        ))
        assert "raw_output" not in json.dumps(response)

    def test_runtime_heartbeat_updates_status_without_appending_events(self, tmp_path):
        registry, run_dir = _make_run(tmp_path)
        status = _status()
        registry.record_status(run_dir, status, "step_started")
        events_before = (run_dir / "events.jsonl").read_text().splitlines()
        status["updated_at"] = "2026-07-31T00:02:00+00:00"

        registry.record_status(run_dir, status, "runtime_heartbeat")

        events = (run_dir / "events.jsonl").read_text().splitlines()
        persisted = registry.get_run_status(run_dir.name)
        assert events == events_before
        assert persisted["updated_at"] == "2026-07-31T00:02:00+00:00"
        assert "本地时间（UTC" in persisted["updated_at_local"]

    def test_md_eta_tool_rejects_invalid_snapshot_and_handles_no_snapshot(self, tmp_path):
        registry, run_dir = _make_run(tmp_path)

        no_snapshot = json.loads(handle_run_tool_call(
            "tools_get_md_eta_run", {}, selected_run_id=run_dir.name, registry=registry,
        ))
        assert no_snapshot["md_eta"]["status"] == "unavailable"

        (run_dir / MDRUN_ETA_FILENAME).write_text(json.dumps({
            "stage": "prod", "status": "available", "step": 12,
            "remaining_seconds": 9, "observed_at": "not-a-time",
            "estimated_end_at": "/private/never-a-time",
        }))
        invalid_snapshot = json.loads(handle_run_tool_call(
            "tools_get_md_eta_run", {}, selected_run_id=run_dir.name, registry=registry,
        ))
        assert invalid_snapshot["md_eta"] == {
            "run_id": run_dir.name,
            "status": "unavailable",
            "reason": "GROMACS 预计结束时间快照无效",
        }

    def test_list_runs_does_not_create_an_index_for_legacy_runs(self, tmp_path):
        run_dir = tmp_path / "md_run" / "md_legacy_202607310001"
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text(json.dumps({
            "run_id": run_dir.name, "created_at": "2026-07-31T00:00:00+00:00",
            "updated_at": "2026-07-31T00:00:00+00:00", "backend": "g16", "total_steps": 7,
        }))
        registry = RunRegistry(tmp_path)

        response = json.loads(handle_run_tool_call(
            "tools_list_runs", {}, selected_run_id=None, registry=registry,
        ))

        assert response["runs"][0]["run_id"] == run_dir.name
        assert not (tmp_path / "md_run" / "index.json").exists()


class TestRunAssistant:
    def test_assistant_answers_status_fast_path_without_llm(self, tmp_path):
        from willy.agent_run import RunAssistant

        registry, run_dir = _make_run(tmp_path)
        registry.record_status(run_dir, _status(), "step_started")
        client = MagicMock()

        answer = RunAssistant(client, registry=registry).answer(
            "现在到哪一步？", selected_run_id=run_dir.name,
        )

        assert "状态：运行中" in answer
        assert "当前工序：G16 单点计算与 mol2 转换" in answer
        client.chat.completions.create.assert_not_called()

    def test_assistant_labels_waiting_confirmation_without_falling_back_to_unknown(self):
        from willy.agent_run import RunAssistant

        lines = RunAssistant._status_lines({"state": "awaiting_confirmation"})

        assert lines == ["状态：等待用户确认调整方案"]

    def test_assistant_can_call_only_bound_read_tool_for_complex_question(self, tmp_path):
        from willy.agent_run import RunAssistant

        registry, run_dir = _make_run(tmp_path)
        registry.record_status(run_dir, _status(), "step_started")
        tool_call = SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(name="tools_get_status_run", arguments="{}"),
        )
        first = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tool_call]))])
        second = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="该运行正在执行第二步。", tool_calls=None))])
        client = MagicMock()
        client.chat.completions.create.side_effect = [first, second]

        answer = RunAssistant(client, registry=registry).answer(
            "为什么当前运行无法继续？", selected_run_id=run_dir.name,
        )

        assert answer == "该运行正在执行第二步。"
        assert client.chat.completions.create.call_args_list[0].kwargs["tools"] == RUN_TOOLS

    def test_assistant_limits_history_to_six_compact_turns(self, tmp_path):
        from willy.agent_run import MAX_HISTORY_CHARS, RunAssistant

        registry, run_dir = _make_run(tmp_path)
        registry.record_status(run_dir, _status(), "step_started")
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="需要更多证据。", tool_calls=None))]
        )
        history = [
            {"role": "user" if index % 2 == 0 else "assistant", "content": "x" * (MAX_HISTORY_CHARS + 50)}
            for index in range(10)
        ]

        answer = RunAssistant(client, registry=registry).answer(
            "请分析当前运行是否可以继续。", selected_run_id=run_dir.name, history=history,
        )

        messages = client.chat.completions.create.call_args.kwargs["messages"]
        prior_messages = messages[1:-1]
        assert answer == "需要更多证据。"
        assert len(prior_messages) == 6
        assert all(len(item["content"]) == MAX_HISTORY_CHARS for item in prior_messages)

    def test_assistant_degrades_complex_question_to_local_facts_without_llm(self, tmp_path):
        from willy.agent_run import RunAssistant

        registry, run_dir = _make_run(tmp_path)
        registry.record_status(run_dir, _status(), "step_started")

        answer = RunAssistant(None, registry=registry).answer(
            "为什么当前运行无法继续？", selected_run_id=run_dir.name,
        )

        assert "运行解释服务暂时不可用" in answer
        assert "状态：运行中" in answer
        assert "预计结束时间：尚不可估算" in answer

    def test_assistant_prefers_local_eta_and_waiting_heartbeat_times(self):
        from willy.agent_run import RunAssistant

        available = RunAssistant._eta_lines({
            "status": "available", "stage": "eq", "remaining_seconds": 120,
            "estimated_end_at": "2026-08-02T13:31:16+00:00",
            "estimated_end_at_local": "2026-08-02 21:31:16 本地时间（UTC+08:00）",
            "eta_observed_at": "2026-08-02T13:29:16+00:00",
            "eta_observed_at_local": "2026-08-02 21:29:16 本地时间（UTC+08:00）",
            "observed_at": "2026-08-02T13:30:16+00:00",
            "observed_at_local": "2026-08-02 21:30:16 本地时间（UTC+08:00）",
        })
        waiting = RunAssistant._eta_lines({
            "status": "waiting", "stage": "eq",
            "observed_at_local": "2026-08-02 21:37:00 本地时间（UTC+08:00）",
            "last_progress_at_local": "2026-08-02 21:36:55 本地时间（UTC+08:00）",
            "last_progress_step": 1553500,
        })

        assert "21:31:16" in available[0]
        assert "13:31:16+00:00" not in "\n".join(available)
        assert "最近运行心跳：2026-08-02 21:37:00" in "\n".join(waiting)
        assert "最近记录步骤 1553500" in "\n".join(waiting)


class TestOrchestratorRunRegistration:
    def test_prepared_workspace_registers_run_and_binds_state(self, tmp_path, monkeypatch):
        import willy.pipeline_orchestrator as po
        import willy.pipeline_state as pstate

        root = tmp_path / "project"
        (root / "struct").mkdir(parents=True)
        (root / "struct" / "Li.gjf").write_text("geometry")
        (root / "config.json").write_text(json.dumps({
            "molecules": {"Li": {"charge": 0}},
            "residues": {"Li": 1},
            "md": default_md_config(),
        }))
        monkeypatch.setattr(po, "ROOT", root)
        monkeypatch.setattr(pstate, "get_project_root", lambda: root)

        orchestrator = po.PipelineOrchestrator(use_llm=False)
        run_dir = root / "md_run" / "md_demo_202607310002"
        orchestrator._prepare_run_directory(run_dir)

        assert (run_dir / "manifest.json").is_file()
        environment = json.loads((run_dir / "environment_report.json").read_text())
        assert environment["schema_version"] == 1
        assert "executable" not in json.dumps(environment)
        status = json.loads((run_dir / "status.json").read_text())
        assert status["run_id"] == run_dir.name
        assert json.loads((root / "md_run" / "index.json").read_text())["runs"][0]["run_id"] == run_dir.name
