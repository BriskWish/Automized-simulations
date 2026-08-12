"""Offline contracts for the live configuration-prompt report."""

from __future__ import annotations

from types import SimpleNamespace

from tests.llm_eval.live_config_runner import build_config_live_report


def test_config_live_report_keeps_scores_calls_and_model_without_completion_text():
    report = build_config_live_report(
        results=[{
            "case_id": "cfg_live_g16_balanced",
            "status": "passed",
            "score": 100,
            "dimensions": {"disposition": 30},
            "completion": {"llm_calls": 2, "tool_calls": 1, "input_audit_calls": 1, "response_latency_ms": 1234.5},
        }],
        settings=SimpleNamespace(model="gpt-5.6-terra", base_url="https://provider.example/v1"),
        batch_id="config-live-test",
        project_version="0.2.0",
    )

    assert report["config"]["model"] == "gpt-5.6-terra"
    assert report["stages"][0]["metrics"]["score_total"] == 100
    assert report["acceptance"]["input_audit_gate"]["status"] == "passed"
    serialized = str(report).lower()
    assert "prompt" not in serialized
    assert "argument" not in serialized
    assert "api_key" not in serialized
