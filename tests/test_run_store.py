import json
from threading import Barrier, Thread

import pytest

from willy.errors import StepResult
from willy.run_registry import EVENTS_FILENAME, RunRegistry
from willy.run_store import (
    PENDING_TRANSACTION_FILENAME,
    RUN_TRANSACTION_SCHEMA_VERSION,
    run_transaction,
)
from willy.simulation.manifest import initialize_manifest, load_manifest, record_box_attempt


def test_manifest_read_modify_write_is_serialized(tmp_path):
    run_dir = tmp_path / "md_run" / "md_test"
    run_dir.mkdir(parents=True)
    config = run_dir / "config.json"
    config.write_text('{"md":{"run_seed":1}}', encoding="utf-8")
    initialize_manifest(run_dir, config, random_seed=1, versions={})
    barrier = Barrier(2)

    def write_attempt(index):
        barrier.wait()
        record_box_attempt(run_dir, {"attempt": index, "box_size_angstrom": 10 + index})

    threads = [Thread(target=write_attempt, args=(index,)) for index in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    attempts = load_manifest(run_dir)["box_attempts"]
    assert {attempt["attempt"] for attempt in attempts} == {1, 2}


def test_events_and_decisions_share_one_monotonic_run_sequence(tmp_path):
    run_dir = tmp_path / "md_run" / "md_test"
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text("{}", encoding="utf-8")
    registry = RunRegistry(tmp_path)
    registry.register_run(run_dir, backend="g16", total_steps=10)
    registry.append_decision_trace(run_dir, {"action_id": "act_1", "layer": "simulation", "result": "proposed"})
    registry.record_step_result(
        run_dir,
        StepResult(step_name="box", step_index=7, success=True),
        label="Packmol", source="test",
    )
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    decisions = [json.loads(line) for line in (run_dir / "decision_trace.jsonl").read_text().splitlines()]
    sequences = [event["sequence"] for event in events] + [event["sequence"] for event in decisions]
    assert sorted(sequences) == list(range(1, len(sequences) + 1))
    assert events[-1]["details"]["artifact_contract"] == "packed_box"


def test_pending_bundle_replays_missing_json_and_event_once(tmp_path):
    run_dir = tmp_path / "md_run" / "md_test"
    run_dir.mkdir(parents=True)
    journal = {
        "schema_version": RUN_TRANSACTION_SCHEMA_VERSION,
        "transaction_id": "tx-recover-1",
        "operation": "test_recovery",
        "json_writes": {"status.json": {"state": "running", "state_revision": 2}},
        "jsonl_appends": {EVENTS_FILENAME: [{
            "timestamp": "2026-08-05T00:00:00+00:00",
            "event_type": "status_recovered",
            "run_id": run_dir.name,
            "details": {"state": "running"},
        }]},
    }
    with run_transaction(run_dir) as store:
        store.write_json(PENDING_TRANSACTION_FILENAME, journal)
        assert store.recover_pending_bundle() is True
        assert store.recover_pending_bundle() is False

    assert json.loads((run_dir / "status.json").read_text())["state"] == "running"
    events = [json.loads(line) for line in (run_dir / EVENTS_FILENAME).read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["transaction_id"] == "tx-recover-1"
    assert not (run_dir / PENDING_TRANSACTION_FILENAME).exists()


@pytest.mark.parametrize("failed_file", ("status.json", "pending_action.json"))
def test_revised_proposal_and_status_recover_as_one_bundle(tmp_path, monkeypatch, failed_file):
    from willy.run_store import RunStore

    directory = tmp_path / "md_run" / "md__202609080001"
    directory.mkdir(parents=True)
    (directory / "config.json").write_text("{}")
    registry = RunRegistry(tmp_path)
    registry.register_run(directory, backend="g16", total_steps=10)
    public_action = {
        "action_id": "original", "state": "pending", "step_label": "EQ",
        "restart_step": 9, "summary": "调整时间步长",
    }
    registry.record_status(directory, {
        "state": "awaiting_confirmation", "step": 9,
        "extra": {"pending_action": public_action},
    }, "fixture_waiting")
    current = registry.get_run_status(directory.name, reconcile=False)
    original = {"run_id": directory.name, "state": "pending", "action_id": "original"}
    (directory / "pending_action.json").write_text(json.dumps(original))
    replacement = {**original, "action_id": "replacement"}
    updated = {**current, "extra": {"pending_action": {**public_action, "action_id": "replacement"}}}
    write_json = RunStore.write_json

    def interrupted_write(store, filename, payload):
        if filename == failed_file:
            raise OSError("simulated interrupted write")
        write_json(store, filename, payload)

    with monkeypatch.context() as scoped:
        scoped.setattr(RunStore, "write_json", interrupted_write)
        with pytest.raises(OSError, match="interrupted"):
            registry.compare_and_swap_status(
                directory, expected_revision=current["state_revision"], status=updated,
                event_type="pending_action_revised", pending_action=replacement,
            )

    for _attempt in range(2):
        recovered = registry.get_run_status(directory.name, reconcile=False)
        assert recovered["state_revision"] == current["state_revision"] + 1
        assert recovered["extra"]["pending_action"]["action_id"] == "replacement"
        assert json.loads((directory / "pending_action.json").read_text()) == replacement
    events = [json.loads(line) for line in (directory / EVENTS_FILENAME).read_text().splitlines()]
    assert len([event for event in events if event["event_type"] == "pending_action_revised"]) == 1
    assert not (directory / PENDING_TRANSACTION_FILENAME).exists()
