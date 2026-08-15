"""Explicit live LLM evaluation with an in-memory fake tool executor.

This command is opt-in and is intentionally separate from the default pytest
suite.  The model sees the production layer prompt and structured failure
context, while all tool effects remain controlled by ``phase2_runner``'s fake
executor.  Only bounded scores and metadata are persisted.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlparse

from tests.reporting.batch_report import build_batch_report, collect_tool_versions, write_batch_report
from tests.llm_eval.matrix import ErrorRoute, ERROR_CASES, matrix_summary
from tests.llm_eval.phase2_runner import print_phase2_report, run_phase2
from tests.llm_eval.scorer import _catalog_and_policy, _route_for_scenario, evaluate_tool_contract
from tests.llm_eval.scenarios import SCENARIOS
from willy.llm_config import load_llm_settings


LIVE_MODEL = "gpt-5.6-terra"


def _endpoint_host(base_url: str) -> str:
    parsed = urlparse(base_url)
    return parsed.netloc or "invalid"


def _config_fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return round(ordered[index], 2)


def _public_results(results) -> tuple[list[dict[str, object]], dict[str, float], list[float]]:
    rows: list[dict[str, object]] = []
    scenarios_by_id = {scenario.scenario_id: scenario for scenario in SCENARIOS}
    totals = {"diagnosis": 0.0, "tool_choice": 0.0, "repair": 0.0, "escalation": 0.0, "total": 0.0}
    response_latencies_ms: list[float] = []
    for result in sorted(results, key=lambda item: item.scenario_id):
        trace = result.trace
        terminal_reason = str(getattr(trace, "recovery_terminal_reason", "")) if trace else ""
        if not terminal_reason and trace and trace.total_llm_calls >= 2:
            # A bounded public inference for older trace objects: if the
            # diagnostic role was observed but the required recovery role was
            # absent, the server necessarily terminated this phase before any
            # repair dispatch. Do not infer a scientific cause.
            contract_preview = evaluate_tool_contract(
                trace, scenarios_by_id[result.scenario_id]
            ) if result.scenario_id in scenarios_by_id else None
            if contract_preview and contract_preview.observed_roles == ("diagnostic",):
                terminal_reason = "model_no_tool_call"
        dimensions = {
            "diagnosis": result.breakdown.diagnosis,
            "tool_choice": result.breakdown.tool_choice,
            "repair": result.breakdown.repair,
            "escalation": result.breakdown.escalation,
        }
        for key, value in dimensions.items():
            totals[key] += value
        totals["total"] += result.total
        call_durations = list(getattr(trace, "provider_call_durations_s", ())) if trace else []
        response_latency_ms = round(sum(call_durations) * 1000, 2)
        response_latencies_ms.extend(round(duration * 1000, 2) for duration in call_durations)
        rows.append({
            "scenario_id": result.scenario_id,
            "layer": result.layer,
            "status": "passed" if result.passed else "failed",
            "score": {**dimensions, "total": result.total, "maximum": 100},
            "completion": {
                "llm_calls": trace.total_llm_calls if trace else 0,
                "tool_calls": trace.total_tool_calls if trace else 0,
                "response_latency_ms": response_latency_ms,
                "duration_ms": round((trace.duration_s if trace else 0.0) * 1000, 2),
                "final_success": bool(trace.final_success) if trace else False,
                "escalated": bool(trace.escalated) if trace else False,
                "terminal_reason": terminal_reason,
                "runtime_errors": len(trace.errors_encountered) if trace else 0,
            },
            "tool_contract": evaluate_tool_contract(trace, scenarios_by_id[result.scenario_id]).to_public_dict()
            if trace and result.scenario_id in scenarios_by_id
            else {
                "required": False,
                "status": "not_run",
                "expected_first_role": None,
                "observed_first_role": None,
                "required_roles": [],
                "observed_roles": [],
                "missing_roles": [],
                "tool_calls": 0,
                "schema_valid_calls": 0,
                "policy_allowed_calls": 0,
                "forbidden_calls": 0,
            },
        })
    count = len(rows) or 1
    averages = {key: round(value / count, 2) for key, value in totals.items()}
    return rows, averages, response_latencies_ms


def build_live_report(*, results, settings, batch_id: str, project_version: str) -> dict[str, object]:
    """Build a public batch report without prompt, trace, argument, or secret fields."""
    rows, averages, response_latencies_ms = _public_results(results)
    scenarios_by_id = {scenario.scenario_id: scenario for scenario in SCENARIOS}
    _catalog, policy = _catalog_and_policy()
    expected_tool_scenarios = sum(
        _route_for_scenario(scenarios_by_id[row["scenario_id"]], policy) is ErrorRoute.BOUNDED_RECOVERY
        for row in rows
        if row["scenario_id"] in scenarios_by_id
    )
    contract_required_rows = [row for row in rows if row["tool_contract"]["required"]]
    contract_passed_rows = [row for row in contract_required_rows if row["tool_contract"]["status"] == "passed"]
    observed_tool_scenarios = len(contract_passed_rows)
    observed_tool_calls = sum(row["completion"]["tool_calls"] for row in rows)
    stages = [
        {
            "name": row["scenario_id"],
            "phase": "live_llm_mock_tools",
            "status": row["status"],
            "metrics": {
                "score_total": row["score"]["total"],
                "score_diagnosis": row["score"]["diagnosis"],
                "score_tool_choice": row["score"]["tool_choice"],
                "score_repair": row["score"]["repair"],
                "score_escalation": row["score"]["escalation"],
                "llm_calls": row["completion"]["llm_calls"],
                "tool_calls": row["completion"]["tool_calls"],
                "response_latency_ms": row["completion"]["response_latency_ms"],
                "duration_ms": row["completion"]["duration_ms"],
                "terminal_reason": row["completion"]["terminal_reason"],
            },
            "checks": {"tool_contract": row["tool_contract"]},
            "artifacts": [],
        }
        for row in rows
    ]
    passed = sum(1 for row in rows if row["status"] == "passed")
    baseline_passed = bool(rows) and passed == len(rows)
    tool_calling_passed = expected_tool_scenarios == 0 or observed_tool_scenarios == expected_tool_scenarios
    conclusion = "passed" if baseline_passed and tool_calling_passed else "failed"
    public_config = {
        "evaluation_mode": "live_llm_mock_tools",
        "model": settings.model,
        "endpoint_host": _endpoint_host(settings.base_url),
        "contract_version": "wpc-v1",
        "layer_contract_versions": {
            "l1": "quantum-agent-v1",
            "l2": "topology-agent-v1",
            "l3": "simulation-agent-v2",
        },
        "matrix": matrix_summary(),
        "actual_scenarios": len(rows),
    }
    public_config["configuration_fingerprint"] = _config_fingerprint(public_config)
    return build_batch_report(
        batch_id=batch_id,
        project_version=project_version,
        config=public_config,
        stages=stages,
        acceptance={
            "conclusion": conclusion,
            "threshold": {"minimum_score": 60, "required_pass_rate": 1.0},
            "completed": {"scenarios": len(rows), "passed": passed, "failed": len(rows) - passed},
            "average_scores": averages,
            "score_gate": {"status": "passed" if baseline_passed else "failed"},
            "tool_calling_gate": {
                "status": "passed" if tool_calling_passed else "failed",
                "expected_scenarios": expected_tool_scenarios,
                "observed_scenarios": observed_tool_scenarios,
                "observed_calls": observed_tool_calls,
                "case_level_contract": {
                    "required_cases": len(contract_required_rows),
                    "passed_cases": len(contract_passed_rows),
                    "failed_cases": len(contract_required_rows) - len(contract_passed_rows),
                },
                "accuracy": "case_level_contract",
            },
            "response_latency_ms": {
                "p50": _percentile(response_latencies_ms, 0.50),
                "p95": _percentile(response_latencies_ms, 0.95),
            },
            "dimensions": {
                "diagnosis": "0-30",
                "tool_choice": "0-25",
                "repair": "0-25",
                "escalation": "0-20",
            },
            "scoring_scope": "tool choice is scored from actual tool name, schema, order, layer and recovery-policy contract",
            "model": settings.model,
        },
        tools=collect_tool_versions(("python", "pytest")),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", action="append", help="run only this historical scenario (repeatable)")
    parser.add_argument("--layer", choices=("quantum", "topology", "simulation"))
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--output", type=Path, default=Path("tests/reports/live_llm_eval.json"))
    parser.add_argument("--project-version", default="0.5.0")
    args = parser.parse_args(argv)

    settings = load_llm_settings()
    if settings is None:
        raise SystemExit("未找到 WILLY_LLM_API_KEY；未执行任何 API 调用。")
    if settings.model != LIVE_MODEL:
        raise SystemExit(f"当前配置模型为 {settings.model!r}，不是请求的 {LIVE_MODEL!r}；未执行任何 API 调用。")
    if args.workers < 1:
        raise SystemExit("--workers 必须为正整数。")

    scenarios = list(SCENARIOS)
    if args.layer:
        scenarios = [scenario for scenario in scenarios if scenario.layer == args.layer]
    if args.scenario:
        wanted = set(args.scenario)
        scenarios = [scenario for scenario in scenarios if scenario.scenario_id in wanted]
    if not scenarios:
        raise SystemExit("没有匹配的评测场景；未执行任何 API 调用。")

    print(f"Live LLM eval: model={settings.model}, endpoint={_endpoint_host(settings.base_url)}, scenarios={len(scenarios)}")
    results = run_phase2(scenarios, max_workers=args.workers, model=settings.model)
    print_phase2_report(results)
    report = build_live_report(
        results=results,
        settings=settings,
        batch_id=f"live-llm-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        project_version=args.project_version,
    )
    target = write_batch_report(args.output, report)
    print(f"Wrote redacted report: {target}")
    return 0 if report["acceptance"]["conclusion"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
