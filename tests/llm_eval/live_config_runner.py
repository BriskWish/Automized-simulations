"""Explicit live evaluation of the current configuration-assistant prompt.

The runner invokes the configured model through ``willy.agent_config.chat``.
All configuration tools and candidate auditing are replaced with in-memory
fakes, so the evaluation cannot write a project configuration, launch a run,
or inspect local scientific inputs.  Only bounded result metadata is written.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import time
from types import SimpleNamespace
from urllib.parse import urlparse

from tests.reporting.batch_report import build_batch_report, collect_tool_versions, write_batch_report
from tests.llm_eval.matrix import matrix_summary
from willy.llm_config import configured_llm_client, load_llm_settings


LIVE_MODEL = "gpt-5.6-terra"


@dataclass(frozen=True)
class ConfigLiveCase:
    case_id: str
    request: str
    disposition: str
    residues: tuple[tuple[str, int], ...] = ()
    backend: str = "g16"
    error_type: str = ""
    requires_input_audit: bool = False


CONFIG_LIVE_CASES = (
    ConfigLiveCase(
        "cfg_live_g16_balanced",
        "请使用 G16 为 Li 10、TFSI 10 和 EC 50 建立模拟方案。",
        "proposal",
        (("Li", 10), ("TFSI", 10), ("EC", 50)),
        "g16",
        requires_input_audit=True,
    ),
    ConfigLiveCase(
        "cfg_live_orca_balanced",
        "请使用 ORCA 为 Li 5、TFSI 5 和 EC 30 建立模拟方案。",
        "proposal",
        (("Li", 5), ("TFSI", 5), ("EC", 30)),
        "orca",
        requires_input_audit=True,
    ),
    ConfigLiveCase(
        "cfg_live_ambiguous",
        "请为锂盐 100 和溶剂 200 建立模拟方案。",
        "rejected",
        error_type="ambiguous",
    ),
    ConfigLiveCase(
        "cfg_live_unknown_molecule",
        "请为 XxImaginary 10 和 EC 10 建立模拟方案。",
        "rejected",
        error_type="invalid_molecule",
    ),
    ConfigLiveCase(
        "cfg_live_charge_imbalance",
        "请使用 G16 为 Li 10 和 EC 100 建立模拟方案。",
        "rejected",
        error_type="charge_imbalance",
        requires_input_audit=True,
    ),
)


def _parse_error_type(content: object) -> str:
    """Extract only a structured error label from an in-memory completion."""
    if not isinstance(content, str):
        return ""
    text = content.strip()
    if text.startswith("```"):
        parts = text.split("\n", 1)
        text = parts[1] if len(parts) == 2 else ""
        text = text.rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return ""
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(error, str):
        return error.strip().lower().replace(" ", "_")
    if isinstance(error, dict) and isinstance(error.get("type"), str):
        return error["type"].strip().lower()
    return ""


class _CountingClient:
    """SDK proxy retaining only completion shape and elapsed time."""

    def __init__(self, client):
        self._client = client
        self.call_durations_s: list[float] = []
        self.completion_error_types: list[str] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        started = time.monotonic()
        try:
            response = self._client.chat.completions.create(**kwargs)
        finally:
            self.call_durations_s.append(time.monotonic() - started)
        message = response.choices[0].message
        self.completion_error_types.append(_parse_error_type(message.content))
        return response


class _FakePlanTools:
    """Read-only fake tool executor; it stores no arguments or response text."""

    _charges = {"Li": 1, "TFSI": -1, "EC": 0}

    def __init__(self):
        self.tool_names: list[str] = []
        self.audit_component_sets: list[tuple[tuple[str, int], ...]] = []

    def handle(self, tool_name: str, args: dict) -> str:
        self.tool_names.append(tool_name)
        if tool_name == "tools_inspect_quantum_inputs":
            backend = str(args.get("backend") or "").lower()
            raw_components = args.get("components")
            if backend not in {"g16", "g09", "orca"} or not isinstance(raw_components, list):
                return json.dumps({"ok": False, "issues": ["invalid request"]})
            components: list[dict[str, object]] = []
            net_charge = 0
            seen: set[str] = set()
            for item in raw_components:
                if not isinstance(item, dict):
                    return json.dumps({"ok": False, "issues": ["invalid component"]})
                name, count = item.get("name"), item.get("count")
                if not isinstance(name, str) or isinstance(count, bool) or not isinstance(count, int) or count < 1:
                    return json.dumps({"ok": False, "issues": ["invalid component"]})
                if name not in self._charges or name in seen:
                    return json.dumps({"ok": False, "issues": ["input unavailable"]})
                seen.add(name)
                charge = self._charges[name]
                net_charge += charge * count
                components.append({"name": name, "count": count, "charge": charge, "spin": 1, "status": "valid"})
            normalized = tuple(sorted((str(item["name"]), int(item["count"])) for item in components))
            self.audit_component_sets.append(normalized)
            return json.dumps({
                "ok": True,
                "backend": backend,
                "components": components,
                "net_charge": net_charge,
                "charge_balance": "balanced" if net_charge == 0 else "imbalanced",
            })
        if tool_name == "tools_lookup_molecule":
            name = args.get("name")
            if name in self._charges:
                return json.dumps({"name": name, "charge": self._charges[name], "spin": 1, "type": "known"})
            return json.dumps({"error": "not found"})
        if tool_name == "tools_resolve_compound":
            return json.dumps({"error": "not a compound"})
        if tool_name == "tools_lookup_md_defaults":
            return json.dumps({"schema_version": 2, "dt": 0.001, "eq": {"target_temperature": 298}, "prod": {"duration_ns": 10, "temperature": 298}})
        if tool_name == "tools_get_box_density":
            return json.dumps({"target_mass_density_g_cm3": 0.7})
        if tool_name == "tools_lookup_basis_set":
            return json.dumps({"recommended": "b3lyp/6-311+g(d,p)"})
        if tool_name == "tools_refresh_structs":
            return json.dumps({"ok": True, "count": 3, "molecules": sorted(self._charges)})
        if tool_name == "tools_diagnose_error_config":
            return json.dumps({"source": "config", "severity": "error", "issues": []})
        if tool_name == "tools_validate_config":
            return json.dumps({"valid": True, "issues": []})
        return json.dumps({"ok": False, "error": "unsupported fake tool"})


def _score_case(case: ConfigLiveCase, *, pending_plan: object, fake_tools: _FakePlanTools, completion_errors: list[str]) -> dict[str, object]:
    """Score public structural outcomes without retaining model response text."""
    allowed_tools = {
        "tools_lookup_molecule", "tools_resolve_compound", "tools_lookup_md_defaults",
        "tools_get_box_density", "tools_lookup_basis_set", "tools_refresh_structs",
        "tools_diagnose_error_config", "tools_validate_config", "tools_inspect_quantum_inputs",
    }
    tool_safety = all(name in allowed_tools for name in fake_tools.tool_names)
    expected_residues = dict(case.residues)
    plan_config = pending_plan.get("config") if isinstance(pending_plan, dict) else None
    disposition_ok = isinstance(plan_config, dict) if case.disposition == "proposal" else pending_plan is None
    if case.disposition == "proposal":
        semantic_ok = (
            isinstance(plan_config, dict)
            and plan_config.get("backend") == case.backend
            and plan_config.get("residues") == expected_residues
        )
        expected_audit = tuple(sorted(case.residues))
        audit_ok = not case.requires_input_audit or expected_audit in fake_tools.audit_component_sets
        score = (30 if disposition_ok else 0) + (30 if semantic_ok else 0) + (25 if audit_ok else 0) + (15 if tool_safety else 0)
        dimensions = {
            "disposition": 30 if disposition_ok else 0,
            "semantic_accuracy": 30 if semantic_ok else 0,
            "input_audit": 25 if audit_ok else 0,
            "tool_safety": 15 if tool_safety else 0,
        }
    else:
        error_ok = case.error_type in completion_errors
        audit_ok = not case.requires_input_audit or bool(fake_tools.audit_component_sets)
        score = (35 if disposition_ok else 0) + (35 if error_ok else 0) + (15 if audit_ok else 0) + (15 if tool_safety else 0)
        dimensions = {
            "safe_rejection": 35 if disposition_ok else 0,
            "error_classification": 35 if error_ok else 0,
            "input_audit": 15 if audit_ok else 0,
            "tool_safety": 15 if tool_safety else 0,
        }
    return {
        "status": "passed" if score >= 60 and disposition_ok and tool_safety else "failed",
        "score": score,
        "dimensions": dimensions,
        "tool_calls": len(fake_tools.tool_names),
        "audit_calls": len(fake_tools.audit_component_sets),
    }


def run_config_case(case: ConfigLiveCase, client) -> dict[str, object]:
    """Exercise the current config chat flow with a real model and fake tools."""
    import willy.agent_config as agent_config

    fake_tools = _FakePlanTools()
    counted_client = _CountingClient(client)
    original_client = agent_config._DS
    original_settings = agent_config._LLM_SETTINGS
    original_tool_handler = agent_config.handle_tool_call
    original_audit_candidate = agent_config._audit_candidate_config
    try:
        agent_config._DS = counted_client
        agent_config.handle_tool_call = fake_tools.handle
        agent_config._audit_candidate_config = lambda config: (dict(config), [])
        updates = list(agent_config.chat(case.request, []))
    finally:
        agent_config._DS = original_client
        agent_config._LLM_SETTINGS = original_settings
        agent_config.handle_tool_call = original_tool_handler
        agent_config._audit_candidate_config = original_audit_candidate

    pending_plan = updates[-1][3] if updates else None
    scored = _score_case(
        case,
        pending_plan=pending_plan,
        fake_tools=fake_tools,
        completion_errors=counted_client.completion_error_types,
    )
    return {
        "case_id": case.case_id,
        "status": scored["status"],
        "score": scored["score"],
        "dimensions": scored["dimensions"],
        "completion": {
            "llm_calls": len(counted_client.call_durations_s),
            "tool_calls": scored["tool_calls"],
            "input_audit_calls": scored["audit_calls"],
            "response_latency_ms": round(sum(counted_client.call_durations_s) * 1000, 2),
        },
    }


def _endpoint_host(base_url: str) -> str:
    return urlparse(base_url).netloc or "invalid"


def _config_fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def build_config_live_report(*, results: list[dict[str, object]], settings, batch_id: str, project_version: str) -> dict[str, object]:
    """Build a redacted report for live configuration-prompt evaluation."""
    stages = []
    for result in results:
        stages.append({
            "name": result["case_id"],
            "phase": "live_config_fake_tools",
            "status": result["status"],
            "metrics": {
                "score_total": result["score"],
                "llm_calls": result["completion"]["llm_calls"],
                "tool_calls": result["completion"]["tool_calls"],
                "input_audit_calls": result["completion"]["input_audit_calls"],
                "response_latency_ms": result["completion"]["response_latency_ms"],
            },
            "artifacts": [],
        })
    total = len(results)
    passed = sum(result["status"] == "passed" for result in results)
    audits_expected = sum(case.requires_input_audit for case in CONFIG_LIVE_CASES if case.case_id in {r["case_id"] for r in results})
    audits_observed = sum(result["completion"]["input_audit_calls"] > 0 for result in results)
    total_score = sum(float(result["score"]) for result in results)
    conclusion = "passed" if total and passed == total and audits_expected == audits_observed else "failed"
    public_config = {
        "evaluation_mode": "live_config_fake_tools",
        "model": settings.model,
        "endpoint_host": _endpoint_host(settings.base_url),
        "contract_version": "wpc-v1",
        "config_assistant_version": "config-assistant-v2",
        "matrix": {"config_cases": matrix_summary()["config_cases"], "actual_cases": total},
    }
    public_config["configuration_fingerprint"] = _config_fingerprint(public_config)
    return build_batch_report(
        batch_id=batch_id,
        project_version=project_version,
        config=public_config,
        stages=stages,
        acceptance={
            "conclusion": conclusion,
            "completed": {"cases": total, "passed": passed, "failed": total - passed},
            "average_score": round(total_score / total, 2) if total else 0.0,
            "input_audit_gate": {"expected_cases": audits_expected, "observed_cases": audits_observed, "status": "passed" if audits_expected == audits_observed else "failed"},
            "dimensions": {"proposal": "0-100", "safe_rejection": "0-100", "tool_safety": "0-15"},
            "model": settings.model,
        },
        tools=collect_tool_versions(("python", "pytest")),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", help="run only this case (repeatable)")
    parser.add_argument("--output", type=Path, default=Path("tests/reports/live_llm_config_eval.json"))
    parser.add_argument("--project-version", default="0.4.0")
    args = parser.parse_args(argv)

    settings = load_llm_settings()
    if settings is None:
        raise SystemExit("未找到 WILLY_LLM_API_KEY；未执行任何 API 调用。")
    if settings.model != LIVE_MODEL:
        raise SystemExit(f"当前配置模型为 {settings.model!r}，不是请求的 {LIVE_MODEL!r}；未执行任何 API 调用。")
    client, configured = configured_llm_client()
    if client is None or configured is None:
        raise SystemExit("无法构造配置的 LLM 客户端；未执行任何 API 调用。")
    cases = list(CONFIG_LIVE_CASES)
    if args.case:
        wanted = set(args.case)
        cases = [case for case in cases if case.case_id in wanted]
    if not cases:
        raise SystemExit("没有匹配的配置评测用例；未执行任何 API 调用。")

    print(f"Live config eval: model={settings.model}, endpoint={_endpoint_host(settings.base_url)}, cases={len(cases)}")
    results = []
    for index, case in enumerate(cases, start=1):
        result = run_config_case(case, client)
        results.append(result)
        print(f"[{index}/{len(cases)}] {case.case_id} {result['status']} ({result['score']}/100, tools={result['completion']['tool_calls']})")
    report = build_config_live_report(
        results=results,
        settings=settings,
        batch_id=f"live-config-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        project_version=args.project_version,
    )
    target = write_batch_report(args.output, report)
    print(f"Wrote redacted report: {target}")
    return 0 if report["acceptance"]["conclusion"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
