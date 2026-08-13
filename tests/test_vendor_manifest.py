"""Regression coverage for the release-facing vendor inventory."""

from __future__ import annotations

import hashlib
import json

from willy._paths import get_project_root
from willy.vendor_manifest import (
    audit_release_artifact,
    audit_vendor_manifest,
    release_artifact_ready,
    release_ready_vendor_files,
)


def _component(path: str, payload: bytes, *, distribution_status: str = "release_ready") -> dict[str, object]:
    return {
        "id": "fixture-runtime",
        "distribution_status": distribution_status,
        "source_url": "https://example.invalid/runtime",
        "version": "1.0",
        "license_files": ["LICENSE.txt"],
        "files": [
            {
                "path": path,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
            {
                "path": "LICENSE.txt",
                "size_bytes": len(b"fixture license\n"),
                "sha256": hashlib.sha256(b"fixture license\n").hexdigest(),
            },
        ],
    }


def _write_manifest(root, component: dict[str, object]) -> None:
    vendor = root / "vendor"
    vendor.mkdir()
    (vendor / "LICENSE.txt").write_text("fixture license\n", encoding="utf-8")
    (vendor / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "components": [component]}), encoding="utf-8"
    )


def test_repository_vendor_inventory_has_no_integrity_drift():
    audit = audit_vendor_manifest(get_project_root())

    assert audit.integrity_ok, audit.integrity_issues
    assert {component.component_id for component in audit.components} >= {
        "multiwfn-linux-x86_64",
        "packmol-linux-runtime",
        "sobtop-runtime",
        "openbabel-minimal-runtime",
    }
    assert not any(issue.startswith("distribution_evidence_pending:multiwfn") for issue in audit.release_issues)
    components = {component.component_id: component for component in audit.components}
    assert components["sobtop-runtime"].distribution_status == "exclude_from_release_artifact"
    assert components["sobtop-runtime"].release_excluded_paths == ("sobtop/",)
    assert components["openbabel-minimal-runtime"].distribution_status == "exclude_from_release_artifact"
    assert "release_artifact_exclusion_required:sobtop-runtime" in audit.release_issues
    assert "release_artifact_exclusion_required:openbabel-minimal-runtime" in audit.release_issues
    assert "release_artifact_exclusion_required:3dmol-legacy-asset" in audit.release_issues
    allowed = set(release_ready_vendor_files(get_project_root()))
    assert "packmol" in allowed
    assert "multiwfn/linux-x86_64/3.8-dev-2025-02-14/Multiwfn" in allowed
    assert not any(path.startswith("sobtop/") for path in allowed)


def test_vendor_inventory_reports_hash_drift(tmp_path):
    payload = b"expected runtime bytes\n"
    component = _component("runtime.bin", payload)
    _write_manifest(tmp_path, component)
    (tmp_path / "vendor" / "runtime.bin").write_bytes(b"modified runtime bytes\n")

    audit = audit_vendor_manifest(tmp_path)

    assert not audit.integrity_ok
    assert "sha256_mismatch:fixture-runtime:runtime.bin" in audit.integrity_issues


def test_vendor_inventory_keeps_unverified_distribution_as_release_blocker(tmp_path):
    payload = b"runtime bytes\n"
    component = _component("runtime.bin", payload, distribution_status="evidence_pending")
    _write_manifest(tmp_path, component)
    (tmp_path / "vendor" / "runtime.bin").write_bytes(payload)

    audit = audit_vendor_manifest(tmp_path)

    assert audit.integrity_ok
    assert not audit.release_ready
    assert "distribution_evidence_pending:fixture-runtime" in audit.release_issues


def test_vendor_inventory_requires_complete_explicit_release_exclusion(tmp_path):
    payload = b"runtime bytes\n"
    component = _component("runtime.bin", payload, distribution_status="exclude_from_release_artifact")
    _write_manifest(tmp_path, component)
    (tmp_path / "vendor" / "runtime.bin").write_bytes(payload)

    audit = audit_vendor_manifest(tmp_path)

    assert audit.integrity_ok
    assert "missing_release_exclusion:fixture-runtime" in audit.release_issues
    assert "release_artifact_exclusion_required:fixture-runtime" in audit.release_issues


def test_vendor_inventory_rejects_partial_release_exclusion(tmp_path):
    payload = b"runtime bytes\n"
    component = _component("runtime.bin", payload, distribution_status="exclude_from_release_artifact")
    component["release_exclusion"] = {
        "paths": ["LICENSE.txt"],
        "reason": "Missing provenance.",
        "manual_verification": ["Verify the source before distribution."],
    }
    _write_manifest(tmp_path, component)
    (tmp_path / "vendor" / "runtime.bin").write_bytes(payload)

    audit = audit_vendor_manifest(tmp_path)

    assert audit.integrity_ok
    assert "uncovered_release_exclusion_file:fixture-runtime:runtime.bin" in audit.release_issues


def test_release_artifact_requires_ready_files_and_omits_excluded_files(tmp_path):
    root = tmp_path / "source"
    vendor = root / "vendor"
    vendor.mkdir(parents=True)
    ready_payload = b"ready runtime\n"
    excluded_payload = b"excluded runtime\n"
    ready = _component("ready.bin", ready_payload)
    ready["id"] = "ready-runtime"
    excluded = _component("excluded.bin", excluded_payload, distribution_status="exclude_from_release_artifact")
    excluded["id"] = "excluded-runtime"
    excluded["license_files"] = []
    excluded["files"] = [excluded["files"][0]]
    excluded["release_exclusion"] = {
        "paths": ["excluded.bin"],
        "reason": "Local provenance is incomplete.",
        "manual_verification": ["Verify release provenance before inclusion."],
    }
    (vendor / "LICENSE.txt").write_text("fixture license\n", encoding="utf-8")
    (vendor / "ready.bin").write_bytes(ready_payload)
    (vendor / "excluded.bin").write_bytes(excluded_payload)
    (vendor / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "components": [ready, excluded]}), encoding="utf-8"
    )

    artifact = tmp_path / "artifact"
    artifact_vendor = artifact / "vendor"
    artifact_vendor.mkdir(parents=True)
    (artifact_vendor / "ready.bin").write_bytes(ready_payload)
    (artifact_vendor / "LICENSE.txt").write_text("fixture license\n", encoding="utf-8")
    (artifact_vendor / "manifest.json").write_text(
        (vendor / "manifest.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    audit = audit_vendor_manifest(root)
    assert audit_release_artifact(artifact, manifest_project_root=root) == ()
    assert release_artifact_ready(audit, ())

    (artifact_vendor / "excluded.bin").write_bytes(excluded_payload)
    issues = audit_release_artifact(artifact, manifest_project_root=root)
    assert "excluded_vendor_path_present:excluded-runtime:excluded.bin" in issues
    assert not release_artifact_ready(audit, issues)

    (artifact_vendor / "excluded.bin").unlink()
    (artifact_vendor / "ready.bin").unlink()
    issues = audit_release_artifact(artifact, manifest_project_root=root)
    assert "release_vendor_file_missing:ready-runtime:ready.bin" in issues


def test_release_artifact_rejects_unregistered_vendor_file(tmp_path):
    root = tmp_path / "source"
    vendor = root / "vendor"
    vendor.mkdir(parents=True)
    payload = b"ready runtime\n"
    component = _component("runtime.bin", payload)
    (vendor / "LICENSE.txt").write_text("fixture license\n", encoding="utf-8")
    source_manifest = {"schema_version": 1, "components": [component]}
    (vendor / "manifest.json").write_text(json.dumps(source_manifest), encoding="utf-8")

    artifact_vendor = tmp_path / "artifact" / "vendor"
    artifact_vendor.mkdir(parents=True)
    (artifact_vendor / "runtime.bin").write_bytes(payload)
    (artifact_vendor / "LICENSE.txt").write_text("fixture license\n", encoding="utf-8")
    (artifact_vendor / "manifest.json").write_text(json.dumps(source_manifest), encoding="utf-8")
    (artifact_vendor / "unexpected.bin").write_bytes(b"not declared\n")

    issues = audit_release_artifact(tmp_path / "artifact", manifest_project_root=root)

    assert "unexpected_release_vendor_file:unexpected.bin" in issues
