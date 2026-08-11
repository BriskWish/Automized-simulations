"""Regression coverage for the release-facing vendor inventory."""

from __future__ import annotations

import hashlib
import json

from willy._paths import get_project_root
from willy.vendor_manifest import audit_vendor_manifest


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
    assert "distribution_evidence_pending:fixture-runtime:evidence_pending" in audit.release_issues
