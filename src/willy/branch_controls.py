"""Parameter changes create child runs without transitioning their parents."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from willy.branching import branch_receipt, control_path, preview_repair_config
from willy.charge_scaling import validate_ion_charge_scale
from willy.config_store import write_json
from willy.controlled_launch import LaunchCleanupError, handoff_controlled_process
from willy.run_control import RunControlError, apply_fork_changes, safe_restart_step, validate_fork_changes
from willy.run_registry import RunRegistry
from willy.simulation.manifest import ManifestError, RunLock, initialize_manifest, load_manifest
from willy.step_contracts import validate_step_inputs
from willy.workflow_config import validate_config


def _source(root: Path, run_id: str | None):
    registry = RunRegistry(root)
    if not isinstance(run_id, str):
        raise RunControlError("请先选择父工程")
    directory = registry.resolve_run_id(run_id)
    status = registry.get_run_status(run_id, reconcile=False)
    if status.get("state") not in {"aborted", "awaiting_confirmation", "escalated", "done"}:
        raise RunControlError("父工程仍在运行或尚未到可分支状态")
    done = status.get("done_steps", [])
    first = 11 if done == list(range(1, 11)) else safe_restart_step(status)
    if done != list(range(1, first)):
        raise RunControlError("父工程完成步骤不是连续前缀")
    backend = registry._read_registry_manifest(directory).get("backend")
    if backend not in {"g16", "g09", "orca"}:
        raise RunControlError("父工程后端无效")
    return registry, directory, status, backend, first


def create_branch(
    parent: Path, config: dict, *, restart_step: int, source_action_id: str = "",
    expected_revision: int | None = None, parameter_paths: tuple[str, ...] = (),
) -> str:
    import willy.frontend_api as api

    reservation = None
    child_registry = RunRegistry(api.ROOT)
    receipt_path = control_path(api.ROOT, "receipts", parent.name, source_action_id) if source_action_id else None
    try:
        registry, parent, status, backend, first = _source(api.ROOT, parent.name)
        if expected_revision is not None and status.get("state_revision") != expected_revision:
            raise RunControlError("父工程状态已变化，请重新确认")
        if source_action_id:
            receipt = branch_receipt(api.ROOT, parent.name, source_action_id)
            if receipt:
                return f"该方案已创建 fork {receipt['run_id']}，不会重复启动。"
        if validate_config(config):
            raise RunControlError("分支配置未通过现有参数契约")
        scale = validate_ion_charge_scale(config.get("ion_charge_scale", 1.0))
        original = registry.get_run_config(parent.name)
        if scale != validate_ion_charge_scale(original.get("ion_charge_scale", 1.0)):
            restart_step = min(restart_step, 3)
        restart_step = min(first, restart_step)
        reservation = api.reserve_pipeline_launch(api.ROOT)
        if source_action_id and branch_receipt(api.ROOT, parent.name, source_action_id):
            receipt = branch_receipt(api.ROOT, parent.name, source_action_id)
            reservation.release()
            return f"已创建 fork {receipt['run_id']}；该方案不会重复启动。"
        with RunLock(parent, remove_artifact=True):
            validate_step_inputs(parent, 6)
            from willy.run_store import run_transaction

            with run_transaction(parent):
                api._copy_fork_workspace(parent, reservation.run_dir, config, restart_step=restart_step)
            after = registry.get_run_status(parent.name, reconcile=False)
            if after.get("state_revision") != status.get("state_revision"):
                raise RunControlError("父工程在复制期间变化，未启动分支")
        child = reservation.run_dir
        child_registry.register_run(child, backend=backend, total_steps=10, parent_run_id=parent.name)
        try:
            load_manifest(child)
        except ManifestError:
            initialize_manifest(child, child / "config.json", random_seed=int(config.get("md", {}).get("run_seed", 1)))
        validate_step_inputs(child, restart_step)
        child_registry.record_status(child, {
            "state": "idle", "step": 0, "step_label": "", "layer": "", "activity": {},
            "done_steps": list(range(1, restart_step)),
        }, "fork_child_initialized")
        child_status = child_registry.get_run_status(child.name, reconcile=False)
        awaiting = api._transition_to_control_awaiting(child_registry, child.name, child_status, event_type="fork_intent_accepted")
        api._transition_control_to_retrying(child_registry, child.name, awaiting, restart_step=restart_step)
        child_registry.record_control_action(
            child.name, action="fork", outcome="created", restart_step=restart_step,
            stopped_step=min(first, 10), parameter_paths=parameter_paths, parent_run_id=parent.name,
        )
        child_registry.append_event(child, "branch_context_inherited", {
            "parent_run_id": parent.name, "source_action_id": source_action_id, "restart_step": restart_step,
        })
        if receipt_path is not None:
            write_json(receipt_path, {"parent_run_id": parent.name, "run_id": child.name, "source_action_id": source_action_id})
        process = api._spawn_controlled_run(reservation, backend=backend, restart_step=restart_step)
        handoff_controlled_process(process, reservation, api.ROOT, lambda: api._cleanup_controlled_launch(
            process, child.name, reservation.token,
        ))
        try:
            child_registry.record_control_action(
                child.name, action="fork", outcome="launched", restart_step=restart_step,
                stopped_step=min(first, 10), parameter_paths=parameter_paths, parent_run_id=parent.name,
            )
        except (OSError, ValueError):
            pass
        return f"已创建 fork {child.name}；完整继承工程上下文，从第 {restart_step} 步按新参数重跑，父工程状态保持不变。"
    except LaunchCleanupError:
        try:
            from willy.run_faults import record_fault
            record_fault(api.ROOT, reservation.run_dir.name, phase="cleanup", error_kind="cleanup_failed")
        except (OSError, ValueError, RuntimeError):
            pass
        return "分支启动交接失败，子进程尚未退出；已保留启动锁，父工程保持不变。"
    except (OSError, ValueError, RuntimeError) as exc:
        if receipt_path is not None:
            try:
                receipt_path.unlink(missing_ok=True)
            except OSError:
                pass
        if reservation is not None:
            try:
                child_registry.record_status(reservation.run_dir, {
                    "state": "aborted", "step": restart_step, "step_label": "分支准备",
                    "layer": "", "done_steps": list(range(1, restart_step)), "activity": {},
                    "error": "分支未启动，请检查继承输入或重新声明输入",
                }, "fork_launch_failed")
            except (OSError, ValueError):
                pass
            reservation.release()
        from willy.step_contracts import StepContractError
        reason = str(exc) if isinstance(exc, (RunControlError, StepContractError)) else "目录复制、启动预留或交接失败"
        return f"分支未启动：{reason}。父工程保持不变。"


def fork_run(run_id: str | None, command, *, expected_proposal: Mapping[str, object] | None = None) -> str:
    import willy.frontend_api as api

    try:
        registry, directory, status, _backend, first = _source(api.ROOT, run_id)
        config = registry.get_run_config(directory.name)
        if expected_proposal is not None:
            if expected_proposal.get("state_revision") != status.get("state_revision"):
                raise RunControlError("待确认 fork 方案已失效")
            from willy.step_contracts import fingerprint
            if expected_proposal.get("config_fingerprint") != fingerprint(directory, "config.json")["sha256"]:
                raise RunControlError("配置在方案生成后变化，请重新确认")
        plan = validate_fork_changes(config, command.changes, stopped_at=min(first, 10))
        config = apply_fork_changes(config, command.changes)
        return create_branch(
            directory, config, restart_step=plan.restart_step,
            expected_revision=status["state_revision"], parameter_paths=plan.parameter_paths,
            source_action_id=str(expected_proposal.get("proposal_id", "")) if expected_proposal else "",
        )
    except (OSError, ValueError):
        return "fork 参数或父工程不可用，未创建分支；父工程保持不变。"


def confirm_repair(
    action_id: str, run_id: str | None = None, *, state_revision: int | None = None,
    config_fingerprint: str | None = None,
) -> str:
    import willy.frontend_api as api

    try:
        registry, directory, status, _backend, _first = _source(api.ROOT, run_id)
        receipt = branch_receipt(api.ROOT, directory.name, action_id)
        if receipt:
            return f"该方案已创建 fork {receipt['run_id']}，不会重复启动。"
        if state_revision is not None and (isinstance(state_revision, bool) or state_revision != status.get("state_revision")):
            raise RunControlError("待确认方案版本已变化")
        if status.get("state") != "awaiting_confirmation":
            raise RunControlError("父工程当前没有等待确认的方案")
        if status.get("extra", {}).get("pending_action", {}).get("action_id") != action_id:
            raise RunControlError("待确认方案与当前状态不一致")
        action, config = preview_repair_config(directory, action_id)
        if config_fingerprint is not None and config_fingerprint != action.get("config_sha256"):
            raise RunControlError("待确认配置指纹已变化")
        return create_branch(
            directory, config, restart_step=min(6, int(action.get("restart_step", 9))),
            source_action_id=action_id, expected_revision=status["state_revision"],
            parameter_paths=tuple(item["field"] for item in action.get("adjustments", [])),
        )
    except (OSError, ValueError):
        return "待确认方案已失效或工程状态已变化，未创建分支，父工程保持不变。"
