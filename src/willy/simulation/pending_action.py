"""Validated, run-local EQ recovery proposals and their one-time application."""

from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
import json
import secrets
import fcntl

from willy.simulation.protocol import EQ_SEGMENT_NAMES
from willy.step_registry import EQ_STEP, PACKMOL_STEP, STEP_REGISTRY
from willy.workflow_config import validate_config
from willy.config_store import write_json
from willy.action_contract import (
    ActionProposal,
    ExecutedAction,
    ParameterChange,
    ToolDeclaration,
    ValidatedAction,
    ActionEffect,
)


PENDING_ACTION_FILENAME = "pending_action.json"
PENDING_ACTION_LOCK_FILENAME = ".pending_action.lock"
_SCHEMA_VERSION = 1
_NUMERIC_FIELDS = {
    "dt": ("时间步长", ("md", "dt"), " ps", 0.0001, 0.005),
    "tau_t": ("恒温耦合时间", ("md", "tau_t"), " ps", 0.0001, 20.0),
    "eq_tau_p": ("EQ 压强耦合时间", ("md", "eq", "tau_p"), " ps", 0.0001, 20.0),
    "lincs_iter": ("LINCS 迭代次数", ("md", "lincs_iter"), "", 1.0, 10.0),
    "lincs_order": ("LINCS 阶数", ("md", "lincs_order"), "", 1.0, 12.0),
}


class PendingActionError(ValueError):
    """Raised when a proposed EQ recovery is stale, malformed, or unsafe."""


def pending_action_proposal(
    action: Mapping[str, Any],
    *,
    model_id: str = "",
    prompt_version: str = "eq_recovery_v1",
) -> ActionProposal:
    """Adapt the persisted EQ record to the generic proposal contract."""
    changes = tuple(
        ParameterChange(
            field_name=str(item.get("field", "")),
            before=item.get("before"),
            after=item.get("after"),
        )
        for item in action.get("adjustments", [])
        if isinstance(item, Mapping) and item.get("field")
    )
    arguments = {
        "restart_step": int(action.get("restart_step", EQ_STEP)),
        "adjustments": [
            {"field": item.get("field"), "value": item.get("value")}
            for item in action.get("adjustments", [])
            if isinstance(item, Mapping) and item.get("field")
        ],
    }
    return ActionProposal(
        decision_id=str(action.get("action_id", "")),
        run_id=str(action.get("run_id", "")),
        layer="simulation",
        tool_name="tools_retry_eq",
        arguments=arguments,
        failed_step=int(action.get("failure_step", EQ_STEP)),
        parameter_changes=changes,
        config_fingerprint=str(action.get("config_sha256", "")),
        model_id=model_id,
        prompt_version=prompt_version,
        created_at=str(action.get("created_at", _now())),
    )


def pending_action_validated(action: Mapping[str, Any]) -> ValidatedAction:
    """Validate an EQ record against its declared tool effect."""
    proposal = pending_action_proposal(action)
    declaration = ToolDeclaration(
        tool_name="tools_retry_eq",
        layer="simulation",
        effect=ActionEffect.REQUIRES_CONFIRMATION,
        restart_step=int(action.get("restart_step", EQ_STEP)),
    )
    return ValidatedAction(proposal=proposal, declaration=declaration, policy_id="simulation.eq.user_confirmation")


def pending_action_executed(
    action: Mapping[str, Any],
    *,
    success: bool,
    result_summary: str = "",
    output_keys: tuple[str, ...] = (),
    error_kind: str = "",
) -> ExecutedAction:
    """Build a bounded execution record without changing the legacy JSON shape."""
    return ExecutedAction(
        action=pending_action_validated(action),
        success=success,
        result_summary=result_summary,
        output_keys=output_keys,
        error_kind=error_kind,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    write_json(path, payload)


@contextmanager
def pending_action_lock(run_dir: str | Path):
    """Serialize confirmation and replacement of one run-local repair action."""
    directory = Path(run_dir)
    lock_path = directory / PENDING_ACTION_LOCK_FILENAME
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _config_fingerprint(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _clean_text(value: object, limit: int) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    if not text or "/" in text or "\\" in text or ".." in text:
        return ""
    return text[:limit]


def _get_value(config: Mapping[str, Any], path: tuple[str, ...]) -> object:
    value: object = config
    for key in path:
        if not isinstance(value, Mapping) or key not in value:
            raise PendingActionError("动作所需的配置字段不存在")
        value = value[key]
    return value


def _set_value(config: dict[str, Any], path: tuple[str, ...], value: object) -> None:
    target: dict[str, Any] = config
    for key in path[:-1]:
        child = target.get(key)
        if not isinstance(child, dict):
            raise PendingActionError("动作所需的配置字段不存在")
        target = child
    target[path[-1]] = value


def _numeric_field_spec(
    field: str,
    config: Mapping[str, Any],
) -> tuple[str, tuple[str, ...], str, float, float] | None:
    """Resolve box-density proposals against the run's active box contract."""
    if field != "box_density":
        return _NUMERIC_FIELDS.get(field)
    box = config.get("box", {})
    if not isinstance(box, Mapping):
        raise PendingActionError("动作所需的 box 配置字段不存在")
    if "target_mass_density_g_cm3" in box:
        return ("初始质量密度", ("box", "target_mass_density_g_cm3"), " g/cm3", 0.001, 25.0)
    if "packing_number_density_nm3" in box:
        return ("建盒数密度", ("box", "packing_number_density_nm3"), " 分子/nm3", 0.001, 100.0)
    raise PendingActionError("动作所需的建盒密度字段不存在")


def _format_value(value: object, unit: str) -> str:
    if isinstance(value, float):
        text = f"{value:g}"
    else:
        text = str(value)
    return f"{text}{unit}"


def _numeric(value: object, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise PendingActionError(f"{field} 必须是数值")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise PendingActionError(f"{field} 必须是数值") from exc
    if not minimum <= numeric <= maximum:
        raise PendingActionError(f"{field} 超出允许范围")
    return numeric


def _editable_parameter_descriptor(
    config: Mapping[str, Any],
    field: object,
    purpose: object = "",
) -> dict[str, str] | None:
    """Build a public, bounded description for one editable EQ field."""
    if not isinstance(field, str):
        return None
    spec = _numeric_field_spec(field, config)
    if spec is not None:
        name, path, unit, minimum, maximum = spec
    elif field.startswith("eq_segment."):
        segment = field.removeprefix("eq_segment.")
        if segment not in EQ_SEGMENT_NAMES:
            return None
        name = f"EQ {segment} 时长"
        path = ("md", "eq", "segments_ns", segment)
        unit, minimum, maximum = " ns", 0.000001, 100.0
    else:
        return None
    current = _get_value(config, path)
    reason = _clean_text(purpose, 120) or "可根据用户约束重新评估"
    return {
        "field": field,
        "name": name,
        "current": _format_value(current, unit),
        "range": f"{_format_value(minimum, unit)} 至 {_format_value(maximum, unit)}",
        "purpose": reason,
    }


def _normalize_editable_parameters(
    config: Mapping[str, Any],
    candidates: object,
    adjustments: list[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Accept only the same bounded fields that the apply gate understands."""
    raw_candidates = candidates if isinstance(candidates, list) else []
    descriptors: list[dict[str, str]] = []
    seen: set[str] = set()
    for candidate in raw_candidates[:8]:
        if isinstance(candidate, Mapping):
            field = candidate.get("field")
            purpose = candidate.get("purpose") or candidate.get("reason")
        else:
            field, purpose = candidate, ""
        if not isinstance(field, str) or field in seen:
            continue
        descriptor = _editable_parameter_descriptor(config, field, purpose)
        if descriptor is None:
            continue
        descriptors.append(descriptor)
        seen.add(field)

    # Older model responses did not have editable_fields; expose the fields
    # it actually proposed instead of silently hiding the revision interface.
    if not descriptors:
        for adjustment in adjustments:
            field = adjustment.get("field")
            if not isinstance(field, str) or field in seen:
                continue
            descriptor = _editable_parameter_descriptor(
                config, field, adjustment.get("purpose"),
            )
            if descriptor is not None:
                descriptors.append(descriptor)
                seen.add(field)
    return descriptors


def _normalize_candidate_adjustments(
    config: dict[str, Any],
    candidates: object,
) -> list[dict[str, Any]]:
    if not isinstance(candidates, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    candidate_config = deepcopy(config)
    for candidate in candidates[:8]:
        if not isinstance(candidate, Mapping):
            continue
        field = candidate.get("field")
        if not isinstance(field, str) or field in seen:
            continue
        purpose = _clean_text(candidate.get("purpose") or candidate.get("reason"), 120)
        spec = _numeric_field_spec(field, candidate_config)
        if spec is not None:
            name, path, unit, minimum, maximum = spec
            after = _numeric(candidate.get("after"), field, minimum, maximum)
            if field in {"lincs_iter", "lincs_order"} and not after.is_integer():
                continue
            before = _get_value(candidate_config, path)
            after_value: object = int(after) if field in {"lincs_iter", "lincs_order"} else after
            if before == after_value:
                continue
            _set_value(candidate_config, path, after_value)
            normalized.append({
                "field": field,
                "name": name,
                "before": _format_value(before, unit),
                "after": _format_value(after_value, unit),
                "value": after_value,
                "purpose": purpose or "降低 EQ 失败再次发生的风险",
            })
            seen.add(field)
            continue

        if field.startswith("eq_segment."):
            segment = field.removeprefix("eq_segment.")
            if segment not in EQ_SEGMENT_NAMES:
                continue
            after = _numeric(candidate.get("after"), field, 0.000001, 100.0)
            path = ("md", "eq", "segments_ns", segment)
            before = _get_value(candidate_config, path)
            if before == after:
                continue
            _set_value(candidate_config, path, after)
            normalized.append({
                "field": field,
                "name": f"EQ {segment} 时长",
                "before": _format_value(before, " ns"),
                "after": _format_value(after, " ns"),
                "value": after,
                "purpose": purpose or "增加目标温度段的稳定采样",
            })
            seen.add(field)
    return normalized


def _fallback_adjustments(
    config: dict[str, Any],
    *,
    requires_box_rebuild: bool,
) -> list[dict[str, Any]]:
    """Provide a conservative proposal only when a model response is unusable."""
    candidates: list[dict[str, object]] = []
    if requires_box_rebuild:
        box = config.get("box", {})
        if not isinstance(box, Mapping):
            raise PendingActionError("动作所需的 box 配置字段不存在")
        density = box.get("target_mass_density_g_cm3", box.get("packing_number_density_nm3"))
        if density is None:
            raise PendingActionError("动作所需的建盒密度字段不存在")
        maximum = 25.0 if "target_mass_density_g_cm3" in box else 100.0
        candidates.append({
            "field": "box_density", "after": min(maximum, round(float(density) * 1.1, 6)),
            "purpose": "消除 EQ 末态真空区后重新建盒",
        })
        return _normalize_candidate_adjustments(config, candidates)
    dt = _get_value(config, ("md", "dt"))
    if float(dt) > 0.0005:
        candidates.append({
            "field": "dt", "after": 0.0005,
            "purpose": "降低高温退火段的数值不稳定风险",
        })
    tau_t = _get_value(config, ("md", "tau_t"))
    if float(tau_t) < 1.0:
        candidates.append({
            "field": "tau_t", "after": 1.0,
            "purpose": "减缓恒温耦合，避免温度过度响应",
        })
    hold_target = _get_value(config, ("md", "eq", "segments_ns", "hold_target"))
    if float(hold_target) < 4.0:
        candidates.append({
            "field": "eq_segment.hold_target", "after": 4.0,
            "purpose": "增加最终 298 K 保温段的验收采样",
        })
    return _normalize_candidate_adjustments(config, candidates)


def _load_config(config_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PendingActionError("运行配置不可读取") from exc
    if not isinstance(payload, dict):
        raise PendingActionError("运行配置格式无效")
    return payload


def create_eq_pending_action(
    run_dir: str | Path,
    *,
    proposal: Mapping[str, Any] | None,
    requires_box_rebuild: bool = False,
    allow_fallback: bool = True,
) -> dict[str, Any]:
    """Persist one validated EQ action without altering its config snapshot.

    Initial EQ failures may use the conservative fallback when the model is
    unavailable.  A user-requested revision must instead contain a valid
    model proposal, so it can opt out through ``allow_fallback``.
    """
    directory = Path(run_dir)
    config_path = directory / "config.json"
    config = _load_config(config_path)
    proposed = proposal if isinstance(proposal, Mapping) else {}
    adjustments = _normalize_candidate_adjustments(config, proposed.get("adjustments"))
    fallback = False
    if not adjustments:
        if not allow_fallback:
            raise PendingActionError("未生成可执行的 EQ 修复修改项")
        adjustments = _fallback_adjustments(config, requires_box_rebuild=requires_box_rebuild)
        fallback = True
    elif requires_box_rebuild and not any(item["field"] == "box_density" for item in adjustments):
        # A detected vacuum cannot be repaired by changing only EQ controls;
        # retain any compatible model suggestions but require a fresh box.
        adjustments = [
            *_fallback_adjustments(config, requires_box_rebuild=True),
            *adjustments,
        ]
    if not adjustments:
        raise PendingActionError("未生成可执行的 EQ 修复修改项")

    editable_parameters = _normalize_editable_parameters(
        config,
        proposed.get("editable_fields", proposed.get("modifiable_fields", proposed.get("modifiable_parameters"))),
        adjustments,
    )
    if requires_box_rebuild and not any(item.get("field") == "box_density" for item in editable_parameters):
        box_descriptor = _editable_parameter_descriptor(
            config, "box_density", "真空区或初始密度问题必须先重新建盒",
        )
        if box_descriptor is not None:
            editable_parameters.insert(0, box_descriptor)
    if not editable_parameters:
        raise PendingActionError("未生成可复审的 EQ 参数接口")

    changed = deepcopy(config)
    for adjustment in adjustments:
        field = adjustment["field"]
        spec = _numeric_field_spec(field, changed)
        if spec is not None:
            _set_value(changed, spec[1], adjustment["value"])
        else:
            segment = field.removeprefix("eq_segment.")
            _set_value(changed, ("md", "eq", "segments_ns", segment), adjustment["value"])
    issues = validate_config(changed)
    if issues:
        raise PendingActionError("提议修改后的运行配置无效")

    summary = _clean_text(proposed.get("summary"), 240)
    if not summary:
        summary = "调整 EQ 数值稳定性与末段采样后，从 EQ 重新执行并重新验收。"
    restart_step = PACKMOL_STEP if any(item["field"] == "box_density" for item in adjustments) else EQ_STEP
    action = {
        "schema_version": _SCHEMA_VERSION,
        "action_id": f"eq-{secrets.token_urlsafe(12)}",
        "run_id": directory.name,
        "state": "pending",
        "created_at": _now(),
        "failure_step": EQ_STEP,
        "restart_step": restart_step,
        "config_sha256": _config_fingerprint(config_path),
        "summary": summary,
        "fallback": fallback,
        "adjustments": adjustments,
        "editable_parameters": editable_parameters,
    }
    try:
        pending_action_validated(action)
    except (TypeError, ValueError) as exc:
        raise PendingActionError("EQ 动作不符合通用动作契约") from exc
    _atomic_write(directory / PENDING_ACTION_FILENAME, action)
    return action


def replace_eq_pending_action(
    run_dir: str | Path,
    *,
    action_id: str,
    proposal: Mapping[str, Any],
) -> dict[str, Any]:
    """Replace one still-pending EQ proposal without touching ``config.json``.

    The existing action ID and frozen config fingerprint are validated before
    replacement.  A prior box rebuild remains mandatory for the replacement,
    preventing a user preference from accidentally bypassing a detected
    vacuum recovery requirement.
    """
    current = validate_pending_action_for_launch(run_dir, action_id)
    return create_eq_pending_action(
        run_dir,
        proposal=proposal,
        requires_box_rebuild=current.get("restart_step") == PACKMOL_STEP,
        allow_fallback=False,
    )


def load_pending_action(run_dir: str | Path) -> dict[str, Any]:
    """Read one private, run-local action record with its minimal invariants."""
    directory = Path(run_dir)
    try:
        payload = json.loads((directory / PENDING_ACTION_FILENAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PendingActionError("待确认方案不可读取") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != _SCHEMA_VERSION
        or payload.get("run_id") != directory.name
        or payload.get("failure_step") != EQ_STEP
        or not STEP_REGISTRY.controlled_restart_allowed(EQ_STEP, payload.get("restart_step"))
        or not isinstance(payload.get("action_id"), str)
        or not isinstance(payload.get("config_sha256"), str)
    ):
        raise PendingActionError("待确认方案格式无效")
    return payload


def public_pending_action(action: Mapping[str, Any]) -> dict[str, Any]:
    """Return just the bounded summary that may cross the frontend boundary."""
    public: dict[str, Any] = {
        "action_id": str(action.get("action_id", "")),
        "state": "pending",
        "step_label": STEP_REGISTRY.label_for(EQ_STEP),
        "restart_step": int(action.get("restart_step", 0)),
        "summary": _clean_text(action.get("summary"), 240),
        "adjustments": [
            {
                key: item[key]
                for key in ("name", "before", "after", "purpose")
                if isinstance(item, Mapping) and isinstance(item.get(key), str)
            }
            for item in action.get("adjustments", [])[:8]
            if isinstance(item, Mapping)
        ],
    }
    editable = []
    for item in action.get("editable_parameters", [])[:8]:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        current = item.get("current")
        value_range = item.get("range")
        purpose = item.get("purpose")
        if not all(isinstance(value, str) and value for value in (name, current, value_range)):
            continue
        entry = {"name": name, "current": current, "range": value_range}
        if isinstance(purpose, str) and purpose:
            entry["purpose"] = purpose
        editable.append(entry)
    if editable:
        public["editable_parameters"] = editable
    return public


def validate_pending_action_for_launch(run_dir: str | Path, action_id: str) -> dict[str, Any]:
    """Reject any action other than the current pending record before launch."""
    directory = Path(run_dir)
    action = load_pending_action(directory)
    if action.get("state") != "pending" or action.get("action_id") != action_id:
        raise PendingActionError("待确认方案已失效或不匹配")
    config_path = directory / "config.json"
    if _config_fingerprint(config_path) != action.get("config_sha256"):
        raise PendingActionError("运行配置已变化，待确认方案已失效")
    return action


def apply_pending_action(run_dir: str | Path, action_id: str) -> dict[str, Any]:
    """Apply exactly one validated action to its own frozen run config."""
    directory = Path(run_dir)
    with pending_action_lock(directory):
        action = validate_pending_action_for_launch(directory, action_id)
        pending_action_validated(action)
        config_path = directory / "config.json"
        updated = _load_config(config_path)
        for adjustment in action.get("adjustments", []):
            if not isinstance(adjustment, Mapping):
                raise PendingActionError("待确认方案修改项无效")
            field = adjustment.get("field")
            spec = _numeric_field_spec(field, updated)
            if spec is not None:
                path = spec[1]
            elif isinstance(field, str) and field.startswith("eq_segment."):
                segment = field.removeprefix("eq_segment.")
                if segment not in EQ_SEGMENT_NAMES:
                    raise PendingActionError("待确认方案修改项无效")
                path = ("md", "eq", "segments_ns", segment)
            else:
                raise PendingActionError("待确认方案修改项无效")
            _set_value(updated, path, adjustment.get("value"))
        if validate_config(updated):
            raise PendingActionError("待确认方案不能生成有效运行配置")
        _atomic_write(config_path, updated)
        action["state"] = "applied"
        action["applied_at"] = _now()
        action["applied_config_sha256"] = _config_fingerprint(config_path)
        _atomic_write(directory / PENDING_ACTION_FILENAME, action)
        return action
