"""Contracts for the primary assistant-ui entry point."""

import base64
import json
from pathlib import Path

import app
from willy.run_registry import RunRegistry


RUN_ID = "md__202608260001"


def _record_run(root: Path, run_id: str, *, state: str, step: int) -> None:
    run_dir = root / "md_run" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text("{}", encoding="utf-8")
    registry = RunRegistry(root)
    registry.register_run(run_dir, backend="g16", total_steps=10)
    registry.record_status(run_dir, {"state": state, "step": step}, "status_updated")


def test_app2_health_reports_the_independent_surface():
    assert app.health() == {"status": "ok", "surface": "assistant-ui"}


def test_app2_proposal_surface_shows_a_thinking_buffer_while_a_request_runs():
    source = (app.ROOT / "frontend" / "src" / "main.jsx").read_text(encoding="utf-8")

    assert "state.thread.isRunning" in source
    assert "正在思考..." in source


def test_app2_run_surface_marks_only_current_unfinished_status_as_loading():
    source = (app.ROOT / "frontend" / "src" / "main.jsx").read_text(encoding="utf-8")

    assert "runActivityActive" in source
    assert "CircleCheck" in source
    assert 'eventKind === "status" && eventActive' in source
    assert 'eventKind === "completion_artifacts"' in source


def test_app2_projects_initial_run_as_preparing_and_counts_the_active_step(tmp_path, monkeypatch):
    run_dir = tmp_path / "md_run" / RUN_ID
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text("{}", encoding="utf-8")
    registry = RunRegistry(tmp_path)
    registry.register_run(run_dir, backend="g16", total_steps=10)
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)

    preparing = app._public_snapshot(RUN_ID)

    assert preparing["state"] == "preparing"
    assert preparing["phase"] == "准备中"
    assert preparing["progress"] == "1/10"
    assert preparing["live_summary"] == "工程正在准备中，将从第 1 步开始。"
    assert app.list_local_runs()["runs"] == [{
        "run_id": RUN_ID,
        "display_name": "",
        "state": "preparing",
        "updated_at": RunRegistry(tmp_path).list_runs()[0]["updated_at"],
    }]

    registry.record_status(run_dir, {
        "state": "running", "step": 10, "done_steps": list(range(1, 10)),
    }, "status_updated")

    running = app._public_snapshot(RUN_ID)

    assert running["state"] == "running"
    assert running["progress"] == "10/10"


def test_app2_status_panel_labels_preparing_state():
    source = (app.ROOT / "frontend" / "src" / "main.jsx").read_text(encoding="utf-8")

    assert 'const preparing = String(data.state).toLowerCase() === "preparing"' in source
    assert 'preparing ? "准备中"' in source


def test_app2_serves_the_existing_about_qr_asset():
    response = app.official_account_qr()

    assert response.path == app.OFFICIAL_ACCOUNT_QR
    assert response.media_type == "image/jpeg"


def test_app2_chat_persists_assistant_ui_messages_in_its_proposal_workspace(tmp_path, monkeypatch):
    calls = []

    def fake_chat(message, history, pending_plan, *, proposal_workspace_id=None):
        calls.append((message, history, pending_plan, proposal_workspace_id))
        yield "", [*history, {"role": "assistant", "content": "方案已生成"}], None, {"id": "plan"}, None, None

    monkeypatch.setattr(app, "chat", fake_chat)
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)
    workspace = app.create_proposal_workspace()["workspace"]

    reply = app.assistant_chat({
        "proposalId": workspace["workspace_id"],
        "messages": [
            {"role": "assistant", "content": [{"type": "text", "text": "你好"}]},
            {"role": "user", "content": [{"type": "text", "text": "Li 1"}]},
        ],
    })

    plan_id = reply["proposalId"]
    assert plan_id.startswith("plan__")
    assert calls == [("Li 1", [], None, workspace["workspace_id"])]
    assert reply["text"] == "方案已生成"
    assert app.get_workspace(tmp_path, plan_id)["pending_plan"] == {"id": "plan"}


def test_app2_chat_rejects_non_list_messages():
    try:
        app.assistant_chat({"messages": "invalid"})
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
    else:
        raise AssertionError("non-list messages must be rejected")


def test_app2_declares_separate_proposal_and_run_assistant_routes():
    routes = {
        (route.path, method)
        for route in app.app.routes
        for method in getattr(route, "methods", set())
    }

    assert {
        ("/api/proposals/default", "POST"),
        ("/api/proposals", "POST"),
        ("/api/proposals/{proposal_id}", "GET"),
        ("/api/proposal/chat", "POST"),
        ("/api/runs", "GET"),
        ("/api/runs/{run_id}/chat", "GET"),
        ("/api/runs/{run_id}/chat", "POST"),
        ("/api/runs/{run_id}/pending-action/confirm", "POST"),
        ("/api/runs/{run_id}/display-name", "PATCH"),
        ("/api/local-items/{item_id}", "DELETE"),
        ("/api/runs/{run_id}/logs", "GET"),
        ("/api/runs/{run_id}/stop", "POST"),
        ("/api/runs/{run_id}/updates", "GET"),
        ("/api/proposal/upload", "POST"),
        ("/api/visualization", "GET"),
    }.issubset(routes)


def test_app2_pending_action_control_is_visible_only_while_awaiting_confirmation():
    source = (app.ROOT / "frontend" / "src" / "main.jsx").read_text(encoding="utf-8")

    assert "function PendingActionConfirmation" in source
    assert 'state === "awaiting_confirmation"' in source
    assert "/pending-action/confirm" in source
    assert "action_id: action.action_id" in source
    assert "state_revision: action.state_revision" in source
    assert "config_fingerprint: action.config_fingerprint" in source


def test_app2_default_proposal_creates_or_restores_a_workspace_only_when_idle(tmp_path, monkeypatch):
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)
    monkeypatch.setattr(app, "pipeline_launch_is_active", lambda _root: False)

    created = app.ensure_default_proposal()
    restored = app.ensure_default_proposal()

    assert created["workspace"]["workspace_id"].startswith("temp__")
    assert restored["workspace"]["workspace_id"] == created["workspace"]["workspace_id"]
    monkeypatch.setattr(app, "pipeline_launch_is_active", lambda _root: True)
    try:
        app.ensure_default_proposal()
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("active pipeline must not receive a default proposal workspace")


def test_app2_lists_registered_local_task_directories_without_paths(tmp_path, monkeypatch):
    older = "md__202608260001"
    newer = "md__202608260002"
    _record_run(tmp_path, older, state="aborted", step=5)
    _record_run(tmp_path, newer, state="running", step=6)
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)
    monkeypatch.setattr(app.frontend_api, "get_active_run_id", lambda: newer)

    response = app.list_local_runs()

    assert response["active_run_id"] == newer
    assert {item["run_id"] for item in response["runs"]} == {older, newer}
    assert all(set(item) == {"run_id", "display_name", "state", "updated_at"} for item in response["runs"])
    assert response["plans"] == []
    assert response["temps"] == []
    assert str(tmp_path) not in str(response)


def test_app2_display_name_is_frontend_only_and_preserves_run_state(tmp_path, monkeypatch):
    _record_run(tmp_path, RUN_ID, state="aborted", step=5)
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)

    run_dir = tmp_path / "md_run" / RUN_ID
    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.is_file():
        manifest_path = run_dir / "manifest.json"
    manifest_before = manifest_path.read_text(encoding="utf-8")
    events_before = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    status_before = (run_dir / "status.json").read_text(encoding="utf-8")
    response = app.set_run_display_name(RUN_ID, {"display_name": "Li_eq_retry"})
    mapping = json.loads((tmp_path / "md_run" / ".willy_display_names.json").read_text(encoding="utf-8"))
    listed = app.list_local_runs()

    assert response == {"run_id": RUN_ID, "display_name": "Li_eq_retry", "changed": True, "state": "aborted"}
    assert run_dir.is_dir()
    assert not (tmp_path / "md_run" / "Li_eq_retry").exists()
    assert mapping == {"schema_version": 1, "names": {RUN_ID: "Li_eq_retry"}}
    assert manifest_path.read_text(encoding="utf-8") == manifest_before
    assert (run_dir / "events.jsonl").read_text(encoding="utf-8") == events_before
    assert (run_dir / "status.json").read_text(encoding="utf-8") == status_before
    assert RunRegistry(tmp_path).list_runs()[0]["display_name"] == ""
    assert listed["runs"] == [{
        "run_id": RUN_ID,
        "display_name": "Li_eq_retry",
        "state": "aborted",
        "updated_at": RunRegistry(tmp_path).get_run_status(RUN_ID, reconcile=False)["updated_at"],
    }]


def test_app2_lists_status_snapshot_instead_of_a_stale_unknown_index(tmp_path, monkeypatch):
    _record_run(tmp_path, RUN_ID, state="done", step=10)
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)
    index_path = tmp_path / "md_run" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["runs"][0]["state"] = "unknown"
    index_path.write_text(json.dumps(index), encoding="utf-8")

    response = app.list_local_runs()

    assert response["runs"][0]["state"] == "done"


def test_app2_display_name_rejects_non_ascii_label(tmp_path, monkeypatch):
    _record_run(tmp_path, RUN_ID, state="aborted", step=5)
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)

    try:
        app.set_run_display_name(RUN_ID, {"display_name": "锂体系"})
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
    else:
        raise AssertionError("non-ASCII display names must be rejected")


def test_app2_logs_expose_only_the_selected_run_audit_records(tmp_path, monkeypatch):
    _record_run(tmp_path, RUN_ID, state="aborted", step=5)
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)

    response = app.run_logs(RUN_ID)

    assert response["run_id"] == RUN_ID
    assert response["manifest"]["filename"] == "run_manifest.json"
    assert json.loads(response["manifest"]["content"])["run_id"] == RUN_ID
    assert response["events"]["filename"] == "events.jsonl"
    assert "status_updated" in response["events"]["content"]
    assert response["manifest"]["truncated"] is False
    assert response["events"]["truncated"] is False
    assert str(tmp_path) not in str(response)


def test_app2_proposal_upload_uses_the_existing_normalization_boundary(tmp_path, monkeypatch):
    from willy.proposal_workspace import save_workspace_conversation

    source = "! B3LYP 6-311+G(d,p) Opt\n\nLi source\n\n* xyz 1 1\nLi 0 0 0\n*\n"
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)
    workspace = app.create_proposal_workspace()["workspace"]
    plan = save_workspace_conversation(
        tmp_path,
        workspace["workspace_id"],
        messages=[],
        pending_plan={"id": "stale"},
    )

    response = app.proposal_upload({
        "proposalId": plan["workspace_id"],
        "filename": "Li+.inp",
        "content_base64": base64.b64encode(source.encode("utf-8")).decode("ascii"),
    })

    assert response["text"] == "已上传Li+.inp，仅保留坐标、电荷、自旋。"
    assert response["structure"] == {"name": "Li", "format": ".inp", "charge": 1, "spin": 1, "atom_count": 1}
    assert response["proposalId"] == plan["workspace_id"]
    assert response["messages"] == [{
        "role": "user",
        "content": "已上传Li+.inp，仅保留坐标、电荷、自旋。",
    }]
    assert (tmp_path / "struct" / "Li.inp").is_file()
    persisted = app.get_workspace(tmp_path, plan["workspace_id"])
    assert persisted["pending_plan"] is None
    assert persisted["messages"] == response["messages"]


def test_app2_upload_feedback_uses_user_bubbles_and_never_starts_a_chat_run():
    source = (app.ROOT / "frontend" / "src" / "main.jsx").read_text(encoding="utf-8")
    styles = (app.ROOT / "frontend" / "src" / "styles.css").read_text(encoding="utf-8")

    assert '<div className="avatar message-user-avatar" aria-label="用户 ZL">ZL</div>' in source
    assert "function UserBubbleAvatar" not in source
    assert "startRun: false" in source
    assert "aui.thread.reset(threadHistoryMessages(payload.messages))" in source
    assert "upload-notice" not in source
    assert ".user-message .message-copy { border-right: 3px solid #6c8997; }" in styles


def test_app2_stop_requires_the_selected_active_run(monkeypatch):
    snapshot = {"run_id": RUN_ID, "state": "stopping"}
    monkeypatch.setattr(app, "_safe_run_id", lambda run_id: RUN_ID)
    monkeypatch.setattr(app.frontend_api, "get_active_run_id", lambda: RUN_ID)
    monkeypatch.setattr(app.frontend_api, "stop_pipeline", lambda clean=False: "已请求安全停止。")
    monkeypatch.setattr(app, "_public_snapshot", lambda run_id: dict(snapshot))

    response = app.stop_run(RUN_ID)

    assert response == {"run_id": RUN_ID, "message": "已请求安全停止。", "snapshot": snapshot}


def test_app2_proposal_and_run_assistant_histories_are_isolated(tmp_path, monkeypatch):
    proposal_calls = []
    run_calls = []
    persisted = []
    snapshot = {
        "run_id": RUN_ID,
        "state": "await",
        "step": None,
        "phase": "—",
        "progress": "0/10",
        "done_steps": [],
        "status_event_id": f"{RUN_ID}:status:await:none:none:none",
        "live_summary": "等待操作。",
    }

    def fake_proposal_chat(message, history, pending_plan, *, proposal_workspace_id=None):
        proposal_calls.append((message, history, pending_plan, proposal_workspace_id))
        yield "", [*history, {"role": "assistant", "content": "方案回复"}], None, {"id": "plan"}, None, None

    monkeypatch.setattr(app, "chat", fake_proposal_chat)
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)
    monkeypatch.setattr(app, "_safe_run_id", lambda run_id: RUN_ID)
    monkeypatch.setattr(app, "_public_snapshot", lambda run_id: dict(snapshot, run_id=run_id))
    monkeypatch.setattr(app.frontend_api, "get_run_assistant_history", lambda run_id: [])
    monkeypatch.setattr(
        app.frontend_api,
        "save_run_assistant_history",
        lambda run_id, history: persisted.append((run_id, history)) or True,
    )
    monkeypatch.setattr(app.frontend_api, "run_assistant_control_command", lambda run_id, message: None)
    monkeypatch.setattr(app.frontend_api, "chat_run_assistant", lambda run_id, message, history: run_calls.append((run_id, message, history)) or "运行回复")

    proposal_workspace = app.create_proposal_workspace()["workspace"]
    proposal = app.proposal_chat({
        "proposalId": proposal_workspace["workspace_id"],
        "messages": [{"role": "user", "content": [{"type": "text", "text": "建立 Li 体系"}]}],
    })
    run = app.run_chat(RUN_ID, {"message": "当前进度？"})

    assert proposal["text"] == "方案回复"
    assert proposal_calls == [("建立 Li 体系", [], None, proposal_workspace["workspace_id"])]
    assert run["text"] == "运行回复"
    assert run_calls[0][0:2] == (RUN_ID, "当前进度？")
    assert all(message["content"] != "建立 Li 体系" for message in run_calls[0][2])
    assert persisted[-1][0] == RUN_ID


def test_app2_run_chat_confirms_only_the_current_awaiting_action(monkeypatch):
    action = {
        "run_id": RUN_ID,
        "action_id": "eq-confirm-1",
        "state_revision": 17,
        "config_fingerprint": "a" * 64,
        "summary": "降低时间步长后重新验收 EQ。",
        "restart_step": 9,
        "adjustments": [],
    }
    state = {"value": "awaiting_confirmation"}
    confirmations = []
    control_calls = []
    llm_calls = []
    persisted = []

    def snapshot(run_id):
        waiting = state["value"] == "awaiting_confirmation"
        return {
            "run_id": run_id,
            "state": state["value"],
            "step": 9,
            "phase": "GROMACS 三点式退火平衡",
            "progress": "8/10",
            "done_steps": list(range(1, 9)),
            "status_event_id": f"{run_id}:status:{state['value']}:9",
            "live_summary": "等待用户确认调整方案。" if waiting else "正在准备受控重跑。",
            "pending_action": dict(action) if waiting else None,
        }

    def confirm(action_id, run_id, *, state_revision, config_fingerprint):
        confirmations.append((action_id, run_id, state_revision, config_fingerprint))
        state["value"] = "retrying"
        return "已确认方案，当前进入重跑准备。"

    monkeypatch.setattr(app, "_safe_run_id", lambda run_id: RUN_ID)
    monkeypatch.setattr(app, "_public_snapshot", snapshot)
    monkeypatch.setattr(
        app.frontend_api,
        "get_pending_action",
        lambda run_id: dict(action) if state["value"] == "awaiting_confirmation" else None,
    )
    monkeypatch.setattr(app.frontend_api, "confirm_pending_action", confirm)
    monkeypatch.setattr(app.frontend_api, "get_run_assistant_history", lambda run_id: [])
    monkeypatch.setattr(
        app.frontend_api,
        "save_run_assistant_history",
        lambda run_id, history: persisted.append((run_id, history)) or True,
    )
    monkeypatch.setattr(
        app.frontend_api,
        "run_assistant_control_command",
        lambda run_id, message: control_calls.append((run_id, message)) or None,
    )
    monkeypatch.setattr(
        app.frontend_api,
        "chat_run_assistant",
        lambda run_id, message, history: llm_calls.append((run_id, message)) or "只读回复",
    )

    approved = app.run_chat(RUN_ID, {"message": "确认调参"})
    ordinary = app.run_chat(RUN_ID, {"message": "确认调参"})

    assert approved["text"] == "已确认方案，当前进入重跑准备。"
    assert confirmations == [("eq-confirm-1", RUN_ID, 17, "a" * 64)]
    assert control_calls == [(RUN_ID, "确认调参")]
    assert ordinary["text"] == "只读回复"
    assert llm_calls == [(RUN_ID, "确认调参")]
    assert persisted[-1][0] == RUN_ID


def test_app2_pending_action_confirmation_endpoint_rejects_stale_browser_fields(monkeypatch):
    action = {
        "run_id": RUN_ID,
        "action_id": "eq-confirm-2",
        "state_revision": 19,
        "config_fingerprint": "b" * 64,
        "summary": "延长 EQ 保温段后重新验收。",
        "restart_step": 9,
        "adjustments": [],
    }
    calls = []
    snapshot = {
        "run_id": RUN_ID,
        "state": "awaiting_confirmation",
        "step": 9,
        "phase": "GROMACS 三点式退火平衡",
        "progress": "8/10",
        "done_steps": list(range(1, 9)),
        "status_event_id": f"{RUN_ID}:status:awaiting_confirmation:9",
        "live_summary": "等待用户确认调整方案。",
        "pending_action": action,
    }

    monkeypatch.setattr(app, "_safe_run_id", lambda run_id: RUN_ID)
    monkeypatch.setattr(app, "_public_snapshot", lambda run_id: dict(snapshot))
    monkeypatch.setattr(app.frontend_api, "get_pending_action", lambda run_id: dict(action))
    monkeypatch.setattr(app.frontend_api, "get_run_assistant_history", lambda run_id: [])
    monkeypatch.setattr(app.frontend_api, "save_run_assistant_history", lambda run_id, history: True)
    monkeypatch.setattr(
        app.frontend_api,
        "confirm_pending_action",
        lambda action_id, run_id, *, state_revision, config_fingerprint: calls.append(
            (action_id, run_id, state_revision, config_fingerprint)
        ) or "已确认方案，当前进入重跑准备。",
    )

    response = app.confirm_run_pending_action(RUN_ID, {
        "action_id": action["action_id"],
        "state_revision": action["state_revision"],
        "config_fingerprint": action["config_fingerprint"],
    })
    try:
        app.confirm_run_pending_action(RUN_ID, {
            "action_id": action["action_id"],
            "state_revision": action["state_revision"] - 1,
            "config_fingerprint": action["config_fingerprint"],
        })
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("stale browser confirmation must be rejected")

    assert response["text"] == "已确认方案，当前进入重跑准备。"
    assert calls == [("eq-confirm-2", RUN_ID, 19, "b" * 64)]


def test_app2_proposal_chat_switches_to_run_only_when_a_new_run_appears(tmp_path, monkeypatch):

    def fake_chat(message, history, pending_plan, *, proposal_workspace_id=None):
        yield "", [*history, {"role": "assistant", "content": "方案回复"}], None, None, None, None

    monkeypatch.setattr(app, "chat", fake_chat)
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)
    messages = [{"role": "user", "content": [{"type": "text", "text": "解释当前方案"}]}]
    proposal_workspace = app.create_proposal_workspace()["workspace"]

    monkeypatch.setattr(app.frontend_api, "latest_run_id", lambda: RUN_ID)
    unchanged = app.proposal_chat({"proposalId": proposal_workspace["workspace_id"], "messages": messages})

    run_ids = iter((RUN_ID, "md__202608260003"))
    monkeypatch.setattr(app.frontend_api, "latest_run_id", lambda: next(run_ids))
    created = app.proposal_chat({"proposalId": proposal_workspace["workspace_id"], "messages": messages})

    assert "runId" not in unchanged
    assert created["runId"] == "md__202608260003"


def test_app2_run_proposal_history_is_persistent_and_new_candidate_moves_to_plan(tmp_path, monkeypatch):
    from willy.proposal_workspace import save_run_conversation

    _record_run(tmp_path, RUN_ID, state="aborted", step=5)
    source_history = [
        {"role": "user", "content": "原工程的问题"},
        {"role": "assistant", "content": "原工程的答复"},
    ]
    save_run_conversation(tmp_path, RUN_ID, messages=source_history)
    calls = []

    def fake_chat(message, history, pending_plan, *, proposal_workspace_id=None):
        calls.append((message, history, pending_plan, proposal_workspace_id))
        yield "", [*history, {"role": "user", "content": message}, {"role": "assistant", "content": "新方案已生成"}], None, {"config": {"backend": "g16", "residues": {"Li": 1}}}, None, None

    monkeypatch.setattr(app, "chat", fake_chat)
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)

    response = app.proposal_chat({
        "proposalId": RUN_ID,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "为该历史工程建立新方案"}]}],
    })
    run_history = app.get_proposal_workspace(RUN_ID)
    new_plan = app.get_proposal_workspace(response["proposalId"])

    assert response["proposalId"].startswith("plan__")
    assert calls == [("为该历史工程建立新方案", source_history, None, None)]
    assert run_history["workspace"]["kind"] == "run"
    assert run_history["messages"] == source_history
    assert new_plan["workspace"]["kind"] == "plan"
    assert new_plan["messages"] == [
        {"role": "user", "content": "为该历史工程建立新方案"},
        {"role": "assistant", "content": "新方案已生成"},
    ]


def test_app2_run_status_history_keeps_one_current_spinner_and_completed_steps(tmp_path, monkeypatch):
    _record_run(tmp_path, RUN_ID, state="running", step=4)
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)
    running = {
        "run_id": RUN_ID,
        "state": "running",
        "step": 4,
        "done_steps": [1, 3],
        "status_event_id": f"{RUN_ID}:status:running:4",
        "live_summary": "正在执行第 4 步。",
    }
    complete = {
        "run_id": RUN_ID,
        "state": "done",
        "step": 10,
        "done_steps": list(range(1, 11)),
        "status_event_id": f"{RUN_ID}:status:done:10",
        "live_summary": "全流程完成。",
    }

    during = app._merge_status_into_history([], running)
    after = app._merge_status_into_history(during, complete)
    current = [message for message in after if message.get("_run_assistant_event_kind") == "status"]
    artifact_notice = next(
        message for message in after
        if message.get("_run_assistant_event_kind") == "completion_artifacts"
    )
    reloaded = app._merge_status_into_history(after, complete)

    assert len([message for message in during if message.get("_run_assistant_event_kind") == "status"]) == 1
    assert during[-1]["_run_assistant_event_active"] is True
    assert len(current) == 1
    assert current[0]["content"] == "全流程完成。"
    assert current[0]["_run_assistant_event_active"] is False
    assert len([message for message in after if message.get("_run_assistant_event_kind") == "step_completed"]) == 10
    assert artifact_notice["content"] == (
        f"全流程完成\n\n当前工程文件保存在：../Willy/md_run/{RUN_ID} 中。\n\n"
        "核心生产文件：prod.gro、prod.xtc、prod.trr、prod.edr 等。"
    )
    assert [message.get("_run_assistant_event_kind") for message in after[-2:]] == [
        "status", "completion_artifacts",
    ]
    assert len([
        message for message in reloaded
        if message.get("_run_assistant_event_kind") == "completion_artifacts"
    ]) == 1

    incomplete = dict(complete, done_steps=list(range(1, 10)))
    assert not [
        message for message in app._merge_status_into_history([], incomplete)
        if message.get("_run_assistant_event_kind") == "completion_artifacts"
    ]


def test_app2_pending_adjustment_is_persisted_below_current_engineering_status(tmp_path, monkeypatch):
    _record_run(tmp_path, RUN_ID, state="awaiting_confirmation", step=9)
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)
    snapshot = {
        "run_id": RUN_ID,
        "state": "awaiting_confirmation",
        "step": 9,
        "done_steps": list(range(1, 9)),
        "status_event_id": f"{RUN_ID}:status:awaiting_confirmation:9:action-1",
        "live_summary": "等待用户确认调整方案。",
        "pending_action": {
            "action_id": "action-1",
            "summary": "降低时间步长并重新验收 EQ。",
            "step_label": "GROMACS 三点式退火平衡",
            "restart_step": 9,
            "adjustments": [{
                "name": "时间步长",
                "before": "0.001 ps",
                "after": "0.0005 ps",
                "purpose": "稳定积分",
                "value": "must-not-leak",
            }],
            "recovery_plan": {
                "problem": "EQ 末态温度统计未通过。",
                "current_step_retry": {
                    "applicable": True,
                    "options": [{
                        "summary": "减小时间步长后重新验收。",
                        "restart_step": 9,
                        "adjustments": [{
                            "name": "时间步长",
                            "before": "0.001 ps",
                            "after": "0.0005 ps",
                            "purpose": "稳定积分",
                        }],
                        "evidence": ["最终温度窗口未通过验收"],
                    }],
                },
                "upstream_retry": {
                    "applicable": False,
                    "summary": "当前没有建盒问题证据。",
                    "evidence": ["未检测到真空区"],
                },
                "evidence": ["EQ 验收失败", "运行配置仍保持冻结"],
                "risk_level": "high",
                "manual_review_required": True,
            },
        },
    }

    history = app._merge_status_into_history([], snapshot)
    app._save_run_history(RUN_ID, history)
    reloaded = app._merge_status_into_history(
        app.frontend_api.get_run_assistant_history(RUN_ID),
        snapshot,
    )
    kinds = [message.get("_run_assistant_event_kind") for message in reloaded]
    proposal = next(message for message in reloaded if message.get("_run_assistant_event_kind") == "pending_action")

    assert kinds[-2:] == ["status", "pending_action"]
    assert kinds.count("pending_action") == 1
    assert "降低时间步长并重新验收 EQ。" in proposal["content"]
    assert "问题：" in proposal["content"]
    assert "1. 当前步调参重试" in proposal["content"]
    assert "2. 打回前序流程重试" in proposal["content"]
    assert "证据：" in proposal["content"]
    assert "高风险科学协议变更" in proposal["content"]
    assert "时间步长: 0.001 ps -> 0.0005 ps（稳定积分）" in proposal["content"]
    assert "must-not-leak" not in proposal["content"]


def test_app2_unknown_recovery_request_is_persisted_as_a_safe_error_turn(tmp_path, monkeypatch):
    _record_run(tmp_path, RUN_ID, state="escalated", step=9)
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)
    snapshot = {
        "run_id": RUN_ID,
        "state": "escalated",
        "step": 9,
        "done_steps": list(range(1, 9)),
        "status_event_id": f"{RUN_ID}:status:escalated:9:none:error-1",
        "live_summary": "自动处理未完成。",
        "error_event": {
            "event_id": f"{RUN_ID}:error:1",
            "content": "<span class=\"pipeline-status-indicator\">×</span>未知错误；可申请联网检索并人工审核。",
        },
    }

    history = app._merge_status_into_history([], snapshot)
    error = next(message for message in history if message.get("_run_assistant_event_kind") == "error")

    assert "联网检索" in error["content"]
    assert "<span" not in error["content"]


def test_app2_delete_local_task_requires_plan_confirmation_and_cleans_run_index(tmp_path, monkeypatch):
    from willy.proposal_workspace import save_workspace_conversation

    _record_run(tmp_path, RUN_ID, state="done", step=10)
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)
    monkeypatch.setattr(app, "pipeline_launch_is_active", lambda _root: False)
    temp = app.create_proposal_workspace()["workspace"]
    plan = save_workspace_conversation(
        tmp_path,
        temp["workspace_id"],
        messages=[],
        pending_plan={"config": {"backend": "g16", "residues": {"Li": 1}}},
    )

    try:
        app.delete_local_task_item(plan["workspace_id"], {})
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("plan deletion must require confirmation")

    assert app.delete_local_task_item(plan["workspace_id"], {"confirm_plan": True})["deleted"]["kind"] == "plan"
    assert app.delete_local_task_item(RUN_ID, {})["deleted"] == {"item_id": RUN_ID, "kind": "run"}
    assert RunRegistry(tmp_path).list_runs() == []


def test_app2_run_updates_expose_one_completion_notice_per_completed_step(monkeypatch):
    snapshot = {
        "run_id": RUN_ID,
        "state": "running",
        "step": 4,
        "phase": "拓扑参数生成",
        "progress": "2/10",
        "done_steps": [1, 3],
        "status_event_id": f"{RUN_ID}:status:running:4:none:none",
        "live_summary": "正在执行第 4 步。",
    }
    monkeypatch.setattr(app, "_safe_run_id", lambda run_id: RUN_ID)
    monkeypatch.setattr(app, "_public_snapshot", lambda run_id: dict(snapshot, run_id=run_id))
    monkeypatch.setattr(app.frontend_api, "get_run_assistant_history", lambda run_id: [])
    monkeypatch.setattr(app.frontend_api, "save_run_assistant_history", lambda run_id, history: True)

    update = app.run_updates(RUN_ID)
    unchanged = app.run_updates(RUN_ID, after=update["cursor"])

    assert update["run_id"] == RUN_ID
    assert update["cursor"] == snapshot["status_event_id"]
    assert [event["kind"] for event in update["events"]] == [
        "step_completed", "step_completed", "status",
    ]
    assert [event["content"] for event in update["events"][:2]] == [
        "第 1 步「结构优化」已完成。",
        "第 3 步「RESP 电荷计算」已完成。",
    ]
    assert unchanged["events"] == []


def test_app2_visualization_passes_validated_viewer_controls(monkeypatch):
    render_calls = []
    monkeypatch.setattr(app, "_selected_run_id", lambda run_id: RUN_ID)
    monkeypatch.setattr(app.frontend_api, "get_run_visualization_run_choices", lambda: [RUN_ID])
    monkeypatch.setattr(app.frontend_api, "get_run_visualization_file_choices", lambda run_id: ["prod.pdb"])
    monkeypatch.setattr(
        app.frontend_api,
        "render_run_visualization_html",
        lambda artifact, sphere_scale, stick_radius, background, *, run_id: render_calls.append(
            (artifact, sphere_scale, stick_radius, background, run_id)
        ) or "<iframe />",
    )
    monkeypatch.setattr(
        app.frontend_api,
        "render_run_visualization_legend_html",
        lambda artifact, *, run_id: "<div>legend</div>",
    )

    response = app.visualization(
        run_id=RUN_ID,
        artifact="prod.pdb",
        sphere_scale="0.45",
        stick_radius="0.18",
        background="silver",
    )

    assert response["sphere_scale"] == 0.45
    assert response["stick_radius"] == 0.18
    assert response["background"] == "silver"
    assert render_calls == [("prod.pdb", 0.45, 0.18, "silver", RUN_ID)]


def test_app2_visualization_rejects_invalid_viewer_controls():
    for parameter, value in (("球体大小", "nan"), ("球体大小", "0.70"), ("棍宽度", "invalid"), ("棍宽度", "0.41")):
        try:
            app._viewer_control_value(
                value,
                name=parameter,
                default=0.35 if parameter == "球体大小" else 0.22,
                value_range=(0.15, 0.65) if parameter == "球体大小" else (0.08, 0.40),
            )
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 422
        else:
            raise AssertionError(f"{parameter} must reject {value}")

    for value in ("blue", "#ffffff", ""):
        try:
            app._viewer_background_value(value)
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 422
        else:
            raise AssertionError(f"background must reject {value}")
