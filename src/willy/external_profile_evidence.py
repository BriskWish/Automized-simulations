"""Read-only evidence contracts for the four real ten-step acceptance profiles.

This module deliberately *does not* start, resume, inspect, or alter a
pipeline.  An acceptance operator supplies one already-finished run directory
and receives a bounded JSON record suitable for external-profile evidence.
The record never includes configuration values, log content, commands,
absolute paths, environment values, or credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence
import argparse
import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET

from willy.run_metadata import RunMetadataError, load_run_manifest


PROFILE_EVIDENCE_SCHEMA_VERSION = 1
_PROFILE_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$")
_DONE_STEPS = tuple(range(1, 11))
_FORBIDDEN_EVIDENCE_KEY = re.compile(
    r"(?:api.?key|secret|token|password|credential|private.?key|"
    r"raw|log|stderr|stdout|traceback|command|config|path|environment|env)",
    re.IGNORECASE,
)

_STAGE_ARTIFACTS: Mapping[str, tuple[str, ...]] = {
    "em": ("tpr", "gro", "xtc", "edr"),
    "eq": ("tpr", "gro", "xtc", "edr", "cpt"),
    "prod": ("tpr", "gro", "xtc", "edr", "cpt"),
}
_STAGE_STATUS = {"em": "accepted", "eq": "accepted", "prod": "completed"}
_COMMON_ARTIFACTS = ("topol.top", "model.pdb")


@dataclass(frozen=True)
class ExternalProfile:
    """One permitted quantum/topology route for full external acceptance."""

    profile_id: str
    quantum_backend: str
    topology_backend: str
    topology_label: str
    forcefield_family: str


EXTERNAL_PROFILES: tuple[ExternalProfile, ...] = (
    ExternalProfile("g16_sobtop_electrolyte", "g16", "sobtop", "Sobtop", "gaff_uff"),
    ExternalProfile("orca_sobtop_electrolyte", "orca", "sobtop", "Sobtop", "gaff_uff"),
    ExternalProfile("g16_ligpargen_solvents", "g16", "oplsaa", "LigParGen", "oplsaa"),
    ExternalProfile("orca_ligpargen_solvents", "orca", "oplsaa", "LigParGen", "oplsaa"),
)
_PROFILES_BY_ID = {profile.profile_id: profile for profile in EXTERNAL_PROFILES}


@dataclass(frozen=True)
class ProfileValidation:
    """A deterministic validation result; ``evidence`` is public-safe."""

    profile_id: str
    accepted: bool
    issues: tuple[str, ...]
    evidence: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "accepted": self.accepted,
            "issues": list(self.issues),
            "evidence": dict(self.evidence),
        }


def get_external_profile(profile_id: str) -> ExternalProfile:
    """Return a declared profile instead of accepting an ad-hoc route."""
    try:
        return _PROFILES_BY_ID[profile_id]
    except KeyError as exc:
        raise ValueError(f"未知外部 profile: {profile_id}") from exc


def selected_external_profiles(raw: str | None = None) -> tuple[ExternalProfile, ...]:
    """Resolve comma-separated profile IDs, with ``all`` as the explicit set."""
    value = (raw or "all").strip()
    if not value or value.lower() == "all":
        return EXTERNAL_PROFILES
    identifiers = tuple(part.strip() for part in value.split(",") if part.strip())
    if not identifiers:
        raise ValueError("外部 profile 列表为空")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("外部 profile 列表包含重复项")
    return tuple(get_external_profile(identifier) for identifier in identifiers)


def _read_json_file(path: Path, *, maximum_bytes: int) -> Mapping[str, Any] | None:
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > maximum_bytes:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def _file_record(run_dir: Path, name: str) -> tuple[dict[str, object] | None, str | None]:
    """Hash one fixed in-run artifact, rejecting links outside the run."""
    path = run_dir / name
    if not path.is_file():
        return None, f"artifact_missing:{name}"
    if path.is_symlink():
        return None, f"artifact_symlink_rejected:{name}"
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(run_dir)
    except (OSError, ValueError):
        return None, f"artifact_outside_run_rejected:{name}"
    digest = sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        size = path.stat().st_size
    except OSError:
        return None, f"artifact_unreadable:{name}"
    if size <= 0:
        return None, f"artifact_empty:{name}"
    return {"name": name, "size_bytes": size, "sha256": digest.hexdigest()}, None


def _as_nonempty_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _stage_output_matches(
    stage: str,
    record: Mapping[str, Any],
    artifact_by_name: Mapping[str, Mapping[str, object]],
) -> tuple[str, ...]:
    """Bind the private stage record to fixed, locally rehashed artifacts."""
    issues: list[str] = []
    outputs = record.get("outputs")
    if not isinstance(outputs, Mapping):
        return (f"stage_outputs_missing:{stage}",)
    for kind in _STAGE_ARTIFACTS[stage]:
        expected_name = f"{stage}.{kind}"
        saved = outputs.get(kind)
        local = artifact_by_name.get(expected_name)
        if not isinstance(saved, Mapping) or local is None:
            issues.append(f"stage_output_evidence_missing:{expected_name}")
            continue
        if saved.get("path") != expected_name:
            issues.append(f"stage_output_name_mismatch:{expected_name}")
        if saved.get("size_bytes") != local["size_bytes"]:
            issues.append(f"stage_output_size_mismatch:{expected_name}")
        if saved.get("sha256") != local["sha256"]:
            issues.append(f"stage_output_hash_mismatch:{expected_name}")
    return tuple(issues)


def _safe_software_summary(provenance: Mapping[str, Any], issues: list[str]) -> dict[str, object]:
    source = provenance.get("source_revision")
    runtime = provenance.get("runtime")
    source = source if isinstance(source, Mapping) else {}
    runtime = runtime if isinstance(runtime, Mapping) else {}
    commit = _as_nonempty_str(source.get("commit"))
    dirty = source.get("dirty")
    version = _as_nonempty_str(runtime.get("willy"))
    if not _COMMIT.fullmatch(commit):
        issues.append("provenance_commit_missing")
    if not isinstance(dirty, bool):
        issues.append("provenance_dirty_state_missing")
    elif dirty:
        issues.append("provenance_dirty_worktree")
    if not _VERSION.fullmatch(version):
        issues.append("provenance_project_version_missing")
    return {
        "source_commit": commit if _COMMIT.fullmatch(commit) else "unavailable",
        "worktree_clean": dirty is False,
        "project_version": version if _VERSION.fullmatch(version) else "unavailable",
    }


def validate_finished_profile(
    profile_id: str,
    run_dir: str | Path,
) -> ProfileValidation:
    """Validate one completed run without invoking a scientific executable.

    The validation is intentionally terminal-only: an active, failed, or
    partially completed workspace can never produce passing external evidence.
    """
    profile = get_external_profile(profile_id)
    directory = Path(run_dir).expanduser().resolve()
    issues: list[str] = []
    if not directory.is_dir():
        evidence = _empty_evidence(profile)
        return ProfileValidation(profile.profile_id, False, ("run_directory_unavailable",), evidence)
    if not _PROFILE_ID.fullmatch(directory.name):
        issues.append("run_id_invalid")

    status = _read_json_file(directory / "status.json", maximum_bytes=256 * 1024)
    if status is None:
        issues.append("status_unavailable")
        status = {}
    state = _as_nonempty_str(status.get("state"))
    total_steps = status.get("total_steps")
    done_steps = status.get("done_steps")
    normalized_done = tuple(done_steps) if isinstance(done_steps, list) else ()
    if state != "done":
        issues.append("final_state_not_done")
    if total_steps != len(_DONE_STEPS):
        issues.append("total_steps_not_ten")
    if normalized_done != _DONE_STEPS:
        issues.append("done_steps_incomplete")
    if _as_nonempty_str(status.get("error")) or _as_nonempty_str(status.get("error_kind")):
        issues.append("final_status_contains_error")

    # A manual typo must never turn this evidence tool into an inspector for
    # an active workspace.  The terminal public status is the only fact read
    # before this point; all private metadata and artifact hashing are gated
    # behind an unambiguous completed state.
    if state != "done":
        evidence = _empty_evidence(profile)
        if _PROFILE_ID.fullmatch(directory.name):
            evidence["run_id"] = directory.name
        evidence["workflow"] = {
            "state": state or "unavailable",
            "total_steps": total_steps if isinstance(total_steps, int) else 0,
            "done_steps": list(normalized_done) if all(isinstance(step, int) for step in normalized_done) else [],
            "stage_states": {},
        }
        return ProfileValidation(profile.profile_id, False, tuple(dict.fromkeys(issues)), evidence)

    manifest_path = directory / "run_manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        issues.append("run_manifest_unavailable")
        manifest: Mapping[str, Any] = {}
    else:
        try:
            manifest = load_run_manifest(directory)
        except (RunMetadataError, OSError, ValueError):
            issues.append("run_manifest_invalid")
            manifest = {}
    sections = manifest.get("sections") if isinstance(manifest, Mapping) else {}
    sections = sections if isinstance(sections, Mapping) else {}
    registry = _section_data(sections, "registry", issues)
    provenance = _section_data(sections, "provenance", issues)
    topology = _section_data(sections, "topology", issues)
    simulation = _section_data(sections, "simulation", issues)

    if manifest and manifest.get("run_id") != directory.name:
        issues.append("manifest_run_id_mismatch")
    if registry.get("backend") != profile.quantum_backend:
        issues.append("quantum_backend_mismatch")
    if topology.get("backend") != profile.topology_backend:
        issues.append("topology_backend_mismatch")
    if topology.get("forcefield_family") != profile.forcefield_family:
        issues.append("forcefield_family_mismatch")
    components = topology.get("components")
    if not isinstance(components, list) or not components:
        issues.append("topology_components_missing")
    elif any(
        not isinstance(component, Mapping)
        or component.get("success") is not True
        or component.get("validated") is not True
        for component in components
    ):
        issues.append("topology_component_unvalidated")

    software = _safe_software_summary(provenance, issues)
    stages = simulation.get("stages") if isinstance(simulation.get("stages"), Mapping) else {}
    artifacts: list[dict[str, object]] = []
    artifact_by_name: dict[str, Mapping[str, object]] = {}
    for name in (*_COMMON_ARTIFACTS, *(f"{stage}.{kind}" for stage, kinds in _STAGE_ARTIFACTS.items() for kind in kinds)):
        record, error = _file_record(directory, name)
        if error:
            issues.append(error)
        elif record is not None:
            artifacts.append(record)
            artifact_by_name[name] = record
    stage_states: dict[str, str] = {}
    for stage, expected_status in _STAGE_STATUS.items():
        record = stages.get(stage)
        if not isinstance(record, Mapping):
            issues.append(f"stage_record_missing:{stage}")
            stage_states[stage] = "unavailable"
            continue
        actual_status = _as_nonempty_str(record.get("status"))
        stage_states[stage] = actual_status or "unavailable"
        if actual_status != expected_status:
            issues.append(f"stage_status_invalid:{stage}")
        issues.extend(_stage_output_matches(stage, record, artifact_by_name))

    evidence = {
        "schema_version": PROFILE_EVIDENCE_SCHEMA_VERSION,
        "profile": {
            "profile_id": profile.profile_id,
            "quantum_backend": profile.quantum_backend,
            "topology_backend": profile.topology_label,
        },
        "run_id": directory.name if _PROFILE_ID.fullmatch(directory.name) else "unavailable",
        "software": software,
        "workflow": {
            "state": state or "unavailable",
            "total_steps": total_steps if isinstance(total_steps, int) else 0,
            "done_steps": list(normalized_done) if all(isinstance(step, int) for step in normalized_done) else [],
            "stage_states": stage_states,
        },
        "artifacts": artifacts,
        "conclusion": "passed" if not issues else "failed",
    }
    return ProfileValidation(profile.profile_id, not issues, tuple(dict.fromkeys(issues)), evidence)


def _section_data(sections: Mapping[str, Any], name: str, issues: list[str]) -> Mapping[str, Any]:
    section = sections.get(name)
    if not isinstance(section, Mapping) or not isinstance(section.get("data"), Mapping):
        issues.append(f"manifest_section_missing:{name}")
        return {}
    return section["data"]


def _empty_evidence(profile: ExternalProfile) -> dict[str, object]:
    return {
        "schema_version": PROFILE_EVIDENCE_SCHEMA_VERSION,
        "profile": {
            "profile_id": profile.profile_id,
            "quantum_backend": profile.quantum_backend,
            "topology_backend": profile.topology_label,
        },
        "run_id": "unavailable",
        "software": {
            "source_commit": "unavailable",
            "worktree_clean": False,
            "project_version": "unavailable",
        },
        "workflow": {"state": "unavailable", "total_steps": 0, "done_steps": [], "stage_states": {}},
        "artifacts": [],
        "conclusion": "failed",
    }


def _expected_artifact_names() -> frozenset[str]:
    names = set(_COMMON_ARTIFACTS)
    for stage, kinds in _STAGE_ARTIFACTS.items():
        names.update(f"{stage}.{kind}" for kind in kinds)
    return frozenset(names)


_EXPECTED_ARTIFACT_NAMES = _expected_artifact_names()


def _validate_evidence_payload(value: object, *, expected_profile: ExternalProfile | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("profile evidence 必须是对象")
    payload = json.loads(json.dumps(dict(value), ensure_ascii=False))
    expected_keys = {"schema_version", "profile", "run_id", "software", "workflow", "artifacts", "conclusion"}
    if set(payload) != expected_keys or payload.get("schema_version") != PROFILE_EVIDENCE_SCHEMA_VERSION:
        raise ValueError("profile evidence schema 无效")
    for key in payload:
        if _FORBIDDEN_EVIDENCE_KEY.search(key):
            raise ValueError("profile evidence 包含禁止字段")
    profile = payload.get("profile")
    software = payload.get("software")
    workflow = payload.get("workflow")
    artifacts = payload.get("artifacts")
    if not isinstance(profile, Mapping) or not isinstance(software, Mapping) or not isinstance(workflow, Mapping):
        raise ValueError("profile evidence 主体无效")
    if set(profile) != {"profile_id", "quantum_backend", "topology_backend"}:
        raise ValueError("profile evidence profile 包含禁止字段")
    if set(software) != {"source_commit", "worktree_clean", "project_version"}:
        raise ValueError("profile evidence 软件摘要包含禁止字段")
    if set(workflow) != {"state", "total_steps", "done_steps", "stage_states"}:
        raise ValueError("profile evidence 工作流摘要包含禁止字段")
    profile_id = profile.get("profile_id")
    if not isinstance(profile_id, str) or profile_id not in _PROFILES_BY_ID:
        raise ValueError("profile evidence profile 无效")
    declared = get_external_profile(profile_id)
    if expected_profile is not None and declared != expected_profile:
        raise ValueError("profile evidence profile 不匹配")
    if profile.get("quantum_backend") != declared.quantum_backend or profile.get("topology_backend") != declared.topology_label:
        raise ValueError("profile evidence 路线无效")
    if not isinstance(payload.get("run_id"), str) or not _PROFILE_ID.fullmatch(payload["run_id"]):
        raise ValueError("profile evidence run_id 无效")
    if not _COMMIT.fullmatch(str(software.get("source_commit", ""))):
        raise ValueError("profile evidence 提交版本无效")
    if software.get("worktree_clean") is not True or not _VERSION.fullmatch(str(software.get("project_version", ""))):
        raise ValueError("profile evidence 软件摘要无效")
    if workflow.get("state") != "done" or workflow.get("total_steps") != len(_DONE_STEPS):
        raise ValueError("profile evidence 工作流终态无效")
    if workflow.get("done_steps") != list(_DONE_STEPS) or not isinstance(workflow.get("stage_states"), Mapping):
        raise ValueError("profile evidence 步骤无效")
    if workflow["stage_states"] != _STAGE_STATUS:
        raise ValueError("profile evidence 阶段状态无效")
    if not isinstance(artifacts, list) or len(artifacts) != len(_EXPECTED_ARTIFACT_NAMES):
        raise ValueError("profile evidence 产物数量无效")
    required = _EXPECTED_ARTIFACT_NAMES
    names: set[str] = set()
    for item in artifacts:
        if not isinstance(item, Mapping):
            raise ValueError("profile evidence 产物无效")
        if set(item) != {"name", "size_bytes", "sha256"}:
            raise ValueError("profile evidence 产物包含禁止字段")
        name = item.get("name")
        if not isinstance(name, str) or name not in required or name in names:
            raise ValueError("profile evidence 产物名称无效")
        if not isinstance(item.get("size_bytes"), int) or item["size_bytes"] <= 0:
            raise ValueError("profile evidence 产物大小无效")
        if not _SHA256.fullmatch(str(item.get("sha256", ""))):
            raise ValueError("profile evidence 产物哈希无效")
        names.add(name)
    if names != required or payload.get("conclusion") != "passed":
        raise ValueError("profile evidence 结论无效")
    return payload


def write_profile_evidence(path: str | Path, validation: ProfileValidation) -> Path:
    """Persist only a successful, fully redacted profile evidence record."""
    if not validation.accepted:
        raise ValueError("未通过的 profile 不能写入通过证据")
    payload = _validate_evidence_payload(validation.evidence, expected_profile=get_external_profile(validation.profile_id))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary).replace(target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return target


def write_profile_junit(path: str | Path, validation: ProfileValidation) -> Path:
    """Write a one-case, issue-code-only JUnit result for manual archival."""
    failure_count = "0" if validation.accepted else "1"
    suite = ET.Element(
        "testsuite",
        name="willy.external_profile_evidence",
        tests="1",
        failures=failure_count,
        errors="0",
        skipped="0",
    )
    case = ET.SubElement(
        suite,
        "testcase",
        classname="willy.external_profile_evidence",
        name=validation.profile_id,
    )
    if not validation.accepted:
        failure = ET.SubElement(case, "failure", type="profile_validation")
        failure.text = ",".join(validation.issues) or "profile_validation_failed"
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            ET.ElementTree(suite).write(handle, encoding="utf-8", xml_declaration=True)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary).replace(target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return target


def verify_profile_evidence(
    directory: str | Path,
    profiles: Sequence[ExternalProfile],
) -> tuple[str, ...]:
    """Verify redacted evidence files without consulting any run workspace."""
    root = Path(directory)
    issues: list[str] = []
    for profile in profiles:
        path = root / f"{profile.profile_id}.json"
        try:
            payload = _read_json_file(path, maximum_bytes=512 * 1024)
            if payload is None:
                raise ValueError("missing")
            _validate_evidence_payload(payload, expected_profile=profile)
        except ValueError:
            issues.append(f"profile_evidence_invalid:{profile.profile_id}")
    return tuple(issues)


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Willy 四条外部十步 profile 的只读验收证据校验")
    parser.add_argument("--profile", help="具名 profile；与 --run-dir 一起使用")
    parser.add_argument("--run-dir", help="已结束的 run 目录；只读取，不启动或修改流程")
    parser.add_argument("--output", help="成功时写入的脱敏 JSON 证据文件")
    parser.add_argument("--junit-output", help="写入脱敏 JUnit 结果，失败时也会写入")
    parser.add_argument("--evidence-dir", help="校验已写出的证据目录")
    parser.add_argument("--profiles", default="all", help="校验证据时的 profile 列表，默认 all")
    options = parser.parse_args(argv)
    if options.evidence_dir:
        if options.profile or options.run_dir or options.output or options.junit_output:
            parser.error("--evidence-dir 不可与 run 校验参数同时使用")
        issues = verify_profile_evidence(options.evidence_dir, selected_external_profiles(options.profiles))
        print(json.dumps({"issues": list(issues)}, ensure_ascii=False))
        return 1 if issues else 0
    if not options.profile or not options.run_dir:
        parser.error("必须同时提供 --profile 与 --run-dir，或提供 --evidence-dir")
    validation = validate_finished_profile(options.profile, options.run_dir)
    if validation.accepted and options.output:
        write_profile_evidence(options.output, validation)
    if options.junit_output:
        write_profile_junit(options.junit_output, validation)
    print(json.dumps(validation.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if validation.accepted else 1


if __name__ == "__main__":
    raise SystemExit(_main())
