"""Durable serial scheduling for confirmed proposal workspaces.

The queue is intentionally separate from ``RunRegistry``.  A ``plan__``
workspace has not yet become a run, and is promoted through the existing
launch lock only when it reaches the head of this queue.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Iterator, Mapping
from uuid import uuid4

from willy.config_store import write_json
from willy.pipeline_launch import active_pipeline_run_id, pipeline_launch_is_active
from willy.proposal_workspace import ProposalWorkspaceError, get_workspace
from willy.run_registry import RunRegistry, RunRegistryError


QUEUE_FILENAME = ".plan_queue.json"
QUEUE_LOCK_FILENAME = ".plan_queue.lock"
QUEUE_SCHEMA_VERSION = 1
_PLAN_ID_RE = re.compile(r"^plan__\d{12}$")
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
_PENDING_STATES = {"queued", "launching", "running"}
_TERMINAL_RUN_STATES = {"awaiting_confirmation", "done", "aborted", "escalated"}


class PlanQueueError(ValueError):
    """Raised when a queued proposal cannot be admitted or changed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runs_dir(root: str | Path) -> Path:
    return Path(root).resolve() / "md_run"


def _fingerprint(value: object) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise PlanQueueError("待确认方案格式无效") from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _empty_queue() -> dict[str, Any]:
    return {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "entries": [],
        "active_entry_id": None,
    }


@contextmanager
def _queue_lock(root: str | Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    runs_dir = _runs_dir(root)
    runs_dir.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(runs_dir / QUEUE_LOCK_FILENAME, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield runs_dir, _load_queue(runs_dir / QUEUE_FILENAME)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _load_queue(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return _empty_queue()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_queue()
    if not isinstance(raw, Mapping) or raw.get("schema_version") != QUEUE_SCHEMA_VERSION:
        return _empty_queue()
    entries = raw.get("entries")
    if not isinstance(entries, list):
        return _empty_queue()
    normalized = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        plan_id = entry.get("plan_id")
        entry_id = entry.get("entry_id")
        revision = entry.get("revision")
        config_fingerprint = entry.get("config_fingerprint")
        state = entry.get("state")
        if (
            not isinstance(plan_id, str)
            or _PLAN_ID_RE.fullmatch(plan_id) is None
            or not isinstance(entry_id, str)
            or not entry_id
            or not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 0
            or not isinstance(config_fingerprint, str)
            or _FINGERPRINT_RE.fullmatch(config_fingerprint) is None
            or state not in {"queued", "launching", "running", "settled", "invalidated", "launch_failed", "cancelled"}
        ):
            continue
        normalized.append({
            "entry_id": entry_id,
            "plan_id": plan_id,
            "revision": revision,
            "config_fingerprint": config_fingerprint,
            "queued_at": str(entry.get("queued_at", "")),
            "state": state,
            "claimed_at": str(entry.get("claimed_at", "")),
            "run_id": str(entry.get("run_id", "")),
            "completed_at": str(entry.get("completed_at", "")),
            "outcome": str(entry.get("outcome", "")),
        })
    active_entry_id = raw.get("active_entry_id")
    if not isinstance(active_entry_id, str) or not any(item["entry_id"] == active_entry_id for item in normalized):
        active_entry_id = None
    return {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "entries": normalized,
        "active_entry_id": active_entry_id,
    }


def _save_queue(runs_dir: Path, queue: Mapping[str, Any]) -> None:
    entries = list(queue.get("entries", []))
    pending = [entry for entry in entries if entry.get("state") in _PENDING_STATES]
    finished = [entry for entry in entries if entry.get("state") not in _PENDING_STATES][-200:]
    write_json(
        runs_dir / QUEUE_FILENAME,
        {
            "schema_version": QUEUE_SCHEMA_VERSION,
            "entries": [*pending, *finished],
            "active_entry_id": queue.get("active_entry_id"),
        },
    )


def _entry(queue: Mapping[str, Any], entry_id: str | None) -> dict[str, Any] | None:
    if not isinstance(entry_id, str):
        return None
    for item in queue.get("entries", []):
        if item.get("entry_id") == entry_id:
            return item
    return None


def _queued_entries(queue: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [entry for entry in queue.get("entries", []) if entry.get("state") == "queued"]


def _public_entry(entry: Mapping[str, Any], *, position: int | None = None) -> dict[str, Any]:
    return {
        "plan_id": entry["plan_id"],
        "state": entry["state"],
        "queue_position": position,
        "queued_at": entry.get("queued_at", ""),
        "run_id": entry.get("run_id", ""),
        "outcome": entry.get("outcome", ""),
    }


def _workspace_candidate(root: str | Path, plan_id: str) -> tuple[dict[str, Any], Mapping[str, object], str]:
    workspace = get_workspace(root, plan_id)
    if workspace.get("kind") != "plan" or workspace.get("state") != "awaiting_confirmation":
        raise PlanQueueError("只有待确认方案可以加入队列")
    pending = workspace.get("pending_plan")
    config = pending.get("config") if isinstance(pending, Mapping) else None
    if not isinstance(config, Mapping):
        raise PlanQueueError("待确认方案缺少有效配置")
    return workspace, config, _fingerprint(config)


def _workspace_revision(workspace: Mapping[str, Any]) -> int:
    value = workspace.get("revision", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def queue_plan(
    root: str | Path,
    plan_id: str,
    *,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Confirm a plan for serial execution and append it to the queue tail."""
    if _PLAN_ID_RE.fullmatch(plan_id) is None:
        raise PlanQueueError("方案工作区标识无效")
    workspace, _config, config_fingerprint = _workspace_candidate(root, plan_id)
    revision = _workspace_revision(workspace)
    if expected_revision is not None and expected_revision != revision:
        raise PlanQueueError("方案已更新，请重新确认后再加入队列")
    with _queue_lock(root) as (runs_dir, queue):
        for entry in reversed(queue["entries"]):
            if entry["plan_id"] != plan_id or entry["state"] not in _PENDING_STATES:
                continue
            if entry["state"] != "queued":
                raise PlanQueueError("该方案正在启动或运行，不能重复加入队列")
            if entry["revision"] != revision or entry["config_fingerprint"] != config_fingerprint:
                entry["state"] = "invalidated"
                entry["completed_at"] = _now()
                entry["outcome"] = "plan_changed"
                break
            position = _queued_entries(queue).index(entry) + 1
            return _public_entry(entry, position=position)
        entry = {
            "entry_id": uuid4().hex,
            "plan_id": plan_id,
            "revision": revision,
            "config_fingerprint": config_fingerprint,
            "queued_at": _now(),
            "state": "queued",
            "claimed_at": "",
            "run_id": "",
            "completed_at": "",
            "outcome": "",
        }
        queue["entries"].append(entry)
        _save_queue(runs_dir, queue)
        return _public_entry(entry, position=len(_queued_entries(queue)))


def dequeue_plan(root: str | Path, plan_id: str, *, expected_revision: int | None = None) -> dict[str, Any]:
    """Remove a waiting plan without affecting a formalized child run."""
    if _PLAN_ID_RE.fullmatch(plan_id) is None:
        raise PlanQueueError("方案工作区标识无效")
    with _queue_lock(root) as (runs_dir, queue):
        for entry in reversed(queue["entries"]):
            if entry["plan_id"] != plan_id or entry["state"] not in _PENDING_STATES:
                continue
            if entry["state"] != "queued":
                raise PlanQueueError("该方案已启动，不能从队列移除")
            if expected_revision is not None and entry["revision"] != expected_revision:
                raise PlanQueueError("方案已更新，请刷新后重试")
            entry["state"] = "cancelled"
            entry["completed_at"] = _now()
            entry["outcome"] = "removed_by_user"
            _save_queue(runs_dir, queue)
            return {"plan_id": plan_id, "queued": False}
    return {"plan_id": plan_id, "queued": False}


def invalidate_queued_plan(root: str | Path, plan_id: str) -> bool:
    """Require a new confirmation when a queued plan conversation changes."""
    with _queue_lock(root) as (runs_dir, queue):
        changed = False
        for entry in reversed(queue["entries"]):
            if entry["plan_id"] != plan_id or entry["state"] not in _PENDING_STATES:
                continue
            if entry["state"] == "queued":
                entry["state"] = "invalidated"
                entry["completed_at"] = _now()
                entry["outcome"] = "plan_changed"
                changed = True
            break
        if changed:
            _save_queue(runs_dir, queue)
        return changed


def queue_metadata(root: str | Path) -> dict[str, dict[str, Any]]:
    """Return browser-safe queue flags keyed by still-materialized plans."""
    with _queue_lock(root) as (_runs_dir, queue):
        metadata: dict[str, dict[str, Any]] = {}
        for position, entry in enumerate(_queued_entries(queue), start=1):
            metadata[entry["plan_id"]] = {
                "queued": True,
                "queue_position": position,
                "queue_state": "queued",
            }
        return metadata


def queue_snapshot(root: str | Path) -> dict[str, Any]:
    """Expose only queue order and public run identifiers."""
    with _queue_lock(root) as (_runs_dir, queue):
        return _snapshot_from_queue(queue)


def _snapshot_from_queue(queue: Mapping[str, Any]) -> dict[str, Any]:
    active = _entry(queue, queue.get("active_entry_id"))
    return {
        "active": _public_entry(active) if active is not None else None,
        "queued": [
            _public_entry(entry, position=position)
            for position, entry in enumerate(_queued_entries(queue), start=1)
        ],
    }


def _run_state(root: str | Path, run_id: str) -> str:
    if not run_id:
        return ""
    try:
        state = RunRegistry(root).get_run_status(run_id, reconcile=False).get("state")
    except (OSError, RunRegistryError, ValueError):
        return ""
    return state if isinstance(state, str) else ""


def _settle_active_if_released(root: str | Path, queue: dict[str, Any]) -> bool:
    active = _entry(queue, queue.get("active_entry_id"))
    if active is None:
        queue["active_entry_id"] = None
        return False
    if active["state"] == "launching":
        run_id = active_pipeline_run_id(root)
        if run_id:
            active["state"] = "running"
            active["run_id"] = run_id
            return True
        active["state"] = "launch_failed"
        active["completed_at"] = _now()
        active["outcome"] = "launch_interrupted"
        queue["active_entry_id"] = None
        return False
    if active["state"] != "running":
        queue["active_entry_id"] = None
        return False
    run_state = _run_state(root, active.get("run_id", ""))
    if run_state in _TERMINAL_RUN_STATES or not pipeline_launch_is_active(root):
        active["state"] = "settled"
        active["completed_at"] = _now()
        active["outcome"] = run_state or "launch_released"
        queue["active_entry_id"] = None
        return False
    return True


def release_queue_run(root: str | Path, run_id: str) -> bool:
    """Release the queue slot after the controlled child process exits."""
    with _queue_lock(root) as (runs_dir, queue):
        active = _entry(queue, queue.get("active_entry_id"))
        if active is None or active.get("run_id") != run_id:
            return False
        active["state"] = "settled"
        active["completed_at"] = _now()
        active["outcome"] = _run_state(root, run_id) or "launch_released"
        queue["active_entry_id"] = None
        _save_queue(runs_dir, queue)
        return True


def dispatch_plan_queue(
    root: str | Path,
    *,
    launcher: Callable[[Mapping[str, object], str], Any],
) -> dict[str, Any]:
    """Start at most one head plan through the normal launch boundary.

    ``launcher`` must be the existing confirmed-plan starter.  It returns an
    object with ``state`` and ``run_id`` attributes, matching
    ``PipelineLaunchReceipt``.  The queue lock remains held until that receipt
    is recorded, preventing an immediately exiting child from being missed by
    its cleanup callback.
    """
    while True:
        with _queue_lock(root) as (runs_dir, queue):
            if _settle_active_if_released(root, queue):
                _save_queue(runs_dir, queue)
                return _snapshot_from_queue(queue)
            candidate = next(iter(_queued_entries(queue)), None)
            if candidate is None or pipeline_launch_is_active(root):
                _save_queue(runs_dir, queue)
                return _snapshot_from_queue(queue)
            try:
                workspace, config, config_fingerprint = _workspace_candidate(root, candidate["plan_id"])
            except (PlanQueueError, ProposalWorkspaceError):
                candidate["state"] = "invalidated"
                candidate["completed_at"] = _now()
                candidate["outcome"] = "plan_unavailable"
                _save_queue(runs_dir, queue)
                continue
            if (
                _workspace_revision(workspace) != candidate["revision"]
                or config_fingerprint != candidate["config_fingerprint"]
            ):
                candidate["state"] = "invalidated"
                candidate["completed_at"] = _now()
                candidate["outcome"] = "plan_changed"
                _save_queue(runs_dir, queue)
                continue
            candidate["state"] = "launching"
            candidate["claimed_at"] = _now()
            queue["active_entry_id"] = candidate["entry_id"]
            _save_queue(runs_dir, queue)
            try:
                receipt = launcher(config, candidate["plan_id"])
                state = getattr(receipt, "state", "failed")
                run_id = getattr(receipt, "run_id", None)
            except Exception:
                state = "failed"
                run_id = None
            if state == "started" and isinstance(run_id, str) and run_id:
                candidate["state"] = "running"
                candidate["run_id"] = run_id
                _save_queue(runs_dir, queue)
                return _snapshot_from_queue(queue)
            queue["active_entry_id"] = None
            if state == "lock_conflict":
                candidate["state"] = "queued"
                candidate["claimed_at"] = ""
                _save_queue(runs_dir, queue)
                return _snapshot_from_queue(queue)
            candidate["state"] = "launch_failed"
            candidate["completed_at"] = _now()
            candidate["outcome"] = state if isinstance(state, str) else "failed"
            _save_queue(runs_dir, queue)


def advance_after_run_release(
    root: str | Path,
    run_id: str,
    *,
    launcher: Callable[[Mapping[str, object], str], Any],
) -> dict[str, Any]:
    """Free a completed queue slot, then start the next admitted plan."""
    release_queue_run(root, run_id)
    return dispatch_plan_queue(root, launcher=launcher)
