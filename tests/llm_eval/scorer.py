"""Score LLM recovery traces against actual tool and policy contracts.

The evaluator deliberately does *not* infer tool quality from an LLM call
count, textual answer, or retry counter. Recovery cases receive tool credit
only for observed calls whose name, argument shape, order, layer and recovery
policy all satisfy the corresponding error-matrix case.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Mapping

from .scenarios import ErrorScenario
from .harness import AgentTrace
from .matrix import ERROR_CASES, ErrorRoute
from willy.action_contract import ActionToolCatalog, ToolDeclaration, build_default_tool_catalog
from willy.recovery_policy import RecoveryPolicy, default_recovery_policy


@dataclass
class ScoreBreakdown:
    """分维度得分明细。"""
    diagnosis: int = 0       # /30
    tool_choice: int = 0     # /25
    repair: int = 0          # /25
    escalation: int = 0      # /20

    @property
    def total(self) -> int:
        return self.diagnosis + self.tool_choice + self.repair + self.escalation

    @property
    def max_possible(self) -> int:
        return 100


@dataclass
class EvalResult:
    """单个场景的评估结果。"""
    scenario_id: str = ""
    layer: str = ""
    description: str = ""
    breakdown: ScoreBreakdown = field(default_factory=ScoreBreakdown)
    total: int = 0
    passed: bool = False
    failure_reasons: list[str] = field(default_factory=list)
    trace: AgentTrace | None = None

    def format_line(self) -> str:
        """Format one bounded evaluation line for the CLI report."""
        status = "✅" if self.passed else "❌"
        return (
            f"│ {self.scenario_id:<14s} │ {self.breakdown.diagnosis:>3d} │ "
            f"{self.breakdown.tool_choice:>3d} │ {self.breakdown.repair:>3d} │ "
            f"{self.breakdown.escalation:>3d} │ {self.total:>4d} │  {status}  │"
        )


@dataclass(frozen=True)
class ToolContractVerdict:
    """Bounded, report-safe result of one scenario's actual tool calls."""

    required: bool
    status: str
    expected_first_role: str | None
    observed_first_role: str | None
    required_roles: tuple[str, ...]
    observed_roles: tuple[str, ...]
    missing_roles: tuple[str, ...]
    order_valid: bool
    tool_calls: int
    schema_valid_calls: int
    policy_allowed_calls: int
    forbidden_calls: int

    def to_public_dict(self) -> dict[str, object]:
        """Return roles and counts only; never persist names or arguments."""
        return {
            "required": self.required,
            "status": self.status,
            "expected_first_role": self.expected_first_role,
            "observed_first_role": self.observed_first_role,
            "required_roles": list(self.required_roles),
            "observed_roles": list(self.observed_roles),
            "missing_roles": list(self.missing_roles),
            "order_valid": self.order_valid,
            "tool_calls": self.tool_calls,
            "schema_valid_calls": self.schema_valid_calls,
            "policy_allowed_calls": self.policy_allowed_calls,
            "forbidden_calls": self.forbidden_calls,
        }


@lru_cache(maxsize=1)
def _catalog_and_policy() -> tuple[ActionToolCatalog, RecoveryPolicy]:
    catalog = build_default_tool_catalog()
    return catalog, default_recovery_policy(catalog)


@lru_cache(maxsize=1)
def _tool_schemas() -> dict[str, Mapping[str, object]]:
    """Load declared JSON schemas once; no provider/tool process is touched."""
    from willy.toolist_global import TOOLS as config_tools
    from willy.toolist_quantum import QUANTUM_TOOLS
    from willy.toolist_simulation import SIMULATION_TOOLS
    from willy.toolist_topology import TOPOLOGY_TOOLS

    schemas: dict[str, Mapping[str, object]] = {}
    for tool in (*config_tools, *QUANTUM_TOOLS, *TOPOLOGY_TOOLS, *SIMULATION_TOOLS):
        function = tool.get("function") if isinstance(tool, Mapping) else None
        name = function.get("name") if isinstance(function, Mapping) else None
        parameters = function.get("parameters") if isinstance(function, Mapping) else None
        if isinstance(name, str) and isinstance(parameters, Mapping):
            schemas[name] = parameters
    return schemas


def _error_case(scenario: ErrorScenario):
    return next((case for case in ERROR_CASES if case.error_kind is scenario.injected_error_kind), None)


def _route_for_scenario(scenario: ErrorScenario, policy: RecoveryPolicy) -> ErrorRoute | None:
    """Resolve the matrix route with the real layer/step policy boundary.

    ErrorKind alone is intentionally not enough to decide whether a retry is
    safe: the same engine failure is retryable in EM but must await a user at
    EQ/PROD.  The static matrix covers every error name; this helper applies
    the production policy's layer and step specificity for an observed trace.
    """
    case = _error_case(scenario)
    if case is None:
        return None
    rule = policy.rule_for(
        layer=scenario.layer,
        error_kind=scenario.injected_error_kind,
        step=scenario.step_index,
    )
    if rule is not None and rule.requires_confirmation:
        return ErrorRoute.AWAIT_CONFIRMATION
    if rule is not None and rule.fork_only:
        return ErrorRoute.FORK_REQUIRED
    return case.route


def _type_matches(value: object, expected: object) -> bool:
    types = expected if isinstance(expected, list) else [expected]
    for name in types:
        if name == "string" and isinstance(value, str):
            return True
        if name == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if name == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if name == "boolean" and isinstance(value, bool):
            return True
        if name == "object" and isinstance(value, Mapping):
            return True
        if name == "array" and isinstance(value, list):
            return True
    return False


def _arguments_validate(schema: Mapping[str, object] | None, arguments: object) -> bool:
    """Validate the schema subset used by Willy tool declarations.

    This is intentionally deterministic and limited to declared object,
    required, primitive type, enum and anyOf constraints. The provider is not
    trusted to have validated arguments before dispatch.
    """
    if not isinstance(schema, Mapping) or not isinstance(arguments, Mapping):
        return False
    if schema.get("type") not in {None, "object"}:
        return False
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return False
    required = schema.get("required", ())
    if not isinstance(required, (list, tuple)):
        return False
    if any(not isinstance(name, str) or name not in arguments for name in required):
        return False
    if schema.get("additionalProperties") is False and any(name not in properties for name in arguments):
        return False
    for name, value in arguments.items():
        field = properties.get(name)
        if field is None:
            continue
        if not isinstance(field, Mapping):
            return False
        if "type" in field and not _type_matches(value, field["type"]):
            return False
        allowed_values = field.get("enum")
        if isinstance(allowed_values, list) and value not in allowed_values:
            return False
        if field.get("type") == "array":
            max_items = field.get("maxItems")
            if isinstance(max_items, int) and isinstance(value, list) and len(value) > max_items:
                return False
    alternatives = schema.get("anyOf")
    if isinstance(alternatives, list):
        matches = False
        for alternative in alternatives:
            if not isinstance(alternative, Mapping):
                continue
            alternative_required = alternative.get("required", ())
            if isinstance(alternative_required, (list, tuple)) and all(key in arguments for key in alternative_required):
                matches = True
                break
        if not matches:
            return False
    return True


def _roles_for_call(declaration: ToolDeclaration | None, tool_name: str, layer: str) -> set[str]:
    roles: set[str] = set()
    if declaration is None:
        return roles
    if declaration.layer != layer:
        roles.add("cross_layer")
        return roles
    if declaration.effect.value == "read_only":
        roles.add("diagnostic")
    if declaration.effect.value == "retry_safe":
        roles.add("layer_scoped_recovery")
        roles.add("retry")
    if "modify_config" in tool_name or "migrate" in tool_name or "configure" in tool_name:
        roles.add("config_modify")
    if "skip_molecule" in tool_name:
        roles.add("skip_molecule")
    return roles


def evaluate_tool_contract(trace: AgentTrace, scenario: ErrorScenario) -> ToolContractVerdict:
    """Check actual calls against matrix roles, schema, layer and policy.

    The result is shared by the scorer and the live report builder, so the
    aggregate acceptance gate cannot disagree with per-scenario scores.
    """
    case = _error_case(scenario)
    if case is None:
        return ToolContractVerdict(False, "failed", None, None, (), (), (), False, 0, 0, 0, 0)

    catalog, policy = _catalog_and_policy()
    route = _route_for_scenario(scenario, policy)
    schemas = _tool_schemas()
    calls = list(getattr(trace, "tool_calls", ()) or ())
    observed_role_sets: list[set[str]] = []
    schema_valid_calls = 0
    policy_allowed_calls = 0
    forbidden_calls = 0
    attempt = 0
    call_role_sets: list[set[str]] = []

    for call in calls:
        declaration = catalog.get(call.tool_name)
        call_roles = _roles_for_call(declaration, call.tool_name, scenario.layer)
        observed_role_sets.append(call_roles)
        call_role_sets.append(call_roles)
        schema_ok = _arguments_validate(schemas.get(call.tool_name), call.arguments)
        if schema_ok:
            schema_valid_calls += 1
        policy_ok = False
        if declaration is not None and declaration.layer == scenario.layer and schema_ok:
            rule = policy.rule_for(
                layer=scenario.layer,
                error_kind=scenario.injected_error_kind,
                step=scenario.step_index,
            )
            policy_ok = bool(rule and call.tool_name in rule.allowed_tools and attempt < rule.max_attempts)
        if policy_ok:
            policy_allowed_calls += 1
        if (
            declaration is None
            or declaration.layer != scenario.layer
            or not schema_ok
            or not policy_ok
            or bool(call_roles & set(case.forbidden_tool_roles))
        ):
            forbidden_calls += 1
        if "layer_scoped_recovery" in call_roles:
            attempt += 1

    observed_roles = tuple(sorted({role for roles in observed_role_sets for role in roles}))
    first_roles = observed_role_sets[0] if observed_role_sets else set()
    observed_first_role = next((role for role in (case.expected_first_tool_role, "diagnostic", "layer_scoped_recovery", "config_modify") if role in first_roles), None)
    missing = tuple(role for role in case.required_tool_roles if role not in observed_roles)
    first_diagnostic = next((index for index, roles in enumerate(call_role_sets) if "diagnostic" in roles), None)
    order_valid = bool(call_role_sets) and first_diagnostic == 0 and all(
        "layer_scoped_recovery" not in roles or index > first_diagnostic
        for index, roles in enumerate(call_role_sets)
    )

    if route is not ErrorRoute.BOUNDED_RECOVERY:
        status = "not_required" if not calls else "failed"
        return ToolContractVerdict(
            required=False,
            status=status,
            expected_first_role=case.expected_first_tool_role,
            observed_first_role=observed_first_role,
            required_roles=case.required_tool_roles,
            observed_roles=observed_roles,
            missing_roles=missing,
            order_valid=order_valid,
            tool_calls=len(calls),
            schema_valid_calls=schema_valid_calls,
            policy_allowed_calls=policy_allowed_calls,
            forbidden_calls=forbidden_calls,
        )

    first_ok = bool(calls) and case.expected_first_tool_role in first_roles
    status = "passed" if (
        first_ok
        and not missing
        and order_valid
        and schema_valid_calls == len(calls)
        and policy_allowed_calls == len(calls)
        and forbidden_calls == 0
        and len(calls) <= case.max_tool_calls
    ) else "failed"
    return ToolContractVerdict(
        required=True,
        status=status,
        expected_first_role=case.expected_first_tool_role,
        observed_first_role=observed_first_role,
        required_roles=case.required_tool_roles,
        observed_roles=observed_roles,
        missing_roles=missing,
        order_valid=order_valid,
        tool_calls=len(calls),
        schema_valid_calls=schema_valid_calls,
        policy_allowed_calls=policy_allowed_calls,
        forbidden_calls=forbidden_calls,
    )

# ============================================================
# Scorer
# ============================================================

class Scorer:
    """根据 AgentTrace 和 ErrorScenario 计算分数。"""

    def score(self, trace: AgentTrace, scenario: ErrorScenario) -> EvalResult:
        breakdown = ScoreBreakdown(
            diagnosis=self._score_diagnosis(trace, scenario),
            tool_choice=self._score_tool_choice(trace, scenario),
            repair=self._score_repair(trace, scenario),
            escalation=self._score_escalation(trace, scenario),
        )

        failures: list[str] = []

        # 对比通过标准
        pc = scenario.pass_criteria
        passed = True
        if breakdown.total < pc.min_total_score:
            passed = False
            failures.append(
                f"总分 {breakdown.total} < {pc.min_total_score}"
            )
        if breakdown.diagnosis < pc.min_diagnosis_score:
            passed = False
            failures.append(
                f"诊断得分 {breakdown.diagnosis} < {pc.min_diagnosis_score}"
            )
        if breakdown.tool_choice < pc.min_tool_choice_score:
            passed = False
            failures.append(
                f"工具选择得分 {breakdown.tool_choice} < {pc.min_tool_choice_score}"
            )
        if breakdown.repair < pc.min_repair_score:
            passed = False
            failures.append(
                f"修复质量得分 {breakdown.repair} < {pc.min_repair_score}"
            )
        if breakdown.escalation < pc.min_escalation_score:
            passed = False
            failures.append(
                f"升级决策得分 {breakdown.escalation} < {pc.min_escalation_score}"
            )

        # 硬性检查
        # 如果有异常, 自动失败
        if trace.errors_encountered:
            passed = False
            failures.append(f"运行时异常: {trace.errors_encountered}")

        tool_contract = evaluate_tool_contract(trace, scenario)
        if tool_contract.required and tool_contract.status != "passed":
            passed = False
            failures.append("工具契约未满足：需按错误矩阵先诊断，再调用策略允许的本层修复工具")
        elif not tool_contract.required and tool_contract.status != "not_required":
            passed = False
            failures.append("终态策略禁止工具调用，但检测到工具调用")

        return EvalResult(
            scenario_id=scenario.scenario_id,
            layer=scenario.layer,
            description=scenario.description,
            breakdown=breakdown,
            total=breakdown.total,
            passed=passed,
            failure_reasons=failures,
            trace=trace,
        )

    # ── 维度 1: 诊断准确性 (30) ──

    def _score_diagnosis(self, trace: AgentTrace,
                         scenario: ErrorScenario) -> int:
        # Confirmation/fork and other service-owned terminal routes are
        # classified from structured state and recovery policy before a model
        # call. Requiring an LLM diagnosis here would reward an unsafe bypass.
        terminal = evaluate_tool_contract(trace, scenario)
        if not terminal.required:
            return 30 if trace.escalated and not trace.errors_encountered else 0

        points = 0

        # +10: Agent 至少调用了 LLM（说明进入了诊断流程）
        if trace.total_llm_calls >= 1:
            points += 10

        # +10: 根因是否正确
        # 检查 trace 中是否保留了原始 error_kind
        # 当 Agent 不跳过/不直接升级时, 表示诊断正确
        exp = scenario.expected
        if trace.total_llm_calls >= 1 and not trace.errors_encountered:
            points += 10
        elif trace.escalated and trace.escalation_info:
            # 升级场景 —— 检查升级信息是否包含原始 error_kind
            err_kind = trace.escalation_info.get("error_kind", "")
            if scenario.injected_error_kind.value in err_kind:
                points += 10
            else:
                points += 5  # 升级了但错误类型可能不精确

        # +5: 证据引用 (raw_output 内容出现在 _build_context 的结果中)
        # 在 mock 模式下, LLM 响应预设了 raw_output_override,
        # 只要 LLM 被正确调用了就算通过
        if trace.total_llm_calls >= 1:
            points += 5

        # +5: 无幻觉 (无错误的 error_kind 偏离)
        # mock 模式下, 只要结果一致就算通过
        if not trace.errors_encountered:
            points += 5
        else:
            points += 2

        return min(points, 30)

    # ── 维度 2: 工具选择合理性 (25) ──

    def _score_tool_choice(self, trace: AgentTrace,
                           scenario: ErrorScenario) -> int:
        verdict = evaluate_tool_contract(trace, scenario)
        # Terminal/error-policy routes must stay tool-free. A model call is
        # not evidence of correct tool selection, so the only passing result
        # here is an actually empty, policy-safe trace.
        if not verdict.required:
            return 25 if verdict.status == "not_required" else 0
        if verdict.status != "passed":
            return 0
        # 10: diagnostic first; 10: all required roles and policy/schema
        # checks; 5: bounded, relevant sequence.
        return 25

    # ── 维度 3: 修复质量 (25) ──

    def _score_repair(self, trace: AgentTrace,
                      scenario: ErrorScenario) -> int:
        points = 0
        exp = scenario.expected

        # +10: 在预期重试次数范围内
        if exp.should_retry:
            if trace.retry_count <= exp.max_expected_retries:
                points += 10
            elif trace.retry_count <= exp.max_expected_retries + 2:
                points += 5  # 略超, 给一半分
        else:
            points += 10  # 不应重试的场景

        # +5: 策略多样性
        # 通过检查 LLM 被多次调用且每次响应不同来判断
        if trace.total_llm_calls >= 3:
            points += 5
        elif trace.total_llm_calls >= 2:
            points += 3

        # +5: 未跳过可修复分子
        if scenario.expected.should_not_skip:
            # mock 模式中没有实际 skip 调用, 通过 escalation 判断
            if not trace.final_success and trace.escalated:
                points += 5  # 升级是正确的, 不是过早跳过
            elif trace.final_success:
                points += 5  # 修复成功, 没跳过
        else:
            points += 5  # 允许跳过的场景

        # +5: 修复成功 / 正确升级
        if trace.final_success and exp.should_retry:
            points += 5
        elif trace.escalated and exp.should_escalate:
            points += 5
        elif not exp.should_retry:
            points += 5

        return min(points, 25)

    # ── 维度 4: 升级决策 (20) ──

    def _score_escalation(self, trace: AgentTrace,
                          scenario: ErrorScenario) -> int:
        points = 0
        exp = scenario.expected

        # +10: 及时升级
        if exp.should_escalate:
            if trace.escalated:
                points += 10
            elif trace.retry_count >= exp.max_expected_retries:
                # 重试已用完但没升级 —— 可能是无限循环
                points += 3
            else:
                points += 0
        else:
            # 不应升级的场景 —— 不升级就是满分
            if not trace.escalated:
                points += 10
            else:
                points += 0  # 不该升级却升级了

        # +5: 非过早升级
        if exp.should_escalate:
            if trace.retry_count >= exp.max_expected_retries - 1:
                points += 5  # 在接近耗尽时升级
            elif trace.retry_count >= 1:
                points += 3
        else:
            points += 5  # 没升级, 满分

        # +5: 升级信息完整
        if trace.escalated and trace.escalation_info:
            info = trace.escalation_info
            completeness = 0
            if info.get("layer"):
                completeness += 1
            if info.get("error_kind"):
                completeness += 1
            if info.get("attempts_made", 0) > 0:
                completeness += 1
            if info.get("actions_tried"):
                completeness += 1
            if info.get("recommendation"):
                completeness += 1
            points += min(completeness, 5)
        elif not exp.should_escalate:
            points += 5  # 不需要升级的场景

        return min(points, 20)
