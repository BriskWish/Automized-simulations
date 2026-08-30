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


def test_app2_serves_the_existing_about_qr_asset():
    response = app.official_account_qr()

    assert response.path == app.OFFICIAL_ACCOUNT_QR
    assert response.media_type == "image/jpeg"


def test_app2_chat_adapts_assistant_ui_messages_to_the_existing_agent(monkeypatch):
    calls = []

    def fake_chat(message, history, pending_plan):
        calls.append((message, history, pending_plan))
        yield "", [*history, {"role": "assistant", "content": "方案已生成"}], None, {"id": "plan"}, None, None

    monkeypatch.setattr(app, "chat", fake_chat)

    reply = app.assistant_chat({
        "threadId": "test-thread",
        "messages": [
            {"role": "assistant", "content": [{"type": "text", "text": "你好"}]},
            {"role": "user", "content": [{"type": "text", "text": "Li 1"}]},
        ],
    })

    assert calls == [("Li 1", [{"role": "assistant", "content": "你好"}], None)]
    assert reply["text"] == "方案已生成"
    assert app._threads["test-thread"]["pending_plan"] == {"id": "plan"}


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
        ("/api/proposal/chat", "POST"),
        ("/api/runs", "GET"),
        ("/api/runs/{run_id}/chat", "GET"),
        ("/api/runs/{run_id}/chat", "POST"),
        ("/api/runs/{run_id}/display-name", "PATCH"),
        ("/api/runs/{run_id}/logs", "GET"),
        ("/api/runs/{run_id}/stop", "POST"),
        ("/api/runs/{run_id}/updates", "GET"),
        ("/api/proposal/upload", "POST"),
        ("/api/visualization", "GET"),
    }.issubset(routes)


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
    source = "! B3LYP 6-311+G(d,p) Opt\n\nLi source\n\n* xyz 1 1\nLi 0 0 0\n*\n"
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)
    app._threads.clear()
    app._threads["proposal"] = {"pending_plan": {"id": "stale"}}

    response = app.proposal_upload({
        "filename": "Li+.inp",
        "content_base64": base64.b64encode(source.encode("utf-8")).decode("ascii"),
    })

    assert response["text"] == "已上传Li+.inp，仅保留坐标、电荷、自旋。"
    assert response["structure"] == {"name": "Li", "format": ".inp", "charge": 1, "spin": 1, "atom_count": 1}
    assert (tmp_path / "struct" / "Li.inp").is_file()
    assert app._threads["proposal"]["pending_plan"] is None


def test_app2_stop_requires_the_selected_active_run(monkeypatch):
    snapshot = {"run_id": RUN_ID, "state": "stopping"}
    monkeypatch.setattr(app, "_safe_run_id", lambda run_id: RUN_ID)
    monkeypatch.setattr(app.frontend_api, "get_active_run_id", lambda: RUN_ID)
    monkeypatch.setattr(app.frontend_api, "stop_pipeline", lambda clean=False: "已请求安全停止。")
    monkeypatch.setattr(app, "_public_snapshot", lambda run_id: dict(snapshot))

    response = app.stop_run(RUN_ID)

    assert response == {"run_id": RUN_ID, "message": "已请求安全停止。", "snapshot": snapshot}


def test_app2_proposal_and_run_assistant_histories_are_isolated(monkeypatch):
    app._threads.clear()
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

    def fake_proposal_chat(message, history, pending_plan):
        proposal_calls.append((message, history, pending_plan))
        yield "", [*history, {"role": "assistant", "content": "方案回复"}], None, {"id": "plan"}, None, None

    monkeypatch.setattr(app, "chat", fake_proposal_chat)
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

    proposal = app.proposal_chat({
        "threadId": "proposal-thread",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "建立 Li 体系"}]}],
    })
    run = app.run_chat(RUN_ID, {"message": "当前进度？"})

    assert proposal["text"] == "方案回复"
    assert proposal_calls == [("建立 Li 体系", [], None)]
    assert run["text"] == "运行回复"
    assert run_calls[0][0:2] == (RUN_ID, "当前进度？")
    assert all(message["content"] != "建立 Li 体系" for message in run_calls[0][2])
    assert persisted[-1][0] == RUN_ID


def test_app2_proposal_chat_switches_to_run_only_when_a_new_run_appears(monkeypatch):
    app._threads.clear()

    def fake_chat(message, history, pending_plan):
        yield "", [*history, {"role": "assistant", "content": "方案回复"}], None, None, None, None

    monkeypatch.setattr(app, "chat", fake_chat)
    messages = [{"role": "user", "content": [{"type": "text", "text": "解释当前方案"}]}]

    monkeypatch.setattr(app.frontend_api, "latest_run_id", lambda: RUN_ID)
    unchanged = app.proposal_chat({"threadId": "unchanged-run", "messages": messages})

    run_ids = iter((RUN_ID, "md__202608260003"))
    monkeypatch.setattr(app.frontend_api, "latest_run_id", lambda: next(run_ids))
    created = app.proposal_chat({"threadId": "new-run", "messages": messages})

    assert "runId" not in unchanged
    assert created["runId"] == "md__202608260003"


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
