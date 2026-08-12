"""Validate the offline LLM evaluation matrix without constructing an LLM client."""

from __future__ import annotations

import ast
import inspect

import pytest

from tests.llm_eval import matrix
from willy.errors import ErrorKind


pytestmark = pytest.mark.contract


def test_error_matrix_covers_each_structured_error_once():
    """Every ErrorKind has one explicit safe LLM evaluation route."""
    assert {case.error_kind for case in matrix.ERROR_CASES} == set(ErrorKind)
    assert len(matrix.ERROR_CASES) == len(ErrorKind)
    assert len({case.case_id for case in matrix.ERROR_CASES}) == len(matrix.ERROR_CASES)
    routed_kinds = (
        matrix._BOUNDED_RECOVERY_ERRORS
        | matrix._POLICY_REJECT_ERRORS
        | matrix._ESCALATION_ERRORS
        | {
            ErrorKind.USER_CONFIRMATION_REQUIRED,
            ErrorKind.ATOMTYPE_CONFLICT,
            ErrorKind.SOBTOP_EXIT_24,
        }
    )
    assert routed_kinds == set(ErrorKind)


def test_retryable_errors_require_diagnosis_and_bounded_recovery():
    """Retryable failures require a diagnosis unless policy owns confirmation."""
    cases = {case.error_kind: case for case in matrix.ERROR_CASES}
    for kind in ErrorKind:
        if kind.retryable:
            case = cases[kind]
            if case.route is matrix.ErrorRoute.AWAIT_CONFIRMATION:
                assert kind in matrix._CONFIRMATION_RECOVERY_ERRORS
                assert case.max_tool_calls == 0
            else:
                assert case.route is matrix.ErrorRoute.BOUNDED_RECOVERY
                assert case.expected_first_tool_role == "diagnostic"
                assert "layer_scoped_recovery" in case.required_tool_roles
                assert case.max_tool_calls > 0


def test_terminal_error_routes_cannot_execute_recovery_tools():
    """Policy rejection, confirmation, fork, and escalation paths fail closed."""
    terminal_routes = {
        matrix.ErrorRoute.POLICY_REJECT,
        matrix.ErrorRoute.ESCALATE,
        matrix.ErrorRoute.AWAIT_CONFIRMATION,
        matrix.ErrorRoute.FORK_REQUIRED,
        matrix.ErrorRoute.VALIDATE_ARTIFACT,
    }
    for case in matrix.ERROR_CASES:
        assert case.terminal_contract
        assert case.forbidden_tool_roles
        if case.route in terminal_routes:
            assert case.max_tool_calls == 0
            assert case.expected_first_tool_role is None


def test_config_matrix_covers_valid_ambiguous_and_rejected_requests():
    """Configuration evaluation includes generation quality and safe refusal paths."""
    dispositions = {case.expected_disposition for case in matrix.CONFIG_CASES}
    input_classes = {case.input_class for case in matrix.CONFIG_CASES}

    assert {"proposal", "proposal_revision", "clarification", "rejected", "confirmation_required"} <= dispositions
    assert {
        "explicit_components",
        "ambiguous_request",
        "invalid_numeric_value",
        "charge_imbalance",
        "backend_input_audit_failure",
        "unsafe_execution_override",
    } <= input_classes
    assert all(case.semantic_assertions for case in matrix.CONFIG_CASES)


def test_protocol_matrix_covers_tool_and_provider_failures_without_side_effects():
    """All malformed, unauthorized, and unavailable fake responses have a safe path."""
    conditions = {case.condition for case in matrix.PROTOCOL_CASES}
    expected = {
        "unknown_tool_name",
        "tool_from_another_layer",
        "tool_arguments_invalid_json",
        "tool_arguments_not_an_object",
        "tool_arguments_missing_required_field",
        "tool_effect_not_authorized",
        "tool_turn_limit_reached",
        "completion_has_no_choices",
        "provider_timeout",
        "provider_network_failure",
        "provider_rate_limited",
        "provider_auth_failure",
        "llm_budget_exhausted",
        "provider_circuit_open",
        "request_cancelled",
    }
    assert expected <= conditions
    assert sum(case.side_effect_allowed for case in matrix.PROTOCOL_CASES) == 1
    assert matrix.PROTOCOL_CASES[0].condition == "allowed_tool_with_schema_valid_arguments"


def test_metric_contracts_cover_tool_accuracy_latency_and_configuration_accuracy():
    """Future reports have explicit multi-dimensional acceptance metrics."""
    dimensions = {metric.dimension for metric in matrix.METRIC_CONTRACTS}
    metric_ids = {metric.metric_id for metric in matrix.METRIC_CONTRACTS}

    assert {"error_routing", "tool_calling", "config_generation", "latency", "safety"} <= dimensions
    assert {
        "metric_first_tool_accuracy",
        "metric_argument_schema_validity",
        "metric_config_semantic_accuracy",
        "metric_response_latency_p95",
    } <= metric_ids


def test_matrix_is_complete_and_has_no_runtime_client_dependency():
    """The matrix remains test-only while prompt engineering is in progress."""
    assert matrix.REAL_LLM_ALLOWED is False
    assert matrix.validate_matrix() == ()

    tree = ast.parse(inspect.getsource(matrix))
    imported_roots = {
        alias.name.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        node.module.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert not {"openai", "httpx", "requests", "socket"} & imported_roots


def test_matrix_summary_reports_static_case_counts():
    """Coverage counts are available without evaluating any fixture or model."""
    assert matrix.matrix_summary() == {
        "error_cases": len(ErrorKind),
        "config_cases": 13,
        "protocol_cases": 20,
        "metric_contracts": 16,
    }
