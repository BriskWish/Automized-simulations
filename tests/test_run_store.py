import json
from threading import Barrier, Thread

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
