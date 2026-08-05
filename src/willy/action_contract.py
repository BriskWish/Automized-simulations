"""Immutable contracts for proposed, validated, and executed Agent actions.

This module is intentionally independent from Agent dispatch.  It gives every
LLM-visible tool one declared effect level before recovery policy and state
machine enforcement are introduced.  Existing handlers keep their current
execution path until those later layers consume these contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import re
import secrets
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence


ACTION_CONTRACT_VERSION = 1

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_TOOL_NAME_RE = re.compile(r"^tools_[a-z0-9_]+$")


class ActionContractError(ValueError):
    """Raised when an action contract is incomplete or internally inconsistent."""


class ActionEffect(str, Enum):
    """The highest-impact effect a tool may have on a pipeline run."""

    READ_ONLY = "read_only"
    RETRY_SAFE = "retry_safe"
    REQUIRES_CONFIRMATION = "requires_confirmation"
    REQUIRES_FORK = "requires_fork"
    DESTRUCTIVE = "destructive"

    @property
    def requires_confirmation(self) -> bool:
        return self in {
            ActionEffect.REQUIRES_CONFIRMATION,
            ActionEffect.REQUIRES_FORK,
            ActionEffect.DESTRUCTIVE,
        }

    @property
    def requires_fork(self) -> bool:
        return self is ActionEffect.REQUIRES_FORK

    @property
    def is_read_only(self) -> bool:
        return self is ActionEffect.READ_ONLY


_EFFECT_RANK = {
    ActionEffect.READ_ONLY: 0,
    ActionEffect.RETRY_SAFE: 1,
    ActionEffect.REQUIRES_CONFIRMATION: 2,
    ActionEffect.REQUIRES_FORK: 3,
    ActionEffect.DESTRUCTIVE: 4,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_decision_id() -> str:
    return f"act_{secrets.token_urlsafe(18)}"


def _require_identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ActionContractError(f"{field_name} 必须是 1-128 位的安全标识符")
    return value


def _require_text(value: str, field_name: str, *, max_length: int = 240) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length or "\n" in value:
        raise ActionContractError(f"{field_name} 必须是非空单行文本")
    return value


def _freeze_json(value: object, field_name: str) -> object:
    """Validate JSON compatibility and make nested mappings immutable."""
    try:
        canonical = json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))
    except (TypeError, ValueError) as exc:
        raise ActionContractError(f"{field_name} 必须为 JSON 兼容值") from exc
    return _freeze_canonical_json(canonical)


def _freeze_canonical_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_canonical_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_canonical_json(item) for item in value)
    return value


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _require_json_mapping(value: Mapping[str, object], field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ActionContractError(f"{field_name} 必须是 JSON 对象")
    frozen = _freeze_json(dict(value), field_name)
    if not isinstance(frozen, Mapping):  # Defensive guard for future helper changes.
        raise ActionContractError(f"{field_name} 必须是 JSON 对象")
    return frozen


@dataclass(frozen=True)
class ParameterChange:
    """One declared configuration difference, without persisting source files."""

    field_name: str
    before: object = None
    after: object = None

    def __post_init__(self) -> None:
        _require_text(self.field_name, "field_name", max_length=160)
        object.__setattr__(self, "before", _freeze_json(self.before, "before"))
        object.__setattr__(self, "after", _freeze_json(self.after, "after"))

    def to_dict(self) -> dict[str, object]:
        return {
            "field_name": self.field_name,
            "before": _thaw_json(self.before),
            "after": _thaw_json(self.after),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ParameterChange":
        if not isinstance(payload, Mapping):
            raise ActionContractError("parameter_change 必须是对象")
        return cls(
            field_name=payload.get("field_name", ""),
            before=payload.get("before"),
            after=payload.get("after"),
        )


@dataclass(frozen=True)
class ToolDeclaration:
    """Static policy-relevant declaration for one LLM-visible tool."""

    tool_name: str
    layer: str
    effect: ActionEffect
    enabled: bool = True
    invalidates_stages: tuple[str, ...] = ()
    restart_step: int | None = None
    parameter_effects: Mapping[str, ActionEffect] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.tool_name, str) or not _TOOL_NAME_RE.fullmatch(self.tool_name):
            raise ActionContractError("tool_name 必须是 tools_ 前缀的小写工具名")
        _require_identifier(self.layer, "layer")
        try:
            effect = ActionEffect(self.effect)
        except ValueError as exc:
            raise ActionContractError("effect 必须是已登记的工具效果等级") from exc
        object.__setattr__(self, "effect", effect)
        if not isinstance(self.enabled, bool):
            raise ActionContractError("enabled 必须是布尔值")
        stages = tuple(self.invalidates_stages)
        if any(not isinstance(stage, str) or not stage for stage in stages):
            raise ActionContractError("invalidates_stages 必须由非空步骤名组成")
        if len(set(stages)) != len(stages):
            raise ActionContractError("invalidates_stages 不可重复")
        object.__setattr__(self, "invalidates_stages", stages)
        if self.restart_step is not None and (
            isinstance(self.restart_step, bool) or not isinstance(self.restart_step, int) or self.restart_step < 1
        ):
            raise ActionContractError("restart_step 必须是正整数或 null")
        if effect.is_read_only and (stages or self.restart_step is not None):
            raise ActionContractError("read_only 工具不能声明失效阶段或重跑步骤")
        if not isinstance(self.parameter_effects, Mapping):
            raise ActionContractError("parameter_effects 必须是对象")
        parameter_effects: dict[str, ActionEffect] = {}
        for parameter, parameter_effect in self.parameter_effects.items():
            _require_text(str(parameter), "parameter_effects 字段名", max_length=128)
            try:
                resolved_effect = ActionEffect(parameter_effect)
            except ValueError as exc:
                raise ActionContractError("parameter_effects 包含无效 effect") from exc
            if _EFFECT_RANK[resolved_effect] < _EFFECT_RANK[effect]:
                raise ActionContractError("parameter_effects 不能降低工具的基础 effect")
            parameter_effects[str(parameter)] = resolved_effect
        object.__setattr__(self, "parameter_effects", MappingProxyType(parameter_effects))

    @property
    def requires_confirmation(self) -> bool:
        return self.effect.requires_confirmation

    @property
    def requires_fork(self) -> bool:
        return self.effect.requires_fork

    @property
    def is_read_only(self) -> bool:
        return self.effect.is_read_only

    def effect_for(self, arguments: Mapping[str, object]) -> ActionEffect:
        """Return the strongest effect activated by this specific call."""
        if not isinstance(arguments, Mapping):
            raise ActionContractError("工具参数必须是对象")
        effect = self.effect
        for parameter, parameter_effect in self.parameter_effects.items():
            if arguments.get(parameter) is not None and _EFFECT_RANK[parameter_effect] > _EFFECT_RANK[effect]:
                effect = parameter_effect
        return effect

    def to_dict(self) -> dict[str, object]:
        return {
            "tool_name": self.tool_name,
            "layer": self.layer,
            "effect": self.effect.value,
            "enabled": self.enabled,
            "invalidates_stages": list(self.invalidates_stages),
            "restart_step": self.restart_step,
            "parameter_effects": {
                key: value.value for key, value in self.parameter_effects.items()
            },
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ToolDeclaration":
        if not isinstance(payload, Mapping):
            raise ActionContractError("tool_declaration 必须是对象")
        stages = payload.get("invalidates_stages", ())
        if not isinstance(stages, Sequence) or isinstance(stages, (str, bytes)):
            raise ActionContractError("invalidates_stages 必须是数组")
        return cls(
            tool_name=payload.get("tool_name", ""),
            layer=payload.get("layer", ""),
            effect=payload.get("effect", ""),
            enabled=payload.get("enabled", True),
            invalidates_stages=tuple(stages),
            restart_step=payload.get("restart_step"),
            parameter_effects=payload.get("parameter_effects", {}),
        )


@dataclass(frozen=True)
class ActionProposal:
    """An LLM or user-provided candidate action before policy validation."""

    run_id: str
    layer: str
    tool_name: str
    arguments: Mapping[str, object]
    failed_step: int | None = None
    parameter_changes: tuple[ParameterChange, ...] = ()
    config_fingerprint: str = ""
    model_id: str = ""
    prompt_version: str = ""
    decision_id: str = field(default_factory=_new_decision_id)
    created_at: str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        _require_identifier(self.run_id, "run_id")
        _require_identifier(self.layer, "layer")
        if not isinstance(self.tool_name, str) or not _TOOL_NAME_RE.fullmatch(self.tool_name):
            raise ActionContractError("tool_name 必须是 tools_ 前缀的小写工具名")
        object.__setattr__(self, "arguments", _require_json_mapping(self.arguments, "arguments"))
        if self.failed_step is not None and (
            isinstance(self.failed_step, bool) or not isinstance(self.failed_step, int) or self.failed_step < 1
        ):
            raise ActionContractError("failed_step 必须是正整数或 null")
        changes = tuple(self.parameter_changes)
        if any(not isinstance(change, ParameterChange) for change in changes):
            raise ActionContractError("parameter_changes 必须由 ParameterChange 组成")
        object.__setattr__(self, "parameter_changes", changes)
        if self.config_fingerprint and not re.fullmatch(r"[a-f0-9]{64}", self.config_fingerprint):
            raise ActionContractError("config_fingerprint 必须是 SHA-256 十六进制摘要")
        if self.model_id:
            _require_text(self.model_id, "model_id", max_length=128)
        if self.prompt_version:
            _require_text(self.prompt_version, "prompt_version", max_length=128)
        _require_identifier(self.decision_id, "decision_id")
        _require_text(self.created_at, "created_at", max_length=80)

    @property
    def action_id(self) -> str:
        """Compatibility alias for code that still calls a decision an action ID."""
        return self.decision_id

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": ACTION_CONTRACT_VERSION,
            "decision_id": self.decision_id,
            "run_id": self.run_id,
            "layer": self.layer,
            "tool_name": self.tool_name,
            "arguments": _thaw_json(self.arguments),
            "failed_step": self.failed_step,
            "parameter_changes": [change.to_dict() for change in self.parameter_changes],
            "config_fingerprint": self.config_fingerprint,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ActionProposal":
        if not isinstance(payload, Mapping):
            raise ActionContractError("action_proposal 必须是对象")
        if payload.get("contract_version") != ACTION_CONTRACT_VERSION:
            raise ActionContractError("不支持的 action contract 版本")
        raw_changes = payload.get("parameter_changes", ())
        if not isinstance(raw_changes, Sequence) or isinstance(raw_changes, (str, bytes)):
            raise ActionContractError("parameter_changes 必须是数组")
        arguments = payload.get("arguments", {})
        if not isinstance(arguments, Mapping):
            raise ActionContractError("arguments 必须是对象")
        return cls(
            decision_id=payload.get("decision_id", ""),
            run_id=payload.get("run_id", ""),
            layer=payload.get("layer", ""),
            tool_name=payload.get("tool_name", ""),
            arguments=arguments,
            failed_step=payload.get("failed_step"),
            parameter_changes=tuple(ParameterChange.from_dict(item) for item in raw_changes),
            config_fingerprint=payload.get("config_fingerprint", ""),
            model_id=payload.get("model_id", ""),
            prompt_version=payload.get("prompt_version", ""),
            created_at=payload.get("created_at", ""),
        )


@dataclass(frozen=True)
class ValidatedAction:
    """A proposal accepted by a declared tool contract and a future policy engine."""

    proposal: ActionProposal
    declaration: ToolDeclaration
    policy_id: str
    validated_at: str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, ActionProposal):
            raise ActionContractError("proposal 必须是 ActionProposal")
        if not isinstance(self.declaration, ToolDeclaration):
            raise ActionContractError("declaration 必须是 ToolDeclaration")
        if not self.declaration.enabled:
            raise ActionContractError(f"工具已禁用: {self.declaration.tool_name}")
        if self.proposal.tool_name != self.declaration.tool_name:
            raise ActionContractError("proposal 与 declaration 的工具名不一致")
        if self.proposal.layer != self.declaration.layer:
            raise ActionContractError("proposal 与 declaration 的层级不一致")
        _require_text(self.policy_id, "policy_id", max_length=128)
        _require_text(self.validated_at, "validated_at", max_length=80)

    @property
    def decision_id(self) -> str:
        return self.proposal.decision_id

    @property
    def requires_confirmation(self) -> bool:
        return self.declaration.effect_for(self.proposal.arguments).requires_confirmation

    @property
    def requires_fork(self) -> bool:
        return self.declaration.effect_for(self.proposal.arguments).requires_fork

    @property
    def effective_effect(self) -> ActionEffect:
        return self.declaration.effect_for(self.proposal.arguments)

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": ACTION_CONTRACT_VERSION,
            "proposal": self.proposal.to_dict(),
            "declaration": self.declaration.to_dict(),
            "policy_id": self.policy_id,
            "validated_at": self.validated_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ValidatedAction":
        if not isinstance(payload, Mapping):
            raise ActionContractError("validated_action 必须是对象")
        if payload.get("contract_version") != ACTION_CONTRACT_VERSION:
            raise ActionContractError("不支持的 action contract 版本")
        proposal = payload.get("proposal")
        declaration = payload.get("declaration")
        if not isinstance(proposal, Mapping) or not isinstance(declaration, Mapping):
            raise ActionContractError("validated_action 缺少 proposal 或 declaration")
        return cls(
            proposal=ActionProposal.from_dict(proposal),
            declaration=ToolDeclaration.from_dict(declaration),
            policy_id=payload.get("policy_id", ""),
            validated_at=payload.get("validated_at", ""),
        )


@dataclass(frozen=True)
class ExecutedAction:
    """The concise, non-raw result of executing a validated action."""

    action: ValidatedAction
    success: bool
    result_summary: str = ""
    output_keys: tuple[str, ...] = ()
    error_kind: str = ""
    executed_at: str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.action, ValidatedAction):
            raise ActionContractError("action 必须是 ValidatedAction")
        if not isinstance(self.success, bool):
            raise ActionContractError("success 必须是布尔值")
        if self.result_summary:
            _require_text(self.result_summary, "result_summary", max_length=240)
        output_keys = tuple(self.output_keys)
        if any(not isinstance(key, str) or not _IDENTIFIER_RE.fullmatch(key) for key in output_keys):
            raise ActionContractError("output_keys 必须是安全产物键名")
        if len(set(output_keys)) != len(output_keys):
            raise ActionContractError("output_keys 不可重复")
        object.__setattr__(self, "output_keys", output_keys)
        if self.error_kind:
            _require_identifier(self.error_kind, "error_kind")
        if self.success and self.error_kind:
            raise ActionContractError("成功动作不能携带 error_kind")
        _require_text(self.executed_at, "executed_at", max_length=80)

    @property
    def decision_id(self) -> str:
        return self.action.decision_id

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": ACTION_CONTRACT_VERSION,
            "action": self.action.to_dict(),
            "success": self.success,
            "result_summary": self.result_summary,
            "output_keys": list(self.output_keys),
            "error_kind": self.error_kind,
            "executed_at": self.executed_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ExecutedAction":
        if not isinstance(payload, Mapping):
            raise ActionContractError("executed_action 必须是对象")
        if payload.get("contract_version") != ACTION_CONTRACT_VERSION:
            raise ActionContractError("不支持的 action contract 版本")
        action = payload.get("action")
        output_keys = payload.get("output_keys", ())
        if not isinstance(action, Mapping) or not isinstance(output_keys, Sequence) or isinstance(output_keys, (str, bytes)):
            raise ActionContractError("executed_action 格式无效")
        return cls(
            action=ValidatedAction.from_dict(action),
            success=payload.get("success"),
            result_summary=payload.get("result_summary", ""),
            output_keys=tuple(output_keys),
            error_kind=payload.get("error_kind", ""),
            executed_at=payload.get("executed_at", ""),
        )


class ActionToolCatalog:
    """Immutable lookup table for all declared LLM-visible tools."""

    def __init__(self, declarations: Iterable[ToolDeclaration]):
        by_name: dict[str, ToolDeclaration] = {}
        for declaration in declarations:
            if not isinstance(declaration, ToolDeclaration):
                raise ActionContractError("catalog 只能包含 ToolDeclaration")
            if declaration.tool_name in by_name:
                raise ActionContractError(f"工具声明重复: {declaration.tool_name}")
            by_name[declaration.tool_name] = declaration
        self._by_name = MappingProxyType(by_name)

    def get(self, tool_name: str) -> ToolDeclaration | None:
        return self._by_name.get(tool_name)

    def require(self, tool_name: str) -> ToolDeclaration:
        declaration = self.get(tool_name)
        if declaration is None:
            raise ActionContractError(f"未登记工具: {tool_name}")
        return declaration

    def declarations(self) -> tuple[ToolDeclaration, ...]:
        return tuple(self._by_name.values())

    def validate(self, proposal: ActionProposal, *, policy_id: str) -> ValidatedAction:
        return ValidatedAction(proposal=proposal, declaration=self.require(proposal.tool_name), policy_id=policy_id)


def declarations_from_tool_metadata(
    tools: Sequence[Mapping[str, object]],
    metadata: Mapping[str, Mapping[str, object]],
    *,
    default_layer: str,
) -> tuple[ToolDeclaration, ...]:
    """Build declarations only when tool schemas and metadata cover each other exactly."""
    schema_names: list[str] = []
    for tool in tools:
        function = tool.get("function") if isinstance(tool, Mapping) else None
        name = function.get("name") if isinstance(function, Mapping) else None
        if not isinstance(name, str):
            raise ActionContractError("工具 schema 缺少 function.name")
        schema_names.append(name)
    if len(set(schema_names)) != len(schema_names):
        raise ActionContractError("工具 schema 存在重复名称")
    if set(schema_names) != set(metadata):
        missing = sorted(set(schema_names) - set(metadata))
        unexpected = sorted(set(metadata) - set(schema_names))
        raise ActionContractError(f"工具元数据覆盖不完整: missing={missing}, unexpected={unexpected}")
    declarations: list[ToolDeclaration] = []
    for name in schema_names:
        meta = metadata[name]
        effect = meta.get("effect")
        if not isinstance(effect, str):
            raise ActionContractError(f"工具缺少 effect 声明: {name}")
        layer = meta.get("layer", default_layer)
        if not isinstance(layer, str):
            raise ActionContractError(f"工具 layer 声明无效: {name}")
        invalidates_stages = meta.get("invalidates_stages", ())
        if not isinstance(invalidates_stages, Sequence) or isinstance(invalidates_stages, (str, bytes)):
            raise ActionContractError(f"工具 invalidates_stages 声明无效: {name}")
        parameter_effects = meta.get("parameter_effects", {})
        if not isinstance(parameter_effects, Mapping):
            raise ActionContractError(f"工具 parameter_effects 声明无效: {name}")
        declarations.append(
            ToolDeclaration(
                tool_name=name,
                layer=layer,
                effect=effect,
                enabled=meta.get("enabled", True),
                invalidates_stages=tuple(invalidates_stages),
                restart_step=meta.get("restart_step"),
                parameter_effects=parameter_effects,
            )
        )
    return tuple(declarations)


def build_default_tool_catalog() -> ActionToolCatalog:
    """Load the project tool declarations lazily, without changing dispatch imports."""
    from willy.toolist_global import TOOL_META as CONFIG_META, TOOLS as CONFIG_TOOLS
    from willy.toolist_quantum import QUANTUM_TOOLS, TOOL_META as QUANTUM_META
    from willy.toolist_run import RUN_TOOLS, TOOL_META as RUN_META
    from willy.toolist_simulation import SIMULATION_TOOLS, TOOL_META as SIMULATION_META
    from willy.toolist_topology import TOOL_META as TOPOLOGY_META, TOPOLOGY_TOOLS

    declarations: list[ToolDeclaration] = []
    for tools, metadata, layer in (
        (CONFIG_TOOLS, CONFIG_META, "config"),
        (QUANTUM_TOOLS, QUANTUM_META, "quantum"),
        (TOPOLOGY_TOOLS, TOPOLOGY_META, "topology"),
        (SIMULATION_TOOLS, SIMULATION_META, "simulation"),
        (RUN_TOOLS, RUN_META, "run"),
    ):
        declarations.extend(declarations_from_tool_metadata(tools, metadata, default_layer=layer))
    return ActionToolCatalog(declarations)
