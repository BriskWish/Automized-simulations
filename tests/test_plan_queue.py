"""Serial plan queue admission and advancement contracts."""

from types import SimpleNamespace

import pytest

import willy.plan_queue as plan_queue
from willy.proposal_workspace import create_temp_workspace, save_workspace_conversation


def _plan(root, residue: str):
    draft = create_temp_workspace(root)
    return save_workspace_conversation(
        root,
        draft["workspace_id"],
        messages=[{"role": "assistant", "content": f"{residue}方案"}],
        pending_plan={"config": {"backend": "g16", "residues": {residue: 1}}},
    )


def test_queue_orders_confirmed_plans_and_allows_head_withdrawal(tmp_path, monkeypatch):
    first = _plan(tmp_path, "Li")
    second = _plan(tmp_path, "EC")
    monkeypatch.setattr(plan_queue, "pipeline_launch_is_active", lambda _root: True)

    assert plan_queue.queue_plan(tmp_path, first["workspace_id"])["queue_position"] == 1
    assert plan_queue.queue_plan(tmp_path, second["workspace_id"])["queue_position"] == 2
    assert [item["plan_id"] for item in plan_queue.queue_snapshot(tmp_path)["queued"]] == [
        first["workspace_id"], second["workspace_id"],
    ]

    plan_queue.dequeue_plan(tmp_path, first["workspace_id"], expected_revision=first["revision"])
    remaining = plan_queue.queue_snapshot(tmp_path)["queued"]
    assert [(item["plan_id"], item["queue_position"]) for item in remaining] == [
        (second["workspace_id"], 1),
    ]


def test_failed_head_does_not_block_next_plan(tmp_path, monkeypatch):
    first = _plan(tmp_path, "Li")
    second = _plan(tmp_path, "EC")
    monkeypatch.setattr(plan_queue, "pipeline_launch_is_active", lambda _root: False)
    calls = []

    def launcher(config, plan_id):
        calls.append((config, plan_id))
        return SimpleNamespace(
            state="failed" if plan_id == first["workspace_id"] else "started",
            run_id=None if plan_id == first["workspace_id"] else "md__202609230001",
        )

    plan_queue.queue_plan(tmp_path, first["workspace_id"])
    plan_queue.queue_plan(tmp_path, second["workspace_id"])
    snapshot = plan_queue.dispatch_plan_queue(tmp_path, launcher=launcher)

    assert [plan_id for _, plan_id in calls] == [first["workspace_id"], second["workspace_id"]]
    assert snapshot["active"]["run_id"] == "md__202609230001"
    assert snapshot["queued"] == []


def test_released_queue_run_starts_next_admitted_plan(tmp_path, monkeypatch):
    first = _plan(tmp_path, "Li")
    second = _plan(tmp_path, "EC")
    monkeypatch.setattr(plan_queue, "pipeline_launch_is_active", lambda _root: False)
    calls = []

    def launcher(_config, plan_id):
        calls.append(plan_id)
        return SimpleNamespace(state="started", run_id=f"md__20260923000{len(calls)}")

    plan_queue.queue_plan(tmp_path, first["workspace_id"])
    plan_queue.queue_plan(tmp_path, second["workspace_id"])
    first_snapshot = plan_queue.dispatch_plan_queue(tmp_path, launcher=launcher)
    assert first_snapshot["active"]["run_id"] == "md__202609230001"

    next_snapshot = plan_queue.advance_after_run_release(
        tmp_path,
        "md__202609230001",
        launcher=launcher,
    )
    assert calls == [first["workspace_id"], second["workspace_id"]]
    assert next_snapshot["active"]["plan_id"] == second["workspace_id"]


def test_plan_change_invalidates_queue_membership(tmp_path, monkeypatch):
    plan = _plan(tmp_path, "Li")
    monkeypatch.setattr(plan_queue, "pipeline_launch_is_active", lambda _root: True)
    queued = plan_queue.queue_plan(tmp_path, plan["workspace_id"])

    save_workspace_conversation(
        tmp_path,
        plan["workspace_id"],
        messages=[{"role": "user", "content": "改参数"}],
        pending_plan={"config": {"backend": "g16", "residues": {"Li": 2}}},
    )

    assert queued["queue_position"] == 1
    assert plan_queue.queue_metadata(tmp_path) == {}
    assert plan_queue.queue_snapshot(tmp_path)["queued"] == []
