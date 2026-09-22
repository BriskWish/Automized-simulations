"""Willy FastAPI entry point for the React assistant-ui workbench."""

from __future__ import annotations

import base64
import binascii
from contextlib import ExitStack, asynccontextmanager, contextmanager
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, Iterator, Mapping

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from willy.python_runtime import PythonRuntimeError, require_python_runtime

try:
    _python_runtime = require_python_runtime()
except PythonRuntimeError as exc:
    raise SystemExit(f"Willy 启动检查失败：{exc}") from None

if __name__ == "__main__" and sys.argv[1:] == ["--check-runtime"]:
    print(f"Python {_python_runtime['python_version']}：运行时与依赖检查通过。")
    raise SystemExit(0)

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from willy import frontend_api
from willy.agent_config import chat
from willy.env_registry import dotenv_value
from willy.pipeline_launch import pipeline_launch_is_active
from willy.proposal_workspace import (
    ProposalWorkspaceError,
    clear_pending_plans,
    create_plan_from_run_conversation,
    create_temp_workspace,
    delete_local_item,
    ensure_default_workspace,
    get_run_conversation,
    get_run_conversation_history,
    get_workspace,
    get_workspace_history,
    is_run_conversation_id,
    list_workspaces,
    save_formalized_conversation,
    save_run_conversation,
    save_workspace_conversation,
)
from willy.run_registry import RunRegistry, RunRegistryError
from willy.step_registry import EQ_STEP, STEP_REGISTRY
from willy.structure_uploads import (
    StructureUploadError,
    normalize_uploaded_structure,
    supported_upload_suffixes,
)


DIST = ROOT / "frontend" / "dist"
OFFICIAL_ACCOUNT_QR = ROOT / "assets" / "qrcode_for_gh_7df1329939c6_258.jpg"


@asynccontextmanager
async def _workbench_lifespan(_application: FastAPI):
    """Recover stale snapshots before the workbench serves them.

    This only terminates an already-unowned transient state after the existing
    ETA/output heartbeat grace period. It never starts or resumes a run.
    """
    frontend_api.reconcile_stale_pipeline_states(source="service_startup")
    yield


app = FastAPI(title="Willy assistant-ui", lifespan=_workbench_lifespan)
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_LAUNCHED_RUN_RE = re.compile(r"(?:^|\n)运行：(?P<run_id>md__\d{12})(?:\n|$)")
_RUN_STATUS_EVENT_KIND = "_run_assistant_event_kind"
_RUN_STATUS_EVENT_ID = "_run_assistant_event_id"
_RUN_STATUS_EVENT_ACTIVE = "_run_assistant_event_active"
_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
_MAX_LOG_RECORD_BYTES = 1024 * 1024
_DISPLAY_NAME_FILENAME = ".willy_display_names.json"
_DISPLAY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_PENDING_ACTION_CONFIRMATION_RE = re.compile(
    r"^(?:确认|确认(?:调参|重跑|方案)|同意(?:调参|重跑|方案)|批准(?:调参|重跑|方案))$"
)
_ORIGINAL_PARAMETER_RESUME_RE = re.compile(
    r"^(?:/resume|(?:按)?原参数(?:续跑|重跑|继续)|不改参数(?:继续|续跑|重跑))[。！!]*$"
)
_PENDING_ACTION_REVISION_RE = re.compile(
    r"^(?:请)?(?:把|将)\s*\S.*?\s*(?:改为|改成|设置为)\s*\S.*$"
)
_PENDING_ACTION_DISCUSSION_RE = re.compile(
    r"[?？;；。\n\r\"'“”‘’]|不|别|勿|没|无需|无须|禁止|取消|吗|么|呢|是否|能|会|可否|"
    r"可以|什么|如何|怎么|为何|为什么|合理|合适|可行|意味着|意思|"
    r"如果|假如|比如|例如|讨论|考虑|还是|或者|应该|觉得|认为|听说|据说|似乎"
)
_PENDING_ACTION_BINDING_FIELDS = ("action_id", "state_revision", "config_fingerprint")
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


def _new_plan_exchange(user_message: str, reply: str) -> list[dict[str, str]]:
    """Start a derived plan with this request only, never its source-run log."""
    exchange = [{"role": "user", "content": user_message}]
    if isinstance(reply, str) and reply.strip():
        exchange.append({"role": "assistant", "content": reply})
    return exchange


def _proposal_id(payload: Mapping[str, object]) -> str:
    proposal_id = payload.get("proposalId")
    if not isinstance(proposal_id, str) or not proposal_id:
        raise HTTPException(status_code=400, detail="方案工作区标识无效")
    return proposal_id


def _append_uploaded_structure_message(proposal_id: str, content: str) -> tuple[str, list[dict[str, str]]]:
    """Persist one normalized-upload notice as a user message in its workspace."""
    message = {"role": "user", "content": content}
    if is_run_conversation_id(proposal_id):
        canonical_id = _safe_run_id(proposal_id)
        get_run_conversation(frontend_api.ROOT, canonical_id)
        history = get_run_conversation_history(frontend_api.ROOT, canonical_id)
        saved = save_run_conversation(
            frontend_api.ROOT,
            canonical_id,
            messages=[*history, message],
        )
        return canonical_id, saved["messages"]
    workspace = get_workspace(frontend_api.ROOT, proposal_id)
    history = get_workspace_history(frontend_api.ROOT, proposal_id)
    saved = save_workspace_conversation(
        frontend_api.ROOT,
        workspace["workspace_id"],
        messages=[*history, message],
        pending_plan=None,
    )
    return saved["workspace_id"], saved["messages"]


def _launched_run_id(reply: str) -> str | None:
    match = _LAUNCHED_RUN_RE.search(reply)
    return match.group("run_id") if match else None


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


@contextmanager
def _open_run_logs_directory(run_id: str) -> Iterator[int]:
    """Pin the selected directory without following run or md_run symlinks."""
    with ExitStack() as stack:
        directory_fd = None
        try:
            for name in (Path(frontend_api.ROOT).resolve(), "md_run", run_id):
                directory_fd = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
                stack.callback(os.close, directory_fd)
        except (OSError, RuntimeError):
            raise HTTPException(status_code=404, detail="未找到该工程") from None
        yield directory_fd


def _read_run_record_bytes(
    directory_fd: int, filename: str, *, allow_truncated: bool = False,
) -> bytes | None:
    """Read a bounded regular file relative to an already-pinned run directory."""
    if filename not in {"config.json", "run_manifest.json", "manifest.json"}:
        raise HTTPException(status_code=404, detail="未找到该记录")
    try:
        record_fd = os.open(
            filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
    except FileNotFoundError:
        return None
    except OSError:
        raise HTTPException(status_code=503, detail=f"{filename} 记录暂时无法读取") from None
    try:
        with os.fdopen(record_fd, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise HTTPException(status_code=503, detail=f"{filename} 不是常规记录文件")
            if metadata.st_size > _MAX_LOG_RECORD_BYTES and not allow_truncated:
                raise HTTPException(status_code=503, detail=f"{filename} 记录过大，无法安全显示")
            payload = handle.read(_MAX_LOG_RECORD_BYTES + 1)
    except OSError:
        raise HTTPException(status_code=503, detail=f"{filename} 记录暂时无法读取") from None
    if len(payload) > _MAX_LOG_RECORD_BYTES and not allow_truncated:
        raise HTTPException(status_code=503, detail=f"{filename} 记录过大，无法安全显示")
    return payload


def _read_run_record(directory_fd: int) -> dict[str, Any]:
    """Read the legacy manifest with its existing bounded text presentation."""
    payload = _read_run_record_bytes(directory_fd, "manifest.json", allow_truncated=True)
    if payload is None:
        return {
            "filename": "manifest.json",
            "content": "当前工程尚无 manifest 记录。",
            "truncated": False,
        }
    truncated = len(payload) > _MAX_LOG_RECORD_BYTES
    content = payload[:_MAX_LOG_RECORD_BYTES].decode("utf-8", errors="replace")
    if truncated:
        content = f"[记录过大，仅显示前 {_MAX_LOG_RECORD_BYTES // 1024} KiB]\n{content}"
    return {"filename": "manifest.json", "content": content, "truncated": truncated}


def _read_public_manifest_record(directory_fd: int) -> dict[str, Any]:
    """Project only the public registry section from a schema-v2 manifest."""
    payload = _read_run_record_bytes(directory_fd, "run_manifest.json")
    if payload is None:
        return _read_run_record(directory_fd)
    try:
        manifest = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
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
        "filename": "run_manifest.json",
        "content": content,
        "truncated": truncated,
    }


def _public_log_config_value(value: Any) -> Any:
    """Remove private credential and connection fields at every nesting level."""
    if isinstance(value, dict):
        public = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized in {
                "key", "user", "username", "host", "hostname", "port", "url",
                "env", "environment", "headers", "proxy", "proxies", "llm",
                "connection", "connections", "remote", "login",
            } or any(marker in normalized for marker in (
                "apikey", "token", "secret", "password", "passwd", "credential",
                "auth", "private", "accesskey", "clientkey", "ssh", "cookie",
                "baseurl", "endpoint",
            )):
                continue
            public[key] = _public_log_config_value(item)
        return public
    if isinstance(value, list):
        return [_public_log_config_value(item) for item in value]
    return value


def _finite_log_config_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("config 包含非有限数值")
    return number


def _read_public_config_record(directory_fd: int) -> dict[str, Any]:
    """Format only the selected run's public science configuration, never defaults."""
    payload = _read_run_record_bytes(directory_fd, "config.json")
    if payload is None:
        raise HTTPException(status_code=503, detail="当前工程缺少 config.json")
    try:
        config = json.loads(
            payload.decode("utf-8"), parse_float=_finite_log_config_number,
            parse_constant=_finite_log_config_number,
        )
        if not isinstance(config, dict) or not config:
            raise ValueError("config 必须是非空对象")
        public = _public_log_config_value({
            key: value for key, value in config.items() if key in {
                "schema_version", "backend", "defaults", "molecules", "residues",
                "md", "topology", "box", "ion_compensation", "non_neutral_confirmed",
                "ion_charge_scale", "forcefield", "force_field", "execution",
            }
        })
        execution = public.get("execution")
        if isinstance(execution, dict):
            public["execution"] = {
                key: value for key, value in execution.items() if key == "stop_after_stage"
                and isinstance(value, (str, type(None)))
            }
            if isinstance(execution.get("md"), dict):
                public["execution"]["md"] = {
                    key: value for key, value in execution["md"].items()
                    if key in {"backend", "profile", "retain_remote_run"}
                    and isinstance(value, (str, bool, type(None)))
                }
        if not public:
            raise ValueError("config 缺少公开配置")
        content = json.dumps(public, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if len(content.encode("utf-8")) > _MAX_LOG_RECORD_BYTES:
            raise HTTPException(status_code=503, detail="config.json 记录过大，无法安全显示")
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(status_code=503, detail="当前工程 config.json 为空或无效") from None
    return {"filename": "config.json", "content": content, "truncated": False}


def _public_snapshot(run_id: str | None) -> dict[str, Any]:
    """Return the existing panel contract plus stable, display-only run facts."""
    fault_review = frontend_api.refresh_run_fault(run_id)
    snapshot = dict(frontend_api.get_run_panel_snapshot(run_id))
    if fault_review:
        snapshot["fault_review"] = fault_review
    snapshot.setdefault("run_id", run_id)
    state = "await"
    revision = None
    step: int | None = None
    done_steps: list[int] = []
    if isinstance(run_id, str):
        try:
            status = RunRegistry(frontend_api.ROOT).get_run_status(run_id, reconcile=False)
        except RunRegistryError:
            status = {}
        value = status.get("state")
        if isinstance(status.get("fault"), dict):
            snapshot["fault"] = status["fault"]
        state = value if isinstance(value, str) and value else "unknown"
        revision = status.get("state_revision")
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
        # A run is registered before its pipeline instance binds status.json.
        # Project only that short, identifiable gap as preparation; corrupted
        # or otherwise unreadable status remains visibly unknown.
        if _is_initial_preparing_state(state, step, done_steps, status.get("message")):
            state = "preparing"
            snapshot["summary"] = "工程正在准备中，将从第 1 步开始。"
            snapshot["live_summary"] = "工程正在准备中，将从第 1 步开始。"
    completed = sorted(set(done_steps))
    snapshot["state"] = state
    snapshot["state_revision"] = revision
    snapshot["step"] = step
    snapshot["phase"] = "准备中" if state == "preparing" else _step_label(step)
    snapshot["progress"] = _display_progress(state, step, completed)
    snapshot["done_steps"] = completed
    return snapshot


def _is_initial_preparing_state(
    state: str,
    step: object,
    done_steps: object,
    message: object,
) -> bool:
    """Identify only the registered-run gap before the first status snapshot."""
    return (
        state == "unknown" and message == "该 run 尚未写入状态快照"
    ) or (
        state == "idle"
        and (
            step is None
            or (isinstance(step, int) and not isinstance(step, bool) and step == 0)
        )
        and not done_steps
    )


def _display_progress(state: str, step: int | None, done_steps: list[int]) -> str:
    """Show the active step, while retaining completed progress for inactive states."""
    total = STEP_REGISTRY.total_steps
    completed_count = len(done_steps)
    if state == "done":
        current = total
    elif state == "preparing":
        current = 1
    elif state in {"running", "retrying"}:
        active_step = step if isinstance(step, int) and 1 <= step <= total else 0
        # A snapshot can be observed after mark_done() and before set_step().
        # In that interval the next unfinished step is the active progress.
        current = max(active_step, completed_count + 1)
    else:
        current = completed_count
    return f"{min(total, max(0, current))}/{total}"


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


def _status_event(snapshot: Mapping[str, object]) -> dict[str, object]:
    event_id = snapshot.get("status_event_id")
    run_id = snapshot.get("run_id")
    safe_event_id = event_id if isinstance(event_id, str) and event_id else f"{run_id or 'none'}:status"
    summary = snapshot.get("live_summary", snapshot.get("summary"))
    content = summary if isinstance(summary, str) and summary.strip() else "当前工程状态暂不可读取。"
    state = str(snapshot.get("state", "")).casefold()
    step = snapshot.get("step")
    done_steps = snapshot.get("done_steps")
    completed = set(done_steps) if isinstance(done_steps, list) else set()
    active = (
        state in {"running", "retrying"}
        and isinstance(step, int)
        and not isinstance(step, bool)
        and step not in completed
    )
    return {
        "event_id": safe_event_id,
        "kind": "status",
        "content": content,
        "active": active,
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


def _completion_artifacts_event(snapshot: Mapping[str, object]) -> dict[str, str] | None:
    """Render the fixed, display-only production handoff after all steps finish."""
    run_id = snapshot.get("run_id")
    done_steps = snapshot.get("done_steps")
    if (
        snapshot.get("state") != "done"
        or not isinstance(run_id, str)
        or not _RUN_ID_PATTERN.fullmatch(run_id)
        or not isinstance(done_steps, list)
    ):
        return None
    completed = {
        step for step in done_steps
        if isinstance(step, int) and not isinstance(step, bool)
    }
    if not set(range(1, STEP_REGISTRY.total_steps + 1)).issubset(completed):
        return None
    return {
        "event_id": f"{run_id}:completion_artifacts:v1",
        "kind": "completion_artifacts",
        "content": (
            "全流程完成\n\n"
            f"当前工程文件保存在：../Willy/md_run/{run_id} 中。\n\n"
            "核心生产文件：prod.gro、prod.xtc、prod.trr、prod.edr 等。"
        ),
    }


def _pending_action_event(snapshot: Mapping[str, object]) -> dict[str, str] | None:
    """Render the public EQ recovery proposal as a durable run-assistant turn."""
    action = snapshot.get("pending_action")
    run_id = snapshot.get("run_id")
    if (
        snapshot.get("state") != "awaiting_confirmation"
        or not isinstance(action, Mapping)
        or not isinstance(run_id, str)
    ):
        return None
    action_id = action.get("action_id")
    summary = action.get("summary")
    restart_step = action.get("restart_step")
    if (
        not isinstance(action_id, str)
        or not action_id
        or not isinstance(summary, str)
        or not summary.strip()
        or not STEP_REGISTRY.controlled_restart_allowed(EQ_STEP, restart_step)
    ):
        return None

    def clean(value: object, limit: int) -> str:
        if not isinstance(value, str):
            return ""
        return " ".join(value.split())[:limit]

    def render_adjustments(adjustments: object) -> list[str]:
        rendered: list[str] = []
        for adjustment in adjustments[:8] if isinstance(adjustments, list) else []:
            if not isinstance(adjustment, Mapping):
                continue
            name = clean(adjustment.get("name"), 80)
            before = clean(adjustment.get("before"), 80)
            after = clean(adjustment.get("after"), 80)
            purpose = clean(adjustment.get("purpose"), 160)
            if not name or not before or not after:
                continue
            detail = f"- {name}: {before} -> {after}"
            if purpose:
                detail += f"（{purpose}）"
            rendered.append(detail)
        return rendered

    report = action.get("recovery_plan")
    if not isinstance(report, Mapping):
        report = {
            "problem": summary,
            "current_step_retry": {
                "applicable": True,
                "options": [{
                    "summary": summary,
                    "restart_step": restart_step,
                    "adjustments": action.get("adjustments", []),
                    "evidence": ["当前公开错误与 EQ 阶段验收结果需要人工复核。"],
                }],
            },
            "upstream_retry": {
                "applicable": False,
                "summary": "当前证据不支持打回前序流程重试。",
                "evidence": ["没有经校验的建盒或前序阶段问题证据。"],
            },
            "evidence": ["当前公开错误、阶段验收结果和冻结运行配置是本方案的证据边界。"],
            "risk_level": "high",
            "manual_review_required": True,
        }

    problem = clean(report.get("problem"), 500) or clean(summary, 500)
    lines = ["待确认调整方案", "问题：", problem]
    summary_text = clean(summary, 500)
    if summary_text and summary_text != problem:
        lines.extend(["当前建议摘要：", summary_text])
    lines.append("方案：")
    evidence_points: list[str] = []
    for value in report.get("evidence", []) if isinstance(report.get("evidence"), list) else []:
        point = clean(value, 240)
        if point and point not in evidence_points:
            evidence_points.append(point)

    def render_path(title: str, path: object) -> list[str]:
        if not isinstance(path, Mapping) or path.get("applicable") is not True:
            summary_text = clean(path.get("summary"), 300) if isinstance(path, Mapping) else ""
            result = [title, f"- 不适用：{summary_text or '当前证据不足，未生成可执行修改。'}"]
            path_evidence = path.get("evidence", []) if isinstance(path, Mapping) else []
            for value in path_evidence if isinstance(path_evidence, list) else []:
                point = clean(value, 240)
                if point and point not in evidence_points:
                    evidence_points.append(point)
            return result
        result = [title]
        options = path.get("options", [])
        for ordinal, option in enumerate(options[:3] if isinstance(options, list) else [], start=1):
            if not isinstance(option, Mapping):
                continue
            option_summary = clean(option.get("summary"), 300)
            option_title = clean(option.get("title"), 100)
            heading = f"- {option_title or f'候选方案 {ordinal}'}"
            if option_summary:
                heading += f"：{option_summary}"
            result.append(heading)
            option_restart = option.get("restart_step")
            if STEP_REGISTRY.controlled_restart_allowed(EQ_STEP, option_restart):
                result.append(f"  将从第 {option_restart} 步开始受控重跑。")
            adjustments = render_adjustments(option.get("adjustments"))
            if adjustments:
                result.extend(["  调整内容：", *[f"  {item}" for item in adjustments]])
            for value in option.get("evidence", []) if isinstance(option.get("evidence"), list) else []:
                point = clean(value, 240)
                if point and point not in evidence_points:
                    evidence_points.append(point)
        return result

    lines.extend(render_path("1. 当前步调参重试", report.get("current_step_retry")))
    lines.extend(render_path("2. 打回前序流程重试", report.get("upstream_retry")))
    lines.append("证据：")
    lines.extend([f"- {point}" for point in evidence_points[:6]] or ["- 当前公开证据不足，未自动扩大恢复范围。"])
    if report.get("manual_review_required") is True or report.get("risk_level") == "high":
        lines.append("风险：高风险科学协议变更，必须经人工审核和明确确认后才会重跑。")
    return {
        "event_id": f"{run_id}:pending_action:{action_id}",
        "kind": "pending_action",
        "content": "\n\n".join(lines[:4]) + ("\n" + "\n".join(lines[4:]) if len(lines) > 4 else ""),
    }


def _error_event(snapshot: Mapping[str, object]) -> dict[str, str] | None:
    """Reuse the server's bounded error projection as a durable chat turn."""
    event = snapshot.get("error_event")
    if not isinstance(event, Mapping):
        return None
    event_id = event.get("event_id")
    content = event.get("content")
    if not isinstance(event_id, str) or not event_id or not isinstance(content, str) or not content.strip():
        return None
    # Legacy summary helpers include presentational HTML indicators. The
    # assistant-ui renderer owns visual state, so only retain readable text.
    safe_content = re.sub(r"<[^>]+>", "", content).strip()
    return {"event_id": event_id, "kind": "error", "content": safe_content[:1_600]}


def _merge_status_into_history(
    history: list[dict[str, str]],
    snapshot: Mapping[str, object],
) -> list[dict[str, str]]:
    """Persist completed steps and exactly one current status notice."""
    pending_event = _pending_action_event(snapshot)
    merged = [
        dict(message)
        for message in history
        if isinstance(message, Mapping)
        and message.get(_RUN_STATUS_EVENT_KIND) not in {"status", "completion_artifacts"}
        and (
            message.get(_RUN_STATUS_EVENT_KIND) != "pending_action"
            or (
                pending_event is not None
                and message.get(_RUN_STATUS_EVENT_ID) == pending_event["event_id"]
            )
        )
    ]
    known_ids = {
        message.get(_RUN_STATUS_EVENT_ID)
        for message in merged
        if isinstance(message.get(_RUN_STATUS_EVENT_ID), str)
    }
    for event in _completion_events(snapshot):
        if event["event_id"] in known_ids:
            continue
        merged.append({
            "role": "assistant",
            "content": event["content"],
            _RUN_STATUS_EVENT_KIND: event["kind"],
            _RUN_STATUS_EVENT_ID: event["event_id"],
        })
        known_ids.add(event["event_id"])
    status = _status_event(snapshot)
    status_message = {
        "role": "assistant",
        "content": status["content"],
        _RUN_STATUS_EVENT_KIND: status["kind"],
        _RUN_STATUS_EVENT_ID: status["event_id"],
        _RUN_STATUS_EVENT_ACTIVE: status["active"],
    }
    completion_artifacts_event = _completion_artifacts_event(snapshot)
    if pending_event is None:
        error_event = _error_event(snapshot)
        existing_error_index = next(
            (
                index for index, message in enumerate(merged)
                if message.get(_RUN_STATUS_EVENT_KIND) == "error"
            ),
            None,
        )
        if existing_error_index is None:
            merged.append(status_message)
        else:
            merged.insert(existing_error_index, status_message)
        if error_event is not None and error_event["event_id"] not in known_ids:
            merged.append({
                "role": "assistant",
                "content": error_event["content"],
                _RUN_STATUS_EVENT_KIND: error_event["kind"],
                _RUN_STATUS_EVENT_ID: error_event["event_id"],
            })
        if completion_artifacts_event is not None:
            merged.append({
                "role": "assistant",
                "content": completion_artifacts_event["content"],
                _RUN_STATUS_EVENT_KIND: completion_artifacts_event["kind"],
                _RUN_STATUS_EVENT_ID: completion_artifacts_event["event_id"],
            })
        return merged

    # Keep an already persisted proposal directly below the refreshed current
    # state. A replacement proposal has a different action ID and is appended.
    pending_index = next(
        (
            index for index, message in enumerate(merged)
            if message.get(_RUN_STATUS_EVENT_ID) == pending_event["event_id"]
        ),
        None,
    )
    if pending_index is None:
        merged.extend([
            status_message,
            {
                "role": "assistant",
                "content": pending_event["content"],
                _RUN_STATUS_EVENT_KIND: pending_event["kind"],
                _RUN_STATUS_EVENT_ID: pending_event["event_id"],
            },
        ])
    else:
        merged[pending_index]["content"] = pending_event["content"]
        merged.insert(pending_index, status_message)
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


def _run_chat_response(
    run_id: str,
    history: list[dict[str, str]],
    *,
    snapshot: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    if snapshot is None:
        snapshot = _public_snapshot(run_id)
    return {
        "run_id": run_id,
        "messages": history,
        "snapshot": snapshot,
    }


def _save_run_chat_exchange(
    run_id: str,
    history: list[dict[str, str]],
    message: str,
    reply: str,
) -> dict[str, Any]:
    """Persist an exchange and project its notices from the response snapshot."""
    history = history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": reply},
    ]
    snapshot = _public_snapshot(run_id)
    history = _merge_status_into_history(history, snapshot)
    _save_run_history(run_id, history)
    response = _run_chat_response(run_id, history, snapshot=snapshot)
    response["text"] = reply
    return response


def _resume_original_parameters(
    run_id: str,
    submitted: Mapping[str, object],
    snapshot: Mapping[str, object],
) -> str:
    """Check the browser revision before resuming this run without a proposal."""
    revision = submitted.get("state_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise HTTPException(status_code=400, detail="续跑请求缺少有效的 state_revision")
    current_revision = snapshot.get("state_revision")
    if (
        isinstance(current_revision, bool)
        or not isinstance(current_revision, int)
        or current_revision != revision
    ):
        raise HTTPException(status_code=409, detail="工程状态已更新，请刷新后重新续跑")
    if snapshot.get("state") not in {"aborted", "awaiting_confirmation", "escalated"}:
        raise HTTPException(status_code=409, detail="当前工程状态不允许原参数续跑")
    return frontend_api.resume_aborted_run(run_id, state_revision=revision)


def _pending_action_revision_request(message: str) -> str | None:
    """Accept narrow editing imperatives, never questions or negated requests."""
    message = message.rstrip("。！!")
    if _PENDING_ACTION_DISCUSSION_RE.search(message):
        return None
    explicit = re.fullmatch(r"(?:请)?修改方案[：:]\s*(\S.*)", message)
    request = explicit.group(1) if explicit else message
    if explicit or _PENDING_ACTION_REVISION_RE.fullmatch(message):
        return request.strip()
    return None


def _is_pending_action_confirmation(message: object) -> bool:
    """Recognize only deliberate, bounded approval words from the Run UI."""
    return isinstance(message, str) and bool(
        _PENDING_ACTION_CONFIRMATION_RE.fullmatch(message.strip())
    )


def _waiting_pending_action(
    run_id: str,
    submitted: Mapping[str, object] | None = None,
) -> Mapping[str, object] | None:
    """Validate one current EQ action only while its run is awaiting approval.

    ``get_pending_action`` is intentionally state-scoped: it returns no action
    outside ``awaiting_confirmation``.  The direct browser control additionally
    has to echo the current action identity, state revision, and configuration
    fingerprint; the lower API repeats these checks under the action lock.
    """
    action = frontend_api.get_pending_action(run_id)
    if not isinstance(action, Mapping):
        if submitted is not None:
            raise HTTPException(status_code=409, detail="当前没有有效的待确认方案")
        return None
    action_id = action.get("action_id")
    revision = action.get("state_revision")
    fingerprint = action.get("config_fingerprint")
    if (
        not isinstance(action_id, str)
        or not action_id
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
        or not isinstance(fingerprint, str)
        or not fingerprint
        or action.get("run_id", run_id) != run_id
    ):
        if submitted is not None:
            raise HTTPException(status_code=409, detail="当前没有有效的待确认方案")
        return None
    if submitted is not None:
        submitted_action_id = submitted.get("action_id")
        submitted_revision = submitted.get("state_revision")
        submitted_fingerprint = submitted.get("config_fingerprint")
        if (
            not isinstance(submitted_action_id, str)
            or not submitted_action_id
            or isinstance(submitted_revision, bool)
            or not isinstance(submitted_revision, int)
            or submitted_revision < 0
            or not isinstance(submitted_fingerprint, str)
            or not submitted_fingerprint
        ):
            raise HTTPException(status_code=400, detail="请求缺少有效的受控方案标识")
        if (
            submitted_action_id != action_id
            or submitted_revision != revision
            or submitted_fingerprint != fingerprint
        ):
            raise HTTPException(status_code=409, detail="待确认方案已更新，请刷新后重新确认")
    return action


def _confirm_waiting_pending_action(
    run_id: str,
    submitted: Mapping[str, object] | None = None,
) -> str | None:
    action = _waiting_pending_action(run_id, submitted)
    if action is None:
        return None
    return frontend_api.confirm_pending_action(
        action["action_id"],
        run_id,
        state_revision=action["state_revision"],
        config_fingerprint=action["config_fingerprint"],
    )


def _revise_waiting_pending_action(
    run_id: str,
    request: str,
    submitted: Mapping[str, object] | None = None,
) -> str | None:
    if len(" ".join(request.split())) > 600:
        raise HTTPException(status_code=400, detail="调整要求超过长度限制")
    action = _waiting_pending_action(run_id, submitted)
    if action is None:
        return None
    return frontend_api.revise_pending_action(
        action["action_id"],
        request,
        run_id,
        state_revision=action["state_revision"],
        config_fingerprint=action["config_fingerprint"],
    )


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
    """List managed runs and separate proposal workspaces without paths."""
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
        if _is_initial_preparing_state(
            state,
            status.get("step"),
            status.get("done_steps"),
            status.get("message"),
        ):
            state = "preparing"
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
    workspaces = []
    try:
        workspaces = list_workspaces(frontend_api.ROOT)
    except (OSError, ProposalWorkspaceError, ValueError):
        workspaces = []
    plans = [workspace for workspace in workspaces if workspace.get("kind") == "plan"]
    temps = [workspace for workspace in workspaces if workspace.get("kind") == "temp"]
    return {
        "runs": runs,
        "plans": plans,
        "temps": temps,
        "active_run_id": frontend_api.get_active_run_id(),
    }


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


@app.delete("/api/local-items/{item_id}")
def delete_local_task_item(item_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Delete exactly one inactive local task directory after UI confirmation."""
    if pipeline_launch_is_active(frontend_api.ROOT):
        raise HTTPException(status_code=409, detail="当前流水线运行中，不能删除工程目录")
    body = payload if isinstance(payload, Mapping) else {}
    confirm_plan = body.get("confirm_plan") is True
    try:
        if is_run_conversation_id(item_id):
            # Refuse unregistered lookalike directories before deleting them.
            canonical_id = _safe_run_id(item_id)
            deleted = delete_local_item(frontend_api.ROOT, canonical_id, confirm_plan=confirm_plan)
            try:
                RunRegistry(frontend_api.ROOT).remove_run_from_index(canonical_id)
            except (OSError, RunRegistryError):
                pass
            names = _load_display_name_mapping()
            if canonical_id in names:
                names.pop(canonical_id, None)
                _save_display_name_mapping(names)
        else:
            deleted = delete_local_item(frontend_api.ROOT, item_id, confirm_plan=confirm_plan)
    except ProposalWorkspaceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError:
        raise HTTPException(status_code=503, detail="工程目录暂时无法删除") from None
    return {"deleted": deleted}


@app.get("/api/runs/{run_id}/logs")
def run_logs(run_id: str) -> dict[str, Any]:
    """Return the selected project's public manifest and frozen configuration."""
    canonical_id = _safe_run_id(run_id)
    with _open_run_logs_directory(canonical_id) as directory_fd:
        return {
            "run_id": canonical_id,
            "manifest": _read_public_manifest_record(directory_fd),
            "config": _read_public_config_record(directory_fd),
        }


@app.post("/api/proposals/default")
def ensure_default_proposal() -> dict[str, Any]:
    """Bind a fresh browser session to one durable draft when no run is live."""
    if pipeline_launch_is_active(frontend_api.ROOT):
        raise HTTPException(status_code=409, detail="当前流水线运行中，不能自动切换方案工作区")
    try:
        workspace = ensure_default_workspace(frontend_api.ROOT)
        history = get_workspace_history(frontend_api.ROOT, workspace["workspace_id"])
    except ProposalWorkspaceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"workspace": workspace, "messages": history}


@app.post("/api/proposals")
def create_proposal_workspace() -> dict[str, Any]:
    """Create one independent ``temp__`` proposal workspace."""
    try:
        workspace = create_temp_workspace(frontend_api.ROOT)
    except (OSError, ProposalWorkspaceError) as exc:
        raise HTTPException(status_code=503, detail="无法新建方案工作区") from exc
    return {"workspace": workspace, "messages": []}


@app.get("/api/proposals/{proposal_id}")
def get_proposal_workspace(proposal_id: str) -> dict[str, Any]:
    """Load one durable proposal conversation without exposing its config."""
    try:
        if is_run_conversation_id(proposal_id):
            canonical_id = _safe_run_id(proposal_id)
            workspace = get_run_conversation(frontend_api.ROOT, canonical_id)
            public_workspace = {
                "workspace_id": canonical_id,
                "kind": "run",
                "state": workspace.get("state", "bound"),
                "created_at": workspace.get("created_at", ""),
                "updated_at": workspace.get("updated_at", ""),
            }
            history = get_run_conversation_history(frontend_api.ROOT, canonical_id)
        else:
            workspace = get_workspace(frontend_api.ROOT, proposal_id)
            public_workspace = {
                "workspace_id": workspace["workspace_id"],
                "kind": workspace["kind"],
                "state": workspace["state"],
                "created_at": workspace.get("created_at", ""),
                "updated_at": workspace.get("updated_at", ""),
            }
            history = get_workspace_history(frontend_api.ROOT, proposal_id)
    except ProposalWorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "workspace": public_workspace,
        "messages": history,
    }


@app.post("/api/proposal/upload")
def proposal_upload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize one browser-uploaded quantum input through the existing boundary."""
    proposal_id = _proposal_id(payload)
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
    text = f"已上传{filename}，仅保留坐标、电荷、自旋。"
    clear_pending_plans(frontend_api.ROOT)
    try:
        proposal_id, messages = _append_uploaded_structure_message(proposal_id, text)
    except ProposalWorkspaceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "text": text,
        "structure": entry.as_dict(),
        "proposalId": proposal_id,
        "messages": messages,
    }


@app.post("/api/proposal/chat")
def proposal_chat(payload: dict[str, Any]) -> dict[str, Any]:
    """Serve one durable proposal workspace, never an in-memory thread."""
    proposal_id = _proposal_id(payload)
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="messages 必须是数组")
    client_history = _assistant_history(messages)
    latest_user = next(
        (item["content"] for item in reversed(client_history) if item["role"] == "user"),
        "",
    )
    if not latest_user:
        return {"text": "请描述需要建立的模拟体系，或询问当前分子库。"}
    is_run_context = is_run_conversation_id(proposal_id)
    try:
        if is_run_context:
            proposal_id = _safe_run_id(proposal_id)
            get_run_conversation(frontend_api.ROOT, proposal_id)
            history = get_run_conversation_history(frontend_api.ROOT, proposal_id)
            # A former run's frozen proposal must never be reconfirmed from
            # its history. A newly generated candidate gets a new plan__.
            pending_plan = None
        else:
            workspace = get_workspace(frontend_api.ROOT, proposal_id)
            history = get_workspace_history(frontend_api.ROOT, proposal_id)
            pending_plan = workspace.get("pending_plan")
            if not isinstance(pending_plan, Mapping):
                pending_plan = None
    except ProposalWorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Persist the user's turn before entering the LLM boundary.  A proposal
    # request can outlive the selected workspace in the browser, and an LLM
    # timeout/error must not erase the draft conversation on a later switch.
    request_history = [*history, {"role": "user", "content": latest_user}]
    if not is_run_context:
        try:
            save_workspace_conversation(
                frontend_api.ROOT,
                proposal_id,
                messages=request_history,
                pending_plan=pending_plan if isinstance(pending_plan, Mapping) else None,
            )
        except ProposalWorkspaceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    prior_run_id = frontend_api.latest_run_id()
    updates = list(chat(
        latest_user,
        history,
        pending_plan,
        proposal_workspace_id=None if is_run_context else proposal_id,
    ))
    if updates:
        _, _, _, pending_plan, _, _ = updates[-1]
        reply = updates[-1][1][-1].get("content", "")
        persisted_history = updates[-1][1]
    else:
        reply = "暂时没有生成新的回复。"
        persisted_history = request_history
    launched_run_id = _launched_run_id(reply)
    if launched_run_id is not None:
        save_formalized_conversation(
            frontend_api.ROOT,
            launched_run_id,
            messages=persisted_history,
        )
        return {"text": reply, "proposalId": proposal_id, "runId": launched_run_id}
    try:
        if is_run_context:
            if isinstance(pending_plan, Mapping):
                saved = create_plan_from_run_conversation(
                    frontend_api.ROOT,
                    proposal_id,
                    messages=_new_plan_exchange(latest_user, reply),
                    pending_plan=pending_plan,
                    source_messages=history,
                )
            else:
                save_run_conversation(
                    frontend_api.ROOT,
                    proposal_id,
                    messages=persisted_history,
                )
                saved = {"workspace_id": proposal_id}
        else:
            saved = save_workspace_conversation(
                frontend_api.ROOT,
                proposal_id,
                messages=persisted_history,
                pending_plan=pending_plan if isinstance(pending_plan, Mapping) else None,
            )
    except ProposalWorkspaceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response = {"text": reply, "proposalId": saved["workspace_id"]}
    current_run_id = frontend_api.latest_run_id()
    if isinstance(current_run_id, str) and current_run_id != prior_run_id:
        response["runId"] = current_run_id
    return response


@app.post("/api/chat")
def assistant_chat(payload: dict[str, Any]) -> dict[str, Any]:
    """Compatibility alias for the proposal workspace chat endpoint."""
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


@app.get("/api/solvents")
def solvents(query: str = Query(default="", max_length=128)) -> dict[str, Any]:
    """Query the Gaussian SMD solvent catalog without mutating it."""
    return frontend_api.get_solvent_catalog(query)


@app.get("/api/molecules")
def molecules() -> dict[str, Any]:
    """List auditable raw quantum inputs for the proposal molecule library."""
    return {"molecules": frontend_api.get_proposal_molecule_catalog()}


@app.post("/api/solvents")
def register_solvent(payload: dict[str, Any]) -> dict[str, Any]:
    """Register one user-provided Gaussian Generic SMD solvent."""
    from willy.quantum.smd_solvents import SMDSolventError, register_manual_solvent
    try:
        record = register_manual_solvent(
            payload.get("name"), payload.get("epsilon"), payload.get("epsinf"),
            project_root=frontend_api.ROOT,
        )
    except SMDSolventError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "solvent": record.as_dict()}


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
    return _run_chat_response(canonical_id, history, snapshot=snapshot)


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

    original_resume = bool(_ORIGINAL_PARAMETER_RESUME_RE.fullmatch(message))
    if original_resume:
        frontend_api.reconcile_stale_pipeline_state(canonical_id, source="resume_request")
    snapshot = _public_snapshot(canonical_id)
    history = _merge_status_into_history(_run_history(canonical_id), snapshot)
    if original_resume:
        submitted = payload if "state_revision" in payload else snapshot
        reply = _resume_original_parameters(canonical_id, submitted, snapshot)
        return _save_run_chat_exchange(canonical_id, history, message, reply)
    submitted_action = (
        payload if any(field in payload for field in _PENDING_ACTION_BINDING_FIELDS) else None
    )
    revision_request = _pending_action_revision_request(message)
    if revision_request is not None:
        reply = _revise_waiting_pending_action(canonical_id, revision_request, submitted_action)
        if reply is None:
            reply = "当前没有有效的待确认方案，未修改参数或启动工程。"
        return _save_run_chat_exchange(canonical_id, history, message, reply)
    # Text confirmation is a narrow state-machine command, not an LLM intent:
    # outside awaiting_confirmation it deliberately falls through as dialogue.
    control_reply = (
        _confirm_waiting_pending_action(canonical_id, submitted_action)
        if _is_pending_action_confirmation(message)
        else None
    )
    if control_reply is None:
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

    return _save_run_chat_exchange(target_run_id, history, message, reply)


@app.post("/api/runs/{run_id}/resume")
def resume_run(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Resume the same project with its frozen parameters, never a pending plan."""
    canonical_id = _safe_run_id(run_id)
    snapshot = _public_snapshot(canonical_id)
    reply = _resume_original_parameters(canonical_id, payload, snapshot)
    return _save_run_chat_exchange(
        canonical_id, _run_history(canonical_id), "原参数续跑", reply,
    )


@app.post("/api/runs/{run_id}/fault/complete")
def complete_run_fault(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Declare human completion of one fault; never approve or start a rerun."""
    from willy.run_registry import RunStateConflict

    canonical_id = _safe_run_id(run_id)
    if type(payload.get("state_revision")) is not int or not isinstance(payload.get("fault_id"), str):
        raise HTTPException(status_code=400, detail="请求缺少故障标识和状态版本")
    try:
        result = frontend_api.refresh_run_fault(canonical_id, declaration=payload)
    except RunStateConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    reply = "人工处理已记录，工程已回到已中止；未启动计算。" if result.get("status") == "resolved" else "处理结果尚未通过复查，工程继续保持故障状态；未启动计算。"
    return _save_run_chat_exchange(canonical_id, _run_history(canonical_id), "已完成人工处理，复查故障", reply)


@app.post("/api/runs/{run_id}/pending-action/revise")
def revise_run_pending_action(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Replace the bound proposal without confirming it or starting a run."""
    canonical_id = _safe_run_id(run_id)
    request = payload.get("request")
    if not isinstance(request, str) or not request.strip():
        raise HTTPException(status_code=400, detail="request 必须是非空文本")
    request = request.strip()
    reply = _revise_waiting_pending_action(canonical_id, request, payload)
    if reply is None:
        raise HTTPException(status_code=409, detail="当前没有有效的待确认方案")
    return _save_run_chat_exchange(
        canonical_id, _run_history(canonical_id), f"修改方案：{request}", reply,
    )


@app.post("/api/runs/{run_id}/pending-action/confirm")
def confirm_run_pending_action(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Confirm the current visible recovery proposal through the browser UI."""
    canonical_id = _safe_run_id(run_id)
    history = _merge_status_into_history(
        _run_history(canonical_id), _public_snapshot(canonical_id)
    )
    reply = _confirm_waiting_pending_action(canonical_id, payload)
    if reply is None:
        raise HTTPException(
            status_code=409,
            detail="当前工程不在等待确认状态，未执行重跑操作",
        )
    target_run_id = _safe_run_id(_follow_controlled_run(canonical_id, reply))
    if target_run_id != canonical_id:
        history = _run_history(target_run_id)
    return _save_run_chat_exchange(target_run_id, history, "确认调参", reply)


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
