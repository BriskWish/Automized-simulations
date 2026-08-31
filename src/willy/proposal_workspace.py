"""Durable, pre-launch workspaces for the proposal assistant.

``temp__`` and ``plan__`` directories live beside managed ``md__`` runs, but
are deliberately outside :class:`RunRegistry`.  They hold only the browser
conversation and an unconfirmed candidate.  A plan becomes a managed run only
when the normal launch lock has been reserved and the directory is promoted.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Iterator, Mapping

from willy.config_store import write_json


WORKSPACE_FILENAME = "proposal.json"
WORKSPACE_SCHEMA_VERSION = 1
WORKSPACE_LOCK_FILENAME = ".proposal_workspaces.lock"
_TEMP_RE = re.compile(r"^temp__(?P<date>\d{8})(?P<counter>\d{4})$")
_PLAN_RE = re.compile(r"^plan__(?P<date>\d{8})(?P<counter>\d{4})$")
_RUN_RE = re.compile(r"^md__\d{12}$")
_MAX_HISTORY_MESSAGES = 80
_MAX_MESSAGE_CHARS = 6_000


class ProposalWorkspaceError(ValueError):
    """Raised when a pre-launch workspace cannot be safely used."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runs_dir(root: str | Path) -> Path:
    return Path(root).resolve() / "md_run"


@contextmanager
def _workspaces_lock(root: str | Path) -> Iterator[Path]:
    """Serialize directory allocation, promotion, and plan writes."""
    runs_dir = _runs_dir(root)
    runs_dir.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(runs_dir / WORKSPACE_LOCK_FILENAME, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield runs_dir
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _kind_for_id(workspace_id: object) -> str:
    if not isinstance(workspace_id, str):
        raise ProposalWorkspaceError("方案工作区标识无效")
    if _TEMP_RE.fullmatch(workspace_id):
        return "temp"
    if _PLAN_RE.fullmatch(workspace_id):
        return "plan"
    raise ProposalWorkspaceError("方案工作区标识无效")


def _workspace_path(runs_dir: Path, workspace_id: str) -> Path:
    _kind_for_id(workspace_id)
    path = (runs_dir / workspace_id).resolve()
    if path.parent != runs_dir.resolve():
        raise ProposalWorkspaceError("方案工作区路径无效")
    return path


def is_run_conversation_id(value: object) -> bool:
    """Return whether ``value`` names a managed-run proposal conversation."""
    return isinstance(value, str) and _RUN_RE.fullmatch(value) is not None


def _run_path(runs_dir: Path, run_id: str) -> Path:
    if not is_run_conversation_id(run_id):
        raise ProposalWorkspaceError("运行工程标识无效")
    path = (runs_dir / run_id).resolve()
    if path.parent != runs_dir.resolve():
        raise ProposalWorkspaceError("运行工程路径无效")
    return path


def _sanitize_messages(messages: object) -> list[dict[str, str]]:
    if not isinstance(messages, list):
        return []
    result: list[dict[str, str]] = []
    for raw in messages[-_MAX_HISTORY_MESSAGES:]:
        if not isinstance(raw, Mapping):
            continue
        role = raw.get("role")
        content = raw.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        text = content.strip()
        if text:
            result.append({"role": role, "content": text[:_MAX_MESSAGE_CHARS]})
    return result


def _config_fingerprint(value: object) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ProposalWorkspaceError("待确认方案格式无效") from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _load_workspace(path: Path, workspace_id: str) -> dict[str, Any]:
    try:
        raw = json.loads((path / WORKSPACE_FILENAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProposalWorkspaceError("方案工作区记录不可读取") from exc
    if not isinstance(raw, dict):
        raise ProposalWorkspaceError("方案工作区记录格式无效")
    if raw.get("schema_version") != WORKSPACE_SCHEMA_VERSION or raw.get("workspace_id") != workspace_id:
        raise ProposalWorkspaceError("方案工作区记录不匹配")
    kind = _kind_for_id(workspace_id)
    if raw.get("kind") != kind:
        raise ProposalWorkspaceError("方案工作区类型不匹配")
    state = raw.get("state")
    if state not in {"draft", "awaiting_confirmation", "formalized"}:
        raise ProposalWorkspaceError("方案工作区状态无效")
    raw["messages"] = _sanitize_messages(raw.get("messages"))
    if raw.get("pending_plan") is not None and not isinstance(raw.get("pending_plan"), Mapping):
        raise ProposalWorkspaceError("待确认方案格式无效")
    return raw


def _bound_run_record(run_id: str) -> dict[str, Any]:
    """Build a run-local conversation record for a run without a prior plan."""
    timestamp = _now()
    return {
        "schema_version": WORKSPACE_SCHEMA_VERSION,
        "workspace_id": run_id,
        "kind": "run",
        "state": "bound",
        "created_at": timestamp,
        "updated_at": timestamp,
        "messages": [],
        "pending_plan": None,
        "bound_run_id": run_id,
    }


def _load_run_conversation(path: Path, run_id: str) -> dict[str, Any]:
    """Load a promoted plan trace or a direct run-local proposal trace."""
    record_path = path / WORKSPACE_FILENAME
    if not record_path.is_file():
        return _bound_run_record(run_id)
    try:
        raw = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProposalWorkspaceError("运行工程会话记录不可读取") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != WORKSPACE_SCHEMA_VERSION:
        raise ProposalWorkspaceError("运行工程会话记录格式无效")

    # Plans promoted during launch retain their plan ID as the immutable
    # provenance key.  Their directory, however, is now the managed run.
    if raw.get("kind") == "plan":
        plan_id = raw.get("workspace_id")
        if (
            not isinstance(plan_id, str)
            or _PLAN_RE.fullmatch(plan_id) is None
            or raw.get("state") != "formalized"
            or raw.get("formalized_run_id") != run_id
        ):
            raise ProposalWorkspaceError("运行工程会话记录不匹配")
    elif raw.get("kind") == "run":
        if raw.get("workspace_id") != run_id or raw.get("state") != "bound":
            raise ProposalWorkspaceError("运行工程会话记录不匹配")
    else:
        raise ProposalWorkspaceError("运行工程会话类型无效")
    raw["messages"] = _sanitize_messages(raw.get("messages"))
    if raw.get("pending_plan") is not None and not isinstance(raw.get("pending_plan"), Mapping):
        raise ProposalWorkspaceError("待确认方案格式无效")
    return raw


def _public_workspace(record: Mapping[str, Any]) -> dict[str, str]:
    return {
        "workspace_id": str(record["workspace_id"]),
        "kind": str(record["kind"]),
        "state": str(record["state"]),
        "created_at": str(record.get("created_at", "")),
        "updated_at": str(record.get("updated_at", "")),
    }


def _public_run_conversation(record: Mapping[str, Any], run_id: str) -> dict[str, str]:
    """Expose a run as a proposal context without leaking its frozen config."""
    return {
        "workspace_id": run_id,
        "kind": "run",
        "state": str(record.get("state", "bound")),
        "created_at": str(record.get("created_at", "")),
        "updated_at": str(record.get("updated_at", "")),
    }


def _next_workspace_id(runs_dir: Path, kind: str) -> str:
    if kind not in {"temp", "plan"}:
        raise ProposalWorkspaceError("方案工作区类型无效")
    today = datetime.now().strftime("%Y%m%d")
    counter = 1
    for directory in runs_dir.iterdir():
        if not directory.is_dir():
            continue
        match = _TEMP_RE.fullmatch(directory.name) or _PLAN_RE.fullmatch(directory.name)
        if match and match.group("date") == today:
            counter = max(counter, int(match.group("counter")) + 1)
    return f"{kind}__{today}{counter:04d}"


def create_temp_workspace(root: str | Path) -> dict[str, str]:
    """Create one independent draft workspace under ``md_run``."""
    with _workspaces_lock(root) as runs_dir:
        while True:
            workspace_id = _next_workspace_id(runs_dir, "temp")
            directory = runs_dir / workspace_id
            try:
                directory.mkdir()
                break
            except FileExistsError:
                continue
        timestamp = _now()
        record = {
            "schema_version": WORKSPACE_SCHEMA_VERSION,
            "workspace_id": workspace_id,
            "kind": "temp",
            "state": "draft",
            "created_at": timestamp,
            "updated_at": timestamp,
            "messages": [],
            "pending_plan": None,
        }
        write_json(directory / WORKSPACE_FILENAME, record)
        return _public_workspace(record)


def list_workspaces(root: str | Path) -> list[dict[str, str]]:
    """Return durable proposal workspaces without treating them as runs."""
    runs_dir = _runs_dir(root)
    if not runs_dir.is_dir():
        return []
    result: list[dict[str, str]] = []
    for directory in runs_dir.iterdir():
        if not directory.is_dir():
            continue
        try:
            _kind_for_id(directory.name)
            record = _load_workspace(directory, directory.name)
        except ProposalWorkspaceError:
            continue
        result.append(_public_workspace(record))
    return sorted(
        result,
        key=lambda item: (item["updated_at"], item["workspace_id"]),
        reverse=True,
    )


def ensure_default_workspace(root: str | Path) -> dict[str, str]:
    """Reuse the newest unfinished workspace, or allocate a new draft."""
    workspaces = list_workspaces(root)
    if workspaces:
        return workspaces[0]
    return create_temp_workspace(root)


def get_workspace(root: str | Path, workspace_id: str) -> dict[str, Any]:
    runs_dir = _runs_dir(root)
    path = _workspace_path(runs_dir, workspace_id)
    if not path.is_dir():
        raise ProposalWorkspaceError("方案工作区不存在")
    return _load_workspace(path, workspace_id)


def get_workspace_history(root: str | Path, workspace_id: str) -> list[dict[str, str]]:
    return _sanitize_messages(get_workspace(root, workspace_id).get("messages"))


def get_run_conversation(root: str | Path, run_id: str) -> dict[str, Any]:
    """Return the durable proposal conversation attached to one ``md__`` run."""
    runs_dir = _runs_dir(root)
    path = _run_path(runs_dir, run_id)
    if not path.is_dir():
        raise ProposalWorkspaceError("运行工程不存在")
    return _load_run_conversation(path, run_id)


def get_run_conversation_history(root: str | Path, run_id: str) -> list[dict[str, str]]:
    return _sanitize_messages(get_run_conversation(root, run_id).get("messages"))


def save_workspace_conversation(
    root: str | Path,
    workspace_id: str,
    *,
    messages: object,
    pending_plan: Mapping[str, object] | None,
) -> dict[str, Any]:
    """Persist one conversation and promote a draft after a valid plan appears."""
    with _workspaces_lock(root) as runs_dir:
        source = _workspace_path(runs_dir, workspace_id)
        if not source.is_dir():
            raise ProposalWorkspaceError("方案工作区不存在")
        record = _load_workspace(source, workspace_id)
        target = source
        if pending_plan is not None and record["kind"] == "temp":
            target_id = _next_workspace_id(runs_dir, "plan")
            target = _workspace_path(runs_dir, target_id)
            os.replace(source, target)
            record["workspace_id"] = target_id
            record["kind"] = "plan"
            workspace_id = target_id
        record["messages"] = _sanitize_messages(messages)
        record["pending_plan"] = copy.deepcopy(dict(pending_plan)) if pending_plan is not None else None
        record["state"] = "awaiting_confirmation" if pending_plan is not None else "draft"
        record["updated_at"] = _now()
        write_json(target / WORKSPACE_FILENAME, record)
        return record


def _save_run_conversation_locked(
    runs_dir: Path,
    run_id: str,
    *,
    messages: object,
) -> dict[str, Any]:
    path = _run_path(runs_dir, run_id)
    if not path.is_dir():
        raise ProposalWorkspaceError("运行工程不存在")
    record = _load_run_conversation(path, run_id)
    record["messages"] = _sanitize_messages(messages)
    record["updated_at"] = _now()
    write_json(path / WORKSPACE_FILENAME, record)
    return record


def save_run_conversation(
    root: str | Path,
    run_id: str,
    *,
    messages: object,
) -> dict[str, Any]:
    """Persist proposal-assistant history in an existing managed run."""
    with _workspaces_lock(root) as runs_dir:
        return _save_run_conversation_locked(runs_dir, run_id, messages=messages)


def create_plan_from_run_conversation(
    root: str | Path,
    run_id: str,
    *,
    messages: object,
    pending_plan: Mapping[str, object],
    source_messages: object | None = None,
) -> dict[str, Any]:
    """Keep a run trace, then fork a newly proposed candidate into ``plan__``.

    A proposal generated while browsing a completed or aborted run is never
    written back as that run's pending configuration.  The original trace is
    saved first and the candidate receives its own confirmation workspace.
    ``source_messages`` permits the caller to retain the old run history while
    placing only the new planning exchange in the new workspace.
    """
    if not isinstance(pending_plan, Mapping):
        raise ProposalWorkspaceError("待确认方案格式无效")
    with _workspaces_lock(root) as runs_dir:
        _save_run_conversation_locked(
            runs_dir,
            run_id,
            messages=messages if source_messages is None else source_messages,
        )
        while True:
            plan_id = _next_workspace_id(runs_dir, "plan")
            directory = _workspace_path(runs_dir, plan_id)
            try:
                directory.mkdir()
                break
            except FileExistsError:
                continue
        timestamp = _now()
        record = {
            "schema_version": WORKSPACE_SCHEMA_VERSION,
            "workspace_id": plan_id,
            "kind": "plan",
            "state": "awaiting_confirmation",
            "created_at": timestamp,
            "updated_at": timestamp,
            "messages": _sanitize_messages(messages),
            "pending_plan": copy.deepcopy(dict(pending_plan)),
            "source_run_id": run_id,
        }
        write_json(directory / WORKSPACE_FILENAME, record)
        return record


def delete_local_item(
    root: str | Path,
    item_id: str,
    *,
    confirm_plan: bool = False,
) -> dict[str, str]:
    """Delete one managed run, plan, or temporary workspace directory only."""
    if is_run_conversation_id(item_id):
        kind = "run"
    else:
        kind = _kind_for_id(item_id)
    if kind == "plan" and not confirm_plan:
        raise ProposalWorkspaceError("删除方案目录需要二次确认")
    with _workspaces_lock(root) as runs_dir:
        path = _run_path(runs_dir, item_id) if kind == "run" else _workspace_path(runs_dir, item_id)
        if not path.is_dir():
            raise ProposalWorkspaceError("目标目录不存在")
        try:
            shutil.rmtree(path)
        except OSError as exc:
            raise ProposalWorkspaceError("目标目录无法删除") from exc
    return {"item_id": item_id, "kind": kind}


def clear_pending_plans(root: str | Path) -> int:
    """Invalidate candidates after the shared quantum-input catalog changes."""
    cleared = 0
    with _workspaces_lock(root) as runs_dir:
        for directory in runs_dir.iterdir():
            if not directory.is_dir():
                continue
            try:
                _kind_for_id(directory.name)
                record = _load_workspace(directory, directory.name)
            except ProposalWorkspaceError:
                continue
            if record.get("pending_plan") is None:
                continue
            record["pending_plan"] = None
            record["state"] = "draft"
            record["updated_at"] = _now()
            write_json(directory / WORKSPACE_FILENAME, record)
            cleared += 1
    return cleared


def formalize_plan_directory(
    root: str | Path,
    *,
    plan_id: str,
    run_dir: str | Path,
    expected_config: Mapping[str, object],
) -> None:
    """Replace an empty reserved ``md__`` directory with its frozen plan."""
    if _kind_for_id(plan_id) != "plan":
        raise ProposalWorkspaceError("只有待确认方案可以启动")
    target = Path(run_dir).resolve()
    project_root = Path(root).resolve()
    runs_dir = _runs_dir(project_root).resolve()
    if target.parent != runs_dir or _RUN_RE.fullmatch(target.name) is None:
        raise ProposalWorkspaceError("正式运行目录无效")
    with _workspaces_lock(project_root):
        source = _workspace_path(runs_dir, plan_id)
        if not source.is_dir():
            raise ProposalWorkspaceError("待确认方案不存在")
        record = _load_workspace(source, plan_id)
        pending = record.get("pending_plan")
        config = pending.get("config") if isinstance(pending, Mapping) else None
        if record.get("state") != "awaiting_confirmation" or not isinstance(config, Mapping):
            raise ProposalWorkspaceError("待确认方案已失效")
        if _config_fingerprint(config) != _config_fingerprint(expected_config):
            raise ProposalWorkspaceError("待确认方案与确认内容不匹配")
        try:
            if any(target.iterdir()):
                raise ProposalWorkspaceError("正式运行目录不是空目录")
            target.rmdir()
            os.replace(source, target)
        except OSError as exc:
            raise ProposalWorkspaceError("方案目录正式化失败") from exc
        record["state"] = "formalized"
        record["formalized_run_id"] = target.name
        record["updated_at"] = _now()
        write_json(target / WORKSPACE_FILENAME, record)


def rollback_formalized_plan(root: str | Path, *, plan_id: str, run_dir: str | Path) -> None:
    """Restore a plan only when child process creation failed before adoption."""
    if _kind_for_id(plan_id) != "plan":
        return
    target = Path(run_dir).resolve()
    project_root = Path(root).resolve()
    runs_dir = _runs_dir(project_root).resolve()
    if target.parent != runs_dir or _RUN_RE.fullmatch(target.name) is None:
        return
    with _workspaces_lock(project_root):
        if not target.is_dir() or (target / "run_manifest.json").exists() or (target / "manifest.json").exists():
            return
        destination = _workspace_path(runs_dir, plan_id)
        if destination.exists():
            return
        record = _load_workspace(target, plan_id)
        os.replace(target, destination)
        record["state"] = "awaiting_confirmation"
        record.pop("formalized_run_id", None)
        record["updated_at"] = _now()
        write_json(destination / WORKSPACE_FILENAME, record)


def save_formalized_conversation(
    root: str | Path,
    run_id: str,
    *,
    messages: object,
) -> None:
    """Append the confirmation exchange to the retained plan trace in one run."""
    if not isinstance(run_id, str) or _RUN_RE.fullmatch(run_id) is None:
        return
    path = _runs_dir(root) / run_id
    try:
        raw = json.loads((path / WORKSPACE_FILENAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(raw, dict):
        return
    plan_id = raw.get("workspace_id")
    if not isinstance(plan_id, str) or _PLAN_RE.fullmatch(plan_id) is None:
        return
    if raw.get("schema_version") != WORKSPACE_SCHEMA_VERSION or raw.get("kind") != "plan":
        return
    record = raw
    if record.get("state") != "formalized":
        return
    record["messages"] = _sanitize_messages(messages)
    record["updated_at"] = _now()
    write_json(path / WORKSPACE_FILENAME, record)


def formalized_plan_id(run_dir: str | Path) -> str | None:
    """Return the original plan ID for a promoted run, without exposing config."""
    path = Path(run_dir)
    # A formalized directory is named ``md__`` while the persisted plan ID
    # remains a ``plan__`` value, so it intentionally bypasses normal plan
    # path validation here.
    try:
        raw = json.loads((path / WORKSPACE_FILENAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, Mapping):
        return None
    plan_id = raw.get("workspace_id")
    if isinstance(plan_id, str) and _PLAN_RE.fullmatch(plan_id) and raw.get("state") == "formalized":
        return plan_id
    return None
