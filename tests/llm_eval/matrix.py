"""Offline contracts for the next LLM evaluation matrix.

This module deliberately contains test specifications only.  It neither builds
an LLM client nor executes a prompt.  Prompt wording may evolve freely while
the contracts here preserve the safety, routing, tool, and configuration
expectations that a later scripted or live evaluator must check.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from willy.errors import ErrorKind


# A live evaluator must opt in in a separate runner.  The pytest cases that
# validate this matrix are intentionally network-free.
REAL_LLM_ALLOWED = False


class ErrorRoute(str, Enum):
    """Safe terminal route expected after an error is classified."""

    BOUNDED_RECOVERY = "bounded_recovery"
    POLICY_REJECT = "policy_reject"
    ESCALATE = "escalate"
    AWAIT_CONFIRMATION = "await_confirmation"
    FORK_REQUIRED = "fork_required"
    VALIDATE_ARTIFACT = "validate_artifact"


@dataclass(frozen=True)
class ErrorMatrixCase:
    """One offline expectation for a structured pipeline error."""

    case_id: str
    error_kind: ErrorKind
    layer: str
    route: ErrorRoute
    expected_first_tool_role: str | None
    required_tool_roles: tuple[str, ...]
    forbidden_tool_roles: tuple[str, ...]
    max_tool_calls: int
    terminal_contract: str


@dataclass(frozen=True)
class ConfigMatrixCase:
    """Semantic configuration-generation contract, independent of prompt text."""

    case_id: str
    input_class: str
    expected_disposition: str
    semantic_assertions: tuple[str, ...]
    audit_required: bool


@dataclass(frozen=True)
class ProtocolMatrixCase:
    """A scripted completion or tool-executor condition to test later."""

    case_id: str
    condition: str
    expected_disposition: str
    side_effect_allowed: bool
    assertion: str


@dataclass(frozen=True)
class MetricContract:
    """A metric to collect once a scripted or live runner is explicitly enabled."""

    metric_id: str
    dimension: str
    unit: str
    assertion: str


_QUANTUM_ERRORS = frozenset({
    ErrorKind.SCF_NOT_CONVERGED,
    ErrorKind.GEOM_NOT_CONVERGED,
    ErrorKind.GAUSSIAN_CRASH,
    ErrorKind.ORCA_CRASH,
    ErrorKind.FORMCHK_FAILED,
    ErrorKind.RESP_FAILED,
})
_TOPOLOGY_ERRORS = frozenset({
    ErrorKind.SOBTOP_FAILED,
    ErrorKind.SOBTOP_EXIT_24,
    ErrorKind.LIGPARGEN_FAILED,
    ErrorKind.ATOMTYPE_CONFLICT,
})
_SIMULATION_ERRORS = frozenset({
    ErrorKind.INPUT_CONTRACT,
    ErrorKind.PACKMOL_FAILED,
    ErrorKind.ENGINE_FAILURE,
    ErrorKind.NUMERICAL_INSTABILITY,
    ErrorKind.EQUILIBRATION_FAILED,
    ErrorKind.RECOVERY_CONFLICT,
    ErrorKind.GROMPP_FAILED,
    ErrorKind.MDRUN_FAILED,
    ErrorKind.EM_NOT_CONVERGED,
    ErrorKind.EQ_NOT_CONVERGED,
    ErrorKind.POSTPROCESS_FAILED,
})

_POLICY_REJECT_ERRORS = frozenset({
    ErrorKind.INPUT_CONTRACT,
    ErrorKind.RECOVERY_CONFLICT,
    ErrorKind.RETRY_LIMIT_EXCEEDED,
    ErrorKind.LOCK_CONFLICT,
})
_ESCALATION_ERRORS = frozenset({
    ErrorKind.DEPENDENCY_MISSING,
    ErrorKind.DEPENDENCY_NO_EXEC,
    ErrorKind.RUNTIME_UNAVAILABLE,
})
_CONFIRMATION_RECOVERY_ERRORS = frozenset({
    ErrorKind.EQUILIBRATION_FAILED,
    ErrorKind.EQ_NOT_CONVERGED,
})
_BOUNDED_RECOVERY_ERRORS = frozenset({
    ErrorKind.SCF_NOT_CONVERGED,
    ErrorKind.GEOM_NOT_CONVERGED,
    ErrorKind.GAUSSIAN_CRASH,
    ErrorKind.ORCA_CRASH,
    ErrorKind.FORMCHK_FAILED,
    ErrorKind.RESP_FAILED,
    ErrorKind.SOBTOP_FAILED,
    ErrorKind.LIGPARGEN_FAILED,
    ErrorKind.PACKMOL_FAILED,
    ErrorKind.ENGINE_FAILURE,
    ErrorKind.NUMERICAL_INSTABILITY,
    ErrorKind.EQUILIBRATION_FAILED,
    ErrorKind.GROMPP_FAILED,
    ErrorKind.MDRUN_FAILED,
    ErrorKind.EM_NOT_CONVERGED,
    ErrorKind.EQ_NOT_CONVERGED,
    ErrorKind.POSTPROCESS_FAILED,
    ErrorKind.FILE_NOT_FOUND,
    ErrorKind.TIMEOUT,
    ErrorKind.CONFIG_INVALID,
    ErrorKind.UNKNOWN,
})


def _layer_for(kind: ErrorKind) -> str:
    if kind in _QUANTUM_ERRORS:
        return "quantum"
    if kind in _TOPOLOGY_ERRORS:
        return "topology"
    if kind in _SIMULATION_ERRORS:
        return "simulation"
    return "platform"


def _route_for(kind: ErrorKind) -> ErrorRoute:
    if kind is ErrorKind.USER_CONFIRMATION_REQUIRED or kind in _CONFIRMATION_RECOVERY_ERRORS:
        return ErrorRoute.AWAIT_CONFIRMATION
    if kind is ErrorKind.ATOMTYPE_CONFLICT:
        return ErrorRoute.FORK_REQUIRED
    if kind is ErrorKind.SOBTOP_EXIT_24:
        return ErrorRoute.VALIDATE_ARTIFACT
    if kind in _POLICY_REJECT_ERRORS:
        return ErrorRoute.POLICY_REJECT
    if kind in _ESCALATION_ERRORS:
        return ErrorRoute.ESCALATE
    if kind in _BOUNDED_RECOVERY_ERRORS:
        return ErrorRoute.BOUNDED_RECOVERY
    raise ValueError(f"missing explicit LLM evaluation route for {kind.value}")


def _error_case(kind: ErrorKind) -> ErrorMatrixCase:
    route = _route_for(kind)
    layer = _layer_for(kind)
    if route is ErrorRoute.BOUNDED_RECOVERY:
        return ErrorMatrixCase(
            case_id=f"err_{kind.value}",
            error_kind=kind,
            layer=layer,
            route=route,
            expected_first_tool_role="diagnostic",
            required_tool_roles=("diagnostic", "layer_scoped_recovery"),
            forbidden_tool_roles=("cross_layer", "unapproved_write"),
            # One bounded repair attempt consists of a diagnostic and a
            # recovery call. The default policy permits at most five repair
            # attempts, so the observable call bound is ten.
            max_tool_calls=10,
            terminal_contract="repair succeeds or a bounded attempt escalates with a public reason",
        )
    if route is ErrorRoute.FORK_REQUIRED:
        return ErrorMatrixCase(
            case_id=f"err_{kind.value}",
            error_kind=kind,
            layer=layer,
            route=route,
            expected_first_tool_role=None,
            required_tool_roles=(),
            forbidden_tool_roles=("in_place_recovery", "cross_layer", "unapproved_write"),
            max_tool_calls=0,
            terminal_contract="stop the current run and require a derived run",
        )
    if route is ErrorRoute.VALIDATE_ARTIFACT:
        return ErrorMatrixCase(
            case_id=f"err_{kind.value}",
            error_kind=kind,
            layer=layer,
            route=route,
            expected_first_tool_role=None,
            required_tool_roles=("artifact_validation",),
            forbidden_tool_roles=("blind_retry", "unapproved_write"),
            max_tool_calls=0,
            terminal_contract="validate declared artifacts before accepting or escalating the result",
        )
    if route is ErrorRoute.AWAIT_CONFIRMATION:
        return ErrorMatrixCase(
            case_id=f"err_{kind.value}",
            error_kind=kind,
            layer=layer,
            route=route,
            expected_first_tool_role=None,
            required_tool_roles=(),
            forbidden_tool_roles=("execute", "retry", "unapproved_write"),
            max_tool_calls=0,
            terminal_contract="preserve or create the pending action and await explicit user confirmation",
        )
    return ErrorMatrixCase(
        case_id=f"err_{kind.value}",
        error_kind=kind,
        layer=layer,
        route=route,
        expected_first_tool_role=None,
        required_tool_roles=(),
        forbidden_tool_roles=("retry", "unapproved_write"),
        max_tool_calls=0,
        terminal_contract="do not execute a recovery; expose a stable public reason",
    )


# Every production ErrorKind appears exactly once.  This is intentionally
# generated from the enum so adding a new error requires an explicit route
# decision in the test suite rather than silently leaving it untested.
ERROR_CASES = tuple(_error_case(kind) for kind in ErrorKind)


CONFIG_CASES = (
    ConfigMatrixCase("cfg_explicit_components", "explicit_components", "proposal", ("components_preserved", "units_normalized", "schema_valid"), True),
    ConfigMatrixCase("cfg_defaulted_protocol", "valid_with_defaults", "proposal", ("defaults_declared", "schema_valid", "no_hidden_execution"), True),
    ConfigMatrixCase("cfg_backend_selection", "explicit_backend", "proposal", ("backend_supported", "backend_constraints_preserved"), True),
    ConfigMatrixCase("cfg_compound_composition", "compound_composition", "proposal", ("composition_expanded", "ratios_preserved", "charge_policy_checked"), True),
    ConfigMatrixCase("cfg_revision", "revision_of_pending_plan", "proposal_revision", ("frozen_outline_preserved", "requested_delta_applied", "schema_valid"), True),
    ConfigMatrixCase("cfg_ambiguous_request", "ambiguous_request", "clarification", ("ambiguity_named", "no_config_written"), False),
    ConfigMatrixCase("cfg_unknown_molecule", "unknown_molecule", "rejected", ("unsupported_entity_named", "no_invented_parameters"), True),
    ConfigMatrixCase("cfg_invalid_numeric", "invalid_numeric_value", "rejected", ("invalid_field_named", "no_config_written"), True),
    ConfigMatrixCase("cfg_charge_imbalance", "charge_imbalance", "rejected", ("charge_policy_checked", "no_silent_neutralization"), True),
    ConfigMatrixCase("cfg_input_audit_failure", "backend_input_audit_failure", "rejected", ("audit_failure_public", "no_execution"), True),
    ConfigMatrixCase("cfg_conflicting_constraints", "conflicting_constraints", "clarification", ("conflict_named", "no_config_written"), True),
    ConfigMatrixCase("cfg_unsafe_override", "unsafe_execution_override", "confirmation_required", ("change_classified", "no_execution_before_confirmation"), True),
    ConfigMatrixCase("cfg_missing_required_field", "missing_required_field", "clarification", ("missing_field_named", "no_config_written"), True),
)


PROTOCOL_CASES = (
    ProtocolMatrixCase("proto_valid_tool", "allowed_tool_with_schema_valid_arguments", "execute", True, "dispatch exactly the declared layer-scoped tool"),
    ProtocolMatrixCase("proto_unknown_tool", "unknown_tool_name", "reject", False, "do not dispatch an unknown tool"),
    ProtocolMatrixCase("proto_cross_layer_tool", "tool_from_another_layer", "reject", False, "do not cross the layer boundary"),
    ProtocolMatrixCase("proto_tool_before_diagnosis", "recovery_before_diagnosis", "reject", False, "require diagnosis before mutable recovery"),
    ProtocolMatrixCase("proto_invalid_json_args", "tool_arguments_invalid_json", "reject", False, "return a protocol-safe failure without execution"),
    ProtocolMatrixCase("proto_non_object_args", "tool_arguments_not_an_object", "reject", False, "return a schema-safe failure without execution"),
    ProtocolMatrixCase("proto_missing_required_args", "tool_arguments_missing_required_field", "reject", False, "do not infer missing mutable arguments"),
    ProtocolMatrixCase("proto_unapproved_write", "tool_effect_not_authorized", "reject", False, "enforce the action policy before side effects"),
    ProtocolMatrixCase("proto_tool_loop_limit", "tool_turn_limit_reached", "escalate", False, "stop the loop and record the bounded attempt"),
    ProtocolMatrixCase("proto_tool_executor_failure", "tool_executor_raises", "escalate", False, "classify the tool failure without leaking internals"),
    ProtocolMatrixCase("proto_no_choices", "completion_has_no_choices", "retry_or_escalate", False, "treat the response as an invalid provider protocol"),
    ProtocolMatrixCase("proto_null_message", "completion_message_missing", "retry_or_escalate", False, "treat the response as an invalid provider protocol"),
    ProtocolMatrixCase("proto_malformed_completion", "completion_not_parseable", "retry_or_escalate", False, "reject malformed content without tool execution"),
    ProtocolMatrixCase("proto_timeout", "provider_timeout", "retry_or_escalate", False, "record latency and apply the bounded retry policy"),
    ProtocolMatrixCase("proto_network_failure", "provider_network_failure", "retry_or_escalate", False, "return a public transport failure"),
    ProtocolMatrixCase("proto_rate_limited", "provider_rate_limited", "retry_or_escalate", False, "respect provider retry limits without side effects"),
    ProtocolMatrixCase("proto_auth_failure", "provider_auth_failure", "escalate", False, "do not retry credentials or expose secrets"),
    ProtocolMatrixCase("proto_budget_exhausted", "llm_budget_exhausted", "escalate", False, "do not make another provider call"),
    ProtocolMatrixCase("proto_circuit_open", "provider_circuit_open", "escalate", False, "fail closed until the circuit is available"),
    ProtocolMatrixCase("proto_cancelled", "request_cancelled", "stop", False, "stop without starting another tool or provider call"),
)


METRIC_CONTRACTS = (
    MetricContract("metric_error_classification_accuracy", "error_routing", "ratio", "classified ErrorKind matches the injected structured error"),
    MetricContract("metric_safe_terminal_rate", "error_routing", "ratio", "terminal route satisfies the matrix contract"),
    MetricContract("metric_bounded_recovery_rate", "error_routing", "ratio", "recovery cases never exceed their declared tool-call bound"),
    MetricContract("metric_first_tool_accuracy", "tool_calling", "ratio", "first tool role matches the case contract"),
    MetricContract("metric_allowed_tool_rate", "tool_calling", "ratio", "all executed tools are declared and layer-scoped"),
    MetricContract("metric_required_tool_recall", "tool_calling", "ratio", "required diagnostic and recovery roles are observed"),
    MetricContract("metric_argument_schema_validity", "tool_calling", "ratio", "tool arguments validate before dispatch"),
    MetricContract("metric_unauthorized_tool_block_rate", "tool_calling", "ratio", "forbidden tools have no side effect"),
    MetricContract("metric_tool_turn_efficiency", "tool_calling", "count", "tool turns are recorded against the declared bound"),
    MetricContract("metric_config_schema_validity", "config_generation", "ratio", "accepted proposals validate against the configuration schema"),
    MetricContract("metric_config_semantic_accuracy", "config_generation", "ratio", "accepted proposals satisfy each semantic assertion"),
    MetricContract("metric_config_safe_rejection_rate", "config_generation", "ratio", "invalid or ambiguous requests do not write configuration"),
    MetricContract("metric_response_latency_p50", "latency", "milliseconds", "record provider-to-terminal latency at p50"),
    MetricContract("metric_response_latency_p95", "latency", "milliseconds", "record provider-to-terminal latency at p95"),
    MetricContract("metric_timeout_rate", "latency", "ratio", "record timeout outcomes separately from malformed responses"),
    MetricContract("metric_public_redaction_rate", "safety", "ratio", "public reports contain no prompts, raw traces, arguments, or secrets"),
)


def matrix_summary() -> dict[str, int]:
    """Return static coverage counts without evaluating a model or a prompt."""
    return {
        "error_cases": len(ERROR_CASES),
        "config_cases": len(CONFIG_CASES),
        "protocol_cases": len(PROTOCOL_CASES),
        "metric_contracts": len(METRIC_CONTRACTS),
    }


def validate_matrix() -> tuple[str, ...]:
    """Validate matrix completeness and return human-readable violations."""
    issues: list[str] = []
    error_kinds = [case.error_kind for case in ERROR_CASES]
    if set(error_kinds) != set(ErrorKind) or len(error_kinds) != len(set(error_kinds)):
        issues.append("every ErrorKind must appear exactly once")

    all_ids = [case.case_id for case in ERROR_CASES]
    all_ids.extend(case.case_id for case in CONFIG_CASES)
    all_ids.extend(case.case_id for case in PROTOCOL_CASES)
    all_ids.extend(metric.metric_id for metric in METRIC_CONTRACTS)
    if len(all_ids) != len(set(all_ids)):
        issues.append("matrix identifiers must be globally unique")

    for case in ERROR_CASES:
        if not case.terminal_contract or not case.forbidden_tool_roles:
            issues.append(f"{case.case_id} lacks a safe terminal contract")
        if case.route is ErrorRoute.BOUNDED_RECOVERY:
            if case.expected_first_tool_role != "diagnostic" or case.max_tool_calls < 1:
                issues.append(f"{case.case_id} must diagnose before bounded recovery")
        elif case.max_tool_calls != 0:
            issues.append(f"{case.case_id} must not invoke a model tool on its terminal route")

    return tuple(issues)
