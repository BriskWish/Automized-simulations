"""声明式恢复策略。

模型只负责提出候选工具；本模块根据错误、层、步骤和工具影响等级裁决
是否可以在当前 run 中执行。策略是无副作用的，便于单元测试和审计。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from willy.action_contract import ActionEffect, ActionToolCatalog, ToolDeclaration
from willy.errors import ErrorKind
from willy.step_registry import EQ_STEP, PROD_STEP, STEP_REGISTRY


@dataclass(frozen=True)
class RecoveryPolicyRule:
    policy_id: str
    layer: str
    error_kinds: tuple[str, ...] = ()
    steps: tuple[int, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    max_attempts: int = 3
    requires_confirmation: bool = False
    restart_step: int | None = None
    fork_only: bool = False

    def matches(self, *, layer: str, error_kind: str, step: int) -> bool:
        if self.layer not in {"*", layer}:
            return False
        if self.error_kinds and error_kind not in self.error_kinds and "*" not in self.error_kinds:
            return False
        return not self.steps or step in self.steps


@dataclass(frozen=True)
class RecoveryDecision:
    allowed: bool
    policy_id: str
    reason: str = ""
    requires_confirmation: bool = False
    fork_only: bool = False
    restart_step: int | None = None
    max_attempts: int = 0


class RecoveryPolicy:
    """First-match policy table with deterministic specificity ordering."""

    def __init__(self, rules: Iterable[RecoveryPolicyRule], *, default_max_attempts: int = 3):
        self.rules = tuple(rules)
        self.default_max_attempts = max(1, int(default_max_attempts))

    def rule_for(self, *, layer: str, error_kind: str | ErrorKind, step: int) -> RecoveryPolicyRule | None:
        kind = error_kind.value if isinstance(error_kind, ErrorKind) else str(error_kind)
        matches = [rule for rule in self.rules if rule.matches(layer=layer, error_kind=kind, step=step)]
        if not matches:
            return None
        # A rule with an exact layer/error/step wins over wildcard defaults.
        return max(matches, key=lambda rule: (
            rule.layer != "*", bool(rule.error_kinds) and "*" not in rule.error_kinds,
            bool(rule.steps),
        ))

    def authorize(
        self,
        *,
        layer: str,
        error_kind: str | ErrorKind,
        step: int,
        tool: ToolDeclaration,
        arguments: Mapping[str, object],
        attempt: int,
    ) -> RecoveryDecision:
        rule = self.rule_for(layer=layer, error_kind=error_kind, step=step)
        if rule is None:
            return RecoveryDecision(False, "no_matching_policy", "没有匹配的恢复策略")
        if attempt >= rule.max_attempts:
            return RecoveryDecision(False, rule.policy_id, "已达到该错误的恢复次数上限", max_attempts=rule.max_attempts)
        if rule.allowed_tools and tool.tool_name not in rule.allowed_tools:
            return RecoveryDecision(False, rule.policy_id, "工具不在当前恢复策略白名单内", max_attempts=rule.max_attempts)
        effect = tool.effect_for(arguments)
        if effect is ActionEffect.REQUIRES_FORK or rule.fork_only:
            return RecoveryDecision(False, rule.policy_id, "该动作只能在派生 run 中执行", fork_only=True, restart_step=rule.restart_step, max_attempts=rule.max_attempts)
        needs_confirmation = rule.requires_confirmation or effect.requires_confirmation
        if needs_confirmation:
            return RecoveryDecision(False, rule.policy_id, "该动作需要用户确认后才能执行", requires_confirmation=True, restart_step=rule.restart_step, max_attempts=rule.max_attempts)
        return RecoveryDecision(True, rule.policy_id, restart_step=rule.restart_step, max_attempts=rule.max_attempts)


def default_recovery_policy(catalog: ActionToolCatalog) -> RecoveryPolicy:
    """Build the shipped conservative policy from declared tool metadata."""
    by_layer: dict[str, list[str]] = {}
    for declaration in catalog.declarations():
        if declaration.enabled:
            by_layer.setdefault(declaration.layer, []).append(declaration.tool_name)

    rules: list[RecoveryPolicyRule] = []
    for layer, tools in by_layer.items():
        rules.append(RecoveryPolicyRule(
            policy_id=f"{layer}.default",
            layer=layer,
            error_kinds=("*",),
            allowed_tools=tuple(sorted(tools)),
            max_attempts=5 if layer == "quantum" else 4 if layer == "topology" else 3,
        ))

    # A stage cannot jump to a later stage while repairing an upstream failure.
    rules.extend([
        RecoveryPolicyRule(
            policy_id="simulation.eq", layer="simulation",
            error_kinds=(ErrorKind.EQ_NOT_CONVERGED.value, ErrorKind.EQUILIBRATION_FAILED.value,
                         ErrorKind.ENGINE_FAILURE.value, ErrorKind.NUMERICAL_INSTABILITY.value),
            steps=(EQ_STEP,), allowed_tools=("tools_diagnose_error_simulation", "tools_retry_eq", "tools_retry_em"),
            max_attempts=3, requires_confirmation=False, restart_step=EQ_STEP,
        ),
        RecoveryPolicyRule(
            policy_id="simulation.prod", layer="simulation",
            error_kinds=(ErrorKind.MDRUN_FAILED.value, ErrorKind.RECOVERY_CONFLICT.value,
                         ErrorKind.ENGINE_FAILURE.value),
            steps=(PROD_STEP,), allowed_tools=("tools_diagnose_error_simulation", "tools_retry_prod"),
            max_attempts=3, requires_confirmation=False, restart_step=PROD_STEP,
        ),
        RecoveryPolicyRule(
            policy_id="topology.fork", layer="topology",
            error_kinds=(ErrorKind.ATOMTYPE_CONFLICT.value,),
            steps=(STEP_REGISTRY.by_id("topology_parameterize").index, STEP_REGISTRY.by_id("topology_assemble").index),
            allowed_tools=("tools_diagnose_error_topology", "tools_modify_config_topology"),
            max_attempts=1, fork_only=True,
        ),
    ])
    return RecoveryPolicy(rules)
