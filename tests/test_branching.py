"""Complete inherited context is data, never the parent's live authority."""

import json

import pytest

from willy.branching import copy_run_context
from willy.config_store import write_json
from willy.step_contracts import archive_files, validate_step_inputs
from tests.test_resume_admission import _inputs


def test_snapshot_copies_nested_context_and_rebinds_internal_paths(tmp_path):
    _registry, parent, _status = _inputs(tmp_path, 9)
    (parent / "nested").mkdir()
    (parent / "nested" / "notes.txt").write_text("original context")
    (parent / "alias.txt").symlink_to(parent / "nested" / "notes.txt")
    write_json(parent / "run_assistant_history.json", {"run_id": parent.name, "messages": [{"role": "user", "content": "原工程对话"}]})
    write_json(parent / "context.json", {"workdir": str(parent / "nested"), "run_id": parent.name})
    write_json(parent / "pending_action.json", {"state": "pending", "action_id": "parent-action"})
    (parent / "stop.request").write_text("parent stop")
    (parent / "runner.pid").write_text("12345")
    before = {path.relative_to(parent): path.read_bytes() for path in parent.rglob("*") if path.is_file()}
    child = parent.with_name("md__202609080002")
    config = json.loads((parent / "config.json").read_text())
    config["md"]["dt"] = 0.0005

    copy_run_context(parent, child, config)

    assert {path: (parent / path).read_bytes() for path in before} == before
    assert (child / "alias.txt").resolve() == child / "nested" / "notes.txt"
    (child / "alias.txt").write_text("child edits")
    assert (parent / "nested" / "notes.txt").read_text() == "original context"
    assert json.loads((child / "context.json").read_text())["workdir"] == str(child / "nested")
    history = json.loads((child / "run_assistant_history.json").read_text())
    assert history["run_id"] == child.name
    assert history["messages"][0]["content"] == "原工程对话"
    assert not (child / "pending_action.json").exists()
    assert not (child / "stop.request").exists()
    assert not (child / "runner.pid").exists()
    assert list((child / "old").glob("fork-source-*/context/pending_action.json"))


@pytest.mark.parametrize("link_type", ["external_file", "external_directory", "dangling"])
def test_external_or_dangling_links_block_copy_without_parent_changes(tmp_path, link_type):
    _registry, parent, _status = _inputs(tmp_path, 9)
    outside = tmp_path / "outside"
    if link_type == "external_file":
        outside.write_text("private external data")
    elif link_type == "external_directory":
        outside.mkdir()
    (parent / "escape").symlink_to(outside)
    config = json.loads((parent / "config.json").read_text())
    child = parent.with_name("md__202609080002")
    with pytest.raises((OSError, ValueError)):
        copy_run_context(parent, child, config)
    assert not child.exists()


def test_earlier_fork_restores_hash_matched_archived_inputs_in_child_only(tmp_path):
    _registry, parent, _status = _inputs(tmp_path, 9)
    before = (parent / "Li.chg").read_bytes()
    archive_files(parent, {parent / "Li.chg"}, "cleanup", step=10)
    config = json.loads((parent / "config.json").read_text())
    child = parent.with_name("md__202609080002")
    copy_run_context(parent, child, config, restart_step=4)
    assert (child / "Li.chg").read_bytes() == before
    assert not (parent / "Li.chg").exists()
    validate_step_inputs(child, 4)


def test_nested_forks_preserve_both_ancestral_snapshots(tmp_path):
    _registry, parent, _status = _inputs(tmp_path, 9)
    config = json.loads((parent / "config.json").read_text())
    child = parent.with_name("md__202609080002")
    grandchild = parent.with_name("md__202609080003")
    copy_run_context(parent, child, config)
    copy_run_context(child, grandchild, config)
    assert (grandchild / "old" / f"fork-source-{parent.name}" / "context" / "config.json").is_file()
    assert (grandchild / "old" / f"fork-source-{child.name}" / "context" / "config.json").is_file()
