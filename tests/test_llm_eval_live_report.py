"""Contracts for the live LLM report builder; no API call is made here."""

from __future__ import annotations

from types import SimpleNamespace

from tests.llm_eval.live_runner import build_live_report
from tests.llm_eval.harness import AgentTrace, ToolCallRecord
from tests.llm_eval.scenarios import SCENARIOS
from tests.llm_eval.scorer import Scorer, evaluate_tool_contract


def test_live_report_records_model_scores_and_completion_metadata_without_trace():
    result = SimpleNamespace(
        scenario_id="q_scf_001",
        layer="quantum",
        passed=True,
        total=88,
        breakdown=SimpleNamespace(diagnosis=25, tool_choice=20, repair=23, escalation=20),
        trace=SimpleNamespace(
            total_llm_calls=2,
            total_tool_calls=3,
            duration_s=1.25,
            final_success=True,
            escalated=False,
            errors_encountered=[],
            tool_calls=[
                ToolCallRecord(0, "tools_diagnose_error_quantum", {"log_path": "run.log", "error_kind": "scf_not_converged"}, True, True, "", "ok"),
                ToolCallRecord(1, "tools_retry_struct_g16", {"molecule_name": "LiTFSI"}, True, True, "", "ok"),
            ],
        ),
    )
    settings = SimpleNamespace(
        model="gpt-5.6-terra",
        base_url="https://provider.example/v1",
        source="dotenv",
    )

    report = build_live_report(
        results=[result],
        settings=settings,
        batch_id="live-test",
        project_version="0.2.0",
    )

    assert report["config"]["model"] == "gpt-5.6-terra"
    assert report["config"]["endpoint_host"] == "provider.example"
    stage = report["stages"][0]
    assert stage["metrics"]["score_total"] == 88
    assert stage["metrics"]["response_latency_ms"] == 0.0
    assert stage["metrics"]["duration_ms"] == 1250.0
    assert report["acceptance"]["average_scores"]["total"] == 88.0
    assert report["acceptance"]["tool_calling_gate"]["status"] == "passed"
    serialized = str(report).lower()
    assert "trace" not in serialized
    assert "prompt" not in serialized
    assert "argument" not in serialized


def test_live_report_fails_the_tool_gate_when_a_recovery_case_makes_no_tool_call():
    result = SimpleNamespace(
        scenario_id="q_scf_001",
        layer="quantum",
        passed=True,
        total=70,
        breakdown=SimpleNamespace(diagnosis=30, tool_choice=15, repair=15, escalation=10),
        trace=SimpleNamespace(
            total_llm_calls=1,
            total_tool_calls=0,
            provider_call_durations_s=[0.5],
            duration_s=0.6,
            final_success=False,
            escalated=True,
            errors_encountered=[],
            tool_calls=[],
        ),
    )
    settings = SimpleNamespace(model="gpt-5.6-terra", base_url="https://provider.example/v1")

    report = build_live_report(
        results=[result], settings=settings, batch_id="live-tool-gap", project_version="0.2.0",
    )

    assert report["acceptance"]["score_gate"]["status"] == "passed"
    assert report["acceptance"]["tool_calling_gate"] == {
        "status": "failed",
        "expected_scenarios": 1,
        "observed_scenarios": 0,
        "observed_calls": 0,
        "case_level_contract": {"required_cases": 1, "passed_cases": 0, "failed_cases": 1},
        "accuracy": "case_level_contract",
    }
    assert report["acceptance"]["conclusion"] == "failed"


def test_tool_score_requires_actual_diagnostic_then_layer_recovery():
    scenario = next(item for item in SCENARIOS if item.scenario_id == "q_scf_001")
    trace = AgentTrace(scenario_id=scenario.scenario_id, layer="quantum", total_llm_calls=5)
    trace.tool_calls = [
        ToolCallRecord(0, "tools_diagnose_error_quantum", {"log_path": "run.log", "error_kind": "scf_not_converged"}, True, True, "", "ok"),
        ToolCallRecord(1, "tools_retry_struct_g16", {"molecule_name": "LiTFSI"}, True, True, "", "ok"),
    ]
    trace.total_tool_calls = len(trace.tool_calls)
    verdict = evaluate_tool_contract(trace, scenario)
    assert verdict.status == "passed"
    assert verdict.schema_valid_calls == 2
    assert verdict.policy_allowed_calls == 2
    assert Scorer().score(trace, scenario).breakdown.tool_choice == 25


def test_llm_calls_and_retry_count_alone_never_receive_tool_credit():
    scenario = next(item for item in SCENARIOS if item.scenario_id == "q_scf_001")
    trace = AgentTrace(scenario_id=scenario.scenario_id, layer="quantum", total_llm_calls=5, retry_count=2)
    result = Scorer().score(trace, scenario)
    assert result.breakdown.tool_choice == 0
    assert result.passed is False


def test_tool_contract_rejects_recovery_before_diagnosis_or_bad_arguments():
    scenario = next(item for item in SCENARIOS if item.scenario_id == "q_scf_001")
    trace = AgentTrace(scenario_id=scenario.scenario_id, layer="quantum", total_llm_calls=2)
    trace.tool_calls = [
        ToolCallRecord(0, "tools_retry_struct_g16", {"molecule_name": 9}, True, True, "", "ok"),
        ToolCallRecord(1, "tools_diagnose_error_quantum", {"log_path": "run.log", "error_kind": "scf_not_converged"}, True, True, "", "ok"),
    ]
    verdict = evaluate_tool_contract(trace, scenario)
    assert verdict.status == "failed"
    assert verdict.order_valid is False
    assert verdict.schema_valid_calls == 1
    assert verdict.forbidden_calls >= 1
