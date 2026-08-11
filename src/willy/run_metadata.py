"""Schema-v2 storage for low-frequency, run-local workflow metadata.

``run_manifest.json`` has this fixed schema::

    {
      "schema_version": 2, "run_id": "md__...", "revision": 0,
      "sections": {
        "registry": {"revision": 0, "visibility": "public", "data": {...}},
        "provenance": {"revision": 0, "visibility": "private", "data": {...}},
        "topology": {"revision": 0, "visibility": "private", "data": {...}},
        "simulation": {"revision": 0, "visibility": "private", "data": {...}},
        "protocol": {"revision": 0, "visibility": "private", "data": {...}}
      }
    }

Use :func:`load_run_manifest` for the internal full record and
:func:`update_run_manifest_section` or :func:`update_run_manifest_sections`
for atomic changes.  :func:`load_run_metadata_section` provides a temporary
new-file-first, legacy-file fallback for internal layer adapters.  There
deliberately is no public-data projection API.

The document is deliberately separate from the hot control records:
``status.json``, ``mdrun_eta.json`` and ``pending_action.json`` continue to
have their own lifecycle and locking requirements.  This module is a storage
foundation only.  It exposes no public projection of private sections; callers
that cross a UI or LLM boundary must continue to use the relevant registry
APIs.

The v2 document keeps data produced by the existing manifest writers in
separate sections.  Legacy migration is explicit, terminal-run gated and
non-destructive, so rollout code can adopt it without changing an active run.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
import json

from willy.run_store import run_transaction


RUN_MANIFEST_FILENAME = "run_manifest.json"
RUN_MANIFEST_SCHEMA_VERSION = 2
RUN_MANIFEST_SECTIONS = ("registry", "provenance", "topology", "simulation", "protocol")
LEGACY_MANIFEST_FILENAMES = {
    "registry": "manifest.json",
    "provenance": "provenance.json",
    "topology": "topology_manifest.json",
    "simulation": "md_manifest.json",
}

_SECTION_VISIBILITY = {
    "registry": "public",
    "provenance": "private",
    "topology": "private",
    "simulation": "private",
    "protocol": "private",
}
_TERMINAL_STATES = frozenset({"done", "aborted"})
_LEGACY_NON_TERMINAL_STATES = frozenset({
    "idle", "running", "retrying", "awaiting_confirmation", "stopping", "escalated", "unknown",
})


class RunMetadataError(ValueError):
    """Raised when a unified run manifest is malformed or unsafe to change."""


class RunManifestRevisionConflict(RunMetadataError):
    """Raised when a caller writes against a stale manifest or section revision."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RunMetadataError(f"{label} 必须是非负整数")
    return value


def _require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RunMetadataError(f"{label} 必须是对象")
    # JSON round-tripping rejects non-serializable values and detaches caller
    # references before a later atomic write.
    try:
        copied = json.loads(json.dumps(dict(value), ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise RunMetadataError(f"{label} 必须是 JSON 可序列化对象") from exc
    if not isinstance(copied, dict):  # Defensive; json.loads is currently deterministic.
        raise RunMetadataError(f"{label} 必须是对象")
    return copied


def _run_directory(run_dir: str | Path) -> Path:
    directory = Path(run_dir)
    if not directory.is_dir():
        raise RunMetadataError("运行目录不存在")
    if not directory.name:
        raise RunMetadataError("运行目录名称无效")
    return directory


def _section_record(section: str, data: Mapping[str, Any], *, revision: int = 0, updated_at: str | None = None) -> dict[str, Any]:
    if section not in _SECTION_VISIBILITY:
        raise RunMetadataError(f"未知 metadata section: {section}")
    return {
        "revision": _require_nonnegative_int(revision, f"{section}.revision"),
        "updated_at": updated_at or _now(),
        "visibility": _SECTION_VISIBILITY[section],
        "data": _require_mapping(data, f"{section}.data"),
    }


def _new_manifest(
    run_id: str,
    *,
    sections: Mapping[str, Mapping[str, Any]] | None = None,
    migration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(run_id, str) or not run_id.strip():
        raise RunMetadataError("run_id 无效")
    supplied = _require_mapping(sections or {}, "sections")
    unknown = set(supplied) - set(RUN_MANIFEST_SECTIONS)
    if unknown:
        raise RunMetadataError(f"sections 包含未知字段: {', '.join(sorted(unknown))}")
    timestamp = _now()
    return {
        "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": timestamp,
        "updated_at": timestamp,
        "revision": 0,
        "migration": _require_mapping(migration, "migration") if migration is not None else None,
        "sections": {
            name: _section_record(name, supplied.get(name, {}), updated_at=timestamp)
            for name in RUN_MANIFEST_SECTIONS
        },
    }


def _validate_section_record(section: str, value: object) -> dict[str, Any]:
    payload = _require_mapping(value, f"sections.{section}")
    expected = {"revision", "updated_at", "visibility", "data"}
    if set(payload) != expected:
        raise RunMetadataError(f"sections.{section} 字段无效")
    if payload.get("visibility") != _SECTION_VISIBILITY[section]:
        raise RunMetadataError(f"sections.{section}.visibility 无效")
    revision = _require_nonnegative_int(payload.get("revision"), f"sections.{section}.revision")
    updated_at = payload.get("updated_at")
    if not isinstance(updated_at, str) or not updated_at:
        raise RunMetadataError(f"sections.{section}.updated_at 无效")
    return _section_record(section, payload.get("data"), revision=revision, updated_at=updated_at)


def validate_run_manifest(value: object, *, expected_run_id: str | None = None) -> dict[str, Any]:
    """Validate and detach a schema-v2 manifest before it is read or written."""
    payload = _require_mapping(value, "run_manifest")
    expected = {"schema_version", "run_id", "created_at", "updated_at", "revision", "migration", "sections"}
    if set(payload) != expected:
        raise RunMetadataError("run_manifest 字段无效")
    if payload.get("schema_version") != RUN_MANIFEST_SCHEMA_VERSION:
        raise RunMetadataError("run_manifest schema_version 无效")
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id or (expected_run_id is not None and run_id != expected_run_id):
        raise RunMetadataError("run_manifest run_id 无效")
    for field in ("created_at", "updated_at"):
        if not isinstance(payload.get(field), str) or not payload[field]:
            raise RunMetadataError(f"run_manifest {field} 无效")
    revision = _require_nonnegative_int(payload.get("revision"), "run_manifest revision")
    migration = payload.get("migration")
    if migration is not None:
        migration = _require_mapping(migration, "run_manifest migration")
    raw_sections = _require_mapping(payload.get("sections"), "run_manifest sections")
    if set(raw_sections) != set(RUN_MANIFEST_SECTIONS):
        raise RunMetadataError("run_manifest sections 不完整或包含未知字段")
    return {
        "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": payload["created_at"],
        "updated_at": payload["updated_at"],
        "revision": revision,
        "migration": migration,
        "sections": {
            name: _validate_section_record(name, raw_sections[name])
            for name in RUN_MANIFEST_SECTIONS
        },
    }


def _read_manifest_from_store(store: Any, directory: Path) -> dict[str, Any]:
    raw = store.read_json(RUN_MANIFEST_FILENAME)
    if raw is None:
        raise RunMetadataError("缺少 run_manifest.json")
    return validate_run_manifest(raw, expected_run_id=directory.name)


def _read_legacy_json(directory: Path, filename: str) -> dict[str, Any] | None:
    path = directory / filename
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunMetadataError(f"旧 metadata 文件不可读取: {filename}") from exc
    return _require_mapping(raw, f"旧 metadata 文件 {filename}")


def _legacy_source_info(directory: Path, filename: str) -> dict[str, Any]:
    path = directory / filename
    digest = sha256(path.read_bytes()).hexdigest()
    return {"path": filename, "sha256": digest, "size_bytes": path.stat().st_size}


def load_legacy_run_sections(run_dir: str | Path) -> dict[str, dict[str, Any]]:
    """Load legacy records into distinct v2 section payloads without writing.

    The MD record owns protocol timing in legacy form.  Its ``protocol`` field
    is extracted into a dedicated section so it cannot be overwritten by other
    simulation evidence after migration.
    """
    directory = _run_directory(run_dir)
    records = {
        section: _read_legacy_json(directory, filename)
        for section, filename in LEGACY_MANIFEST_FILENAMES.items()
    }
    md = records["simulation"] or {}
    simulation = dict(md)
    protocol = simulation.pop("protocol", {})
    if not isinstance(protocol, Mapping):
        raise RunMetadataError("旧 MD manifest 的 protocol 必须是对象")
    return {
        "registry": records["registry"] or {},
        "provenance": records["provenance"] or {},
        "topology": records["topology"] or {},
        "simulation": simulation,
        "protocol": _require_mapping(protocol, "旧 MD protocol"),
    }


def load_run_metadata_sections(run_dir: str | Path) -> dict[str, dict[str, Any]]:
    """Load complete internal section data, preferring v2 over legacy files.

    This compatibility reader is for migration-period layer adapters only.
    It never treats an invalid v2 document as a reason to fall back to stale
    legacy records: once ``run_manifest.json`` exists, its integrity is the
    authoritative condition.
    """
    directory = _run_directory(run_dir)
    if (directory / RUN_MANIFEST_FILENAME).is_file():
        manifest = load_run_manifest(directory)
        return {
            section: deepcopy(manifest["sections"][section]["data"])
            for section in RUN_MANIFEST_SECTIONS
        }
    return load_legacy_run_sections(directory)


def load_run_metadata_section(run_dir: str | Path, section: str) -> dict[str, Any]:
    """Load one full internal section, preferring v2 and otherwise legacy."""
    if section not in _SECTION_VISIBILITY:
        raise RunMetadataError(f"未知 metadata section: {section}")
    return load_run_metadata_sections(run_dir)[section]


def _migration_allowed(directory: Path, *, allow_active: bool) -> None:
    if allow_active:
        return
    status = _read_legacy_json(directory, "status.json")
    if status is None:
        raise RunMetadataError("缺少 status.json，无法证明工程已终态，拒绝迁移 run_manifest")
    state = status.get("state")
    if state not in _TERMINAL_STATES:
        if state in _LEGACY_NON_TERMINAL_STATES or not isinstance(state, str):
            raise RunMetadataError("仅终态工程可以迁移 run_manifest；当前工程仍在运行、等待确认或未完成")
        raise RunMetadataError("运行状态未知，拒绝迁移 run_manifest")
    pending = _read_legacy_json(directory, "pending_action.json")
    if pending is not None and pending.get("state") in {"pending", "validated", "executing"}:
        raise RunMetadataError("存在待确认或待执行动作，拒绝迁移 run_manifest")


class RunMetadataStore:
    """Run-local schema-v2 manifest store with global and section CAS revisions."""

    def __init__(self, run_dir: str | Path):
        self.directory = _run_directory(run_dir)

    @property
    def path(self) -> Path:
        return self.directory / RUN_MANIFEST_FILENAME

    def load(self) -> dict[str, Any]:
        with run_transaction(self.directory) as store:
            store.recover_pending_bundle()
            return _read_manifest_from_store(store, self.directory)

    def create(
        self,
        *,
        sections: Mapping[str, Mapping[str, Any]] | None = None,
        migration: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create one v2 document; never overwrite an existing manifest."""
        with run_transaction(self.directory) as store:
            store.recover_pending_bundle()
            if store.read_json(RUN_MANIFEST_FILENAME) is not None:
                raise RunMetadataError("run_manifest.json 已存在")
            payload = _new_manifest(self.directory.name, sections=sections, migration=migration)
            store.write_json(RUN_MANIFEST_FILENAME, payload)
            return deepcopy(payload)

    def update_section(
        self,
        section: str,
        data: Mapping[str, Any],
        *,
        expected_revision: int | None = None,
        expected_section_revision: int | None = None,
    ) -> dict[str, Any]:
        """Replace exactly one section under one RunStore transaction.

        Both compare-and-swap guards are optional, but integrations that react
        to user controls should pass both to reject stale browser or agent work.
        """
        if section not in _SECTION_VISIBILITY:
            raise RunMetadataError(f"未知 metadata section: {section}")
        clean_data = _require_mapping(data, f"{section}.data")
        if expected_revision is not None:
            _require_nonnegative_int(expected_revision, "expected_revision")
        if expected_section_revision is not None:
            _require_nonnegative_int(expected_section_revision, "expected_section_revision")
        with run_transaction(self.directory) as store:
            store.recover_pending_bundle()
            payload = _read_manifest_from_store(store, self.directory)
            actual_revision = payload["revision"]
            actual_section_revision = payload["sections"][section]["revision"]
            if expected_revision is not None and actual_revision != expected_revision:
                raise RunManifestRevisionConflict("run_manifest 已更新，请刷新后重试")
            if expected_section_revision is not None and actual_section_revision != expected_section_revision:
                raise RunManifestRevisionConflict(f"{section} section 已更新，请刷新后重试")
            timestamp = _now()
            payload["revision"] = actual_revision + 1
            payload["updated_at"] = timestamp
            payload["sections"][section] = _section_record(
                section,
                clean_data,
                revision=actual_section_revision + 1,
                updated_at=timestamp,
            )
            validated = validate_run_manifest(payload, expected_run_id=self.directory.name)
            store.write_json(RUN_MANIFEST_FILENAME, validated)
            return deepcopy(validated)

    def update_sections(
        self,
        payloads: Mapping[str, Mapping[str, Any]],
        *,
        expected_revision: int | None = None,
        expected_section_revisions: Mapping[str, int] | None = None,
    ) -> dict[str, Any]:
        """Atomically replace multiple sections in one revisioned write.

        ``expected_section_revisions`` only needs entries for the sections a
        caller wants to guard.  A supplied entry for an untouched or unknown
        section is rejected, making accidental cross-layer writes visible.
        """
        clean_payloads = _require_mapping(payloads, "section payloads")
        if not clean_payloads:
            raise RunMetadataError("至少需要更新一个 metadata section")
        unknown = set(clean_payloads) - set(RUN_MANIFEST_SECTIONS)
        if unknown:
            raise RunMetadataError(f"sections 包含未知字段: {', '.join(sorted(unknown))}")
        clean_payloads = {
            section: _require_mapping(data, f"{section}.data")
            for section, data in clean_payloads.items()
        }
        if expected_revision is not None:
            _require_nonnegative_int(expected_revision, "expected_revision")
        expected_revisions = _require_mapping(
            expected_section_revisions or {},
            "expected_section_revisions",
        )
        if set(expected_revisions) - set(clean_payloads):
            raise RunMetadataError("expected_section_revisions 只能约束本次更新的 section")
        for section, revision in expected_revisions.items():
            _require_nonnegative_int(revision, f"expected_section_revisions.{section}")

        with run_transaction(self.directory) as store:
            store.recover_pending_bundle()
            payload = _read_manifest_from_store(store, self.directory)
            actual_revision = payload["revision"]
            if expected_revision is not None and actual_revision != expected_revision:
                raise RunManifestRevisionConflict("run_manifest 已更新，请刷新后重试")
            for section, expected in expected_revisions.items():
                if payload["sections"][section]["revision"] != expected:
                    raise RunManifestRevisionConflict(f"{section} section 已更新，请刷新后重试")
            timestamp = _now()
            payload["revision"] = actual_revision + 1
            payload["updated_at"] = timestamp
            for section, data in clean_payloads.items():
                payload["sections"][section] = _section_record(
                    section,
                    data,
                    revision=payload["sections"][section]["revision"] + 1,
                    updated_at=timestamp,
                )
            validated = validate_run_manifest(payload, expected_run_id=self.directory.name)
            store.write_json(RUN_MANIFEST_FILENAME, validated)
            return deepcopy(validated)

    def migrate_legacy(
        self,
        *,
        allow_active: bool = False,
        delete_legacy: bool = False,
    ) -> dict[str, Any]:
        """Explicitly create v2 metadata from legacy files without deleting them.

        ``delete_legacy`` is intentionally unsupported during this foundation
        phase.  A later, separately audited cleanup command can add deletion
        after all readers are migrated.
        """
        if delete_legacy is not False:
            raise RunMetadataError("当前迁移只支持 delete_legacy=False，不会删除旧 metadata 文件")
        _migration_allowed(self.directory, allow_active=allow_active)
        with run_transaction(self.directory) as store:
            store.recover_pending_bundle()
            existing = store.read_json(RUN_MANIFEST_FILENAME)
            if existing is not None:
                return _read_manifest_from_store(store, self.directory)
            sections = load_legacy_run_sections(self.directory)
            legacy_files = [
                _legacy_source_info(self.directory, filename)
                for filename in LEGACY_MANIFEST_FILENAMES.values()
                if (self.directory / filename).is_file()
            ]
            migration = {
                "kind": "legacy_manifest_v1",
                "migrated_at": _now(),
                "delete_legacy": False,
                "legacy_files": legacy_files,
            }
            payload = _new_manifest(self.directory.name, sections=sections, migration=migration)
            store.write_json(RUN_MANIFEST_FILENAME, payload)
            return deepcopy(payload)


def load_run_manifest(run_dir: str | Path) -> dict[str, Any]:
    """Load a validated v2 manifest.  This is an internal full-record API."""
    return RunMetadataStore(run_dir).load()


def create_run_manifest(
    run_dir: str | Path,
    *,
    sections: Mapping[str, Mapping[str, Any]] | None = None,
    migration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a blank or explicitly populated v2 manifest."""
    return RunMetadataStore(run_dir).create(sections=sections, migration=migration)


def update_run_manifest_section(
    run_dir: str | Path,
    section: str,
    data: Mapping[str, Any],
    *,
    expected_revision: int | None = None,
    expected_section_revision: int | None = None,
) -> dict[str, Any]:
    """Atomically replace one v2 section with optional global/section CAS."""
    return RunMetadataStore(run_dir).update_section(
        section,
        data,
        expected_revision=expected_revision,
        expected_section_revision=expected_section_revision,
    )


def update_run_manifest_sections(
    run_dir: str | Path,
    payloads: Mapping[str, Mapping[str, Any]],
    *,
    expected_revision: int | None = None,
    expected_section_revisions: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Atomically replace several v2 sections with global/section CAS guards."""
    return RunMetadataStore(run_dir).update_sections(
        payloads,
        expected_revision=expected_revision,
        expected_section_revisions=expected_section_revisions,
    )


def migrate_legacy_run_manifest(
    run_dir: str | Path,
    *,
    allow_active: bool = False,
    delete_legacy: bool = False,
) -> dict[str, Any]:
    """Explicitly and non-destructively migrate a legacy terminal run."""
    return RunMetadataStore(run_dir).migrate_legacy(
        allow_active=allow_active,
        delete_legacy=delete_legacy,
    )
