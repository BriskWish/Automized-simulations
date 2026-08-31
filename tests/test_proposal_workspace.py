"""Durable pre-launch proposal workspace contracts."""

from pathlib import Path

import pytest

from willy.proposal_workspace import (
    ProposalWorkspaceError,
    create_plan_from_run_conversation,
    create_temp_workspace,
    delete_local_item,
    ensure_default_workspace,
    formalize_plan_directory,
    get_run_conversation_history,
    get_workspace,
    list_workspaces,
    save_formalized_conversation,
    save_run_conversation,
    save_workspace_conversation,
)
from willy.run_registry import RunRegistry


def _candidate_config() -> dict[str, object]:
    return {"backend": "g16", "residues": {"Li": 1}}


def test_default_workspace_creates_temp_and_reuses_unfinished_plan(tmp_path):
    created = ensure_default_workspace(tmp_path)

    assert created["workspace_id"].startswith("temp__")
    assert created["kind"] == "temp"
    saved = save_workspace_conversation(
        tmp_path,
        created["workspace_id"],
        messages=[{"role": "user", "content": "建立 Li 体系"}],
        pending_plan={"config": _candidate_config()},
    )

    assert saved["workspace_id"].startswith("plan__")
    assert saved["state"] == "awaiting_confirmation"
    assert ensure_default_workspace(tmp_path)["workspace_id"] == saved["workspace_id"]
    assert list_workspaces(tmp_path)[0]["workspace_id"] == saved["workspace_id"]


def test_formalizing_plan_reuses_its_directory_and_preserves_conversation(tmp_path):
    draft = create_temp_workspace(tmp_path)
    plan = save_workspace_conversation(
        tmp_path,
        draft["workspace_id"],
        messages=[
            {"role": "user", "content": "建立 Li 体系"},
            {"role": "assistant", "content": "模拟方案确认"},
        ],
        pending_plan={"config": _candidate_config()},
    )
    run_dir = tmp_path / "md_run" / "md__202608310001"
    run_dir.mkdir(parents=True)

    formalize_plan_directory(
        tmp_path,
        plan_id=plan["workspace_id"],
        run_dir=run_dir,
        expected_config=_candidate_config(),
    )
    save_formalized_conversation(
        tmp_path,
        run_dir.name,
        messages=[
            {"role": "user", "content": "建立 Li 体系"},
            {"role": "assistant", "content": "模拟方案确认"},
            {"role": "user", "content": "确认运行"},
        ],
    )

    assert not (tmp_path / "md_run" / plan["workspace_id"]).exists()
    record = (run_dir / "proposal.json").read_text(encoding="utf-8")
    assert '"workspace_id": "' + plan["workspace_id"] + '"' in record
    assert '"formalized_run_id": "md__202608310001"' in record
    assert '"确认运行"' in record
    with pytest.raises(ProposalWorkspaceError):
        get_workspace(tmp_path, plan["workspace_id"])


def test_formalization_rejects_changed_candidate_without_modifying_directories(tmp_path):
    draft = create_temp_workspace(tmp_path)
    plan = save_workspace_conversation(
        tmp_path,
        draft["workspace_id"],
        messages=[],
        pending_plan={"config": _candidate_config()},
    )
    run_dir = tmp_path / "md_run" / "md__202608310002"
    run_dir.mkdir(parents=True)

    with pytest.raises(ProposalWorkspaceError, match="不匹配"):
        formalize_plan_directory(
            tmp_path,
            plan_id=plan["workspace_id"],
            run_dir=run_dir,
            expected_config={"backend": "g16", "residues": {"Li": 2}},
        )

    assert (tmp_path / "md_run" / plan["workspace_id"]).is_dir()
    assert run_dir.is_dir()


def test_formalized_plan_has_manifest_and_event_audit_after_run_registration(tmp_path):
    draft = create_temp_workspace(tmp_path)
    plan = save_workspace_conversation(
        tmp_path,
        draft["workspace_id"],
        messages=[],
        pending_plan={"config": _candidate_config()},
    )
    run_dir = tmp_path / "md_run" / "md__202608310003"
    run_dir.mkdir(parents=True)
    formalize_plan_directory(
        tmp_path,
        plan_id=plan["workspace_id"],
        run_dir=run_dir,
        expected_config=_candidate_config(),
    )

    registry = RunRegistry(tmp_path)
    registry.register_run(run_dir, backend="g16", total_steps=10)
    registry.record_proposal_origin(run_dir, plan["workspace_id"])

    manifest = (run_dir / "run_manifest.json").read_text(encoding="utf-8")
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert '"proposal_workspace_id": "' + plan["workspace_id"] + '"' in manifest
    assert '"event_type": "proposal_formalized"' in events
    assert '"plan_id": "' + plan["workspace_id"] + '"' in events


def test_run_conversation_creates_a_fresh_plan_without_replacing_run_history(tmp_path):
    run_id = "md__202608310004"
    run_dir = tmp_path / "md_run" / run_id
    run_dir.mkdir(parents=True)
    original = [
        {"role": "user", "content": "原工程的方案问题"},
        {"role": "assistant", "content": "原工程的方案答复"},
    ]
    save_run_conversation(tmp_path, run_id, messages=original)
    next_history = [
        *original,
        {"role": "user", "content": "改为新的体系"},
        {"role": "assistant", "content": "新方案已生成"},
    ]

    plan = create_plan_from_run_conversation(
        tmp_path,
        run_id,
        messages=next_history,
        pending_plan={"config": _candidate_config()},
        source_messages=original,
    )

    assert plan["workspace_id"].startswith("plan__")
    assert plan["workspace_id"] != run_id
    assert get_run_conversation_history(tmp_path, run_id) == original
    assert get_workspace(tmp_path, plan["workspace_id"])["messages"] == next_history
    assert get_workspace(tmp_path, plan["workspace_id"])["source_run_id"] == run_id


def test_delete_local_item_requires_plan_confirmation_and_removes_only_target(tmp_path):
    temp = create_temp_workspace(tmp_path)
    plan = save_workspace_conversation(
        tmp_path,
        temp["workspace_id"],
        messages=[],
        pending_plan={"config": _candidate_config()},
    )
    run_id = "md__202608310005"
    run_dir = tmp_path / "md_run" / run_id
    run_dir.mkdir(parents=True)

    with pytest.raises(ProposalWorkspaceError, match="二次确认"):
        delete_local_item(tmp_path, plan["workspace_id"])
    assert (tmp_path / "md_run" / plan["workspace_id"]).is_dir()

    assert delete_local_item(tmp_path, plan["workspace_id"], confirm_plan=True) == {
        "item_id": plan["workspace_id"],
        "kind": "plan",
    }
    assert not (tmp_path / "md_run" / plan["workspace_id"]).exists()
    assert delete_local_item(tmp_path, run_id) == {"item_id": run_id, "kind": "run"}
    assert not run_dir.exists()
