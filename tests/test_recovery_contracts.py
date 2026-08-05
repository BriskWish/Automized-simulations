import json

from willy.run_registry import RunRegistry, DECISION_TRACE_FILENAME
from willy.simulation.pending_action import pending_action_executed, pending_action_validated


def _action():
    return {
        "action_id": "eq-test-action",
        "run_id": "md_test",
        "failure_step": 9,
        "restart_step": 9,
        "created_at": "2026-01-01T00:00:00+00:00",
        "config_sha256": "a" * 64,
        "adjustments": [{"field": "dt", "before": "0.002 ps", "after": "0.001 ps", "value": 0.001}],
    }


def test_pending_action_adapts_to_generic_contract():
    validated = pending_action_validated(_action())
    assert validated.proposal.tool_name == "tools_retry_eq"
    assert validated.requires_confirmation
    executed = pending_action_executed(_action(), success=True, result_summary="EQ accepted")
    assert executed.success is True
    assert executed.decision_id == "eq-test-action"


def test_decision_trace_redacts_paths_and_secrets(tmp_path):
    root = tmp_path
    run_dir = root / "md_run" / "md_test"
    run_dir.mkdir(parents=True)
    registry = RunRegistry(root)
    registry.register_run(run_dir, backend="g16", total_steps=10)
    registry.append_decision_trace(run_dir, {
        "action_id": "eq-test-action",
        "layer": "simulation",
        "selected_tool": "tools_retry_eq",
        "model_id": "safe-model",
        "result": "/home/user/raw_output.txt",
        "confirmation_source": "Bearer secret-token",
    })
    record = json.loads((run_dir / DECISION_TRACE_FILENAME).read_text().splitlines()[-1])
    assert record["result"] == "[redacted]"
    assert record["confirmation_source"] == "[redacted]"
    assert "secret-token" not in (run_dir / DECISION_TRACE_FILENAME).read_text()
