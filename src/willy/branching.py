"""Independent copies of complete run context with fresh execution authority."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Mapping

from willy.charge_scaling import validate_ion_charge_scale
from willy.config_store import write_json
from willy.simulation.protocol import canonical_json_fingerprint
from willy.step_contracts import CONTRACT_FILENAME, fingerprint


def control_path(root: Path, kind: str, run_id: str, action_id: str = "") -> Path:
    key = hashlib.sha256(f"{run_id}:{action_id}".encode()).hexdigest()
    return root / ".willy" / "run_controls" / kind / f"{key}.json"


def branch_receipt(root: Path, run_id: str, action_id: str) -> dict[str, Any] | None:
    try:
        value = json.loads(control_path(root, "receipts", run_id, action_id).read_text())
        return value if isinstance(value, dict) and value.get("parent_run_id") == run_id else None
    except (OSError, ValueError):
        return None


def _rebind(value: Any, parent: Path, child: Path, key: str = "") -> Any:
    if isinstance(value, dict):
        return {name: _rebind(item, parent, child, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_rebind(item, parent, child, key) for item in value]
    if isinstance(value, str):
        if value == str(parent) and key in {"run_dir", "workspace", "output_dir"}:
            return str(child)
        if value.startswith(str(parent) + os.sep):
            return str(child / Path(value).relative_to(parent))
        if key in {"run_id", "workspace_id"} and value == parent.name:
            return child.name
    return value


def copy_run_context(parent_dir: Path, child_dir: Path, config: Mapping[str, Any], *, restart_step: int = 6) -> None:
    parent = parent_dir.resolve()
    child = child_dir.resolve()
    if parent == child or child.is_relative_to(parent) or parent.is_relative_to(child):
        raise ValueError("分支必须使用独立的兄弟工程目录")
    if (parent / "old").is_symlink():
        raise ValueError("old/ 必须是独立归档目录")
    if (parent / ".run-transaction.json").exists():
        raise ValueError("父工程仍有未完成事务，请先恢复事务后创建分支")
    original = json.loads((parent / "config.json").read_text(encoding="utf-8"))
    scale = validate_ion_charge_scale(config.get("ion_charge_scale", 1.0))
    if scale != validate_ion_charge_scale(original.get("ion_charge_scale", 1.0)) and restart_step > 3:
        raise ValueError("ion_charge_scale 变化必须从第 3 步或更早重建")
    links = []
    for source in parent.rglob("*"):
        if source.is_symlink():
            resolved = source.resolve(strict=True)
            if not resolved.is_relative_to(parent):
                raise ValueError("工程包含指向外部的链接，须先将输入导入当前工程")
            links.append((source.relative_to(parent), resolved.relative_to(parent)))
        elif not source.is_file() and not source.is_dir():
            raise ValueError("工程包含不可复制的特殊文件")

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name.endswith((".lock", ".pid")) or name == "stop.request" or name.startswith(".status")}

    shutil.copytree(parent, child, dirs_exist_ok=True, symlinks=True, ignore=ignore)
    for source, target in links:
        copied = child / source
        if copied.is_symlink():
            copied.unlink()
            copied.symlink_to(os.path.relpath(child / target, copied.parent))
    snapshot = child / "old" / f"fork-source-{parent.name}" / "context"
    snapshot.mkdir(parents=True, exist_ok=True)
    for source in list(child.iterdir()):
        if source.is_file() and source.suffix in {".json", ".jsonl"}:
            shutil.copy2(source, snapshot / source.name)
            if source.suffix == ".json":
                try:
                    payload = json.loads(source.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                payload = _rebind(payload, parent, child)
                if source.name == "pending_action.json":
                    payload["state"] = "inherited"
                if source.name == "proposal.json":
                    payload["pending_plan"] = None
                if source.name == "run_manifest.json":
                    payload["sections"]["registry"]["data"] = {}
                write_json(source, payload)
            elif source.name == "events.jsonl":
                events = []
                for line in source.read_text(encoding="utf-8").splitlines():
                    event = json.loads(line)
                    event["origin_run_id"] = event.get("run_id", parent.name)
                    event["run_id"] = child.name
                    events.append(json.dumps(event, ensure_ascii=False))
                source.write_text("\n".join(events) + "\n", encoding="utf-8")
    for name in ("status.json", "mdrun_eta.json", "pending_action.json", "manifest.json", ".process_owners.json", ".fault_context.json"):
        (child / name).unlink(missing_ok=True)
    write_json(child / "config.json", dict(config))
    contract_path = child / CONTRACT_FILENAME
    if contract_path.exists():
        payload = json.loads(contract_path.read_text())
        payload["run_id"] = child.name
        payload["origin_run_id"] = parent.name
        payload["config_signature"] = canonical_json_fingerprint(config)
        observed = fingerprint(child, "config.json")
        payload["artifacts"]["config.json"] = {**observed, "source": "fork_config", "producer_step": 0, "accepted": True}
        payload.get("adoptions", {}).pop("config.json", None)
        from willy.step_contracts import step_input_paths, step_output_paths, _matches

        invalidated = {
            str(path.relative_to(child))
            for step in range(restart_step, 11)
            for path in step_output_paths(child, step)
        }
        for name, entry in payload["artifacts"].items():
            if entry.get("producer_step", 0) >= restart_step or name in invalidated:
                entry["accepted"] = False
                invalidated.add(name)
        for name in invalidated:
            payload["adoptions"].pop(name, None)
        for step, record in payload["steps"].items():
            if int(step) >= restart_step:
                record["status"] = "invalidated"
        write_json(contract_path, payload)

        for path in step_input_paths(child, restart_step):
            if path.exists():
                continue
            relative = str(path.relative_to(child))
            expected = payload["artifacts"].get(relative, {})
            if expected.get("accepted") is not True:
                continue
            for archived in sorted((child / "old").glob(f"*/files/{relative}")):
                if _matches(fingerprint(child, archived), expected):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(archived, path)
                    break
    write_json(child / "fork_context.json", {
        "schema_version": 1, "parent_run_id": parent.name, "run_id": child.name,
        "restart_step": restart_step, "source_context": str(snapshot.relative_to(child)),
    })


def preview_repair_config(run_dir: Path, action_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from willy.simulation.pending_action import (
        PendingActionError, _load_config, _numeric_field_spec, _selected_option,
        _set_value, pending_action_validated, validate_pending_action_for_launch,
    )
    from willy.simulation.protocol import EQ_SEGMENT_NAMES
    from willy.workflow_config import validate_config

    action = validate_pending_action_for_launch(run_dir, action_id)
    pending_action_validated(action)
    config = copy.deepcopy(_load_config(run_dir / "config.json"))
    for adjustment in _selected_option(action).get("adjustments", []):
        field = adjustment.get("field")
        spec = _numeric_field_spec(field, config)
        if spec is not None:
            path = spec[1]
        elif isinstance(field, str) and field.startswith("eq_segment.") and field.removeprefix("eq_segment.") in EQ_SEGMENT_NAMES:
            path = ("md", "eq", "segments_ns", field.removeprefix("eq_segment."))
        else:
            raise PendingActionError("待确认方案修改项无效")
        _set_value(config, path, adjustment.get("value"))
    if validate_config(config):
        raise PendingActionError("分支方案不能生成有效配置")
    return action, config
