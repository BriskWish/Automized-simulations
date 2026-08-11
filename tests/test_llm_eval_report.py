"""Ensure the baseline LLM report remains public and trace-free."""

from __future__ import annotations

from tests.llm_eval.run_eval import public_report, run_all_scenarios, write_public_report


def test_mock_eval_public_report_excludes_prompts_traces_and_tool_arguments(tmp_path):
    results = run_all_scenarios(layer_filter="quantum")
    report = public_report(results)
    path = write_public_report(tmp_path / "mock-eval.json", results)

    assert report["mode"] == "mock"
    assert report["total"] == 6
    assert report["passed"] == 6
    serialized = path.read_text(encoding="utf-8").lower()
    assert "trace" not in serialized
    assert "arguments" not in serialized
    assert "raw_output" not in serialized
