"""Offline contracts for the live protocol probe report."""

from __future__ import annotations

from types import SimpleNamespace

from tests.llm_eval.live_protocol_runner import _failure_category, build_protocol_report, run_protocol_case


def test_protocol_report_records_only_bounded_protocol_metrics():
    results = [{
        "name": name,
        "status": "passed",
        "score": 100,
        "completion": {"llm_calls": 1, "tool_calls": 1 if name != "text" else 0, "valid_tool_calls": 1 if name != "text" else 0, "response_latency_ms": 100.0},
        "failure": {"category": "", "remediation": ""},
    } for name in ("text", "natural_tool", "required_tool")]
    report = build_protocol_report(
        results=results,
        settings=SimpleNamespace(model="gpt-5.6-terra", base_url="https://provider.example/v1"),
        batch_id="protocol-test",
        project_version="0.2.0",
    )

    assert report["acceptance"]["conclusion"] == "passed"
    assert report["acceptance"]["gates"]["required_tool"] == "passed"
    assert report["acceptance"]["failure_categories"] == {}
    assert report["stages"][0]["checks"] == {"failure_category": "", "recommended_action": ""}
    assert report["config"]["configuration_fingerprint"]
    serialized = str(report).lower()
    assert "content" not in serialized
    assert "argument" not in serialized
    assert "api_key" not in serialized


def test_required_tool_choice_is_a_compatibility_diagnostic_not_a_product_gate():
    results = [{
        "name": name,
        "status": "failed" if name == "required_tool" else "passed",
        "score": 0 if name == "required_tool" else 100,
        "completion": {"llm_calls": 1, "tool_calls": 0, "valid_tool_calls": 0, "response_latency_ms": 100.0},
        "failure": {
            "category": "tool_choice_rejected" if name == "required_tool" else "",
            "remediation": "verify_provider_tool_choice_support" if name == "required_tool" else "",
        },
    } for name in ("text", "natural_tool", "required_tool")]
    report = build_protocol_report(
        results=results,
        settings=SimpleNamespace(model="gpt-5.6-terra", base_url="https://provider.example/v1"),
        batch_id="protocol-auto-compatible",
        project_version="0.2.0",
    )

    assert report["acceptance"]["conclusion"] == "passed"
    assert report["acceptance"]["preflight"]["required_tool_choice_compatibility"] == "failed"


def test_protocol_failure_categories_are_redacted_and_actionable():
    class NetworkFailure(Exception):
        pass

    class AuthFailure(Exception):
        status_code = 401

    class ToolChoiceFailure(Exception):
        status_code = 400

    class GatewayFailure(Exception):
        status_code = 502

    assert _failure_category(name="text", exc=NetworkFailure()) == "network_failure"
    assert _failure_category(name="natural_tool", exc=AuthFailure()) == "authentication_failure"
    assert _failure_category(name="required_tool", exc=ToolChoiceFailure()) == "tool_choice_rejected"
    assert _failure_category(name="text", exc=GatewayFailure()) == "gateway_unavailable"


def test_protocol_case_does_not_retention_exception_class_or_message():
    class APIConnectionError(Exception):
        pass

    class FakeCompletions:
        @staticmethod
        def create(**_kwargs):
            raise APIConnectionError("credential=not-for-report")

    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    result = run_protocol_case(
        name="text",
        client=client,
        settings=SimpleNamespace(model="test"),
    )

    assert result["failure"] == {
        "category": "network_failure",
        "remediation": "verify_network_and_base_url",
    }
    serialized = str(result).lower()
    assert "apiconnectionerror" not in serialized
    assert "credential" not in serialized
