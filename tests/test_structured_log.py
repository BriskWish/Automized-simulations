import json

from willy.structured_log import (
    REDACTED,
    StructuredRunLogger,
    append_structured_event,
)


def _run_dir(tmp_path):
    return tmp_path / "md_run" / "md_test"


def test_append_creates_run_local_log_and_shared_sequence(tmp_path):
    run_dir = _run_dir(tmp_path)
    first = append_structured_event(
        run_dir,
        "step_started",
        step=9,
        step_name="GROMACS 三点式退火平衡",
        layer="simulation",
        source="orchestrator",
        message_code="eq.started",
        model_id="gpt-5",
        prompt_version="simulation.v1",
        parameter_fields=["eq_tau_p"],
        artifact_refs=["eq.tpr", "eq.gro"],
    )
    second = append_structured_event(run_dir, "step_finished", outcome="accepted", duration_ms=12.5)

    assert first is not None and second is not None
    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert first["run_id"] == "md_test"
    assert first["step_name"] == "GROMACS 三点式退火平衡"
    assert first["model_id"] == "gpt-5"
    assert first["prompt_version"] == "simulation.v1"
    assert first["parameter_fields"] == ["eq_tau_p"]
    assert (run_dir / "logs" / "structured.jsonl").is_file()
    rows = [json.loads(line) for line in (run_dir / "logs" / "structured.jsonl").read_text().splitlines()]
    assert [row["event_code"] for row in rows] == ["step_started", "step_finished"]


def test_sensitive_paths_and_command_like_values_are_redacted(tmp_path):
    event = append_structured_event(
        _run_dir(tmp_path),
        "tool_finished",
        source="gmx mdrun -s eq.tpr",
        action_id="Authorization: Bearer secret-token",
        message_code="/home/user/private/raw_output.txt",
        artifact_refs=["eq.tpr", "/home/user/eq.log", "prompt=private"],
    )

    assert event is not None
    assert event["source"] == REDACTED
    assert event["action_id"] == REDACTED
    assert event["message_code"] == REDACTED
    assert event["artifact_refs"] == ["eq.tpr", REDACTED, REDACTED]


def test_malformed_fields_are_omitted_without_rejecting_event(tmp_path):
    event = StructuredRunLogger(_run_dir(tmp_path)).append(
        "step_started",
        step={"not": "an integer"},
        duration_ms=float("nan"),
        artifact_refs="eq.tpr",
        layer=["simulation"],
        source=object(),
    )

    assert event is not None
    assert "step" not in event
    assert "duration_ms" not in event
    assert "artifact_refs" not in event
    assert "layer" not in event
    assert "source" not in event


def test_invalid_required_event_is_non_fatal_and_does_not_write(tmp_path):
    logger = StructuredRunLogger(_run_dir(tmp_path))
    assert logger.append({"event": "invalid"}) is None
    assert not logger.path.exists()


def test_logging_failure_is_non_blocking(tmp_path, monkeypatch):
    logger = StructuredRunLogger(_run_dir(tmp_path))

    def fail_transaction(_run_dir):
        raise OSError("simulated disk failure")

    monkeypatch.setattr("willy.structured_log.run_transaction", fail_transaction)
    assert logger.append("step_failed", error_kind="grompp") is None
