"""Version-bound fault evidence and non-executing human-recovery checks."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
import fcntl
import json
import os
import re
import secrets
import traceback

from willy.errors import ErrorKind


FAULT_CONTEXT = ".fault_context.json"
STOP_REVIEW_SECONDS = 120
PHASES = {"initialization", "preflight", "execution", "acceptance", "recovery", "launch", "stopping", "cleanup"}
FAULT_KINDS = {item.value for item in ErrorKind} | {"unexpected_exception", "stop_failed", "stop_timeout", "cleanup_failed"}
STATES = {"idle", "running", "retrying", "awaiting_confirmation", "stopping", "escalated", "aborted", "done"}
PHASE_LABELS = {
    "initialization": "工程初始化", "preflight": "执行预检", "execution": "步骤执行",
    "acceptance": "产物验收", "recovery": "恢复处理", "launch": "启动交接",
    "stopping": "安全停止", "cleanup": "进程回收",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_fault(value: object) -> dict:
    if not isinstance(value, Mapping) or not re.fullmatch(r"[a-f0-9]{24}", str(value.get("fault_id", ""))):
        return {}
    if not isinstance(value.get("phase"), str) or not isinstance(value.get("error_kind"), str):
        return {}
    if value["phase"] not in PHASES or value["error_kind"] not in FAULT_KINDS:
        return {}
    result = {key: value[key] for key in ("fault_id", "phase", "error_kind")}
    for key in ("occurred_at", "resolved_at"):
        try:
            stamp = datetime.fromisoformat(value.get(key, ""))
            if stamp.tzinfo is not None:
                result[key] = stamp.isoformat()
        except (TypeError, ValueError):
            pass
    step = value.get("step")
    result["step"] = step if type(step) is int and 0 <= step <= 10 else 0
    state = value.get("state_at_error")
    result["state_at_error"] = state if isinstance(state, str) and state in STATES else "idle"
    result["resolution"] = "resolved" if value.get("resolution") == "resolved" else "pending"
    for key in ("cause_error_kind",):
        if isinstance(value.get(key), str) and value[key] in FAULT_KINDS:
            result[key] = value[key]
    if re.fullmatch(r"[a-f0-9]{24}", str(value.get("cause_fault_id", ""))):
        result["cause_fault_id"] = value["cause_fault_id"]
    if isinstance(value.get("resolution_basis"), str) and value["resolution_basis"] in {"prerequisites", "process_exit", "manual_declaration"}:
        result["resolution_basis"] = value["resolution_basis"]
    return result


def new_fault(status: Mapping, *, phase: str, error_kind: str) -> dict:
    previous = public_fault(status.get("fault"))
    value = {
        "fault_id": secrets.token_hex(12), "phase": phase, "error_kind": error_kind,
        "step": status.get("step", 0), "state_at_error": status.get("state", "idle"),
        "occurred_at": _now(), "resolution": "pending",
        "cause_error_kind": status.get("error_kind") or previous.get("error_kind"), "cause_fault_id": previous.get("fault_id"),
    }
    return public_fault(value)


def record_fault(root: Path, run_id: str, *, phase: str, error_kind: str = "unexpected_exception",
                 exception: Exception | None = None, module: str | None = None) -> dict:
    from willy.run_registry import RunRegistry
    from willy.run_store import run_transaction

    registry = RunRegistry(root)
    directory = registry.resolve_run_id(run_id)
    current = registry.get_run_status(run_id, reconcile=False)
    if current.get("state") == "done":
        return current
    if current.get("state") == "stopping":
        phase = "stopping"
    fault = new_fault(current, phase=phase, error_kind=error_kind)
    if not fault:
        raise ValueError("故障位置或类型无效")
    context = {"run_id": run_id, "fault_id": fault["fault_id"], "module": module}
    updated = registry.compare_and_swap_status(
        directory, expected_revision=current.get("state_revision", 0),
        status={**current, "state": "escalated", "fault": fault, "repair": {},
                "error_kind": error_kind, "error": f"{PHASE_LABELS[phase]}发生故障，自动推进已禁止，等待处理后复查。"},
        event_type="run_fault_reported", fault_context=context,
    )
    if exception is not None:
        try:
            frames = [{"file": frame.filename, "function": frame.name, "line": frame.lineno}
                      for frame in traceback.extract_tb(exception.__traceback__)[-16:]]
            with run_transaction(directory) as store:
                store.append_jsonl(".fault_diagnostics.jsonl", {
                    "fault_id": fault["fault_id"], "exception_type": type(exception).__name__, "frames": frames,
                })
        except (OSError, ValueError):
            pass
    return updated


@contextmanager
def _idle_control_guard(root: Path, directory: Path):
    from willy.pipeline_launch import PipelineLockConflict, _lock_path, _read_lock_fd, _record_is_active, _process_group_is_alive, _pid_alive
    from willy.run_processes import active_process_run

    lock_path = _lock_path(root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as launch, (directory / ".md.lock").open("a+") as md_lock:
        try:
            fcntl.flock(launch.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            if _record_is_active(_read_lock_fd(launch.fileno())) or active_process_run(root):
                raise PipelineLockConflict(directory.name)
            legacy_pid = root / ".pipeline.pid"
            if legacy_pid.exists():
                try:
                    pid = int(legacy_pid.read_text().strip())
                except (OSError, ValueError) as exc:
                    raise PipelineLockConflict(directory.name) from exc
                if _pid_alive(pid) or _process_group_is_alive(pid):
                    raise PipelineLockConflict(directory.name)
            fcntl.flock(md_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        except BlockingIOError as exc:
            raise PipelineLockConflict(directory.name) from exc
        finally:
            fcntl.flock(md_lock.fileno(), fcntl.LOCK_UN)
            fcntl.flock(launch.fileno(), fcntl.LOCK_UN)


def _remaining_processes(directory: Path) -> bool:
    from willy.pipeline_launch import _process_group_is_alive
    from willy.controlled_launch import _UNREAPED
    from willy.run_processes import has_live_processes

    if has_live_processes(directory):
        return True

    for reservation, process in list(_UNREAPED.values()):
        if getattr(reservation, "run_dir", None) == directory and process.poll() is None:
            return True
    path = directory / "process_lifecycle.jsonl"
    if path.exists():
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - 131072))
            rows = handle.read().splitlines()
        for row in reversed(rows):
            try:
                record = json.loads(row)
            except (ValueError, UnicodeError):
                continue
            if isinstance(record, dict) and record.get("phase") in {"killed", "terminate", "interrupt"}:
                if _process_group_is_alive(record.get("pid")):
                    return True
    return False


def _environment_ready(status: Mapping, config: Mapping, context: Mapping) -> bool:
    from willy.env_checker import check_module
    from willy.step_registry import EXECUTION_MODULE_REGISTRY

    step = status.get("step")
    backend = config.get("backend", "g16")
    topology = config.get("topology") if isinstance(config.get("topology"), Mapping) else {}
    module = context.get("module") or {
        1: f"struct_{backend}", 2: f"sp_{backend}", 3: "chg_resp",
        4: "topo_opls" if topology.get("backend") == "ligpargen" else "topo_gaff",
        7: "box", 8: "gromacs_em", 9: "gromacs_eq", 10: "gromacs_prod",
    }.get(step)
    if not isinstance(module, str) or not EXECUTION_MODULE_REGISTRY.has_module(module):
        return False
    report = check_module(module)
    return bool(report.results) and report.is_ok(module)


def _resolution_basis(registry, directory: Path, status: Mapping, context: Mapping) -> str | None:
    from willy.resume_admission import validate_resume_admission
    from willy.run_control import safe_restart_step

    fault = public_fault(status.get("fault"))
    kind = fault.get("error_kind", status.get("error_kind"))
    if fault.get("phase") in {"stopping", "cleanup"} or kind in {"stop_failed", "stop_timeout", "cleanup_failed"}:
        return "process_exit"
    if kind in {"dependency_missing", "dependency_no_exec", "runtime_unavailable"}:
        config = registry.get_run_config(directory.name)
        config.setdefault("backend", registry._read_registry_manifest(directory).get("backend"))
        return "prerequisites" if _environment_ready(status, config, context) else None
    if kind in {"input_contract", "file_not_found", "config_invalid", "recovery_conflict"}:
        backend = registry._read_registry_manifest(directory).get("backend")
        validate_resume_admission(directory, backend=backend, status=status, restart_step=safe_restart_step(status), lock_held=True)
        return "prerequisites"
    if context.get("fault_id") == fault.get("fault_id") and context.get("manual_completed") is True:
        return "manual_declaration"
    return None


def review_fault(root: Path, run_id: str, *, declaration: Mapping | None = None) -> dict:
    """Refresh fault prerequisites without launching, hashing adoption or LLM calls."""
    from willy.pipeline_launch import PipelineLockConflict
    from willy.run_registry import RunRegistry, RunStateConflict
    from willy.run_store import run_transaction

    registry = RunRegistry(root)
    directory = registry.resolve_run_id(run_id)
    status = registry.get_run_status(run_id, reconcile=False)
    if declaration is not None:
        if type(declaration.get("state_revision")) is not int or declaration["state_revision"] != status.get("state_revision"):
            raise RunStateConflict("故障状态已更新，请刷新后重新声明")
        if declaration.get("fault_id") != public_fault(status.get("fault")).get("fault_id"):
            raise RunStateConflict("故障记录已更新，请刷新后重新声明")
    if status.get("state") == "stopping":
        requested_at = directory / "stop.request"
        try:
            timestamp = requested_at.stat().st_mtime if requested_at.exists() else datetime.fromisoformat(status["updated_at"]).timestamp()
            overdue = datetime.now(timezone.utc).timestamp() - timestamp >= STOP_REVIEW_SECONDS
        except (OSError, ValueError, KeyError):
            overdue = False
        if overdue:
            status = record_fault(root, run_id, phase="stopping", error_kind="stop_timeout")
    if status.get("state") != "escalated":
        if declaration is not None:
            raise RunStateConflict("当前工程不处于人工处理状态")
        return {}
    result = {"status": "blocked", "reason": "manual_review_required", "manual_completion_allowed": False}
    try:
        from willy.controlled_launch import _UNREAPED
        from willy.pipeline_launch import _process_group_is_alive
        for pid, (reservation, process) in list(_UNREAPED.items()):
            if getattr(reservation, "run_dir", None) == directory and process.poll() is not None and not _process_group_is_alive(pid):
                process.wait(timeout=0)
                reservation.release()
                del _UNREAPED[pid]
        with _idle_control_guard(root, directory):
            if registry.get_live_stage_evidence(run_id, status).get("active") or _remaining_processes(directory):
                return {**result, "reason": "process_exit_unconfirmed"}
            current = registry.get_run_status(run_id, reconcile=False)
            if current.get("state_revision") != status.get("state_revision") or current.get("state") != "escalated":
                raise RunStateConflict("复查期间状态发生变化，请刷新后重试")
            fault = public_fault(status.get("fault"))
            if not fault:
                status = record_fault(root, run_id, phase="recovery", error_kind=status.get("error_kind") if status.get("error_kind") in FAULT_KINDS else "unknown")
                fault = status["fault"]
            with run_transaction(directory) as store:
                context = store.read_json(FAULT_CONTEXT, {})
            if not isinstance(context, dict) or context.get("fault_id") != fault["fault_id"]:
                context = {"run_id": run_id, "fault_id": fault["fault_id"]}
            if declaration is not None:
                if declaration.get("fault_id") != fault["fault_id"]:
                    raise RunStateConflict("故障记录已更新，请刷新后重新声明")
                context = {**context, "manual_completed": True, "declared_at": _now()}
            kind = fault["error_kind"]
            result["manual_completion_allowed"] = kind not in {
                "dependency_missing", "dependency_no_exec", "runtime_unavailable", "input_contract",
                "file_not_found", "config_invalid", "recovery_conflict", "stop_failed", "stop_timeout", "cleanup_failed",
            }
            basis = _resolution_basis(registry, directory, status, context)
            if basis is None:
                return {**result, "reason": "manual_review_required" if result["manual_completion_allowed"] else "prerequisites_unmet"}
            resolved = {**fault, "resolution": "resolved", "resolved_at": _now(), "resolution_basis": basis}
            registry.compare_and_swap_status(
                directory, expected_revision=status["state_revision"],
                status={**status, "state": "aborted", "activity": {}, "repair": {}, "fault": resolved,
                        "error": "", "error_kind": "", "escalation": {}},
                event_type="run_fault_resolved", fault_context=context,
            )
            return {"status": "resolved", "reason": basis, "manual_completion_allowed": False}
    except PipelineLockConflict:
        return {**result, "reason": "process_exit_unconfirmed"}
