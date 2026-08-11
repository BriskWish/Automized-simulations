import json

import pytest

from willy.run_metadata import (
    RUN_MANIFEST_FILENAME,
    RunManifestRevisionConflict,
    RunMetadataError,
    create_run_manifest,
    load_legacy_run_sections,
    load_run_metadata_section,
    load_run_manifest,
    migrate_legacy_run_manifest,
    update_run_manifest_section,
    update_run_manifest_sections,
)


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _legacy_run(tmp_path, *, state="done"):
    run_dir = tmp_path / "md_run" / "md_test"
    run_dir.mkdir(parents=True)
    _write_json(run_dir / "manifest.json", {"schema_version": 1, "run_id": "md_test", "artifacts": []})
    _write_json(run_dir / "provenance.json", {"schema_version": 1, "run_id": "md_test", "runtime": {"python": "3.12"}})
    _write_json(run_dir / "topology_manifest.json", {"version": 3, "components": []})
    _write_json(
        run_dir / "md_manifest.json",
        {"schema_version": 1, "stages": {"em": {"status": "accepted"}}, "protocol": {"mdp": {"dt": 0.001}}},
    )
    _write_json(run_dir / "status.json", {"state": state, "run_id": "md_test"})
    return run_dir


def test_load_legacy_sections_keeps_protocol_separate_and_does_not_write(tmp_path):
    run_dir = _legacy_run(tmp_path)

    sections = load_legacy_run_sections(run_dir)

    assert sections["registry"]["run_id"] == "md_test"
    assert sections["simulation"]["stages"]["em"]["status"] == "accepted"
    assert "protocol" not in sections["simulation"]
    assert sections["protocol"]["mdp"]["dt"] == pytest.approx(0.001)
    assert not (run_dir / RUN_MANIFEST_FILENAME).exists()


def test_section_reader_prefers_v2_and_falls_back_to_legacy(tmp_path):
    run_dir = _legacy_run(tmp_path)

    assert load_run_metadata_section(run_dir, "registry")["run_id"] == "md_test"
    create_run_manifest(run_dir, sections={"registry": {"run_id": "v2", "backend": "orca"}})

    assert load_run_metadata_section(run_dir, "registry") == {"run_id": "v2", "backend": "orca"}


def test_explicit_legacy_migration_retains_old_files(tmp_path):
    run_dir = _legacy_run(tmp_path)

    migrated = migrate_legacy_run_manifest(run_dir)

    assert migrated["schema_version"] == 2
    assert migrated["revision"] == 0
    assert migrated["sections"]["registry"]["visibility"] == "public"
    assert migrated["sections"]["simulation"]["data"]["stages"]["em"]["status"] == "accepted"
    assert migrated["sections"]["protocol"]["data"]["mdp"]["dt"] == pytest.approx(0.001)
    assert migrated["migration"]["delete_legacy"] is False
    assert {record["path"] for record in migrated["migration"]["legacy_files"]} == {
        "manifest.json", "provenance.json", "topology_manifest.json", "md_manifest.json",
    }
    assert (run_dir / RUN_MANIFEST_FILENAME).is_file()
    for filename in ("manifest.json", "provenance.json", "topology_manifest.json", "md_manifest.json"):
        assert (run_dir / filename).is_file()
    with pytest.raises(RunMetadataError, match="delete_legacy=False"):
        migrate_legacy_run_manifest(run_dir, delete_legacy=True)


def test_section_update_uses_global_and_section_compare_and_swap(tmp_path):
    run_dir = tmp_path / "md_run" / "md_test"
    run_dir.mkdir(parents=True)
    created = create_run_manifest(run_dir, sections={"topology": {"components": []}})

    updated = update_run_manifest_section(
        run_dir,
        "topology",
        {"components": [{"residue_name": "EC"}]},
        expected_revision=created["revision"],
        expected_section_revision=created["sections"]["topology"]["revision"],
    )

    assert updated["revision"] == 1
    assert updated["sections"]["topology"]["revision"] == 1
    assert updated["sections"]["registry"]["revision"] == 0
    assert load_run_manifest(run_dir)["sections"]["topology"]["data"]["components"][0]["residue_name"] == "EC"
    with pytest.raises(RunManifestRevisionConflict, match="已更新"):
        update_run_manifest_section(run_dir, "topology", {}, expected_revision=0)
    with pytest.raises(RunMetadataError, match="必须是对象"):
        update_run_manifest_section(run_dir, "topology", [])


def test_multi_section_update_is_one_atomic_global_revision(tmp_path):
    run_dir = tmp_path / "md_run" / "md_test"
    run_dir.mkdir(parents=True)
    created = create_run_manifest(run_dir)

    updated = update_run_manifest_sections(
        run_dir,
        {"registry": {"backend": "g16"}, "provenance": {"runtime": {"python": "3.12"}}},
        expected_revision=0,
        expected_section_revisions={"registry": 0, "provenance": 0},
    )

    assert updated["revision"] == 1
    assert updated["sections"]["registry"]["revision"] == 1
    assert updated["sections"]["provenance"]["revision"] == 1
    assert updated["sections"]["simulation"]["revision"] == 0
    with pytest.raises(RunMetadataError, match="本次更新"):
        update_run_manifest_sections(
            run_dir,
            {"registry": {}},
            expected_section_revisions={"provenance": 1},
        )


@pytest.mark.parametrize("state", ["running", "awaiting_confirmation", "retrying"])
def test_migration_rejects_active_or_awaiting_runs(tmp_path, state):
    run_dir = _legacy_run(tmp_path, state=state)

    with pytest.raises(RunMetadataError, match="仅终态工程"):
        migrate_legacy_run_manifest(run_dir)

    assert not (run_dir / RUN_MANIFEST_FILENAME).exists()


def test_migration_rejects_pending_action_even_if_status_is_terminal(tmp_path):
    run_dir = _legacy_run(tmp_path)
    _write_json(run_dir / "pending_action.json", {"state": "pending", "action_id": "action_1"})

    with pytest.raises(RunMetadataError, match="待确认"):
        migrate_legacy_run_manifest(run_dir)


def test_migration_rejects_run_without_terminal_status_evidence(tmp_path):
    run_dir = _legacy_run(tmp_path)
    (run_dir / "status.json").unlink()

    with pytest.raises(RunMetadataError, match="缺少 status.json"):
        migrate_legacy_run_manifest(run_dir)


def test_active_migration_requires_explicit_opt_in(tmp_path):
    run_dir = _legacy_run(tmp_path, state="running")

    migrated = migrate_legacy_run_manifest(run_dir, allow_active=True)

    assert migrated["run_id"] == "md_test"
