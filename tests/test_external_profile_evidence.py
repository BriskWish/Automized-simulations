"""Deterministic contracts for redacted four-profile external evidence."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import xml.etree.ElementTree as ET

import pytest

from willy.external_profile_evidence import (
    EXTERNAL_PROFILES,
    _main,
    get_external_profile,
    selected_external_profiles,
    validate_finished_profile,
    verify_profile_evidence,
    write_profile_evidence,
    write_profile_junit,
)
from willy.run_metadata import create_run_manifest, update_run_manifest_sections


_STAGE_ARTIFACTS = {
    "em": ("tpr", "gro", "xtc", "edr"),
    "eq": ("tpr", "gro", "xtc", "edr", "cpt"),
    "prod": ("tpr", "gro", "xtc", "edr", "cpt"),
}


def _record(path: Path, root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }


def _finished_run(tmp_path: Path, profile_id: str = "g16_sobtop_electrolyte") -> Path:
    profile = get_external_profile(profile_id)
    run_dir = tmp_path / "md__acceptance_0001"
    run_dir.mkdir()
    stages: dict[str, dict[str, object]] = {}
    for name in ("topol.top", "model.pdb"):
        (run_dir / name).write_text(f"fixture {name}\n", encoding="utf-8")
    for stage, kinds in _STAGE_ARTIFACTS.items():
        outputs = {}
        for kind in kinds:
            name = f"{stage}.{kind}"
            path = run_dir / name
            path.write_text(f"fixture {name}\n", encoding="utf-8")
            outputs[kind] = _record(path, run_dir)
        stages[stage] = {
            "status": "completed" if stage == "prod" else "accepted",
            "outputs": outputs,
        }
    create_run_manifest(run_dir, sections={
        "registry": {"run_id": run_dir.name, "backend": profile.quantum_backend},
        "provenance": {
            "source_revision": {"commit": "a" * 40, "dirty": False},
            "runtime": {"willy": "0.4.0"},
        },
        "topology": {
            "backend": profile.topology_backend,
            "forcefield_family": profile.forcefield_family,
            "components": [{"success": True, "validated": True}],
        },
        "simulation": {"stages": stages},
    })
    (run_dir / "status.json").write_text(json.dumps({
        "state": "done", "total_steps": 10, "done_steps": list(range(1, 11)),
        "error": "", "error_kind": "",
    }), encoding="utf-8")
    return run_dir


def test_declared_external_profiles_are_exact_four_routes():
    assert [(item.quantum_backend, item.topology_backend) for item in EXTERNAL_PROFILES] == [
        ("g16", "sobtop"), ("orca", "sobtop"), ("g16", "oplsaa"), ("orca", "oplsaa"),
    ]
    assert selected_external_profiles("all") == EXTERNAL_PROFILES
    with pytest.raises(ValueError, match="重复"):
        selected_external_profiles("g16_sobtop_electrolyte,g16_sobtop_electrolyte")


@pytest.mark.parametrize("profile_id", [profile.profile_id for profile in EXTERNAL_PROFILES])
def test_each_declared_profile_accepts_its_matching_finished_route(tmp_path, profile_id):
    result = validate_finished_profile(profile_id, _finished_run(tmp_path, profile_id))

    assert result.accepted
    assert result.evidence["profile"]["profile_id"] == profile_id


def test_finished_profile_produces_only_redacted_hashed_evidence(tmp_path):
    run_dir = _finished_run(tmp_path)

    result = validate_finished_profile("g16_sobtop_electrolyte", run_dir)

    assert result.accepted
    assert result.issues == ()
    evidence = result.evidence
    assert evidence["workflow"]["done_steps"] == list(range(1, 11))
    assert evidence["workflow"]["stage_states"] == {
        "em": "accepted", "eq": "accepted", "prod": "completed",
    }
    assert len(evidence["artifacts"]) == 16
    rendered = json.dumps(evidence, ensure_ascii=False)
    assert str(run_dir) not in rendered
    assert "fixture em.tpr" not in rendered
    assert "config" not in rendered.lower()
    assert all(set(item) == {"name", "size_bytes", "sha256"} for item in evidence["artifacts"])


def test_profile_rejects_incomplete_terminal_workflow(tmp_path):
    run_dir = _finished_run(tmp_path)
    (run_dir / "status.json").write_text(json.dumps({
        "state": "running", "total_steps": 10, "done_steps": list(range(1, 10)),
    }), encoding="utf-8")

    result = validate_finished_profile("g16_sobtop_electrolyte", run_dir)

    assert not result.accepted
    assert "final_state_not_done" in result.issues
    assert "done_steps_incomplete" in result.issues
    with pytest.raises(ValueError, match="未通过"):
        write_profile_evidence(tmp_path / "evidence.json", result)
    junit = write_profile_junit(tmp_path / "result.xml", result)
    failure = ET.parse(junit).find(".//failure")
    assert failure is not None
    assert str(run_dir) not in (failure.text or "")
    assert "final_state_not_done" in (failure.text or "")


def test_nonterminal_run_is_rejected_before_private_metadata_or_artifacts_are_read(tmp_path):
    run_dir = tmp_path / "md__active_0001"
    run_dir.mkdir()
    (run_dir / "status.json").write_text(json.dumps({
        "state": "running", "total_steps": 10, "done_steps": list(range(1, 9)),
    }), encoding="utf-8")
    (run_dir / "run_manifest.json").write_text("{not valid json", encoding="utf-8")
    (run_dir / "eq.xtc").write_text("active artifact", encoding="utf-8")

    result = validate_finished_profile("g16_sobtop_electrolyte", run_dir)

    assert not result.accepted
    assert result.issues == ("final_state_not_done", "done_steps_incomplete")
    assert result.evidence["artifacts"] == []


def test_profile_rejects_stage_artifact_hash_drift(tmp_path):
    run_dir = _finished_run(tmp_path)
    (run_dir / "eq.edr").write_text("tampered\n", encoding="utf-8")

    result = validate_finished_profile("g16_sobtop_electrolyte", run_dir)

    assert not result.accepted
    assert "stage_output_size_mismatch:eq.edr" in result.issues
    assert "stage_output_hash_mismatch:eq.edr" in result.issues


def test_profile_rejects_wrong_declared_route(tmp_path):
    run_dir = _finished_run(tmp_path, "g16_sobtop_electrolyte")

    result = validate_finished_profile("orca_sobtop_electrolyte", run_dir)

    assert not result.accepted
    assert "quantum_backend_mismatch" in result.issues


def test_written_evidence_can_be_verified_without_reading_run_workspace(tmp_path):
    run_dir = _finished_run(tmp_path)
    result = validate_finished_profile("g16_sobtop_electrolyte", run_dir)
    evidence_dir = tmp_path / "evidence"

    saved = write_profile_evidence(evidence_dir / "g16_sobtop_electrolyte.json", result)
    run_dir.rename(tmp_path / "removed_run")

    assert saved.is_file()
    assert verify_profile_evidence(evidence_dir, (get_external_profile("g16_sobtop_electrolyte"),)) == ()


def test_archived_historical_profile_evidence_is_complete():
    evidence_dir = Path(__file__).resolve().parents[1] / "tests/reports/baselines/external_profiles_20260814"

    assert verify_profile_evidence(evidence_dir, EXTERNAL_PROFILES) == ()


def test_evidence_verifier_rejects_path_or_raw_content_fields(tmp_path):
    run_dir = _finished_run(tmp_path)
    result = validate_finished_profile("g16_sobtop_electrolyte", run_dir)
    evidence_dir = tmp_path / "evidence"
    saved = write_profile_evidence(evidence_dir / "g16_sobtop_electrolyte.json", result)
    payload = json.loads(saved.read_text(encoding="utf-8"))
    payload["artifacts"][0]["path"] = "/private/run/eq.xtc"
    saved.write_text(json.dumps(payload), encoding="utf-8")

    assert verify_profile_evidence(evidence_dir, (get_external_profile("g16_sobtop_electrolyte"),)) == (
        "profile_evidence_invalid:g16_sobtop_electrolyte",
    )


def test_evidence_verifier_rejects_nested_configuration_or_path_fields(tmp_path):
    run_dir = _finished_run(tmp_path)
    result = validate_finished_profile("g16_sobtop_electrolyte", run_dir)
    evidence_dir = tmp_path / "evidence"
    saved = write_profile_evidence(evidence_dir / "g16_sobtop_electrolyte.json", result)
    payload = json.loads(saved.read_text(encoding="utf-8"))
    payload["software"]["config"] = {"private": "value"}
    saved.write_text(json.dumps(payload), encoding="utf-8")

    assert verify_profile_evidence(evidence_dir, (get_external_profile("g16_sobtop_electrolyte"),)) == (
        "profile_evidence_invalid:g16_sobtop_electrolyte",
    )


def test_evidence_requires_commit_but_preserves_historical_dirty_provenance(tmp_path):
    run_dir = _finished_run(tmp_path)
    update_run_manifest_sections(run_dir, {
        "provenance": {"source_revision": {"commit": "b" * 40, "dirty": True}, "runtime": {"willy": "0.4.0"}},
    })

    result = validate_finished_profile("g16_sobtop_electrolyte", run_dir)

    assert result.accepted
    assert result.evidence["software"] == {
        "source_commit": "b" * 40,
        "worktree_clean": False,
        "project_version": "0.4.0",
    }

    update_run_manifest_sections(run_dir, {
        "provenance": {"source_revision": {"commit": "unavailable", "dirty": True}, "runtime": {"willy": "0.4.0"}},
    })
    result = validate_finished_profile("g16_sobtop_electrolyte", run_dir)
    assert not result.accepted
    assert "provenance_commit_missing" in result.issues


def test_cli_writes_then_verifies_finished_profile_evidence(tmp_path, capsys):
    run_dir = _finished_run(tmp_path)
    output = tmp_path / "evidence" / "g16_sobtop_electrolyte.json"
    junit = tmp_path / "evidence" / "g16_sobtop_electrolyte.xml"

    assert _main([
        "--profile", "g16_sobtop_electrolyte", "--run-dir", str(run_dir), "--output", str(output),
        "--junit-output", str(junit),
    ]) == 0
    assert output.is_file()
    assert ET.parse(junit).getroot().attrib["failures"] == "0"
    assert _main([
        "--evidence-dir", str(output.parent), "--profiles", "g16_sobtop_electrolyte",
    ]) == 0

    captured = capsys.readouterr().out
    assert str(run_dir) not in captured
    assert "fixture" not in captured
