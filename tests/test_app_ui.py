"""Contracts for the primary assistant-ui entry point."""

import asyncio
import base64
from copy import deepcopy
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

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


def test_configuration_preflight_displays_the_runtime_report(monkeypatch):
    report = {
        "runtime": {"ready": True, "python_version": "3.12.3"},
        "markdown": "Python 运行时：3.12.3（满足）\n沿用父进程解释器。",
    }
    monkeypatch.setattr(app.frontend_api, "run_local_dependency_preflight", lambda: report)

    assert app.configuration_preflight() == report
    source = (app.ROOT / "frontend" / "src" / "main.jsx").read_text()
    styles = (app.ROOT / "frontend" / "src" / "styles.css").read_text()
    assert "payload.markdown || payload.detail" in source
    assert ".configuration-content .action-status { white-space: pre-wrap;" in styles


def test_app_lifespan_reconciles_stale_runs_before_serving(monkeypatch):
    calls = []
    monkeypatch.setattr(
        app.frontend_api,
        "reconcile_stale_pipeline_states",
        lambda **kwargs: calls.append(kwargs) or [],
    )

    async def enter_lifespan():
        async with app._workbench_lifespan(app.app):
            pass

    asyncio.run(enter_lifespan())

    assert calls == [{"source": "service_startup"}]


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
        ("/api/runs/{run_id}/resume", "POST"),
        ("/api/runs/{run_id}/pending-action/revise", "POST"),
        ("/api/runs/{run_id}/pending-action/confirm", "POST"),
        ("/api/runs/{run_id}/display-name", "PATCH"),
        ("/api/local-items/{item_id}", "DELETE"),
        ("/api/runs/{run_id}/logs", "GET"),
        ("/api/runs/{run_id}/stop", "POST"),
        ("/api/runs/{run_id}/updates", "GET"),
        ("/api/proposal/upload", "POST"),
        ("/api/molecules", "GET"),
        ("/api/visualization", "GET"),
    }.issubset(routes)


def test_app2_pending_action_control_is_visible_only_while_awaiting_confirmation():
    source = (app.ROOT / "frontend" / "src" / "main.jsx").read_text(encoding="utf-8")

    assert "function PendingActionConfirmation" in source
    assert 'state === "awaiting_confirmation"' in source
    assert '`pending-action/${operation}`' in source
    assert 'submit("confirm")' in source
    assert "action_id: context.action.action_id" in source
    assert "state_revision: context.action.state_revision" in source
    assert "config_fingerprint: context.action.config_fingerprint" in source


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


@pytest.fixture
def logs_run(tmp_path, monkeypatch):
    _record_run(tmp_path, RUN_ID, state="aborted", step=5)
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)
    run_dir = tmp_path / "md_run" / RUN_ID
    (run_dir / "config.json").write_text(
        json.dumps({"backend": "g16", "ion_charge_scale": 0.8}), encoding="utf-8",
    )
    (tmp_path / "config.json").write_text(
        json.dumps({"backend": "orca", "ion_charge_scale": 0.4}), encoding="utf-8",
    )
    return run_dir


def test_app2_logs_expose_only_the_selected_run_manifest_and_config(logs_run, tmp_path):
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*") if path.is_file()
    }

    response = app.run_logs(RUN_ID)

    assert set(response) == {"run_id", "manifest", "config"}
    assert response["run_id"] == RUN_ID
    assert response["manifest"]["filename"] == "run_manifest.json"
    assert json.loads(response["manifest"]["content"])["run_id"] == RUN_ID
    assert response["config"] == {
        "filename": "config.json",
        "content": '{\n  "backend": "g16",\n  "ion_charge_scale": 0.8\n}\n',
        "truncated": False,
    }
    assert response["manifest"]["truncated"] is False
    assert str(tmp_path) not in str(response)
    assert before == {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*") if path.is_file()
    }
    registry = RunRegistry(tmp_path)
    registry.append_event(logs_run, "logs_read_audit_check", {"state": "aborted"})
    assert (logs_run / "events.jsonl").read_bytes().startswith(before[logs_run / "events.jsonl"][0])
    assert "logs_read_audit_check" in (logs_run / "events.jsonl").read_text()
    assert app.run_logs(RUN_ID) == response


def test_app2_logs_preserve_science_fields_and_strip_nested_credentials(logs_run):
    from willy.simulation.protocol import default_md_config

    public = {
        "backend": "g16", "ion_charge_scale": 0.75,
        "defaults": {"mem": "5GB", "nproc": 8},
        "molecules": {"Li": {"charge": 1, "spin": 1, "basis": "b3lyp", "solvent": "水"}},
        "residues": {"Li": 10}, "md": default_md_config(),
        "topology": {"backend": "sobtop", "force_field": "gaff_uff"},
        "box": {"target_mass_density_g_cm3": 0.7},
        "ion_compensation": {"enabled": True}, "non_neutral_confirmed": False,
        "execution": {"md": {"backend": "local", "profile": None, "retain_remote_run": True}},
    }
    config = deepcopy(public)
    config.update(api_key="TOP-SECRET", llm={"model": "PRIVATE-MODEL"}, private={"body": "PRIVATE-BODY"})
    config["md"]["API_Key"] = "MD-SECRET"
    config["md"]["eq"]["credentials"] = {"anything": "EQ-SECRET"}
    config["molecules"]["Li"]["accessToken"] = "MOLECULE-SECRET"
    config["defaults"]["Password"] = "DEFAULT-SECRET"
    config["execution"]["md"].update(host="HOST-SECRET", command="COMMAND-SECRET")
    config["execution"]["profiles"] = {"anything": "PROFILE-SECRET"}
    config["topology"]["extensions"] = [{"charge": 0, "private_key": "LIST-SECRET"}]
    public["topology"]["extensions"] = [{"charge": 0}]
    config_path = logs_run / "config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    before = config_path.read_bytes()

    response = app.run_logs(RUN_ID)

    assert json.loads(response["config"]["content"]) == public
    assert "SECRET" not in str(response)
    assert "PRIVATE" not in str(response)
    assert "水" in response["config"]["content"]
    assert config_path.read_bytes() == before


@pytest.mark.parametrize("payload", [
    b"", b" \n\t", b"{}", b"[]", b"null", b'"text"', b"42", b"true", b"\xff",
    b'{"backend":', b'{"backend":"g16",}', b'{"backend":"g16"} trailing',
    b'{"md":{"dt":NaN}}', b'{"md":{"dt":Infinity}}', b'{"md":{"dt":-Infinity}}',
    b'{"md":{"dt":1e999}}', b'{"backend":"\\ud800"}',
    b'{"api_key":"PRIVATE-ONLY"}', b'{"md":' + b"[" * 1100 + b"0" + b"]" * 1100 + b"}",
])
def test_app2_logs_reject_empty_or_damaged_config_without_root_fallback(logs_run, payload):
    (logs_run / "config.json").write_bytes(payload)

    with pytest.raises(app.HTTPException) as raised:
        app.run_logs(RUN_ID)

    assert raised.value.status_code == 503
    assert raised.value.detail == "当前工程 config.json 为空或无效"
    assert (logs_run / "config.json").read_bytes() == payload


def test_app2_logs_reject_missing_config_without_root_fallback(logs_run):
    (logs_run / "config.json").unlink()

    with pytest.raises(app.HTTPException, match="config.json") as raised:
        app.run_logs(RUN_ID)

    assert raised.value.status_code == 503
    assert not (logs_run / "config.json").exists()


@pytest.mark.parametrize("formatted_only", [False, True])
def test_app2_logs_reject_oversized_config_instead_of_truncating(logs_run, monkeypatch, formatted_only):
    limit = 128
    monkeypatch.setattr(app, "_MAX_LOG_RECORD_BYTES", limit)
    (logs_run / "run_manifest.json").unlink()
    payload = json.dumps({"md": [0] * 25}) if formatted_only else json.dumps({"backend": "g16", "md": "x" * limit})
    assert (len(payload) <= limit) is formatted_only
    (logs_run / "config.json").write_text(payload)

    with pytest.raises(app.HTTPException, match="过大") as raised:
        app.run_logs(RUN_ID)

    assert raised.value.status_code == 503


def test_app2_logs_bound_config_that_grows_after_stat(logs_run, monkeypatch):
    original_fstat = app.os.fstat
    config_path = logs_run / "config.json"
    config_inode = config_path.stat().st_ino

    def grow_after_stat(descriptor):
        metadata = original_fstat(descriptor)
        if metadata.st_ino == config_inode:
            config_path.write_bytes(b" " * (app._MAX_LOG_RECORD_BYTES + 1))
        return metadata

    monkeypatch.setattr(app.os, "fstat", grow_after_stat)
    with pytest.raises(app.HTTPException, match="过大"):
        app.run_logs(RUN_ID)


@pytest.mark.parametrize("run_id", ["", "..", "../config.json", "/tmp", "md/../other", "md\\other", "md__missing"])
def test_app2_logs_reject_nonlocal_run_ids(logs_run, run_id):
    with pytest.raises(app.HTTPException) as raised:
        app.run_logs(run_id)

    assert raised.value.status_code == 404


@pytest.mark.parametrize("filename", ["config.json", "run_manifest.json", "manifest.json"])
@pytest.mark.parametrize("target", ["outside", "same_run", "dangling"])
def test_app2_logs_reject_record_symlinks(logs_run, tmp_path, filename, target):
    if filename == "manifest.json":
        (logs_run / "run_manifest.json").unlink()
    path = logs_run / filename
    path.unlink(missing_ok=True)
    destination = logs_run / "other.json" if target == "same_run" else tmp_path / "private.json"
    if target != "dangling":
        destination.write_text('{"backend":"PRIVATE-CONTENT"}')
    path.symlink_to(destination)

    with pytest.raises(app.HTTPException) as raised:
        app.run_logs(RUN_ID)

    assert raised.value.status_code == 503
    assert "PRIVATE-CONTENT" not in raised.value.detail
    assert str(tmp_path) not in raised.value.detail


@pytest.mark.parametrize("directory", ["run", "md_run"])
def test_app2_logs_reject_directory_symlink_escape(logs_run, tmp_path, directory):
    original = logs_run if directory == "run" else logs_run.parent
    destination = tmp_path / "outside"
    original.rename(destination)
    original.symlink_to(destination, target_is_directory=True)

    with pytest.raises(app.HTTPException) as raised:
        app.run_logs(RUN_ID)

    assert raised.value.status_code == 404


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_app2_logs_reject_nonregular_config_without_blocking(logs_run, kind):
    config_path = logs_run / "config.json"
    config_path.unlink()
    if kind == "directory":
        config_path.mkdir()
    else:
        app.os.mkfifo(config_path)

    with pytest.raises(app.HTTPException) as raised:
        app.run_logs(RUN_ID)

    assert raised.value.status_code == 503


def test_app2_logs_pin_directory_during_symlink_swap(logs_run, tmp_path, monkeypatch):
    original_open = app.os.open
    other_run = tmp_path / "other-run"
    other_run.mkdir()
    (other_run / "config.json").write_text('{"backend":"OTHER-RUN"}')

    def swap_before_config_read(path, flags, *args, **kwargs):
        if path == "config.json":
            logs_run.rename(tmp_path / "pinned-run")
            logs_run.symlink_to(other_run, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(app.os, "open", swap_before_config_read)

    assert json.loads(app.run_logs(RUN_ID)["config"]["content"])["backend"] == "g16"


@pytest.mark.parametrize("legacy", [False, True])
def test_app2_logs_keep_manifest_and_never_read_events(logs_run, tmp_path, legacy):
    if legacy:
        (logs_run / "run_manifest.json").unlink()
        (logs_run / "manifest.json").write_text(json.dumps({"run_id": RUN_ID}))
    else:
        path = logs_run / "run_manifest.json"
        manifest = json.loads(path.read_text())
        manifest["sections"]["private"] = {"visibility": "private", "api_key": "PRIVATE-MANIFEST"}
        path.write_text(json.dumps(manifest))
    events_path = logs_run / "events.jsonl"
    events_path.unlink()
    events_path.symlink_to(tmp_path / "absent-events")

    response = app.run_logs(RUN_ID)

    assert response["manifest"]["filename"] == ("manifest.json" if legacy else "run_manifest.json")
    assert json.loads(response["manifest"]["content"])["run_id"] == RUN_ID
    assert "PRIVATE-MANIFEST" not in str(response)
    assert events_path.is_symlink()
    assert "events" not in response


def test_app2_logs_http_contract_and_config_failure(logs_run, monkeypatch):
    monkeypatch.setattr(app.frontend_api, "reconcile_stale_pipeline_states", lambda **kwargs: [])
    with TestClient(app.app) as client:
        response = client.get(f"/api/runs/{RUN_ID}/logs")
        assert response.status_code == 200
        assert set(response.json()) == {"run_id", "manifest", "config"}
        (logs_run / "config.json").unlink()
        response = client.get(f"/api/runs/{RUN_ID}/logs")
        assert response.status_code == 503
        assert response.json() == {"detail": "当前工程缺少 config.json"}


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


def test_app2_proposal_persists_user_turn_before_llm_returns(tmp_path, monkeypatch):
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)
    workspace = app.create_proposal_workspace()["workspace"]

    def stalled_chat(*_args, **_kwargs):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(app, "chat", stalled_chat)

    with pytest.raises(RuntimeError, match="llm unavailable"):
        app.proposal_chat({
            "proposalId": workspace["workspace_id"],
            "messages": [{"role": "user", "content": [{"type": "text", "text": "建立 Li 体系"}]}],
        })

    assert app.get_workspace_history(tmp_path, workspace["workspace_id"]) == [
        {"role": "user", "content": "建立 Li 体系"},
    ]


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


def test_pending_action_confirmation_follows_child_and_saves_only_child_history(monkeypatch):
    child_id = "md__202609080002"
    action = {"action_id": "fork-repair", "state_revision": 3, "config_fingerprint": "a" * 64}
    saved = []
    monkeypatch.setattr(app, "_safe_run_id", lambda run_id: run_id)
    monkeypatch.setattr(app, "_public_snapshot", lambda run_id: {
        "run_id": run_id, "state": "awaiting_confirmation" if run_id == RUN_ID else "retrying",
        "step": 9, "done_steps": list(range(1, 9)), "pending_action": action if run_id == RUN_ID else None,
    })
    monkeypatch.setattr(app.frontend_api, "get_pending_action", lambda run_id: action)
    monkeypatch.setattr(app.frontend_api, "get_run_assistant_history", lambda run_id: [{"role": "user", "content": "继承的对话"}])
    monkeypatch.setattr(app.frontend_api, "save_run_assistant_history", lambda run_id, history: saved.append(run_id) or True)
    monkeypatch.setattr(app.frontend_api, "confirm_pending_action", lambda *args, **kwargs: f"已创建 fork {child_id}；父工程不变。")
    response = app.confirm_run_pending_action(RUN_ID, dict(action))
    assert response["run_id"] == child_id
    assert saved == [child_id]
    assert any(message["content"] == "继承的对话" for message in response["messages"])


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


@pytest.fixture
def recovery_ui(tmp_path, monkeypatch):
    child_id = "md__202609080003"
    action = {
        "run_id": RUN_ID,
        "action_id": "repair-original",
        "state_revision": 17,
        "config_fingerprint": "a" * 64,
        "summary": "原待确认调整方案",
        "restart_step": 9,
        "adjustments": [],
    }
    snapshots = {RUN_ID: {
        "run_id": RUN_ID,
        "state": "awaiting_confirmation",
        "state_revision": 17,
        "step": 9,
        "done_steps": list(range(1, 9)),
        "pending_action": action,
    }}
    histories = {RUN_ID: app._merge_status_into_history(
        [{"role": "user", "content": "保留原工程对话"}], snapshots[RUN_ID],
    )}
    calls = {name: [] for name in ("resume", "revise", "confirm", "llm", "control", "save")}

    def safe_run_id(run_id):
        if run_id not in snapshots:
            raise app.HTTPException(status_code=404, detail="未找到该工程")
        return run_id

    def get_action(run_id):
        snapshot = snapshots[run_id]
        return deepcopy(snapshot.get("pending_action")) if snapshot["state"] == "awaiting_confirmation" else None

    def save_history(run_id, history):
        calls["save"].append(run_id)
        histories[run_id] = deepcopy(history)
        return True

    def resume(run_id, *, state_revision=None):
        calls["resume"].append((run_id, state_revision))
        snapshots[run_id].update(
            state="retrying", state_revision=state_revision + 1, pending_action=None,
        )
        return "已按原参数续跑，未应用待确认方案。"

    def revise(action_id, user_request, run_id, *, state_revision, config_fingerprint):
        calls["revise"].append((action_id, user_request, run_id, state_revision, config_fingerprint))
        replacement = dict(
            snapshots[run_id]["pending_action"],
            action_id=f"repair-revised-{state_revision + 1}",
            state_revision=state_revision + 1,
            summary=f"新版待确认方案：{user_request}",
        )
        snapshots[run_id].update(state_revision=state_revision + 1, pending_action=replacement)
        return "已更新待确认方案；尚未确认或启动工程。"

    def confirm(action_id, run_id, *, state_revision, config_fingerprint):
        calls["confirm"].append((action_id, run_id, state_revision, config_fingerprint))
        histories[child_id] = deepcopy(histories[run_id])
        snapshots[child_id] = dict(
            snapshots[run_id], run_id=child_id, state="retrying", state_revision=1, pending_action=None,
        )
        snapshots[run_id]["pending_action"] = None
        return f"已创建 fork {child_id}；父工程不变。"

    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)
    monkeypatch.setattr(app, "_safe_run_id", safe_run_id)
    monkeypatch.setattr(app, "_public_snapshot", lambda run_id: deepcopy(snapshots[run_id]))
    monkeypatch.setattr(app.frontend_api, "get_pending_action", get_action)
    monkeypatch.setattr(app.frontend_api, "get_run_assistant_history", lambda run_id: deepcopy(histories.get(run_id, [])))
    monkeypatch.setattr(app.frontend_api, "save_run_assistant_history", save_history)
    monkeypatch.setattr(app.frontend_api, "resume_aborted_run", resume)
    monkeypatch.setattr(app.frontend_api, "revise_pending_action", revise)
    monkeypatch.setattr(app.frontend_api, "confirm_pending_action", confirm)
    monkeypatch.setattr(app.frontend_api, "get_run_assistant_switch_target", lambda message: None)
    monkeypatch.setattr(app.frontend_api, "run_assistant_control_command", lambda run_id, message: calls["control"].append(message))
    monkeypatch.setattr(app.frontend_api, "chat_run_assistant", lambda run_id, message, history: calls["llm"].append(message) or "只读讨论")
    monkeypatch.setattr(app.frontend_api, "reconcile_stale_pipeline_states", lambda **kwargs: [])
    with TestClient(app.app) as client:
        yield {
            "client": client, "snapshots": snapshots, "histories": histories,
            "calls": calls, "action": deepcopy(action), "child_id": child_id,
        }


def test_recovery_snapshot_exposes_registry_revision(tmp_path, monkeypatch):
    _record_run(tmp_path, RUN_ID, state="aborted", step=9)
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)
    monkeypatch.setattr(app.frontend_api, "get_run_panel_snapshot", lambda run_id: {})
    status = RunRegistry(tmp_path).get_run_status(RUN_ID, reconcile=False)

    assert app._public_snapshot(RUN_ID)["state_revision"] == status["state_revision"]


@pytest.mark.parametrize("state", ["aborted", "awaiting_confirmation", "escalated"])
def test_recovery_resume_endpoint_preserves_original_run_and_ignores_pending_plan(recovery_ui, state):
    recovery_ui["snapshots"][RUN_ID]["state"] = state
    response = recovery_ui["client"].post(f"/api/runs/{RUN_ID}/resume", json={"state_revision": 17})

    assert response.status_code == 200
    result = response.json()
    assert result["run_id"] == result["snapshot"]["run_id"] == RUN_ID
    assert result["snapshot"]["state"] == "retrying"
    assert result["snapshot"]["state_revision"] == 18
    assert result["snapshot"]["pending_action"] is None
    assert result["text"] == "已按原参数续跑，未应用待确认方案。"
    assert recovery_ui["histories"][RUN_ID] == result["messages"]
    assert any(message["content"] == "保留原工程对话" for message in result["messages"])
    assert not any(message.get(app._RUN_STATUS_EVENT_KIND) == "pending_action" for message in result["messages"])
    assert recovery_ui["calls"]["resume"] == [(RUN_ID, 17)]
    assert recovery_ui["calls"]["save"] == [RUN_ID]
    assert all(not recovery_ui["calls"][name] for name in ("revise", "confirm", "llm", "control"))
    assert recovery_ui["child_id"] not in recovery_ui["snapshots"]


@pytest.mark.parametrize("payload, expected", [
    ({}, 400), ({"state_revision": None}, 400), ({"state_revision": True}, 400),
    ({"state_revision": "17"}, 400), ({"state_revision": 17.0}, 400),
    ({"state_revision": -1}, 400), ({"state_revision": 16}, 409),
    ({"state_revision": 18}, 409),
])
def test_recovery_resume_endpoint_rejects_invalid_or_stale_revision(recovery_ui, payload, expected):
    response = recovery_ui["client"].post(f"/api/runs/{RUN_ID}/resume", json=payload)

    assert response.status_code == expected
    assert all(not calls for calls in recovery_ui["calls"].values())


@pytest.mark.parametrize("state", ["running", "retrying", "done", "unknown", "idle"])
def test_recovery_resume_endpoint_rejects_ineligible_state(recovery_ui, state):
    recovery_ui["snapshots"][RUN_ID]["state"] = state
    response = recovery_ui["client"].post(f"/api/runs/{RUN_ID}/resume", json={"state_revision": 17})

    assert response.status_code == 409
    assert all(not calls for calls in recovery_ui["calls"].values())


@pytest.mark.parametrize("message", ["原参数续跑", "按原参数重跑", "不改参数继续", "原参数续跑。", "/resume"])
def test_recovery_chat_original_parameter_commands_use_resume_not_proposal(recovery_ui, message):
    response = recovery_ui["client"].post(f"/api/runs/{RUN_ID}/chat", json={"message": message})

    assert response.status_code == 200
    result = response.json()
    assert result["run_id"] == RUN_ID
    assert recovery_ui["histories"][RUN_ID] == result["messages"]
    assert {"role": "user", "content": message} in result["messages"]
    assert recovery_ui["calls"]["resume"] == [(RUN_ID, 17)]
    assert all(not recovery_ui["calls"][name] for name in ("revise", "confirm", "llm", "control"))


def test_recovery_chat_resume_honors_submitted_revision(recovery_ui):
    response = recovery_ui["client"].post(f"/api/runs/{RUN_ID}/chat", json={
        "message": "原参数续跑", "state_revision": 16,
    })

    assert response.status_code == 409
    assert all(not calls for calls in recovery_ui["calls"].values())


def test_recovery_chat_reconciles_stale_process_before_resume_state_gate(recovery_ui, monkeypatch):
    recovery_ui["snapshots"][RUN_ID].update(state="running", pending_action=None)
    reconciled = []

    def reconcile(run_id, *, source):
        reconciled.append((run_id, source))
        recovery_ui["snapshots"][run_id].update(state="aborted", state_revision=18)

    monkeypatch.setattr(app.frontend_api, "reconcile_stale_pipeline_state", reconcile)
    response = recovery_ui["client"].post(f"/api/runs/{RUN_ID}/chat", json={"message": "/resume"})

    assert response.status_code == 200
    assert reconciled == [(RUN_ID, "resume_request")]
    assert recovery_ui["calls"]["resume"] == [(RUN_ID, 18)]


@pytest.mark.parametrize("revision_surface", ["endpoint", "chat"])
@pytest.mark.parametrize("confirmation_surface", ["endpoint", "chat"])
def test_recovery_revise_replaces_bubble_rejects_old_binding_and_confirms_new_child(
    recovery_ui, revision_surface, confirmation_surface,
):
    client = recovery_ui["client"]
    original = recovery_ui["action"]
    request = "把时间步长改为 0.0005 ps"
    revision_path = f"/api/runs/{RUN_ID}/pending-action/revise" if revision_surface == "endpoint" else f"/api/runs/{RUN_ID}/chat"
    revision_payload = {"request": request} if revision_surface == "endpoint" else {"message": f"修改方案：{request}"}
    confirmation_path = f"/api/runs/{RUN_ID}/pending-action/confirm" if confirmation_surface == "endpoint" else f"/api/runs/{RUN_ID}/chat"
    response = client.post(revision_path, json=dict(original, **revision_payload))

    assert response.status_code == 200
    revised = response.json()
    assert revised["run_id"] == RUN_ID
    assert revised["snapshot"]["state"] == "awaiting_confirmation"
    assert revised["snapshot"]["state_revision"] == 18
    latest = revised["snapshot"]["pending_action"]
    assert latest["action_id"] != original["action_id"]
    assert latest["state_revision"] == 18
    assert latest["config_fingerprint"] == original["config_fingerprint"]
    proposals = [message for message in revised["messages"] if message.get(app._RUN_STATUS_EVENT_KIND) == "pending_action"]
    assert len(proposals) == 1
    assert proposals[0][app._RUN_STATUS_EVENT_ID] == f"{RUN_ID}:pending_action:{latest['action_id']}"
    assert request in proposals[0]["content"]
    assert "原待确认调整方案" not in str(revised["messages"])
    assert recovery_ui["histories"][RUN_ID] == revised["messages"]
    assert client.get(f"/api/runs/{RUN_ID}/chat").json()["messages"] == revised["messages"]
    assert recovery_ui["calls"]["revise"] == [(original["action_id"], request, RUN_ID, 17, "a" * 64)]
    assert all(not recovery_ui["calls"][name] for name in ("confirm", "resume", "llm", "control"))

    assert client.post(revision_path, json=dict(original, **revision_payload)).status_code == 409
    assert client.post(confirmation_path, json=dict(original, message="确认方案")).status_code == 409
    confirmed = client.post(confirmation_path, json=dict(latest, message="确认方案"))
    assert confirmed.status_code == 200
    child = confirmed.json()
    assert child["run_id"] == child["snapshot"]["run_id"] == recovery_ui["child_id"]
    assert child["snapshot"]["pending_action"] is None
    assert child["snapshot"]["state"] == "retrying"
    assert recovery_ui["histories"][child["run_id"]] == child["messages"]
    assert not any(message.get(app._RUN_STATUS_EVENT_KIND) == "pending_action" for message in child["messages"])
    assert recovery_ui["calls"]["confirm"] == [(latest["action_id"], RUN_ID, 18, "a" * 64)]
    assert len(recovery_ui["calls"]["revise"]) == 1
    assert not recovery_ui["calls"]["resume"]
    assert client.post(confirmation_path, json=dict(latest, message="确认方案")).status_code == 409


@pytest.mark.parametrize("operation", ["revise", "confirm", "chat-revise", "chat-confirm"])
@pytest.mark.parametrize("field, value, expected", [
    ("action_id", "old-action", 409), ("state_revision", 16, 409),
    ("config_fingerprint", "b" * 64, 409), ("action_id", None, 400),
    ("state_revision", True, 400), ("state_revision", -1, 400),
    ("config_fingerprint", "", 400),
])
def test_recovery_action_controls_require_exact_current_binding(recovery_ui, operation, field, value, expected):
    payload = dict(recovery_ui["action"], request="降低时间步长")
    payload[field] = value
    if operation.startswith("chat-"):
        path = f"/api/runs/{RUN_ID}/chat"
        payload["message"] = "确认方案" if operation == "chat-confirm" else "修改方案：降低时间步长"
    else:
        path = f"/api/runs/{RUN_ID}/pending-action/{operation}"
    response = recovery_ui["client"].post(path, json=payload)

    assert response.status_code == expected
    assert all(not calls for calls in recovery_ui["calls"].values())


@pytest.mark.parametrize("user_request", [None, "", "   ", 17, "调" * 601])
def test_recovery_revise_requires_bounded_nonempty_request(recovery_ui, user_request):
    response = recovery_ui["client"].post(f"/api/runs/{RUN_ID}/pending-action/revise", json={
        **recovery_ui["action"], "request": user_request,
    })

    assert response.status_code == 400
    assert all(not calls for calls in recovery_ui["calls"].values())


@pytest.mark.parametrize("message", [
    "修改方案：降低时间步长", "修改方案: 延长 EQ 保温段", "把 dt 改为 0.0005",
    "将时间步长设置为 0.0005 ps", "把时间步长改成 0.0005 ps", "请把 dt 改为 0.0005。",
    "请修改方案：降低时间步长。", "把你建议的时间步长改为 0.0005 ps",
])
def test_recovery_chat_revises_only_pending_plan_without_confirmation(recovery_ui, message):
    response = recovery_ui["client"].post(f"/api/runs/{RUN_ID}/chat", json={"message": message})

    assert response.status_code == 200
    assert response.json()["snapshot"]["pending_action"]["state_revision"] == 18
    assert len(recovery_ui["calls"]["revise"]) == 1
    assert all(not recovery_ui["calls"][name] for name in ("confirm", "resume", "llm", "control"))


@pytest.mark.parametrize("message", [
    "原参数续跑？", "不要按原参数重跑", "按原参数重跑是否合适", "不改参数继续吗", "我想了解原参数续跑",
    "修改方案：把 dt 改为 0.0005 可以吗", "修改方案：不要降低时间步长", "修改方案：如果降低时间步长",
    "把 dt 改为 0.0005？", "把 dt 改为 0.0005 合理吗", "把 dt 改为 0.0005 是否合适",
    "不要把 dt 改为 0.0005", "将时间步长改为 0.0005 会怎样", "我们讨论把 dt 改为 0.0005",
    "如果把 dt 改为 0.0005", "为什么要修改方案", "降低时间步长", "修改方案：",
    "把 dt 改为 0.0005 会更稳定", "把 dt 改为 0.0005 应该更稳定", "修改方案：‘把 dt 改为 0.0005’",
    "请问把 dt 改为 0.0005", "修改方案：把 dt 改为 0.0005。这里只是讨论",
])
def test_recovery_chat_questions_negation_and_discussion_remain_read_only(recovery_ui, message):
    response = recovery_ui["client"].post(f"/api/runs/{RUN_ID}/chat", json={"message": message})

    assert response.status_code == 200
    assert response.json()["text"] == "只读讨论"
    assert recovery_ui["calls"]["llm"] == [message]
    assert all(not recovery_ui["calls"][name] for name in ("revise", "confirm", "resume"))


@pytest.mark.parametrize("state", ["awaiting_confirmation", "escalated", "unknown", "aborted"])
def test_recovery_no_pending_action_never_invents_or_launches_a_plan(recovery_ui, state):
    snapshot = recovery_ui["snapshots"][RUN_ID]
    snapshot.update(state=state, pending_action=None)
    client = recovery_ui["client"]
    response = client.post(f"/api/runs/{RUN_ID}/chat", json={"message": "修改方案：降低时间步长"})

    assert response.status_code == 200
    assert "当前没有有效的待确认方案" in response.json()["text"]
    assert not any(message.get(app._RUN_STATUS_EVENT_KIND) == "pending_action" for message in response.json()["messages"])
    assert client.post(f"/api/runs/{RUN_ID}/pending-action/revise", json={
        **recovery_ui["action"], "request": "降低时间步长",
    }).status_code == 409
    assert all(not recovery_ui["calls"][name] for name in ("revise", "confirm", "resume", "llm", "control"))


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
