"""Validate the provenance and integrity record for bundled vendor payloads.

This module is intentionally release-facing rather than part of runtime tool
resolution.  It never executes a vendor binary and it never writes to a run
directory.  Normal validation detects accidental file drift; the stricter
release view additionally reports components whose source, version, or
distribution terms still need evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from willy._paths import get_project_root


MANIFEST_RELATIVE_PATH = Path("vendor") / "manifest.json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_READY = "release_ready"
_EVIDENCE_PENDING = "evidence_pending"
_EXCLUDED_FROM_RELEASE = "exclude_from_release_artifact"


@dataclass(frozen=True)
class VendorComponentAudit:
    """Redacted result for one declared bundled component."""

    component_id: str
    distribution_status: str
    release_excluded_paths: tuple[str, ...]
    integrity_issues: tuple[str, ...]
    release_issues: tuple[str, ...]


@dataclass(frozen=True)
class VendorManifestAudit:
    """The result of an entirely read-only vendor manifest validation."""

    manifest_path: Path
    components: tuple[VendorComponentAudit, ...]
    manifest_issues: tuple[str, ...]

    @property
    def integrity_issues(self) -> tuple[str, ...]:
        issues = list(self.manifest_issues)
        for component in self.components:
            issues.extend(component.integrity_issues)
        return tuple(issues)

    @property
    def release_issues(self) -> tuple[str, ...]:
        issues = list(self.integrity_issues)
        for component in self.components:
            issues.extend(component.release_issues)
        return tuple(issues)

    @property
    def integrity_ok(self) -> bool:
        return not self.integrity_issues

    @property
    def release_ready(self) -> bool:
        return not self.release_issues

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest": str(self.manifest_path),
            "integrity_ok": self.integrity_ok,
            "release_ready": self.release_ready,
            "integrity_issues": list(self.integrity_issues),
            "release_issues": list(self.release_issues),
            "components": [
                {
                    "component_id": component.component_id,
                    "distribution_status": component.distribution_status,
                    "release_excluded_paths": list(component.release_excluded_paths),
                    "integrity_issues": list(component.integrity_issues),
                    "release_issues": list(component.release_issues),
                }
                for component in self.components
            ],
        }


def _project_root(project_root: str | Path | None) -> Path:
    return Path(project_root).resolve() if project_root is not None else get_project_root()


def _safe_vendor_file(vendor_root: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        return None
    candidate = (vendor_root / value).resolve()
    try:
        candidate.relative_to(vendor_root.resolve())
    except ValueError:
        return None
    return candidate


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _release_exclusion_paths(component: Mapping[str, object]) -> tuple[str, ...]:
    exclusion = component.get("release_exclusion")
    if not isinstance(exclusion, Mapping):
        return ()
    raw_paths = exclusion.get("paths")
    if not isinstance(raw_paths, list):
        return ()
    return tuple(value for value in raw_paths if isinstance(value, str))


def _is_valid_exclusion_path(value: object) -> bool:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        return False
    parts = Path(value.rstrip("/")).parts
    return bool(parts) and all(part not in {"", ".", ".."} for part in parts)


def _path_is_excluded(path: str, excluded_paths: tuple[str, ...]) -> bool:
    return any(
        path.startswith(excluded) if excluded.endswith("/") else path == excluded
        for excluded in excluded_paths
    )


def _release_artifact_issues(component: Mapping[str, object], artifact_root: Path) -> list[str]:
    """Check a prepared artifact without executing or altering its contents."""
    component_id = component.get("id")
    if not isinstance(component_id, str) or not component_id:
        return ["invalid_component_id"]

    status = component.get("distribution_status")
    files = component.get("files")
    if not isinstance(files, list):
        return [f"missing_file_inventory:{component_id}"]

    issues: list[str] = []
    artifact_vendor = artifact_root / "vendor"
    if status == _EXCLUDED_FROM_RELEASE:
        for excluded_path in _release_exclusion_paths(component):
            if not _is_valid_exclusion_path(excluded_path):
                continue
            candidate = artifact_vendor / excluded_path
            if candidate.exists():
                issues.append(f"excluded_vendor_path_present:{component_id}:{excluded_path}")
        return issues

    if status != _RELEASE_READY:
        return [f"unreleasable_vendor_component:{component_id}:{status}"]

    for record in files:
        if not isinstance(record, Mapping):
            continue
        relative = record.get("path")
        if not isinstance(relative, str):
            continue
        candidate = _safe_vendor_file(artifact_vendor, relative)
        if candidate is None:
            issues.append(f"unsafe_release_vendor_file:{component_id}:{relative}")
        elif candidate.is_symlink() or not candidate.is_file():
            issues.append(f"release_vendor_file_missing:{component_id}:{relative}")
        else:
            expected_size = record.get("size_bytes")
            if isinstance(expected_size, int) and candidate.stat().st_size != expected_size:
                issues.append(f"release_vendor_file_size_mismatch:{component_id}:{relative}")
            expected_hash = record.get("sha256")
            if isinstance(expected_hash, str) and _SHA256.fullmatch(expected_hash):
                if _sha256_file(candidate) != expected_hash:
                    issues.append(f"release_vendor_file_sha256_mismatch:{component_id}:{relative}")
    return issues


def _release_metadata_issues(component_id: str, component: Mapping[str, object], vendor_root: Path) -> list[str]:
    status = component.get("distribution_status")
    if status == _EVIDENCE_PENDING:
        return [f"distribution_evidence_pending:{component_id}"]
    if status == _EXCLUDED_FROM_RELEASE:
        issues: list[str] = []
        exclusion = component.get("release_exclusion")
        excluded_paths = _release_exclusion_paths(component)
        if not isinstance(exclusion, Mapping):
            issues.append(f"missing_release_exclusion:{component_id}")
        else:
            reason = exclusion.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                issues.append(f"missing_release_exclusion_reason:{component_id}")
            checks = exclusion.get("manual_verification")
            if not isinstance(checks, list) or not checks or not all(
                isinstance(value, str) and value.strip() for value in checks
            ):
                issues.append(f"missing_release_exclusion_manual_verification:{component_id}")
        if not excluded_paths:
            issues.append(f"missing_release_exclusion_paths:{component_id}")
        else:
            for path in excluded_paths:
                if not _is_valid_exclusion_path(path):
                    issues.append(f"invalid_release_exclusion_path:{component_id}:{path}")
        files = component.get("files")
        if isinstance(files, list) and excluded_paths:
            for record in files:
                if isinstance(record, Mapping):
                    path = record.get("path")
                    if isinstance(path, str) and not _path_is_excluded(path, excluded_paths):
                        issues.append(f"uncovered_release_exclusion_file:{component_id}:{path}")
        # An exclusion describes intent only. It stays a release gate until a
        # package build proves the excluded paths are absent from its payload.
        issues.append(f"release_artifact_exclusion_required:{component_id}")
        return issues
    if status != _RELEASE_READY:
        return [f"invalid_distribution_status:{component_id}"]

    issues: list[str] = []
    for field in ("source_url", "version"):
        value = component.get(field)
        if not isinstance(value, str) or not value or value == "unverified":
            issues.append(f"missing_release_metadata:{component_id}:{field}")

    license_files = component.get("license_files")
    if not isinstance(license_files, list) or not license_files:
        issues.append(f"missing_release_metadata:{component_id}:license_files")
    else:
        for value in license_files:
            path = _safe_vendor_file(vendor_root, value)
            if path is None or not path.is_file():
                issues.append(f"missing_license_file:{component_id}:{value}")
    return issues


def _audit_component(component: object, vendor_root: Path) -> VendorComponentAudit:
    if not isinstance(component, Mapping):
        return VendorComponentAudit("<invalid>", "", (), ("invalid_component_record",), ())

    component_id = component.get("id")
    if not isinstance(component_id, str) or not component_id:
        return VendorComponentAudit("<invalid>", "", (), ("invalid_component_id",), ())

    status = component.get("distribution_status")
    status_text = status if isinstance(status, str) else ""
    integrity_issues: list[str] = []
    files = component.get("files")
    if not isinstance(files, list) or not files:
        integrity_issues.append(f"missing_file_inventory:{component_id}")
    else:
        seen_paths: set[str] = set()
        for record in files:
            if not isinstance(record, Mapping):
                integrity_issues.append(f"invalid_file_record:{component_id}")
                continue
            relative = record.get("path")
            if not isinstance(relative, str) or relative in seen_paths:
                integrity_issues.append(f"invalid_file_path:{component_id}:{relative}")
                continue
            seen_paths.add(relative)
            path = _safe_vendor_file(vendor_root, relative)
            if path is None:
                integrity_issues.append(f"unsafe_file_path:{component_id}:{relative}")
                continue
            if not path.is_file():
                integrity_issues.append(f"missing_vendor_file:{component_id}:{relative}")
                continue
            expected_size = record.get("size_bytes")
            if not isinstance(expected_size, int) or expected_size < 0:
                integrity_issues.append(f"invalid_file_size:{component_id}:{relative}")
            elif path.stat().st_size != expected_size:
                integrity_issues.append(f"size_mismatch:{component_id}:{relative}")
            expected_hash = record.get("sha256")
            if not isinstance(expected_hash, str) or not _SHA256.fullmatch(expected_hash):
                integrity_issues.append(f"invalid_sha256:{component_id}:{relative}")
            elif _sha256_file(path) != expected_hash:
                integrity_issues.append(f"sha256_mismatch:{component_id}:{relative}")

    return VendorComponentAudit(
        component_id,
        status_text,
        _release_exclusion_paths(component),
        tuple(integrity_issues),
        tuple(_release_metadata_issues(component_id, component, vendor_root)),
    )


def audit_vendor_manifest(project_root: str | Path | None = None) -> VendorManifestAudit:
    """Audit ``vendor/manifest.json`` without executing or modifying any file."""
    root = _project_root(project_root)
    manifest_path = root / MANIFEST_RELATIVE_PATH
    vendor_root = root / "vendor"
    issues: list[str] = []
    payload: Mapping[str, object] = {}
    try:
        parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        issues.append("vendor_manifest_missing")
    except (OSError, json.JSONDecodeError):
        issues.append("vendor_manifest_invalid_json")
    else:
        if not isinstance(parsed, Mapping):
            issues.append("vendor_manifest_not_object")
        else:
            payload = parsed

    if payload.get("schema_version") != 1:
        issues.append("unsupported_vendor_manifest_schema")
    components_raw = payload.get("components")
    components: list[VendorComponentAudit] = []
    if not isinstance(components_raw, list) or not components_raw:
        issues.append("vendor_manifest_components_missing")
    else:
        seen_ids: set[str] = set()
        for raw_component in components_raw:
            audited = _audit_component(raw_component, vendor_root)
            if audited.component_id in seen_ids:
                issues.append(f"duplicate_component_id:{audited.component_id}")
            seen_ids.add(audited.component_id)
            components.append(audited)

    return VendorManifestAudit(manifest_path, tuple(components), tuple(issues))


def release_ready_vendor_files(project_root: str | Path | None = None) -> tuple[str, ...]:
    """Return manifest-listed vendor files permitted in a release artifact."""
    root = _project_root(project_root)
    audit = audit_vendor_manifest(root)
    if not audit.integrity_ok:
        raise ValueError("vendor manifest integrity must pass before staging")
    try:
        payload = json.loads((root / MANIFEST_RELATIVE_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("vendor manifest is unavailable") from exc
    components = payload.get("components") if isinstance(payload, Mapping) else None
    if not isinstance(components, list):
        raise ValueError("vendor manifest components are unavailable")
    files: list[str] = []
    for component in components:
        if not isinstance(component, Mapping) or component.get("distribution_status") != _RELEASE_READY:
            continue
        records = component.get("files")
        if not isinstance(records, list):
            raise ValueError("release-ready vendor component has no file inventory")
        for record in records:
            path = record.get("path") if isinstance(record, Mapping) else None
            if not isinstance(path, str) or _safe_vendor_file(root / "vendor", path) is None:
                raise ValueError("release-ready vendor component has an unsafe file path")
            files.append(path)
    return tuple(dict.fromkeys(files))


def audit_release_artifact(
    artifact_root: str | Path,
    *,
    manifest_project_root: str | Path | None = None,
) -> tuple[str, ...]:
    """Return release-artifact vendor boundary violations.

    ``artifact_root`` is an already-built staging directory, not an archive and
    not the source checkout. The function never invokes vendor programs; it
    checks the inclusion/exclusion contract plus declared file sizes and
    SHA-256 values from the source manifest.
    """
    source_root = _project_root(manifest_project_root)
    artifact = Path(artifact_root).resolve()
    manifest_path = source_root / MANIFEST_RELATIVE_PATH
    try:
        parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ("vendor_manifest_unavailable_for_artifact_audit",)
    if not isinstance(parsed, Mapping) or not isinstance(parsed.get("components"), list):
        return ("vendor_manifest_unavailable_for_artifact_audit",)

    artifact_vendor = artifact / "vendor"
    issues: list[str] = []
    allowed_files = {"manifest.json"}
    for component in parsed["components"]:
        if isinstance(component, Mapping):
            issues.extend(_release_artifact_issues(component, artifact))
            if component.get("distribution_status") == _RELEASE_READY:
                files = component.get("files")
                if isinstance(files, list):
                    allowed_files.update(
                        record["path"]
                        for record in files
                        if isinstance(record, Mapping) and isinstance(record.get("path"), str)
                    )

    artifact_manifest = artifact_vendor / "manifest.json"
    if artifact_manifest.is_symlink() or not artifact_manifest.is_file():
        issues.append("release_vendor_manifest_missing")
    elif artifact_manifest.read_bytes() != manifest_path.read_bytes():
        issues.append("release_vendor_manifest_mismatch")

    if artifact_vendor.is_dir():
        for candidate in artifact_vendor.rglob("*"):
            if candidate.is_dir():
                continue
            relative = candidate.relative_to(artifact_vendor).as_posix()
            if candidate.is_symlink():
                issues.append(f"release_vendor_symlink_not_allowed:{relative}")
            elif relative not in allowed_files:
                issues.append(f"unexpected_release_vendor_file:{relative}")
    return tuple(issues)


def release_artifact_ready(audit: VendorManifestAudit, artifact_issues: tuple[str, ...]) -> bool:
    """Whether a concrete artifact closes declared exclusion-only release gates."""
    unresolved = [
        issue
        for issue in audit.release_issues
        if not issue.startswith("release_artifact_exclusion_required:")
    ]
    return audit.integrity_ok and not unresolved and not artifact_issues


def format_vendor_manifest_audit(audit: VendorManifestAudit) -> str:
    """Render an operator-safe audit summary without machine-local paths."""
    lines = [
        "Vendor manifest audit",
        f"integrity: {'ok' if audit.integrity_ok else 'failed'}",
        f"release readiness: {'ready' if audit.release_ready else 'blocked'}",
    ]
    for component in audit.components:
        result = "ok" if not component.integrity_issues else "integrity_failed"
        lines.append(f"- {component.component_id}: {result}; distribution={component.distribution_status}")
        if component.release_excluded_paths:
            lines.append(f"  excluded paths: {', '.join(component.release_excluded_paths)}")
    for issue in audit.release_issues:
        lines.append(f"  ! {issue}")
    return "\n".join(lines)
