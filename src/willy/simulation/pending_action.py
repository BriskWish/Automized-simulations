"""Validated, run-local EQ recovery proposals and their one-time application."""

from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
import json
import re
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
_SCHEMA_VERSION = 2
_OPTION_LIMIT = 3
_NUMERIC_FIELDS = {
    "dt": ("时间步长", ("md", "dt"), " ps", 0.0001, 0.005),
    "tau_t": ("恒温耦合时间", ("md", "tau_t"), " ps", 0.0001, 20.0),
    "eq_tau_p": ("EQ 压强耦合时间", ("md", "eq", "tau_p"), " ps", 0.0001, 20.0),
    "lincs_iter": ("LINCS 迭代次数", ("md", "lincs_iter"), "", 1.0, 10.0),
    "lincs_order": ("LINCS 阶数", ("md", "lincs_order"), "", 1.0, 12.0),
}

# The model is instructed to use the canonical names above, but a user-facing
# EQ revision naturally says ``tau_p`` or ``hold time``.  This local alias
# table accepts only unambiguous EQ spellings; it never exposes a generic
# dotted-path configuration editor and therefore cannot alter PROD settings.
_EQ_FIELD_ALIASES = {
    "tau_p": "eq_tau_p",
    "taup": "eq_tau_p",
    "eq.tau_p": "eq_tau_p",
    "md.eq.tau_p": "eq_tau_p",
    "eq_taup": "eq_tau_p",
    "pressure_tau": "eq_tau_p",
    "eq_pressure_tau": "eq_tau_p",
    "压强耦合时间": "eq_tau_p",
    "压力耦合时间": "eq_tau_p",
    "eq压强耦合时间": "eq_tau_p",
    "hold_target": "eq_segment.hold_target",
    "hold_time": "eq_segment.hold_target",
    "eq.hold_target": "eq_segment.hold_target",
    "eq_hold_target": "eq_segment.hold_target",
    "target_hold": "eq_segment.hold_target",
    "final_hold": "eq_segment.hold_target",
    "最终保温段": "eq_segment.hold_target",
    "最终保温段时长": "eq_segment.hold_target",
    "目标温度保温段": "eq_segment.hold_target",
    "hold时间": "eq_segment.hold_target",
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
    selected = _selected_option(action)
    changes = tuple(
        ParameterChange(
            field_name=str(item.get("field", "")),
            before=item.get("before"),
            after=item.get("after"),
        )
        for item in selected.get("adjustments", [])
        if isinstance(item, Mapping) and item.get("field")
    )
    arguments = {
        "restart_step": int(selected.get("restart_step", action.get("restart_step", EQ_STEP))),
        "adjustments": [
            {"field": item.get("field"), "value": item.get("value")}
            for item in selected.get("adjustments", [])
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


_KNOWLEDGE_STATUSES = {"retrieved", "not_matched", "unavailable"}
_ADVICE_SOURCES = {"knowledge_base", "llm_unverified"}


def _knowledge_name(value: object) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def _validated_knowledge_entries(raw: object, context: object) -> list[dict[str, Any]]:
    """Keep only references returned by the server-side lookup tool."""
    available: dict[int, dict[str, Any]] = {}
    for item in context if isinstance(context, list) else []:
        if not isinstance(item, Mapping) or isinstance(item.get("number"), bool):
            continue
        try:
            number = int(item.get("number"))
        except (TypeError, ValueError):
            continue
        name = item.get("name")
        if isinstance(name, str) and name:
            available[number] = {"number": number, "name": name}
    result: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if len(result) >= 3:
            break
        if isinstance(item, Mapping):
            number, name = item.get("number"), item.get("name")
        else:
            number, name = item, None
        if isinstance(number, bool):
            continue
        try:
            number = int(number)
        except (TypeError, ValueError):
            continue
        canonical = available.get(number)
        if canonical is None or (
            name is not None and _knowledge_name(name) != _knowledge_name(canonical["name"])
        ):
            continue
        if number not in {entry["number"] for entry in result}:
            result.append(canonical)
    return result


def _knowledge_source_fields(
    proposed: Mapping[str, Any],
    *,
    context: object = None,
    lookup_status: object = None,
) -> dict[str, Any]:
    entries = _validated_knowledge_entries(proposed.get("knowledge_entries"), context)
    raw_status = str(proposed.get("knowledge_status") or "").strip()
    if entries:
        status, source = "retrieved", "knowledge_base"
    else:
        status = raw_status if raw_status in _KNOWLEDGE_STATUSES else str(lookup_status or "").strip()
        if status not in _KNOWLEDGE_STATUSES:
            return {}
        if status == "retrieved":
            fallback_status = str(lookup_status or "").strip()
            status = fallback_status if fallback_status in {"not_matched", "unavailable"} else "not_matched"
        source = "llm_unverified"
    return {
        "knowledge_status": status,
        "knowledge_entries": entries,
        "advice_source": source,
        "compatibility_notice": _clean_text(
            proposed.get("compatibility_notice")
            or "GROMACS User Guide 条目可能与当前安装版本或力场不完全适配，请在确认前复核。",
            300,
        ),
    }


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
    if isinstance(value, str):
        # Models occasionally retain units from the user's text ("2.5 ps"),
        # while the action schema needs a number. Accept only a complete
        # numeric token with a supported scientific unit, never free prose.
        matched = re.fullmatch(
            r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*"
            r"(?:ps|ns|g/cm\^?3)?\s*",
            value,
            flags=re.IGNORECASE,
        )
        if matched is None:
            raise PendingActionError(f"{field} 必须是数值")
        value = matched.group(1)
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise PendingActionError(f"{field} 必须是数值") from exc
    if not minimum <= numeric <= maximum:
        raise PendingActionError(f"{field} 超出允许范围")
    return numeric


def _canonical_eq_field(value: object) -> str | None:
    """Map bounded user/LLM aliases to one canonical EQ action field."""
    if not isinstance(value, str):
        return None
    text = "".join(value.strip().casefold().split()).replace("-", "_")
    if not text:
        return None
    return _EQ_FIELD_ALIASES.get(text, text)


def _append_rejection_reason(reasons: list[str] | None, reason: str) -> None:
    """Keep a small public-safe explanation for a rejected replacement."""
    if reasons is None or not reason or len(reasons) >= 4 or reason in reasons:
        return
    reasons.append(reason[:240])


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
    *,
    rejection_reasons: list[str] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(candidates, list):
        _append_rejection_reason(rejection_reasons, "调整项格式无效：需要 adjustments 数组")
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    candidate_config = deepcopy(config)
    for candidate in candidates[:8]:
        if not isinstance(candidate, Mapping):
            continue
        field = _canonical_eq_field(candidate.get("field"))
        if field is None:
            _append_rejection_reason(rejection_reasons, "调整项缺少可识别的参数字段")
            continue
        if field in seen:
            _append_rejection_reason(rejection_reasons, "调整方案重复修改同一参数")
            continue
        purpose = _clean_text(candidate.get("purpose") or candidate.get("reason"), 120)
        try:
            spec = _numeric_field_spec(field, candidate_config)
        except PendingActionError as exc:
            _append_rejection_reason(rejection_reasons, str(exc))
            continue
        if spec is not None:
            name, path, unit, minimum, maximum = spec
            try:
                after = _numeric(candidate.get("after"), field, minimum, maximum)
            except PendingActionError as exc:
                _append_rejection_reason(rejection_reasons, str(exc))
                continue
            if field in {"lincs_iter", "lincs_order"} and not after.is_integer():
                _append_rejection_reason(rejection_reasons, f"{field} 必须是整数")
                continue
            try:
                before = _get_value(candidate_config, path)
            except PendingActionError:
                _append_rejection_reason(rejection_reasons, f"{name} 在当前 EQ 配置中不可用")
                continue
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
                _append_rejection_reason(
                    rejection_reasons,
                    "EQ 时长字段无效：请使用 eq_segment.<六段名称>",
                )
                continue
            try:
                after = _numeric(candidate.get("after"), field, 0.000001, 100.0)
                before = _get_value(candidate_config, ("md", "eq", "segments_ns", segment))
            except PendingActionError as exc:
                _append_rejection_reason(rejection_reasons, str(exc))
                continue
            path = ("md", "eq", "segments_ns", segment)
            if before == after:
                _append_rejection_reason(rejection_reasons, f"EQ {segment} 时长没有实际变化")
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
            continue

        _append_rejection_reason(
            rejection_reasons,
            "存在不支持的调整字段：EQ 压浴请使用 eq_tau_p，最终保温请使用 eq_segment.hold_target",
        )
    return normalized


def _selected_option(action: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the selected option, or the first option for audit compatibility."""
    options = action.get("options")
    if isinstance(options, list):
        selected_id = action.get("selected_option_id")
        for option in options:
            if isinstance(option, Mapping) and option.get("option_id") == selected_id:
                return option
        for option in options:
            if isinstance(option, Mapping):
                return option
    return action


def _chinese_ordinal(value: int) -> str:
    return {1: "一", 2: "二", 3: "三"}.get(value, str(value))


def _normalize_option(
    config: dict[str, Any],
    raw_option: Mapping[str, Any],
    *,
    ordinal: int,
    requires_box_rebuild: bool,
    allow_fallback: bool,
    knowledge_context: object = None,
    knowledge_lookup_status: object = None,
    rejection_reasons: list[str] | None = None,
) -> dict[str, Any] | None:
    """Validate one candidate independently against the frozen base config."""
    proposed = raw_option if isinstance(raw_option, Mapping) else {}
    adjustments = _normalize_candidate_adjustments(
        config,
        proposed.get("adjustments"),
        rejection_reasons=rejection_reasons,
    )
    fallback = False
    if not adjustments:
        if not allow_fallback:
            return None
        adjustments = _fallback_adjustments(config, requires_box_rebuild=requires_box_rebuild)
        fallback = True
    elif requires_box_rebuild and not any(item["field"] == "box_density" for item in adjustments):
        adjustments = [*_fallback_adjustments(config, requires_box_rebuild=True), *adjustments]
    if not adjustments:
        _append_rejection_reason(
            rejection_reasons,
            "方案未包含可执行的允许修改项",
        )
        return None

    editable_parameters = _normalize_editable_parameters(
        config,
        proposed.get("editable_fields", proposed.get("modifiable_fields", proposed.get("modifiable_parameters"))),
        adjustments,
    )
    if requires_box_rebuild and not any(item.get("field") == "box_density" for item in editable_parameters):
        descriptor = _editable_parameter_descriptor(config, "box_density", "真空区或初始密度问题必须先重新建盒")
        if descriptor is not None:
            editable_parameters.insert(0, descriptor)
    if not editable_parameters:
        _append_rejection_reason(rejection_reasons, "方案未提供可复审的允许参数")
        return None

    changed = deepcopy(config)
    for adjustment in adjustments:
        field = adjustment["field"]
        spec = _numeric_field_spec(field, changed)
        if spec is not None:
            path = spec[1]
        else:
            segment = field.removeprefix("eq_segment.")
            if segment not in EQ_SEGMENT_NAMES:
                return None
            path = ("md", "eq", "segments_ns", segment)
        _set_value(changed, path, adjustment["value"])
    config_issues = validate_config(changed)
    if config_issues:
        issue = str(config_issues[0]).replace("\n", " ").strip()[:160]
        _append_rejection_reason(
            rejection_reasons,
            f"调整后运行配置不符合协议：{issue or '参数组合无效'}",
        )
        return None

    normalized = {
        "option_id": f"option_{ordinal}",
        "title": _clean_text(proposed.get("title"), 120) or f"方案{_chinese_ordinal(ordinal)}",
        "cause": _clean_text(proposed.get("cause"), 240) or "根据当前公开错误证据提出的可能原因",
        "evidence": _clean_text(proposed.get("evidence"), 300) or "当前公开证据不足以排除其他原因",
        "summary": _clean_text(proposed.get("summary"), 240) or "调整 EQ 参数后重新验收。",
        "restart_step": PACKMOL_STEP if any(item["field"] == "box_density" for item in adjustments) else EQ_STEP,
        "fallback": fallback,
        "adjustments": adjustments,
        "editable_parameters": editable_parameters,
    }
    source_fields = _knowledge_source_fields(
        proposed,
        context=knowledge_context,
        lookup_status=knowledge_lookup_status,
    )
    if source_fields:
        normalized.update(source_fields)
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
    knowledge_context = proposed.get("_retrieved_entries")
    knowledge_lookup_status = proposed.get("_knowledge_lookup_status")
    raw_options = proposed.get("options")
    if isinstance(raw_options, list):
        candidates = [item for item in raw_options[:_OPTION_LIMIT] if isinstance(item, Mapping)]
    else:
        candidates = [proposed]
    options: list[dict[str, Any]] = []
    rejection_reasons: list[str] = []
    for ordinal, candidate in enumerate(candidates, start=1):
        option = _normalize_option(
            config, candidate, ordinal=ordinal,
            requires_box_rebuild=requires_box_rebuild,
            allow_fallback=False,
            knowledge_context=knowledge_context,
            knowledge_lookup_status=knowledge_lookup_status,
            rejection_reasons=rejection_reasons,
        )
        if option is not None:
            options.append(option)
    if not options:
        fallback = _normalize_option(
            config, {}, ordinal=1,
            requires_box_rebuild=requires_box_rebuild,
            allow_fallback=allow_fallback,
            knowledge_context=knowledge_context,
            knowledge_lookup_status=knowledge_lookup_status,
            rejection_reasons=rejection_reasons,
        )
        if fallback is not None:
            options = [fallback]
        else:
            detail = "；".join(rejection_reasons[:4])
            raise PendingActionError(detail or "未生成可执行的 EQ 修复修改项")
    selected_option_id = options[0]["option_id"] if len(options) == 1 else None
    selected = options[0] if selected_option_id else {}
    summary = _clean_text(proposed.get("problem_summary") or proposed.get("summary"), 240)
    if not summary:
        summary = "EQ 失败可能由多个因素造成，请选择一个独立修复方案后确认。" if len(options) > 1 else options[0]["summary"]
    restart_step = int(selected.get("restart_step", EQ_STEP)) if selected else min(int(item["restart_step"]) for item in options)
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
        "fallback": all(bool(item.get("fallback")) for item in options),
        "options": options,
        "selected_option_id": selected_option_id,
        "selection_required": len(options) > 1,
        # Keep the original public shape populated for one option only.
        "adjustments": selected.get("adjustments", []),
        "editable_parameters": selected.get("editable_parameters", []),
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
    current = load_pending_action(run_dir)
    if current.get("state") != "pending" or current.get("action_id") != action_id:
        raise PendingActionError("待确认方案已失效或不匹配")
    if _config_fingerprint(Path(run_dir) / "config.json") != current.get("config_sha256"):
        raise PendingActionError("运行配置已变化，待确认方案已失效")
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
    if not isinstance(payload, dict):
        raise PendingActionError("待确认方案格式无效")
    # Schema v1 records are upgraded in memory as one already-selected option.
    if payload.get("schema_version") == 1:
        legacy = dict(payload)
        legacy_option = {
            "option_id": "option_1",
            "title": "方案一",
            "cause": "根据当前错误证据提出的修复方案",
            "evidence": "兼容旧版单方案记录",
            "summary": legacy.get("summary", ""),
            "restart_step": legacy.get("restart_step", EQ_STEP),
            "fallback": bool(legacy.get("fallback")),
            "adjustments": legacy.get("adjustments", []),
            "editable_parameters": legacy.get("editable_parameters", []),
        }
        legacy.update({
            "schema_version": _SCHEMA_VERSION,
            "options": [legacy_option],
            "selected_option_id": "option_1",
            "selection_required": False,
        })
        return legacy
    if (
        payload.get("schema_version") != _SCHEMA_VERSION
        or payload.get("run_id") != directory.name
        or payload.get("failure_step") != EQ_STEP
        or not isinstance(payload.get("action_id"), str)
        or not isinstance(payload.get("config_sha256"), str)
        or not isinstance(payload.get("options"), list)
        or not 1 <= len(payload.get("options", [])) <= _OPTION_LIMIT
    ):
        raise PendingActionError("待确认方案格式无效")
    for option in payload["options"]:
        if (
            not isinstance(option, Mapping)
            or not isinstance(option.get("option_id"), str)
            or not STEP_REGISTRY.controlled_restart_allowed(EQ_STEP, option.get("restart_step"))
            or not isinstance(option.get("adjustments"), list)
        ):
            raise PendingActionError("待确认方案选项格式无效")
    return payload


def public_pending_action(action: Mapping[str, Any]) -> dict[str, Any]:
    """Return just the bounded summary that may cross the frontend boundary."""
    options = action.get("options")
    selected = _selected_option(action) if not (
        isinstance(options, list) and len(options) > 1 and not action.get("selected_option_id")
    ) else {}

    def public_adjustments(items: object) -> list[dict[str, str]]:
        result = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, Mapping) or len(result) >= 8:
                continue
            entry = {
                key: item[key]
                for key in ("name", "before", "after", "purpose")
                if isinstance(item.get(key), str) and item.get(key)
            }
            if all(key in entry for key in ("name", "before", "after")):
                result.append(entry)
        return result

    def public_editables(items: object) -> list[dict[str, str]]:
        result = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, Mapping) or len(result) >= 8:
                continue
            values = {key: item.get(key) for key in ("name", "current", "range")}
            if not all(isinstance(value, str) and value for value in values.values()):
                continue
            entry = dict(values)
            if isinstance(item.get("purpose"), str) and item["purpose"]:
                entry["purpose"] = item["purpose"]
            result.append(entry)
        return result

    def public_knowledge_source(option: Mapping[str, Any]) -> dict[str, Any]:
        status = option.get("knowledge_status")
        source = option.get("advice_source")
        if status not in _KNOWLEDGE_STATUSES or source not in _ADVICE_SOURCES:
            return {}
        entries = []
        for item in option.get("knowledge_entries", []) if isinstance(option.get("knowledge_entries"), list) else []:
            if not isinstance(item, Mapping) or isinstance(item.get("number"), bool):
                continue
            try:
                number = int(item.get("number"))
            except (TypeError, ValueError):
                continue
            name = item.get("name")
            if isinstance(name, str) and name and len(entries) < 3:
                entries.append({"number": number, "name": _clean_text(name, 180)})
        return {
            "knowledge_status": status,
            "knowledge_entries": entries,
            "advice_source": source,
            "compatibility_notice": _clean_text(option.get("compatibility_notice"), 300),
        }

    public: dict[str, Any] = {
        "action_id": str(action.get("action_id", "")),
        "state": "pending",
        "step_label": STEP_REGISTRY.label_for(EQ_STEP),
        "restart_step": int(selected.get("restart_step", action.get("restart_step", 0))),
        "summary": _clean_text(action.get("summary"), 240),
        "adjustments": public_adjustments(selected.get("adjustments", action.get("adjustments", []))),
    }
    editable = public_editables(selected.get("editable_parameters", action.get("editable_parameters", [])))
    if editable:
        public["editable_parameters"] = editable
    if selected:
        public.update(public_knowledge_source(selected))
    options_public = []
    for ordinal, option in enumerate(action.get("options", []), start=1):
        if not isinstance(option, Mapping) or len(options_public) >= _OPTION_LIMIT:
            continue
        option_public = {
            "option_id": str(option.get("option_id", f"option_{ordinal}")),
            "ordinal": ordinal,
            "title": _clean_text(option.get("title"), 120) or f"方案{_chinese_ordinal(ordinal)}",
            "cause": _clean_text(option.get("cause"), 240),
            "evidence": _clean_text(option.get("evidence"), 300),
            "summary": _clean_text(option.get("summary"), 240),
            "restart_step": int(option.get("restart_step", EQ_STEP)),
            "adjustments": public_adjustments(option.get("adjustments")),
            "editable_parameters": public_editables(option.get("editable_parameters")),
        }
        option_public.update(public_knowledge_source(option))
        options_public.append(option_public)
    if len(options_public) > 1:
        public["options"] = options_public
        public["selected_option_id"] = action.get("selected_option_id")
        public["selection_required"] = bool(action.get("selection_required", False))
    return public


def validate_pending_action_for_launch(run_dir: str | Path, action_id: str) -> dict[str, Any]:
    """Reject any action other than the current pending record before launch."""
    directory = Path(run_dir)
    action = load_pending_action(directory)
    if action.get("state") != "pending" or action.get("action_id") != action_id:
        raise PendingActionError("待确认方案已失效或不匹配")
    if action.get("selection_required") and not action.get("selected_option_id"):
        raise PendingActionError("请先选择方案，再确认重跑")
    config_path = directory / "config.json"
    if _config_fingerprint(config_path) != action.get("config_sha256"):
        raise PendingActionError("运行配置已变化，待确认方案已失效")
    return action


def select_pending_action_option(
    run_dir: str | Path,
    action_id: str,
    option_id: str,
) -> dict[str, Any]:
    """Select one validated option without changing config or run state."""
    directory = Path(run_dir)
    action = load_pending_action(directory)
    if action.get("state") != "pending" or action.get("action_id") != action_id:
        raise PendingActionError("待确认方案已失效或不匹配")
    if _config_fingerprint(directory / "config.json") != action.get("config_sha256"):
        raise PendingActionError("运行配置已变化，待确认方案已失效")
    options = action.get("options", [])
    if not isinstance(option_id, str) or not any(
        isinstance(option, Mapping) and option.get("option_id") == option_id for option in options
    ):
        raise PendingActionError("方案选择无效，请选择方案一、方案二或方案三")
    action["selected_option_id"] = option_id
    action["selection_required"] = False
    selected = _selected_option(action)
    action["restart_step"] = selected.get("restart_step", EQ_STEP)
    action["adjustments"] = selected.get("adjustments", [])
    action["editable_parameters"] = selected.get("editable_parameters", [])
    _atomic_write(directory / PENDING_ACTION_FILENAME, action)
    return action


def apply_pending_action(run_dir: str | Path, action_id: str) -> dict[str, Any]:
    """Apply exactly one validated action to its own frozen run config."""
    directory = Path(run_dir)
    with pending_action_lock(directory):
        action = validate_pending_action_for_launch(directory, action_id)
        pending_action_validated(action)
        config_path = directory / "config.json"
        updated = _load_config(config_path)
        selected = _selected_option(action)
        for adjustment in selected.get("adjustments", []):
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
