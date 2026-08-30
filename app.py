"""Willy FastAPI entry point for the React assistant-ui workbench."""

from __future__ import annotations

import base64
import binascii
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Mapping

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from willy import frontend_api
from willy.agent_config import chat
from willy.env_registry import dotenv_value
from willy.run_registry import RunRegistry, RunRegistryError
from willy.step_registry import STEP_REGISTRY
from willy.structure_uploads import (
    StructureUploadError,
    normalize_uploaded_structure,
    supported_upload_suffixes,
)


DIST = ROOT / "frontend" / "dist"
OFFICIAL_ACCOUNT_QR = ROOT / "assets" / "qrcode_for_gh_7df1329939c6_258.jpg"
app = FastAPI(title="Willy assistant-ui")
_threads: dict[str, dict[str, Any]] = {}
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_RUN_STATUS_EVENT_KIND = "_run_assistant_event_kind"
_RUN_STATUS_EVENT_ID = "_run_assistant_event_id"
_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
_MAX_LOG_RECORD_BYTES = 1024 * 1024
_DISPLAY_NAME_FILENAME = ".willy_display_names.json"
_DISPLAY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_RUN_HISTORY_WELCOME = {
    "role": "assistant",
    "content": (
        "你好！我是 Willy-运行助理。\n\n"
        "我会跟踪当前工程的阶段、完成进度、错误摘要和受控续跑。"
    ),
}


def _text_content(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _assistant_history(messages: list[Any]) -> list[dict[str, str]]:
    history: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role") if isinstance(message, dict) else None
        text = _text_content(message)
        if role in {"user", "assistant"} and text.strip():
            history.append({"role": role, "content": text})
    return history


def _safe_run_id(run_id: object) -> str:
    """Validate a public run identifier before delegating to the registry."""
    if not isinstance(run_id, str) or not _RUN_ID_PATTERN.fullmatch(run_id):
        raise HTTPException(status_code=404, detail="未找到该工程")
    try:
        return RunRegistry(frontend_api.ROOT).resolve_run_id(run_id).name
    except RunRegistryError as exc:
        raise HTTPException(status_code=404, detail="未找到该工程") from exc


def _selected_run_id(run_id: object | None = None) -> str | None:
    """Resolve an optional browser selection, following the active run by default."""
    if run_id is None or run_id == "":
        return frontend_api.latest_run_id()
    return _safe_run_id(run_id)


def _viewer_control_value(
    value: str | None,
    *,
    name: str,
    default: float,
    value_range: tuple[float, float],
) -> float:
    """Parse one viewer control without silently accepting invalid URLs."""
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{name}必须是数值") from exc
    lower, upper = value_range
    if not math.isfinite(parsed) or not lower <= parsed <= upper:
        raise HTTPException(
            status_code=422,
            detail=f"{name}必须在 {lower:.2f} 至 {upper:.2f} 之间",
        )
    return parsed


def _viewer_background_value(value: str | None) -> str:
    """Accept only named visualization backgrounds from the browser."""
    if value is None:
        return frontend_api.DEFAULT_VIEWER_BACKGROUND
    if value not in frontend_api.VIEWER_BACKGROUNDS:
        raise HTTPException(status_code=422, detail="背景必须是米色、白色或银色")
    return value


def _display_name_store_path() -> Path:
    return Path(frontend_api.ROOT) / "md_run" / _DISPLAY_NAME_FILENAME


def _load_display_name_mapping() -> dict[str, str]:
    """Read display-only names without touching manifests, events, or status."""
    try:
        with _display_name_store_path().open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    raw_names = payload.get("names") if isinstance(payload, Mapping) else None
    if not isinstance(raw_names, Mapping):
        return {}
    return {
        run_id: display_name
        for run_id, display_name in raw_names.items()
        if isinstance(run_id, str)
        and _RUN_ID_PATTERN.fullmatch(run_id)
        and isinstance(display_name, str)
        and _DISPLAY_NAME_RE.fullmatch(display_name)
    }


def _save_display_name_mapping(names: Mapping[str, str]) -> None:
    """Atomically persist the browser-facing name map beside the run index."""
    path = _display_name_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "names": dict(names)}, handle, indent=2)
            handle.write("\n")
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _normalized_display_name(value: object) -> str:
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="工程名称必须是文本")
    normalized = value.strip()
    if not _DISPLAY_NAME_RE.fullmatch(normalized):
        raise HTTPException(
            status_code=400,
            detail="工程名称只能包含英文字母、数字、下划线和连字符，长度不超过 64",
        )
    return normalized


def _read_run_record(
    run_dir: Path,
    *,
    filenames: tuple[str, ...],
    record_name: str,
    tail_when_truncated: bool,
) -> dict[str, Any]:
    """Read one fixed, run-local audit record without exposing arbitrary files."""
    path = next((run_dir / name for name in filenames if (run_dir / name).is_file()), None)
    if path is None:
        return {
            "filename": filenames[0],
            "content": f"当前工程尚无 {record_name} 记录。",
            "truncated": False,
        }
    try:
        size = path.stat().st_size
        truncated = size > _MAX_LOG_RECORD_BYTES
        with path.open("rb") as handle:
            if truncated and tail_when_truncated:
                handle.seek(size - _MAX_LOG_RECORD_BYTES)
            payload = handle.read(_MAX_LOG_RECORD_BYTES)
    except OSError:
        raise HTTPException(status_code=503, detail=f"{record_name} 记录暂时无法读取") from None
    content = payload.decode("utf-8", errors="replace")
    if truncated and tail_when_truncated:
        # Do not start a JSONL view in the middle of an event line.
        separator = content.find("\n")
        if separator >= 0:
            content = content[separator + 1:]
        content = f"[记录过大，仅显示最后 {_MAX_LOG_RECORD_BYTES // 1024} KiB]\n{content}"
    elif truncated:
        content = f"[记录过大，仅显示前 {_MAX_LOG_RECORD_BYTES // 1024} KiB]\n{content}"
    return {"filename": path.name, "content": content, "truncated": truncated}


def _read_public_manifest_record(run_dir: Path) -> dict[str, Any]:
    """Project only the public registry section from a schema-v2 manifest."""
    v2_path = run_dir / "run_manifest.json"
    if not v2_path.is_file():
        return _read_run_record(
            run_dir,
            filenames=("manifest.json",),
            record_name="manifest",
            tail_when_truncated=False,
        )
    try:
        with v2_path.open(encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, json.JSONDecodeError):
        raise HTTPException(status_code=503, detail="manifest 记录暂时无法读取") from None
    sections = manifest.get("sections") if isinstance(manifest, Mapping) else None
    registry = sections.get("registry") if isinstance(sections, Mapping) else None
    if not isinstance(registry, Mapping) or registry.get("visibility") != "public":
        raise HTTPException(status_code=503, detail="manifest 公共记录无效")
    public_manifest = {
        key: manifest.get(key)
        for key in ("schema_version", "run_id", "created_at", "updated_at", "revision")
    }
    public_manifest["registry"] = dict(registry)
    content = json.dumps(public_manifest, ensure_ascii=False, indent=2) + "\n"
    encoded = content.encode("utf-8")
    truncated = len(encoded) > _MAX_LOG_RECORD_BYTES
    if truncated:
        visible = encoded[:_MAX_LOG_RECORD_BYTES].decode("utf-8", errors="ignore")
        content = f"[公开 manifest 记录过大，仅显示前 {_MAX_LOG_RECORD_BYTES // 1024} KiB]\n{visible}"
    return {
        "filename": v2_path.name,
        "content": content,
        "truncated": truncated,
    }


def _public_snapshot(run_id: str | None) -> dict[str, Any]:
    """Return the existing panel contract plus stable, display-only run facts."""
    snapshot = dict(frontend_api.get_run_panel_snapshot(run_id))
    snapshot.setdefault("run_id", run_id)
    state = "await"
    step: int | None = None
    done_steps: list[int] = []
    if isinstance(run_id, str):
        try:
            status = RunRegistry(frontend_api.ROOT).get_run_status(run_id, reconcile=False)
        except RunRegistryError:
            status = {}
        value = status.get("state")
        state = value if isinstance(value, str) and value else "unknown"
        candidate_step = status.get("step")
        if isinstance(candidate_step, int) and not isinstance(candidate_step, bool):
            step = candidate_step
        raw_done_steps = status.get("done_steps")
        if isinstance(raw_done_steps, list):
            done_steps = [
                item for item in raw_done_steps
                if isinstance(item, int) and not isinstance(item, bool)
                and 1 <= item <= STEP_REGISTRY.total_steps
            ]
    snapshot["state"] = state
    snapshot["step"] = step
    snapshot["phase"] = _step_label(step)
    snapshot["progress"] = f"{len(set(done_steps))}/{STEP_REGISTRY.total_steps}"
    snapshot["done_steps"] = sorted(set(done_steps))
    return snapshot


def _step_label(step: int | None) -> str:
    if not isinstance(step, int) or step < 1 or step > STEP_REGISTRY.total_steps:
        return "—"
    return STEP_REGISTRY.label_for(step)


def _run_history(run_id: str) -> list[dict[str, str]]:
    """Load one durable conversation while ensuring a stable first message."""
    history = frontend_api.get_run_assistant_history(run_id)
    if not history:
        return [dict(_RUN_HISTORY_WELCOME)]
    return history


def _save_run_history(run_id: str, history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Persist only the frontend API's bounded history representation."""
    frontend_api.save_run_assistant_history(run_id, history)
    return history


def _status_event(snapshot: Mapping[str, object]) -> dict[str, str]:
    event_id = snapshot.get("status_event_id")
    run_id = snapshot.get("run_id")
    safe_event_id = event_id if isinstance(event_id, str) and event_id else f"{run_id or 'none'}:status"
    summary = snapshot.get("live_summary", snapshot.get("summary"))
    content = summary if isinstance(summary, str) and summary.strip() else "当前工程状态暂不可读取。"
    return {
        "event_id": safe_event_id,
        "kind": "status",
        "content": content,
    }


def _completion_events(snapshot: Mapping[str, object]) -> list[dict[str, str]]:
    """Expose one deduplicable notice per recorded completed pipeline step."""
    run_id = snapshot.get("run_id")
    if not isinstance(run_id, str):
        return []
    done_steps = snapshot.get("done_steps")
    if not isinstance(done_steps, list):
        return []
    events: list[dict[str, str]] = []
    for step in done_steps:
        if not isinstance(step, int) or isinstance(step, bool):
            continue
        if not 1 <= step <= STEP_REGISTRY.total_steps:
            continue
        events.append({
            "event_id": f"{run_id}:step:{step}:completed",
            "kind": "step_completed",
            "content": f"第 {step} 步「{STEP_REGISTRY.label_for(step)}」已完成。",
        })
    return events


def _merge_status_into_history(
    history: list[dict[str, str]],
    snapshot: Mapping[str, object],
) -> list[dict[str, str]]:
    """Persist status and completion notices once, never as LLM context."""
    merged = [dict(message) for message in history if isinstance(message, Mapping)]
    known_ids = {
        message.get(_RUN_STATUS_EVENT_ID)
        for message in merged
        if isinstance(message.get(_RUN_STATUS_EVENT_ID), str)
    }
    for event in [*_completion_events(snapshot), _status_event(snapshot)]:
        if event["event_id"] in known_ids:
            continue
        merged.append({
            "role": "assistant",
            "content": event["content"],
            _RUN_STATUS_EVENT_KIND: event["kind"],
            _RUN_STATUS_EVENT_ID: event["event_id"],
        })
        known_ids.add(event["event_id"])
    return merged


def _llm_run_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Remove app-owned state notices before invoking the read-only assistant."""
    return [
        {"role": message["role"], "content": message["content"]}
        for message in history
        if message.get("role") in {"user", "assistant"}
        and isinstance(message.get("content"), str)
        and _RUN_STATUS_EVENT_KIND not in message
    ]


def _follow_controlled_run(run_id: str, reply: str) -> str:
    """Move the browser binding to a newly created fork and no other reply."""
    match = re.match(r"^已创建 fork (md__\d{12})(?:[；。]|$)", reply)
    return match.group(1) if match else run_id


def _run_chat_response(run_id: str, history: list[dict[str, str]]) -> dict[str, Any]:
    snapshot = _public_snapshot(run_id)
    return {
        "run_id": run_id,
        "messages": history,
        "snapshot": snapshot,
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "surface": "assistant-ui"}


@app.get("/app-assets/qrcode_for_gh_7df1329939c6_258.jpg")
def official_account_qr() -> FileResponse:
    """Serve the existing About-page asset without exposing the project tree."""
    if not OFFICIAL_ACCOUNT_QR.is_file():
        raise HTTPException(status_code=404, detail="未找到公众号二维码")
    return FileResponse(OFFICIAL_ACCOUNT_QR, media_type="image/jpeg")


@app.get("/api/workspace")
def workspace() -> dict[str, Any]:
    run_id = frontend_api.latest_run_id()
    snapshot = _public_snapshot(run_id)
    return {
        "run_id": run_id,
        "snapshot": snapshot,
        "sections": ["本地任务", "配置", "新手指南", "关于"],
    }


@app.get("/api/runs")
def list_local_runs() -> dict[str, Any]:
    """List registered local projects without exposing their directory paths."""
    registry = RunRegistry(frontend_api.ROOT)
    display_names = _load_display_name_mapping()
    runs: list[dict[str, str]] = []
    try:
        records = registry.list_runs(limit=200)
    except (OSError, RunRegistryError, ValueError):
        records = []
    for record in records:
        run_id = record.get("run_id") if isinstance(record, Mapping) else None
        if not isinstance(run_id, str):
            continue
        try:
            canonical_id = registry.resolve_run_id(run_id).name
        except RunRegistryError:
            continue
        state = record.get("state") if isinstance(record.get("state"), str) else "unknown"
        updated_at = record.get("updated_at") if isinstance(record.get("updated_at"), str) else ""
        try:
            status = registry.get_run_status(canonical_id, reconcile=False)
        except RunRegistryError:
            status = {}
        if isinstance(status.get("state"), str):
            state = status["state"]
        if isinstance(status.get("updated_at"), str):
            updated_at = status["updated_at"]
        display_name = display_names.get(canonical_id)
        if display_name is None:
            # One-time compatibility fallback for labels written by earlier UI builds.
            display_name = record.get("display_name") if isinstance(record.get("display_name"), str) else ""
        runs.append({
            "run_id": canonical_id,
            "display_name": display_name[:64],
            "state": state,
            "updated_at": updated_at,
        })
    # RunRegistry already orders by update timestamp; sorting again gives a
    # deterministic order even if a legacy index contains equivalent times.
    runs.sort(key=lambda item: (item["updated_at"], item["run_id"]), reverse=True)
    return {"runs": runs, "active_run_id": frontend_api.get_active_run_id()}


@app.patch("/api/runs/{run_id}/display-name")
def set_run_display_name(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist a frontend-only label without changing run metadata or state."""
    canonical_id = _safe_run_id(run_id)
    display_name = _normalized_display_name(payload.get("display_name"))
    try:
        names = _load_display_name_mapping()
        changed = names.get(canonical_id) != display_name
        if changed:
            names[canonical_id] = display_name
            _save_display_name_mapping(names)
    except OSError:
        raise HTTPException(status_code=503, detail="工程名称暂时无法保存") from None
    return {
        "run_id": canonical_id,
        "display_name": display_name,
        "changed": changed,
        "state": _public_snapshot(canonical_id).get("state", "unknown"),
    }


@app.get("/api/runs/{run_id}/logs")
def run_logs(run_id: str) -> dict[str, Any]:
    """Return the selected project's fixed manifest and event audit records."""
    canonical_id = _safe_run_id(run_id)
    try:
        run_dir = RunRegistry(frontend_api.ROOT).resolve_run_id(canonical_id)
    except RunRegistryError:
        raise HTTPException(status_code=404, detail="未找到该工程") from None
    return {
        "run_id": canonical_id,
        "manifest": _read_public_manifest_record(run_dir),
        "events": _read_run_record(
            run_dir,
            filenames=("events.jsonl", "events.json"),
            record_name="events",
            tail_when_truncated=True,
        ),
    }


@app.post("/api/proposal/upload")
def proposal_upload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize one browser-uploaded quantum input through the existing boundary."""
    filename = payload.get("filename")
    encoded = payload.get("content_base64")
    if not isinstance(filename, str) or not filename:
        raise HTTPException(status_code=400, detail="上传文件缺少文件名")
    if "/" in filename or "\\" in filename or Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="上传文件名无效")
    suffix = Path(filename).suffix.casefold()
    if suffix not in supported_upload_suffixes():
        raise HTTPException(
            status_code=400,
            detail=f"仅支持可审计的原始量子输入：{'、'.join(supported_upload_suffixes())}",
        )
    if not isinstance(encoded, str) or not encoded:
        raise HTTPException(status_code=400, detail="上传内容无效")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="上传内容不是有效的 Base64 数据") from None
    if not content or len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="上传文件不能为空且不得超过 5 MiB")

    try:
        with tempfile.TemporaryDirectory(prefix="willy-upload-") as temporary_dir:
            source = Path(temporary_dir) / filename
            source.write_bytes(content)
            entry = normalize_uploaded_structure(source, project_root=frontend_api.ROOT)
        from willy.toolist_global import _registry
        _registry._load()
    except StructureUploadError as exc:
        raise HTTPException(status_code=400, detail=f"上传失败：{exc}") from exc
    except OSError:
        raise HTTPException(status_code=503, detail="上传文件暂时无法写入，请稍后重试") from None
    for state in _threads.values():
        state["pending_plan"] = None
    return {
        "text": f"已上传{filename}，仅保留坐标、电荷、自旋。",
        "structure": entry.as_dict(),
    }


@app.post("/api/proposal/chat")
def proposal_chat(payload: dict[str, Any]) -> dict[str, Any]:
    """Serve the proposal assistant's isolated browser thread."""
    thread_id = str(payload.get("threadId") or "default")
    if len(thread_id) > 128:
        raise HTTPException(status_code=400, detail="方案助理线程标识过长")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="messages 必须是数组")
    history = _assistant_history(messages)
    if not history:
        return {"text": "请描述需要建立的模拟体系，或询问当前分子库。"}
    state = _threads.setdefault(thread_id, {"pending_plan": None})
    latest_user = next(
        (item["content"] for item in reversed(history) if item["role"] == "user"),
        "",
    )
    prior_run_id = frontend_api.latest_run_id()
    updates = list(chat(latest_user, history[:-1], state.get("pending_plan")))
    if updates:
        _, _, _, pending_plan, _, _ = updates[-1]
        state["pending_plan"] = pending_plan
        reply = updates[-1][1][-1].get("content", "")
    else:
        reply = "暂时没有生成新的回复。"
    current_run_id = frontend_api.latest_run_id()
    response = {"text": reply, "threadId": thread_id}
    if isinstance(current_run_id, str) and current_run_id != prior_run_id:
        response["runId"] = current_run_id
    return response


@app.post("/api/chat")
def assistant_chat(payload: dict[str, Any]) -> dict[str, Any]:
    """Compatibility alias for the first assistant-ui prototype."""
    return proposal_chat(payload)


def _config_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key, "")
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{key} 必须是文本")
    return value


@app.get("/api/config")
def configuration() -> dict[str, Any]:
    """Return the existing configuration-page public state, never a secret."""
    return {
        "provider_mode": frontend_api.get_llm_provider_mode(),
        "status": frontend_api.get_llm_config_status(),
        "notice": frontend_api.get_llm_config_notice(),
        "execution": frontend_api.get_execution_profile_snapshot(),
    }


@app.post("/api/config/test")
def test_configuration(payload: dict[str, Any]) -> dict[str, Any]:
    """Probe one submitted LLM configuration without persisting it."""
    result = frontend_api.test_llm_connection(
        _config_text(payload, "api_key"),
        _config_text(payload, "base_url"),
        _config_text(payload, "model"),
    )
    return dict(result)


@app.post("/api/config/save")
def save_configuration(payload: dict[str, Any]) -> dict[str, str]:
    """Persist a user-entered local LLM configuration through the existing boundary."""
    message = frontend_api.save_llm_config(
        _config_text(payload, "api_key"),
        _config_text(payload, "base_url"),
        _config_text(payload, "model"),
    )
    return {"message": message}


@app.post("/api/config/preflight")
def configuration_preflight() -> dict[str, Any]:
    """Run the advisory preflight; callers remain free to launch afterwards."""
    try:
        return dict(frontend_api.run_local_dependency_preflight())
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="依赖预检执行失败，请检查本机环境后重试。",
        ) from None


@app.get("/api/runs/{run_id}/chat")
def get_run_chat(run_id: str) -> dict[str, Any]:
    """Load one project-local run conversation and the latest public status."""
    canonical_id = _safe_run_id(run_id)
    snapshot = _public_snapshot(canonical_id)
    history = _merge_status_into_history(_run_history(canonical_id), snapshot)
    _save_run_history(canonical_id, history)
    return _run_chat_response(canonical_id, history)


@app.post("/api/runs/{run_id}/chat")
def run_chat(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Handle one run-assistant message through existing control boundaries."""
    canonical_id = _safe_run_id(run_id)
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise HTTPException(status_code=400, detail="message 必须是非空文本")
    if len(message) > 6_000:
        raise HTTPException(status_code=400, detail="message 超过长度限制")
    message = message.strip()

    snapshot = _public_snapshot(canonical_id)
    history = _merge_status_into_history(_run_history(canonical_id), snapshot)
    control_reply = frontend_api.run_assistant_control_command(canonical_id, message)
    target_run_id = canonical_id
    if isinstance(control_reply, str):
        switch_target = frontend_api.get_run_assistant_switch_target(message)
        if switch_target and control_reply.startswith("已切换至工程"):
            _save_run_history(canonical_id, history)
            target_run_id = _safe_run_id(switch_target)
            history = _merge_status_into_history(
                _run_history(target_run_id), _public_snapshot(target_run_id)
            )
        else:
            followed = _follow_controlled_run(canonical_id, control_reply)
            if followed != canonical_id:
                _save_run_history(canonical_id, history)
                target_run_id = _safe_run_id(followed)
                history = _merge_status_into_history(
                    _run_history(target_run_id), _public_snapshot(target_run_id)
                )
        reply = control_reply
    else:
        try:
            reply = frontend_api.chat_run_assistant(
                canonical_id, message, _llm_run_history(history)
            )
        except Exception:
            reply = "运行助理暂时不可用，请稍后重试。"

    history.extend([
        {"role": "user", "content": message},
        {"role": "assistant", "content": reply},
    ])
    final_snapshot = _public_snapshot(target_run_id)
    history = _merge_status_into_history(history, final_snapshot)
    _save_run_history(target_run_id, history)
    response = _run_chat_response(target_run_id, history)
    response["text"] = reply
    return response


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: str) -> dict[str, Any]:
    """Request the same checkpoint-first stop exposed by the legacy UI."""
    canonical_id = _safe_run_id(run_id)
    if frontend_api.get_active_run_id() != canonical_id:
        raise HTTPException(status_code=409, detail="只能中止当前活动流水线")
    message = frontend_api.stop_pipeline(clean=False)
    return {
        "run_id": canonical_id,
        "message": message,
        "snapshot": _public_snapshot(canonical_id),
    }


@app.get("/api/runs/{run_id}/updates")
def run_updates(
    run_id: str,
    after: str | None = Query(default=None, max_length=256),
) -> dict[str, Any]:
    """Poll bounded public state updates for a run-assistant conversation.

    ``after`` is the prior ``cursor``.  On a new state transition, consumers
    receive public completion notices and a latest status card, each with a
    stable event ID for client-side deduplication.
    """
    canonical_id = _safe_run_id(run_id)
    snapshot = _public_snapshot(canonical_id)
    cursor = snapshot.get("status_event_id")
    if not isinstance(cursor, str) or not cursor:
        cursor = f"{canonical_id}:status:unknown"
    events = [] if after == cursor else [*_completion_events(snapshot), _status_event(snapshot)]
    history = _merge_status_into_history(_run_history(canonical_id), snapshot)
    _save_run_history(canonical_id, history)
    return {
        "run_id": canonical_id,
        "snapshot": snapshot,
        "cursor": cursor,
        "events": events,
    }


@app.get("/api/runs/{run_id}/snapshot")
def run_snapshot(run_id: str) -> dict[str, Any]:
    return _public_snapshot(_safe_run_id(run_id))


@app.get("/api/visualization")
def visualization(
    run_id: str | None = Query(default=None, max_length=128),
    artifact: str | None = Query(default=None, max_length=512),
    sphere_scale: str | None = Query(default=None, max_length=32),
    stick_radius: str | None = Query(default=None, max_length=32),
    background: str | None = Query(default=None, max_length=16),
) -> dict[str, Any]:
    """Return authorized run-relative structure artifacts for the viewer tab."""
    normalized_sphere_scale = _viewer_control_value(
        sphere_scale,
        name="球体大小",
        default=frontend_api.DEFAULT_SPHERE_SCALE,
        value_range=frontend_api.VIEWER_SPHERE_SCALE_RANGE,
    )
    normalized_stick_radius = _viewer_control_value(
        stick_radius,
        name="棍宽度",
        default=frontend_api.DEFAULT_STICK_RADIUS,
        value_range=frontend_api.VIEWER_STICK_RADIUS_RANGE,
    )
    normalized_background = _viewer_background_value(background)
    choices = frontend_api.get_run_visualization_run_choices()
    selected_run_id = _selected_run_id(run_id)
    if selected_run_id is None:
        selected_run_id = choices[0] if choices else None
    if selected_run_id is not None and selected_run_id not in choices:
        # The registry may have changed between the two read-only calls.
        raise HTTPException(status_code=404, detail="未找到可视化工程")
    artifacts = frontend_api.get_run_visualization_file_choices(selected_run_id)
    if artifact is not None and artifact not in artifacts:
        raise HTTPException(status_code=400, detail="未找到指定结构产物")
    selected_artifact = artifact or (artifacts[0] if artifacts else None)
    response: dict[str, Any] = {
        "run_choices": choices,
        "run_id": selected_run_id,
        "artifacts": artifacts,
        "selected_artifact": selected_artifact,
        "sphere_scale": normalized_sphere_scale,
        "stick_radius": normalized_stick_radius,
        "background": normalized_background,
    }
    if selected_artifact is not None:
        response["html"] = frontend_api.render_run_visualization_html(
            selected_artifact,
            sphere_scale=normalized_sphere_scale,
            stick_radius=normalized_stick_radius,
            background=normalized_background,
            run_id=selected_run_id,
        )
        response["legend_html"] = frontend_api.render_run_visualization_legend_html(
            selected_artifact, run_id=selected_run_id
        )
    return response


if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{path:path}")
def frontend(path: str = ""):
    if not DIST.is_dir():
        return JSONResponse(
            {"detail": "前端尚未构建，请在 frontend/ 执行 npm install && npm run build"},
            status_code=503,
        )
    dist_root = DIST.resolve()
    target = (DIST / path).resolve()
    if target.is_file() and target.is_relative_to(dist_root):
        return FileResponse(target)
    return FileResponse(DIST / "index.html")


if __name__ == "__main__":
    port = int(os.getenv("WILLY_SERVER_PORT") or dotenv_value("WILLY_SERVER_PORT") or "7860")
    uvicorn.run(app, host="127.0.0.1", port=port)
