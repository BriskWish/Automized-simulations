"""Explicit live probe for an OpenAI-compatible text and tool-call protocol.

The probe never dispatches a returned tool. It records only bounded response
shape metrics and a redacted failure category. This separates basic transport
reachability from model tool support and ``tool_choice`` support without
retaining exception text, response text, arguments, credentials, or prompts.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import time
from urllib.parse import urlparse

from tests.reporting.batch_report import build_batch_report, collect_tool_versions, write_batch_report
from willy.llm_config import configured_llm_client, load_llm_settings


LIVE_MODEL = "gpt-5.6-terra"
_TOOL = {
    "type": "function",
    "function": {
        "name": "report_ready",
        "description": "Report that the protocol probe is ready.",
        "parameters": {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["ready"]}},
            "required": ["status"],
            "additionalProperties": False,
        },
    },
}


def _endpoint_host(base_url: str) -> str:
    return urlparse(base_url).netloc or "invalid"


def _config_fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _tool_outcome(response) -> tuple[int, int]:
    """Return the number of valid tool calls and all observed calls only."""
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        return 0, 0
    calls = getattr(choices[0].message, "tool_calls", None) or []
    valid = 0
    for call in calls:
        function = getattr(call, "function", None)
        if getattr(function, "name", None) != "report_ready":
            continue
        try:
            arguments = json.loads(getattr(function, "arguments", ""))
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(arguments, dict) and arguments == {"status": "ready"}:
            valid += 1
    return valid, len(calls)


def _status_code(exc: Exception) -> int | None:
    """Extract an HTTP status from common OpenAI-compatible exceptions."""
    direct = getattr(exc, "status_code", None)
    if isinstance(direct, int):
        return direct
    response = getattr(exc, "response", None)
    nested = getattr(response, "status_code", None)
    return nested if isinstance(nested, int) else None


def _failure_category(*, name: str, exc: Exception | None = None, response=None) -> str:
    """Classify a failed probe without persisting provider exception details."""
    if exc is not None:
        class_name = type(exc).__name__.lower()
        status = _status_code(exc)
        if status in {401, 403} or "auth" in class_name or "permission" in class_name:
            return "authentication_failure"
        if status == 429 or "rate" in class_name:
            return "rate_limited"
        if "timeout" in class_name or status in {408, 504}:
            return "transport_timeout"
        if status in {502, 503}:
            return "gateway_unavailable"
        if any(marker in class_name for marker in ("connection", "network", "connect", "dns", "ssl")):
            return "network_failure"
        if name == "required_tool" and status in {400, 404, 405, 422}:
            return "tool_choice_rejected"
        if name == "natural_tool" and status in {400, 404, 405, 422}:
            return "model_tool_protocol_rejected"
        if status is not None or "api" in class_name or "http" in class_name:
            return "transport_failure"
        return "provider_failure"

    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        return "invalid_completion"
    if name == "text":
        return "text_protocol_failure"
    calls = getattr(choices[0].message, "tool_calls", None) or []
    if name == "required_tool" and not calls:
        return "tool_choice_not_honored"
    return "model_tool_protocol_failure"


def _remediation(category: str) -> str:
    """Return a public next-step code instead of an exception or provider text."""
    return {
        "authentication_failure": "verify_credentials",
        "rate_limited": "wait_or_adjust_quota",
        "transport_timeout": "verify_timeout_and_endpoint",
        "gateway_unavailable": "verify_gateway_availability",
        "network_failure": "verify_network_and_base_url",
        "transport_failure": "verify_endpoint_transport",
        "provider_failure": "verify_provider_availability",
        "model_tool_protocol_rejected": "verify_model_tool_support",
        "model_tool_protocol_failure": "verify_model_tool_support",
        "tool_choice_rejected": "verify_provider_tool_choice_support",
        "tool_choice_not_honored": "verify_provider_tool_choice_support",
        "invalid_completion": "verify_provider_response_protocol",
        "text_protocol_failure": "verify_provider_response_protocol",
    }.get(category, "inspect_provider_configuration")


def run_protocol_case(*, name: str, client, settings) -> dict[str, object]:
    """Issue one bounded live request and expose no response text."""
    use_tool = name != "text"
    call_kwargs: dict[str, object] = {
        "model": settings.model,
        "messages": [{"role": "user", "content": "Reply exactly with READY."}] if not use_tool else [{"role": "user", "content": "Call report_ready with status ready. Do not answer with ordinary text."}],
        "temperature": 0,
        "timeout": 30.0,
    }
    if use_tool:
        call_kwargs["tools"] = [_TOOL]
    if name == "required_tool":
        call_kwargs["tool_choice"] = "required"

    started = time.monotonic()
    try:
        response = client.chat.completions.create(**call_kwargs)
    except Exception as exc:
        category = _failure_category(name=name, exc=exc)
        return {
            "name": name,
            "status": "failed",
            "score": 0,
            "completion": {"llm_calls": 1, "tool_calls": 0, "valid_tool_calls": 0, "response_latency_ms": round((time.monotonic() - started) * 1000, 2)},
            "failure": {"category": category, "remediation": _remediation(category)},
        }
    elapsed_ms = round((time.monotonic() - started) * 1000, 2)
    valid_calls, observed_calls = _tool_outcome(response)
    if not use_tool:
        choices = getattr(response, "choices", None)
        content = getattr(choices[0].message, "content", None) if isinstance(choices, list) and choices else None
        score = 100 if isinstance(content, str) and bool(content.strip()) else 0
    else:
        score = 100 if valid_calls == 1 and observed_calls == 1 else 0
    category = "" if score == 100 else _failure_category(name=name, response=response)
    return {
        "name": name,
        "status": "passed" if score == 100 else "failed",
        "score": score,
        "completion": {"llm_calls": 1, "tool_calls": observed_calls, "valid_tool_calls": valid_calls, "response_latency_ms": elapsed_ms},
        "failure": {"category": category, "remediation": _remediation(category) if category else ""},
    }


def build_protocol_report(*, results: list[dict[str, object]], settings, batch_id: str, project_version: str) -> dict[str, object]:
    """Build a public protocol report without response content or arguments."""
    stages = [{
        "name": result["name"],
        "phase": "live_protocol_fake_tools",
        "status": result["status"],
        "metrics": {
            "score_total": result["score"],
            "llm_calls": result["completion"]["llm_calls"],
            "tool_calls": result["completion"]["tool_calls"],
            "valid_tool_calls": result["completion"]["valid_tool_calls"],
            "response_latency_ms": result["completion"]["response_latency_ms"],
        },
        "checks": {
            "failure_category": str((result.get("failure") or {}).get("category") or ""),
            "recommended_action": str((result.get("failure") or {}).get("remediation") or ""),
        },
        "artifacts": [],
    } for result in results]
    by_name = {result["name"]: result for result in results}
    passed = sum(result["status"] == "passed" for result in results)
    gates = {
        "text": by_name.get("text", {}).get("status", "not_run"),
        "natural_tool": by_name.get("natural_tool", {}).get("status", "not_run"),
        "required_tool": by_name.get("required_tool", {}).get("status", "not_run"),
    }
    failure_categories: dict[str, int] = {}
    for result in results:
        failure = result.get("failure") if isinstance(result.get("failure"), dict) else {}
        category = str(failure.get("category") or "")
        if category:
            failure_categories[category] = failure_categories.get(category, 0) + 1
    # Willy uses automatic tool selection and verifies the returned call on the
    # server. ``required`` remains an interoperability diagnostic only: some
    # OpenAI-compatible reasoning gateways reject that extension while still
    # supporting the controlled automatic-call path used in production.
    required_gates = (gates["text"], gates["natural_tool"])
    conclusion = "passed" if results and all(value == "passed" for value in required_gates) else "failed"
    public_config = {
        "evaluation_mode": "live_protocol_fake_tools",
        "model": settings.model,
        "endpoint_host": _endpoint_host(settings.base_url),
    }
    public_config["configuration_fingerprint"] = _config_fingerprint(public_config)
    return build_batch_report(
        batch_id=batch_id,
        project_version=project_version,
        config=public_config,
        stages=stages,
        acceptance={
            "conclusion": conclusion,
            "completed": {"cases": len(results), "passed": passed, "failed": len(results) - passed},
            "gates": gates,
            "preflight": {
                "text_connectivity": gates["text"],
                "model_tool_calling": gates["natural_tool"],
                "required_tool_choice_compatibility": gates["required_tool"],
            },
            "failure_categories": failure_categories,
            "model": settings.model,
        },
        tools=collect_tool_versions(("python", "pytest")),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("tests/reports/live_llm_protocol_probe.json"))
    parser.add_argument("--project-version", default="0.3.0")
    args = parser.parse_args(argv)

    settings = load_llm_settings()
    if settings is None:
        raise SystemExit("未找到 WILLY_LLM_API_KEY；未执行任何 API 调用。")
    if settings.model != LIVE_MODEL:
        raise SystemExit(f"当前配置模型为 {settings.model!r}，不是请求的 {LIVE_MODEL!r}；未执行任何 API 调用。")
    client, configured = configured_llm_client()
    if client is None or configured is None:
        raise SystemExit("无法构造配置的 LLM 客户端；未执行任何 API 调用。")

    results = [run_protocol_case(name=name, client=client, settings=settings) for name in ("text", "natural_tool", "required_tool")]
    for result in results:
        print(f"{result['name']}: {result['status']} ({result['score']}/100, tools={result['completion']['tool_calls']})")
    report = build_protocol_report(
        results=results,
        settings=settings,
        batch_id=f"live-protocol-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        project_version=args.project_version,
    )
    target = write_batch_report(args.output, report)
    print(f"Wrote redacted report: {target}")
    return 0 if report["acceptance"]["conclusion"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
