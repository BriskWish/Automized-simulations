"""Run registry and immutable-ish audit records for the Run Assistant.

The registry is the only API used by read-only run tools.  It keeps run facts
inside ``md_run/<run_id>/`` and never resolves user-supplied paths directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
from contextlib import contextmanager
import fcntl
import json
import os
import re
import tempfile

from willy._paths import get_project_root
from willy.errors import StepResult, public_error_summary
from willy.pipeline_state import StateTransitionError, validate_state_transition
from willy.simulation.mdrun_eta import MDRUN_ETA_FILENAME, MDRUN_HEARTBEAT_INTERVAL_S
from willy.run_metadata import (
    RUN_MANIFEST_FILENAME,
    RunManifestRevisionConflict,
    RunMetadataError,
    create_run_manifest,
    load_run_metadata_section,
    load_run_manifest,
    update_run_manifest_section,
)
from willy.run_store import run_transaction
from willy.run_provenance import RUN_PROVENANCE_FILENAME
from willy.step_registry import EQ_STEP, STEP_REGISTRY


RUNS_DIRNAME = "md_run"
INDEX_FILENAME = "index.json"
MANIFEST_FILENAME = "manifest.json"
_QUANTUM_INPUT_SUFFIXES = frozenset({".gjf", ".inp", ".fchk", ".molden"})
STATUS_FILENAME = "status.json"
STATUS_LOCK_FILENAME = ".status.lock"
EVENTS_FILENAME = "events.jsonl"
DECISION_TRACE_FILENAME = "decision_trace.jsonl"
LEGACY_ENVIRONMENT_REPORT_FILENAME = "environment_report.json"
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_DISPLAY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_PROPOSAL_WORKSPACE_ID_RE = re.compile(r"^plan__\d{12}$")
_LOG_SUFFIXES = {".err", ".log", ".out", ".txt"}
_MD_STAGE_STEPS = {
    stage: STEP_REGISTRY.index_for_stage(stage)
    for stage in ("em", "eq", "prod")
}
_MD_STAGE_LABELS = {
    stage: STEP_REGISTRY.label_for(step)
    for stage, step in _MD_STAGE_STEPS.items()
    if step is not None
}


class RunRegistryError(ValueError):
    """Raised when a requested run or run-local artifact is invalid."""


class RunStateConflict(RunRegistryError):
    """Raised when a control request was prepared from a stale status revision."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _state_revision(value: object, default: int = 0) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return default


class RunRegistry:
    """Persist and read run facts without exposing arbitrary filesystem paths."""

    def __init__(self, project_root: str | Path | None = None):
        self.project_root = Path(project_root) if project_root is not None else get_project_root()
        self.runs_dir = self.project_root / RUNS_DIRNAME
        self.index_path = self.runs_dir / INDEX_FILENAME
        self.lock_path = self.runs_dir / ".registry.lock"

    def register_run(
        self,
        run_dir: str | Path,
        *,
        backend: str,
        total_steps: int,
        parent_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a manifest and index entry, or reopen an existing run safely."""
        directory = self._validate_run_directory(run_dir)
        run_id = directory.name
        manifest_file = directory / MANIFEST_FILENAME
        with run_transaction(directory) as store:
            store.recover_pending_bundle()
        unified = self._load_unified_manifest(directory)
        is_fresh_v2_candidate = unified is None and not manifest_file.exists()
        created = is_fresh_v2_candidate or (
            unified is not None and not bool(unified["sections"]["registry"]["data"])
        )
        if created:
            config_file = directory / "config.json"
            input_files = self._input_files(directory)
            manifest = {
                "schema_version": 1,
                "run_id": run_id,
                "created_at": _now(),
                "updated_at": _now(),
                "parent_run_id": parent_run_id,
                "mode": "fork" if parent_run_id else "new",
                "backend": backend,
                "total_steps": total_steps,
                "config_path": "config.json",
                "config_sha256": _file_sha256(config_file) if config_file.is_file() else "",
                "input_files": input_files,
                "dependencies": [],
                "artifacts": [],
                "status_path": STATUS_FILENAME,
            }
            if is_fresh_v2_candidate:
                try:
                    unified = create_run_manifest(directory, sections={"registry": manifest})
                except RunMetadataError as exc:
                    raise RunRegistryError(f"无法创建运行 metadata: {exc}") from exc
            else:
                self._write_registry_manifest(directory, manifest, unified=unified)
            self.append_event(directory, "run_created", {
                "backend": backend,
                "total_steps": total_steps,
                "parent_run_id": parent_run_id,
            })
        else:
            manifest = self._read_registry_manifest(directory, unified=unified)
            self.append_event(directory, "run_reopened", {"backend": backend})
        self._update_index(directory, manifest)
        return manifest

    def refresh_input_files(self, run_dir: str | Path) -> dict[str, Any]:
        """Refresh only the manifest's frozen quantum-input fingerprints.

        The workspace is registered before status persistence is bound, while
        raw inputs are copied later in the same setup transaction.  Updating
        this constrained field afterwards keeps the public audit accurate
        without reopening or changing the run's execution state.
        """
        directory = self._validate_run_directory(run_dir)
        with self._status_lock(directory):
            unified = self._load_unified_manifest(directory)
            manifest = self._read_registry_manifest(directory, unified=unified)
            manifest["input_files"] = self._input_files(directory)
            manifest["updated_at"] = _now()
            self._write_registry_manifest(directory, manifest, unified=unified)
        self._update_index(directory, manifest)
        return manifest

    def set_display_name(self, run_id: str, display_name: str) -> dict[str, Any]:
        """Set an ASCII display name while preserving the immutable run ID.

        ``md_run/<run_id>`` remains the physical directory because its name is
        referenced by the run manifest, events, pipeline lock and controlled
        resume/fork commands.  The display name is the user-facing folder
        label, recorded in the registry manifest and event log instead.
        """
        if not isinstance(display_name, str):
            raise RunRegistryError("工程名称必须是文本")
        normalized = display_name.strip()
        if not _DISPLAY_NAME_RE.fullmatch(normalized):
            raise RunRegistryError(
                "工程名称只能包含英文字母、数字、下划线和连字符，长度不超过 64"
            )
        directory = self.resolve_run_id(run_id)
        with self._status_lock(directory):
            unified = self._load_unified_manifest(directory)
            manifest = self._read_registry_manifest(directory, unified=unified)
            previous = manifest.get("display_name")
            if previous == normalized:
                return {
                    "run_id": directory.name,
                    "display_name": normalized,
                    "changed": False,
                }
            manifest["display_name"] = normalized
            manifest["updated_at"] = _now()
            manifest = self._write_registry_manifest(directory, manifest, unified=unified)
        self.append_event(directory, "run_display_name_changed", {
            "display_name": normalized,
        })
        self._update_index(directory, manifest)
        return {
            "run_id": directory.name,
            "display_name": normalized,
            "changed": True,
        }

    def refresh_config_fingerprint(self, run_dir: str | Path) -> dict[str, Any]:
        """Refresh the registry manifest's hash for the final run config.

        Registration happens before the orchestrator applies audited fields and
        the per-run seed.  This explicit refresh keeps the top-level registry
        fingerprint aligned with the immutable config snapshot written during
        setup, without changing status or execution state.
        """
        directory = self._validate_run_directory(run_dir)
        config_file = directory / "config.json"
        if not config_file.is_file():
            raise RunRegistryError("运行目录缺少 config.json，无法刷新配置指纹")
        config_hash = _file_sha256(config_file)
        with self._status_lock(directory):
            unified = self._load_unified_manifest(directory)
            manifest = self._read_registry_manifest(directory, unified=unified)
            manifest["config_sha256"] = config_hash
            manifest["updated_at"] = _now()
            self._write_registry_manifest(directory, manifest, unified=unified)
        self._update_index(directory, manifest)
        return manifest

    def record_proposal_origin(self, run_dir: str | Path, plan_id: str) -> None:
        """Bind a formalized plan ID into the public registry audit.

        The candidate configuration and conversation remain private to the
        retained ``proposal.json`` file.  The regular manifest and event log
        expose only the opaque pre-launch workspace identifier.
        """
        if not isinstance(plan_id, str) or not _PROPOSAL_WORKSPACE_ID_RE.fullmatch(plan_id):
            raise RunRegistryError("方案工作区标识无效")
        directory = self._validate_run_directory(run_dir)
        with self._status_lock(directory):
            unified = self._load_unified_manifest(directory)
            manifest = self._read_registry_manifest(directory, unified=unified)
            manifest["proposal_workspace_id"] = plan_id
            manifest["updated_at"] = _now()
            manifest = self._write_registry_manifest(directory, manifest, unified=unified)
        self.append_event(directory, "proposal_formalized", {"plan_id": plan_id})
        self._update_index(directory, manifest)

    def record_control_action(
        self,
        run_id: str,
        *,
        action: str,
        outcome: str,
        restart_step: int | None = None,
        stopped_step: int | None = None,
        parameter_paths: list[str] | tuple[str, ...] = (),
        parent_run_id: str | None = None,
    ) -> None:
        """Record a bounded resume/fork audit in registry metadata and events.

        Parameter values never enter public metadata.  Keeping only their
        canonical paths lets a later reader explain the replay boundary while
        preserving the frozen configuration as the authoritative value source.
        """
        if action not in {"resume", "fork"}:
            raise ValueError("运行控制动作无效")
        if outcome not in {"accepted", "rejected", "launched", "launch_failed", "created", "proposed"}:
            raise ValueError("运行控制结果无效")
        directory = self.resolve_run_id(run_id)
        clean_paths = [
            value for value in parameter_paths
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,159}", value)
        ][:16]
        details: dict[str, Any] = {"action": action, "outcome": outcome}
        for key, value in (("restart_step", restart_step), ("stopped_step", stopped_step)):
            if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= STEP_REGISTRY.total_steps:
                details[key] = value
        if clean_paths:
            details["parameter_paths"] = clean_paths
        if isinstance(parent_run_id, str) and _RUN_ID_RE.fullmatch(parent_run_id):
            details["parent_run_id"] = parent_run_id
        with self._status_lock(directory):
            manifest = self._read_registry_manifest(directory)
            history = manifest.get("control_history", [])
            history = list(history) if isinstance(history, list) else []
            history.append({"recorded_at": _now(), **details})
            manifest["control_history"] = history[-32:]
            manifest["updated_at"] = _now()
            self._write_registry_manifest(directory, manifest)
        self.append_event(directory, f"{action}_{outcome}", details)
        self._update_index(directory, manifest)

    @staticmethod
    def _load_unified_manifest(directory: Path) -> dict[str, Any] | None:
        """Return v2 metadata only when this run has opted into it.

        A malformed v2 record is not silently replaced by legacy files.  Once
        a run owns ``run_manifest.json``, that document is its source of truth
        and a corrupted section must remain visible to the caller.
        """
        if not (directory / RUN_MANIFEST_FILENAME).is_file():
            return None
        try:
            return load_run_manifest(directory)
        except RunMetadataError as exc:
            raise RunRegistryError(f"运行 metadata 不可读取: {exc}") from exc

    def _read_registry_manifest(
        self,
        directory: Path,
        *,
        unified: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Read the v2 registry section, with legacy fallback per run."""
        record = unified if unified is not None else self._load_unified_manifest(directory)
        if record is None:
            return self._read_json(directory / MANIFEST_FILENAME, "运行 manifest")
        try:
            payload = record["sections"]["registry"]["data"]
        except (KeyError, TypeError) as exc:
            raise RunRegistryError("运行 metadata 缺少 registry section") from exc
        if not isinstance(payload, Mapping):
            raise RunRegistryError("运行 metadata registry section 格式无效")
        return dict(payload)

    def _write_registry_manifest(
        self,
        directory: Path,
        payload: Mapping[str, Any],
        *,
        unified: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Write registry facts to v2 or the legacy manifest for this run."""
        clean = dict(payload)
        record = unified if unified is not None else self._load_unified_manifest(directory)
        if record is None:
            _atomic_write(directory / MANIFEST_FILENAME, clean)
            return clean
        try:
            expected_section_revision = record["sections"]["registry"]["revision"]
            for _ in range(2):
                try:
                    updated = update_run_manifest_section(
                        directory,
                        "registry",
                        clean,
                        expected_revision=record["revision"],
                        expected_section_revision=expected_section_revision,
                    )
                    break
                except RunManifestRevisionConflict:
                    # A different v2 section may have been refreshed between
                    # this read and write.  Retry only when registry itself is
                    # untouched; otherwise preserving the competing registry
                    # update is safer than overwriting its artifact audit.
                    record = self._load_unified_manifest(directory)
                    if record is None or record["sections"]["registry"]["revision"] != expected_section_revision:
                        raise
            else:
                raise RunManifestRevisionConflict("运行 metadata 已并发更新")
            value = updated["sections"]["registry"]["data"]
        except (KeyError, TypeError, RunMetadataError) as exc:
            raise RunRegistryError(f"无法更新运行 metadata registry section: {exc}") from exc
        if not isinstance(value, Mapping):
            raise RunRegistryError("运行 metadata registry section 格式无效")
        return dict(value)

    @staticmethod
    def _input_files(directory: Path) -> list[dict[str, str]]:
        return [
            {"path": path.name, "sha256": _file_sha256(path)}
            for path in sorted(directory.iterdir())
            if path.is_file() and path.suffix.lower() in _QUANTUM_INPUT_SUFFIXES
        ]

    def record_status(self, run_dir: str | Path, status: Mapping[str, Any], event_type: str) -> None:
        """Persist the latest state and a concise event without interrupting a run."""
        directory = self._validate_run_directory(run_dir)
        with self._status_lock(directory):
            payload = self._public_status_payload(status, directory.name)
            if "state_revision" not in status:
                current = self._read_current_status(directory)
                payload["state_revision"] = _state_revision(current.get("state_revision")) + 1
            with run_transaction(directory) as store:
                store.commit_bundle(
                    operation="record_status",
                    json_writes={STATUS_FILENAME: payload},
                    jsonl_appends={} if event_type == "runtime_heartbeat" else {
                        EVENTS_FILENAME: [{
                            "timestamp": _now(),
                            "event_type": event_type,
                            "run_id": directory.name,
                            "details": self._status_summary(payload),
                        }],
                    },
                )
        try:
            manifest = self._read_registry_manifest(directory)
            self._update_index(directory, manifest, status=payload)
        except (OSError, RunRegistryError):
            # The state snapshot is the primary record; an index refresh can be retried later.
            return

    def compare_and_swap_status(
        self,
        run_dir: str | Path,
        *,
        expected_revision: int,
        status: Mapping[str, Any],
        event_type: str,
        event_details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically persist one legal control transition from a known revision."""
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise ValueError("expected_revision 必须是非负整数")
        directory = self._validate_run_directory(run_dir)
        with self._status_lock(directory):
            current = self._read_current_status(directory)
            actual_revision = _state_revision(current.get("state_revision"))
            if actual_revision != expected_revision:
                raise RunStateConflict("运行状态已更新，请刷新后重试")
            current_state = current.get("state", "idle") if current else "idle"
            if current_state == "unknown":
                current_state = "idle"
            try:
                validate_state_transition(current_state, status.get("state", ""))
            except StateTransitionError as exc:
                raise RunRegistryError(str(exc)) from exc
            payload = self._public_status_payload(status, directory.name)
            payload["state_revision"] = actual_revision + 1
            details = self._status_summary(payload)
            if event_details:
                details["audit"] = {
                    str(key): value
                    for key, value in event_details.items()
                    if isinstance(key, str) and key in {
                        "source", "reason", "stage", "evidence_source",
                        "evidence_age_s", "checked_revision", "grace_s",
                    }
                }
            with run_transaction(directory) as store:
                store.commit_bundle(
                    operation="compare_and_swap_status",
                    json_writes={STATUS_FILENAME: payload},
                    jsonl_appends={} if event_type == "runtime_heartbeat" else {
                        EVENTS_FILENAME: [{
                            "timestamp": _now(),
                            "event_type": event_type,
                            "run_id": directory.name,
                            "details": details,
                        }],
                    },
                )
        try:
            manifest = self._read_registry_manifest(directory)
            self._update_index(directory, manifest, status=payload)
        except (OSError, RunRegistryError):
            pass
        return payload

    def request_stop(self, run_id: str) -> dict[str, Any]:
        """Persist a checkpoint-first user stop request for one active run."""
        return self._write_stop_state(run_id, state="stopping", event_type="run_stop_requested")

    def mark_aborted(
        self,
        run_id: str,
        *,
        event_type: str = "run_aborted",
        expected_revision: int | None = None,
        event_details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist a terminal user/interrupted-run outcome without engine details."""
        return self._write_stop_state(
            run_id,
            state="aborted",
            event_type=event_type,
            expected_revision=expected_revision,
            event_details=event_details,
        )

    def _write_stop_state(
        self,
        run_id: str,
        *,
        state: str,
        event_type: str,
        expected_revision: int | None = None,
        event_details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if state not in {"stopping", "aborted"}:
            raise ValueError("无效的运行停止状态")
        directory = self.resolve_run_id(run_id)
        current = self.get_run_status(run_id, reconcile=False)
        if current.get("state") in {"done", "escalated", "aborted"}:
            return current
        current_revision = _state_revision(current.get("state_revision"))
        if expected_revision is not None and expected_revision != current_revision:
            raise RunStateConflict("运行状态已更新，请刷新后重试")
        updated = dict(current)
        updated.update({
            "state": state,
            "error": "",
            "error_kind": "",
            "repair": {},
            "updated_at": _now(),
        })
        updated = self.compare_and_swap_status(
            directory,
            expected_revision=current_revision,
            status=updated,
            event_type=event_type,
            event_details=event_details,
        )
        return self.get_run_status(run_id, reconcile=False)

    def get_environment_report(self, run_id: str) -> dict[str, Any]:
        """Read the redacted capability snapshot from run provenance.

        ``environment_report.json`` was a duplicate of this snapshot.  It is
        accepted only as a read fallback for historical runs created before
        the consolidation, never written for new runs.
        """
        directory = self.resolve_run_id(run_id)
        unified = self._load_unified_manifest(directory)
        if unified is not None:
            try:
                payload = unified["sections"]["provenance"]["data"]
            except (KeyError, TypeError) as exc:
                raise RunRegistryError("运行 metadata 缺少 provenance section") from exc
            if not isinstance(payload, Mapping):
                raise RunRegistryError("运行 metadata provenance section 格式无效")
        else:
            try:
                payload = self._read_json(directory / RUN_PROVENANCE_FILENAME, "运行 provenance")
            except RunRegistryError:
                payload = self._read_json(
                    directory / LEGACY_ENVIRONMENT_REPORT_FILENAME,
                    "运行环境报告",
                )
        raw_capabilities = payload.get("capabilities", {})
        if not isinstance(raw_capabilities, Mapping):
            raise RunRegistryError("运行环境报告格式无效")
        allowed_statuses = {
            "available", "missing", "misconfigured", "not_executable",
            "version_incompatible", "runtime_unavailable",
        }
        allowed_sources = {"willy_env", "dotenv", "legacy_env", "default", "path"}
        capabilities: dict[str, dict[str, Any]] = {}
        for tool_id, value in raw_capabilities.items():
            if not isinstance(tool_id, str) or not _RUN_ID_RE.fullmatch(tool_id):
                continue
            if not isinstance(value, Mapping):
                continue
            status = value.get("status")
            source = value.get("source")
            capabilities[tool_id] = {
                "status": status if status in allowed_statuses else "unknown",
                "source": source if source in allowed_sources else "",
            }
        return {"run_id": run_id, "capabilities": capabilities}

    def get_mdrun_eta(self, run_id: str) -> dict[str, Any]:
        """Return a validated, path-free live ETA from the current mdrun stage."""
        directory = self.resolve_run_id(run_id)
        path = directory / MDRUN_ETA_FILENAME
        unavailable = {"run_id": run_id, "status": "unavailable"}
        if not path.is_file():
            return {**unavailable, "reason": "本次运行尚未产生 GROMACS 预计结束时间"}
        try:
            payload = self._read_json(path, "GROMACS 预计结束时间")
        except RunRegistryError:
            return {**unavailable, "reason": "GROMACS 预计结束时间快照无效"}

        stage = payload.get("stage")
        status = payload.get("status")
        observed_at = self._public_timestamp(payload.get("observed_at"))
        if stage not in {"em", "eq", "prod"} or observed_at is None:
            return {**unavailable, "reason": "GROMACS 预计结束时间快照无效"}
        if status == "waiting":
            result = {
                "run_id": run_id, "status": "waiting", "stage": stage,
                "observed_at": observed_at,
            }
            self._add_local_timestamp(result, "observed_at")
            self._add_live_mdrun_facts(payload, result)
            return result
        if status == "available":
            try:
                step = int(payload.get("step"))
                remaining_seconds = float(payload.get("remaining_seconds"))
            except (TypeError, ValueError):
                return {**unavailable, "reason": "GROMACS 预计结束时间快照无效"}
            estimated_end_at = self._public_timestamp(payload.get("estimated_end_at"))
            if step < 0 or not 0 <= remaining_seconds <= 31_536_000 or estimated_end_at is None:
                return {**unavailable, "reason": "GROMACS 预计结束时间快照无效"}
            eta_observed_at = self._public_timestamp(payload.get("eta_observed_at")) or observed_at
            result = {
                "run_id": run_id,
                "status": "available",
                "stage": stage,
                "step": step,
                "remaining_seconds": round(remaining_seconds, 3),
                "observed_at": observed_at,
                "eta_observed_at": eta_observed_at,
                "estimated_end_at": estimated_end_at,
            }
            self._add_local_timestamp(result, "observed_at")
            self._add_local_timestamp(result, "eta_observed_at")
            self._add_local_timestamp(result, "estimated_end_at")
            self._add_live_mdrun_facts(payload, result)
            return result
        if status == "finished":
            finished_at = self._public_timestamp(payload.get("finished_at"))
            if finished_at is None:
                return {**unavailable, "reason": "GROMACS 预计结束时间快照无效"}
            result = {
                "run_id": run_id, "status": "finished", "stage": stage,
                "observed_at": observed_at, "finished_at": finished_at,
            }
            self._add_local_timestamp(result, "observed_at")
            self._add_local_timestamp(result, "finished_at")
            self._add_live_mdrun_facts(payload, result)
            return result
        return {**unavailable, "reason": "GROMACS 当前未提供可用的预计结束时间"}

    def record_step_result(
        self,
        run_dir: str | Path,
        result: StepResult,
        *,
        label: str,
        source: str = "pipeline",
        activity: Mapping[str, Any] | None = None,
    ) -> None:
        """Append only public result facts and register run-local artifacts."""
        directory = self._validate_run_directory(run_dir)
        try:
            artifact_contract = STEP_REGISTRY.step(result.step_index).artifact_contract
        except (TypeError, ValueError):
            artifact_contract = "unregistered"
        outputs = self._safe_outputs(directory, result.outputs)
        public_activity = self._public_activity(activity or {})
        target = result.target or public_activity.get("target", "当前体系")
        if target and public_activity:
            public_activity["target"] = target
        error_summary = ""
        if not result.success:
            error_summary = public_error_summary(
                public_activity.get("tool", "流水线"),
                public_activity.get("operation", label),
                target,
                result.error,
            )
        event = {
            "step_id": result.step_index,
            "artifact_contract": artifact_contract,
            "operation": label,
            "activity": public_activity,
            "success": result.success,
            "error_kind": result.error.kind.value if result.error else "",
            "error": error_summary,
        }
        unified = self._load_unified_manifest(directory)
        if unified is not None:
            manifest = self._read_registry_manifest(directory, unified=unified)
            artifacts = manifest.setdefault("artifacts", [])
            known = {(item.get("path"), item.get("step_id"), item.get("kind")) for item in artifacts}
            for kind, relative_path in outputs.items():
                path = directory / relative_path
                item = {
                    "path": relative_path,
                    "kind": kind,
                    "step_id": result.step_index,
                    "step_name": result.step_name,
                    "artifact_contract": artifact_contract,
                    "size_bytes": path.stat().st_size if path.is_file() else None,
                }
                key = (item["path"], item["step_id"], item["kind"])
                if key not in known:
                    artifacts.append(item)
                    known.add(key)
            manifest["updated_at"] = _now()
            self._write_registry_manifest(directory, manifest, unified=unified)
            self.append_event(directory, "step_result", event)
        else:
            with run_transaction(directory) as store:
                manifest = store.read_json(MANIFEST_FILENAME)
                if not isinstance(manifest, dict):
                    raise RunRegistryError("运行 manifest 不可读取")
                artifacts = manifest.setdefault("artifacts", [])
                known = {(item.get("path"), item.get("step_id"), item.get("kind")) for item in artifacts}
                for kind, relative_path in outputs.items():
                    path = directory / relative_path
                    item = {
                        "path": relative_path,
                        "kind": kind,
                        "step_id": result.step_index,
                        "step_name": result.step_name,
                        "artifact_contract": artifact_contract,
                        "size_bytes": path.stat().st_size if path.is_file() else None,
                    }
                    key = (item["path"], item["step_id"], item["kind"])
                    if key not in known:
                        artifacts.append(item)
                        known.add(key)
                manifest["updated_at"] = _now()
                store.commit_bundle(
                    operation="record_step_result",
                    json_writes={MANIFEST_FILENAME: manifest},
                    jsonl_appends={EVENTS_FILENAME: [{
                        "timestamp": _now(),
                        "event_type": "step_result",
                        "run_id": directory.name,
                        "details": event,
                    }]},
                )
        self._update_index(directory, manifest)

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return summaries newest first without writing during a read-only query."""
        limit = max(1, min(int(limit), 200))
        if not self.index_path.is_file():
            return self._discover_runs()[:limit]
        try:
            payload = self._read_json(self.index_path, "运行索引")
            runs = payload.get("runs", [])
            if not isinstance(runs, list):
                raise RunRegistryError("运行索引格式无效")
        except (OSError, RunRegistryError):
            return []
        return sorted(
            runs,
            key=lambda item: (str(item.get("updated_at", "")), str(item.get("run_id", ""))),
            reverse=True,
        )[:limit]

    def remove_run_from_index(self, run_id: str) -> bool:
        """Remove a deleted run's stale index entry without touching any run data."""
        if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
            raise RunRegistryError("run_id 格式无效")
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                payload = {"version": 1, "runs": []}
                if self.index_path.is_file():
                    try:
                        payload = self._read_json(self.index_path, "运行索引")
                    except RunRegistryError:
                        payload = {"version": 1, "runs": []}
                entries = payload.get("runs", [])
                if not isinstance(entries, list):
                    entries = []
                remaining = [item for item in entries if item.get("run_id") != run_id]
                changed = len(remaining) != len(entries)
                if changed:
                    _atomic_write(self.index_path, {"version": 1, "runs": remaining})
                return changed
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def get_run_status(self, run_id: str, *, reconcile: bool = True) -> dict[str, Any]:
        directory = self.resolve_run_id(run_id)
        status_file = directory / STATUS_FILENAME
        if not status_file.is_file():
            return {"run_id": run_id, "state": "unknown", "message": "该 run 尚未写入状态快照"}
        status = self._read_json(status_file, "运行状态")
        if reconcile:
            status = self._reconcile_active_simulation_stage(directory, status)
        status["run_id"] = run_id
        self._add_local_timestamp(status, "started_at")
        self._add_local_timestamp(status, "updated_at")
        return status

    def get_live_stage_evidence(
        self,
        run_id: str,
        status: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return bounded, read-only evidence that a run-local MD stage is alive.

        This is intentionally separate from ``get_run_status`` so a liveness
        decision can be made without triggering any status repair.  The result
        contains no paths, commands, or raw engine output.
        """
        directory = self.resolve_run_id(run_id)
        snapshot = dict(status) if isinstance(status, Mapping) else self.get_run_status(run_id, reconcile=False)
        stage = self._status_stage(snapshot)
        if stage is None:
            stage = self._manifest_running_stage(directory)
        if stage is None:
            return {"active": False, "stage": None, "source": "none"}

        freshness_s = max(60.0, MDRUN_HEARTBEAT_INTERVAL_S * 3.0)
        now = datetime.now(timezone.utc)
        eta_file = directory / MDRUN_ETA_FILENAME
        if eta_file.is_file():
            try:
                eta = self._read_json(eta_file, "GROMACS ETA 快照")
            except RunRegistryError:
                eta = {}
            if eta.get("stage") == stage and eta.get("process_alive") is True:
                observed = self._public_timestamp(eta.get("observed_at"))
                age_s = self._timestamp_age_seconds(observed, now)
                if age_s is not None and age_s <= freshness_s:
                    return {
                        "active": True,
                        "stage": stage,
                        "source": "mdrun_eta",
                        "evidence_age_s": round(age_s, 3),
                        "observed_at": observed,
                    }

        for suffix in ("log", "edr", "xtc", "cpt"):
            try:
                modified = datetime.fromtimestamp(
                    (directory / f"{stage}.{suffix}").stat().st_mtime,
                    timezone.utc,
                )
            except OSError:
                continue
            age_s = max(0.0, (now - modified).total_seconds())
            if age_s <= freshness_s:
                return {
                    "active": True,
                    "stage": stage,
                    "source": f"stage_output:{suffix}",
                    "evidence_age_s": round(age_s, 3),
                }

        updated = self._public_timestamp(snapshot.get("updated_at"))
        age_s = self._timestamp_age_seconds(updated, now)
        if age_s is not None and age_s <= freshness_s and snapshot.get("state") in {
            "running", "retrying", "stopping",
        }:
            return {
                "active": True,
                "stage": stage,
                "source": "status_heartbeat",
                "evidence_age_s": round(age_s, 3),
                "observed_at": updated,
            }
        return {"active": False, "stage": stage, "source": "stale"}

    @staticmethod
    def _timestamp_age_seconds(value: str | None, now: datetime) -> float | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds())

    @staticmethod
    def _status_stage(status: Mapping[str, Any]) -> str | None:
        activity = status.get("activity")
        if isinstance(activity, Mapping):
            stage = activity.get("target")
            if stage in {"em", "eq", "prod"}:
                return str(stage)
        return None

    def _manifest_running_stage(self, directory: Path) -> str | None:
        try:
            manifest = self._read_simulation_manifest(directory)
        except (RunRegistryError, RunMetadataError):
            return None
        if manifest is None:
            return None
        stages = manifest.get("stages", {})
        if not isinstance(stages, Mapping):
            return None
        return next(
            (
                stage for stage in ("em", "eq", "prod")
                if isinstance(stages.get(stage), Mapping)
                and stages[stage].get("status") == "running"
            ),
            None,
        )

    def _reconcile_active_simulation_stage(
        self,
        directory: Path,
        status: dict[str, Any],
    ) -> dict[str, Any]:
        """Correct a transient status that claims progress beyond a live stage.

        This is a narrow audit repair for historical orchestrator defects.  It
        never touches GROMACS inputs, outputs, manifests, locks, or processes;
        it only withdraws an impossible public ``done_steps`` claim when the
        private MD manifest says an earlier stage is still running.
        """
        if status.get("state") in {"stopping", "aborted"}:
            return status
        try:
            manifest = self._read_simulation_manifest(directory)
        except (RunRegistryError, RunMetadataError):
            return status
        if manifest is None:
            return status
        stages = manifest.get("stages", {})
        if not isinstance(stages, Mapping):
            return status
        active_stage = next(
            (
                stage for stage in ("em", "eq", "prod")
                if isinstance(stages.get(stage), Mapping)
                and stages[stage].get("status") == "running"
            ),
            None,
        )
        if active_stage is None:
            return status
        if not self._has_live_stage_evidence(directory, active_stage):
            return status
        active_step = _MD_STAGE_STEPS[active_stage]
        done_steps = status.get("done_steps", [])
        if not isinstance(done_steps, list):
            done_steps = []
        status_step = status.get("step", 0)
        if not isinstance(status_step, int):
            status_step = 0
        if status_step <= active_step and active_step not in done_steps:
            return status

        repair = status.get("repair") if isinstance(status.get("repair"), Mapping) else {}
        try:
            max_attempts = max(1, int(repair.get("max_attempts", 0)))
        except (TypeError, ValueError):
            max_attempts = 1
        stage_attempt = stages[active_stage].get("attempt", 1)
        try:
            repair_attempt = max(1, int(stage_attempt) - 1)
        except (TypeError, ValueError):
            repair_attempt = 1
        corrected = dict(status)
        corrected.update({
            "state": "retrying",
            "step": active_step,
            "step_label": _MD_STAGE_LABELS[active_stage],
            "layer": "simulation",
            "error": "",
            "error_kind": "",
            "repair": {
                "attempt": repair_attempt,
                "max_attempts": max_attempts,
                "adjustments": repair.get("adjustments", []),
            },
            "activity": {
                "tool": "GROMACS",
                "operation": "运行模拟",
                "target_type": "stage",
                "target": active_stage,
                "current": 2,
                "total": 2,
            },
            "done_steps": [
                step for step in done_steps
                if isinstance(step, int) and step < active_step
            ],
            "updated_at": _now(),
        })
        # This is an evidence-backed correction for an old, impossible
        # completion claim.  It is not a normal workflow transition, but it
        # must still consume a new revision so stale control requests fail.
        corrected.pop("state_revision", None)
        self.record_status(directory, corrected, "stage_status_reconciled")
        return self._read_json(directory / STATUS_FILENAME, "运行状态")

    def _has_live_stage_evidence(self, directory: Path, stage: str) -> bool:
        """Require a current ETA heartbeat or recently written stage output.

        A manifest's ``running`` record survives abnormal exits, so it is not
        sufficient evidence that a stage is still active.  Older runs may lack
        the ``process_alive`` ETA field; for those, the bounded fallback is a
        fresh write to a standard stage output.
        """
        evidence = self.get_live_stage_evidence(
            directory.name,
            {"activity": {"target": stage}, "state": "running"},
        )
        return bool(evidence.get("active"))

    def get_run_config(self, run_id: str) -> dict[str, Any]:
        directory = self.resolve_run_id(run_id)
        return self._read_json(directory / "config.json", "运行配置")

    def get_step_report(self, run_id: str, step_id: int) -> dict[str, Any]:
        if step_id < 1 or step_id > 99:
            raise RunRegistryError("step_id 必须是 1 到 99 的整数")
        directory = self.resolve_run_id(run_id)
        events = [
            event for event in self._read_events(directory)
            if event.get("event_type") == "step_result"
            and event.get("details", {}).get("step_id") == step_id
        ]
        status = self.get_run_status(run_id, reconcile=False)
        latest = events[-1].get("details", {}) if events else {}
        activity = latest.get("activity", {})
        if not activity and status.get("step") == step_id:
            activity = status.get("activity", {})
        return {
            "run_id": run_id,
            "step_id": step_id,
            "activity": activity,
            "state": status.get("state", "unknown"),
            "completed": step_id in status.get("done_steps", []),
            "success": latest.get("success"),
            "error_kind": latest.get("error_kind", status.get("error_kind", "")),
            "error": latest.get("error", status.get("error", "")),
        }

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        directory = self.resolve_run_id(run_id)
        manifest = self._read_registry_manifest(directory)
        result = []
        for item in manifest.get("artifacts", []):
            relative = item.get("path", "")
            try:
                path = self._safe_relative_file(directory, relative)
            except RunRegistryError:
                continue
            result.append({**item, "exists": path.is_file()})
        return result

    def get_box_parameters(self, run_id: str) -> dict[str, Any]:
        """Return the latest audited Packmol geometry without exposing paths."""
        directory = self.resolve_run_id(run_id)
        try:
            payload = self._read_simulation_manifest(directory)
        except (RunRegistryError, RunMetadataError):
            payload = None
        if payload is None:
            return {"run_id": run_id, "status": "unavailable", "reason": "尚未创建 MD 建盒记录"}
        attempts = payload.get("box_attempts", [])
        if not isinstance(attempts, list) or not attempts:
            return {"run_id": run_id, "status": "unavailable", "reason": "本次运行尚未完成 Packmol 建盒"}
        latest = attempts[-1]
        if not isinstance(latest, Mapping):
            return {"run_id": run_id, "status": "unavailable", "reason": "建盒记录格式无效"}
        allowed = (
            "time", "box_strategy", "target_mass_density_g_cm3",
            "packing_number_density_nm3", "total_mass_amu",
            "requested_box_vectors_angstrom", "actual_box_vectors_angstrom",
            "actual_box_angles_degrees", "box_size_angstrom", "box_volume_nm3",
            "actual_mass_density_g_cm3", "tolerance_angstrom",
            "expected_molecules", "expected_atoms", "actual_atoms",
        )
        return {
            "run_id": run_id,
            "status": "available",
            "box": {key: latest[key] for key in allowed if key in latest},
        }

    @staticmethod
    def _read_simulation_manifest(directory: Path) -> dict[str, Any] | None:
        """Read private MD evidence from v2, or the legacy MD file."""
        if (directory / RUN_MANIFEST_FILENAME).is_file():
            return load_run_metadata_section(directory, "simulation")
        path = directory / "md_manifest.json"
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RunRegistryError(f"MD manifest 格式无效: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise RunRegistryError("MD manifest 格式无效")
        return dict(payload)

    def tail_log(self, run_id: str, log_name: str, max_chars: int = 4000) -> dict[str, Any]:
        directory = self.resolve_run_id(run_id)
        log_path = Path(log_name) if isinstance(log_name, str) else Path()
        if (
            not log_name
            or log_path.is_absolute()
            or ".." in log_path.parts
            or len(log_path.parts) > 2
            or (len(log_path.parts) == 2 and log_path.parts[0] != "logs")
        ):
            raise RunRegistryError("日志名必须是 run 目录或 logs/ 目录内的相对文件名")
        if log_path.suffix.lower() not in _LOG_SUFFIXES:
            raise RunRegistryError("该文件类型不在可读取日志白名单中")
        path = self._safe_relative_file(directory, log_name)
        if not path.is_file():
            raise RunRegistryError(f"日志不存在: {log_name}")
        max_chars = max(100, min(int(max_chars), 4000))
        text = path.read_text(errors="replace")[-max_chars:]
        return {"run_id": run_id, "log_name": log_name, "content": self._redact(text), "truncated": path.stat().st_size > len(text.encode())}

    def explain_error(self, run_id: str) -> dict[str, Any]:
        status = self.get_run_status(run_id, reconcile=False)
        return {
            "run_id": run_id,
            "state": status.get("state", "unknown"),
            "step_id": status.get("step", 0),
            "activity": status.get("activity", {}),
            "done_steps": status.get("done_steps", []),
            "error_kind": status.get("error_kind", ""),
            "error": status.get("error", ""),
        }

    def resolve_run_id(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
            raise RunRegistryError("run_id 格式无效")
        directory = self.runs_dir / run_id
        if not directory.is_dir():
            raise RunRegistryError(f"运行不存在: {run_id}")
        return directory

    def append_event(self, run_dir: str | Path, event_type: str, details: Mapping[str, Any]) -> None:
        directory = self._validate_run_directory(run_dir)
        event = {
            "timestamp": _now(),
            "event_type": event_type,
            "run_id": directory.name,
            "details": details,
        }
        with run_transaction(directory) as store:
            store.append_jsonl(EVENTS_FILENAME, event)

    def append_decision_trace(self, run_dir: str | Path, trace: Mapping[str, Any]) -> None:
        """Append a bounded, redacted recovery decision record.

        Decision traces contain evidence and outcomes only.  Raw logs, prompts,
        credentials and filesystem paths are intentionally excluded.
        """
        directory = self._validate_run_directory(run_dir)
        allowed = {
            "decision_id", "action_id", "layer", "step", "error_kind", "policy_id",
            "candidate_tools", "selected_tool", "tool_effect", "parameter_changes",
            "model_id", "prompt_version", "confirmation_source", "result", "success",
            "attempt", "restart_step", "requires_confirmation", "requires_fork",
        }
        clean: dict[str, Any] = {}
        for key in allowed:
            if key not in trace:
                continue
            value = trace[key]
            if key in {"candidate_tools", "parameter_changes"}:
                if isinstance(value, (list, tuple)):
                    clean[key] = [self._trace_scalar(item) for item in list(value)[:16]]
                continue
            clean[key] = self._trace_scalar(value)
        clean = {key: value for key, value in clean.items() if value is not None}
        event = {"timestamp": _now(), "run_id": directory.name, **clean}
        with run_transaction(directory) as store:
            store.append_jsonl(DECISION_TRACE_FILENAME, event)

    @staticmethod
    def _trace_scalar(value: Any) -> Any:
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, (int, float)):
            return value
        text = str(value).replace("\n", " ").replace("\r", " ").strip()
        # Never retain paths, raw output or credential-like material.
        if any(token in text.lower() for token in ("api_key", "authorization", "bearer ", "raw_output", "prompt")):
            return "[redacted]"
        if "/" in text or "\\" in text or len(text) > 240:
            return text[:80] + "…" if len(text) > 80 else "[redacted]"
        return text

    def _validate_run_directory(self, run_dir: str | Path) -> Path:
        directory = Path(run_dir).resolve()
        runs_dir = self.runs_dir.resolve()
        if directory.parent != runs_dir or not _RUN_ID_RE.fullmatch(directory.name):
            raise RunRegistryError("运行目录必须是 md_run/<run_id>")
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    @contextmanager
    def _status_lock(self, directory: Path):
        """Serialize status writes independently for each run directory."""
        path = directory / STATUS_LOCK_FILENAME
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read_current_status(self, directory: Path) -> dict[str, Any]:
        path = directory / STATUS_FILENAME
        if not path.is_file():
            return {}
        try:
            payload = self._read_json(path, "运行状态")
        except RunRegistryError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _safe_relative_file(self, directory: Path, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path:
            raise RunRegistryError("产物路径无效")
        candidate = Path(relative_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise RunRegistryError("不允许读取 run 目录以外的文件")
        path = (directory / candidate).resolve()
        try:
            path.relative_to(directory.resolve())
        except ValueError:
            raise RunRegistryError("不允许读取 run 目录以外的文件")
        return path

    def _safe_outputs(self, directory: Path, outputs: Mapping[str, Any]) -> dict[str, str]:
        safe: dict[str, str] = {}
        for key, value in outputs.items():
            if not isinstance(value, (str, Path)):
                continue
            try:
                path = Path(value).resolve()
                relative = path.relative_to(directory.resolve())
            except (OSError, ValueError):
                continue
            safe[str(key)] = relative.as_posix()
        return safe

    def _status_summary(self, status: Mapping[str, Any]) -> dict[str, Any]:
        summary = {
            "state": status.get("state", ""),
            "state_revision": _state_revision(status.get("state_revision")),
            "step_id": status.get("step", 0),
            "activity": self._public_activity(status.get("activity", {})),
            "error_kind": status.get("error_kind", ""),
            "error": status.get("error", ""),
            "repair": self._public_repair(status),
            "escalation": self._public_escalation(status.get("escalation")),
            "done_steps": status.get("done_steps", []),
        }
        pending_action = self._public_pending_action(status.get("extra", {}))
        if pending_action:
            summary["pending_action"] = pending_action
        completion_scope = self._public_completion_scope(status.get("extra", {}))
        if completion_scope:
            summary["completion_scope"] = completion_scope
        return summary

    @staticmethod
    def _public_activity(activity: Mapping[str, Any]) -> dict[str, Any]:
        keys = ("tool", "operation", "target_type", "target", "current", "total")
        if not isinstance(activity, Mapping) or set(activity) != set(keys):
            return {}
        try:
            current = int(activity["current"])
            total = int(activity["total"])
        except (TypeError, ValueError):
            return {}
        if current < 0 or total < 0 or current > total:
            return {}
        if not all(isinstance(activity[key], str) and activity[key].strip() for key in keys[:4]):
            return {}
        return {
            "tool": activity["tool"].strip(),
            "operation": activity["operation"].strip(),
            "target_type": activity["target_type"].strip(),
            "target": activity["target"].strip(),
            "current": current,
            "total": total,
        }

    def _public_status_payload(self, status: Mapping[str, Any], run_id: str) -> dict[str, Any]:
        """Strip agent internals, raw output, absolute paths, and free text."""
        extra: dict[str, Any] = {"run_id": run_id}
        pending_action = self._public_pending_action(status.get("extra", {}))
        if pending_action:
            extra["pending_action"] = pending_action
        pending_fork = self._public_pending_fork(status.get("extra", {}))
        if pending_fork:
            extra["pending_fork"] = pending_fork
        completion_scope = self._public_completion_scope(status.get("extra", {}))
        if completion_scope:
            extra["completion_scope"] = completion_scope
        return {
            "run_id": run_id,
            "state": status.get("state", "unknown"),
            "step": status.get("step", 0),
            "step_label": status.get("step_label", ""),
            "layer": status.get("layer", ""),
            "error": status.get("error", ""),
            "error_kind": status.get("error_kind", ""),
            "repair": self._public_repair(status),
            "escalation": self._public_escalation(status.get("escalation")),
            "activity": self._public_activity(status.get("activity", {})),
            "started_at": status.get("started_at", ""),
            "updated_at": status.get("updated_at", ""),
            "state_revision": _state_revision(status.get("state_revision")),
            "total_steps": status.get("total_steps", 0),
            "done_steps": list(status.get("done_steps", [])),
            "extra": extra,
        }

    @staticmethod
    def _public_pending_action(extra: object) -> dict[str, Any]:
        """Keep only the fixed EQ confirmation record in public run state."""
        if not isinstance(extra, Mapping):
            return {}
        raw = extra.get("pending_action")
        if not isinstance(raw, Mapping):
            return {}

        def clean(value: object, limit: int) -> str:
            text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
            if not text or "/" in text or "\\" in text or ".." in text:
                return ""
            return text[:limit]

        def knowledge_source(value: object) -> dict[str, Any]:
            if not isinstance(value, Mapping):
                return {}
            status = value.get("knowledge_status")
            source = value.get("advice_source")
            if status not in {"retrieved", "not_matched", "unavailable"} or source not in {"knowledge_base", "llm_unverified"}:
                return {}
            entries = []
            for item in value.get("knowledge_entries", []) if isinstance(value.get("knowledge_entries"), list) else []:
                if len(entries) >= 3 or not isinstance(item, Mapping) or isinstance(item.get("number"), bool):
                    continue
                try:
                    number = int(item.get("number"))
                except (TypeError, ValueError):
                    continue
                name = clean(item.get("name"), 180)
                if name:
                    entries.append({"number": number, "name": name})
            return {
                "knowledge_status": status,
                "knowledge_entries": entries,
                "advice_source": source,
                "compatibility_notice": clean(value.get("compatibility_notice"), 300),
            }

        action_id = clean(raw.get("action_id"), 80)
        step_label = clean(raw.get("step_label"), 80)
        summary = clean(raw.get("summary"), 240)
        restart_step = raw.get("restart_step")
        if (
            not action_id
            or not step_label
            or not summary
            or isinstance(restart_step, bool)
            or not STEP_REGISTRY.controlled_restart_allowed(EQ_STEP, restart_step)
            or raw.get("state") not in {"pending", "awaiting_confirmation"}
        ):
            return {}
        adjustments = []
        for item in raw.get("adjustments", []) if isinstance(raw.get("adjustments"), list) else []:
            if not isinstance(item, Mapping) or len(adjustments) >= 8:
                continue
            name = clean(item.get("name"), 80)
            before = clean(item.get("before"), 80)
            after = clean(item.get("after"), 80)
            purpose = clean(item.get("purpose") or item.get("reason"), 120)
            if not name or not before or not after:
                continue
            adjustment = {"name": name, "before": before, "after": after}
            if purpose:
                adjustment["purpose"] = purpose
            adjustments.append(adjustment)
        public = {
            "action_id": action_id,
            "state": "pending",
            "step_label": step_label,
            "restart_step": restart_step,
            "summary": summary,
            "adjustments": adjustments,
        }
        public.update(knowledge_source(raw))
        if isinstance(raw.get("selected_option_id"), str):
            public["selected_option_id"] = raw["selected_option_id"]
        public["selection_required"] = bool(raw.get("selection_required", False))
        options = []
        for ordinal, option in enumerate(raw.get("options", []) if isinstance(raw.get("options"), list) else [], 1):
            if not isinstance(option, Mapping) or len(options) >= 3:
                continue
            option_summary = clean(option.get("summary"), 240)
            option_restart = option.get("restart_step")
            if not option_summary or not isinstance(option_restart, int) or not STEP_REGISTRY.controlled_restart_allowed(EQ_STEP, option_restart):
                continue
            option_adjustments = []
            for item in option.get("adjustments", []) if isinstance(option.get("adjustments"), list) else []:
                if not isinstance(item, Mapping) or len(option_adjustments) >= 8:
                    continue
                name = clean(item.get("name"), 80)
                before = clean(item.get("before"), 80)
                after = clean(item.get("after"), 80)
                if name and before and after:
                    entry = {"name": name, "before": before, "after": after}
                    purpose = clean(item.get("purpose") or item.get("reason"), 120)
                    if purpose:
                        entry["purpose"] = purpose
                    option_adjustments.append(entry)
            safe_editable = []
            for raw_editable in option.get("editable_parameters", []) if isinstance(option.get("editable_parameters"), list) else []:
                if not isinstance(raw_editable, Mapping) or len(safe_editable) >= 8:
                    continue
                editable_entry = {
                    key: clean(raw_editable.get(key), 80)
                    for key in ("name", "current", "range")
                }
                if all(editable_entry.values()):
                    purpose = clean(raw_editable.get("purpose"), 120)
                    if purpose:
                        editable_entry["purpose"] = purpose
                    safe_editable.append(editable_entry)
            option_public = {
                "option_id": clean(option.get("option_id"), 80) or f"option_{ordinal}",
                "ordinal": ordinal,
                "title": clean(option.get("title"), 120) or f"方案{ordinal}",
                "cause": clean(option.get("cause"), 240),
                "evidence": clean(option.get("evidence"), 300),
                "summary": option_summary,
                "restart_step": option_restart,
                "adjustments": option_adjustments,
                "editable_parameters": safe_editable,
            }
            option_public.update(knowledge_source(option))
            options.append(option_public)
        if len(options) > 1:
            public["options"] = options
        return public

    @staticmethod
    def _public_pending_fork(extra: object) -> dict[str, Any]:
        """Keep a bounded, user-reviewable natural-language fork proposal."""
        if not isinstance(extra, Mapping):
            return {}
        raw = extra.get("pending_fork")
        if not isinstance(raw, Mapping) or raw.get("status") != "awaiting_confirmation":
            return {}
        proposal_id = raw.get("proposal_id")
        run_id = raw.get("run_id")
        fingerprint = raw.get("config_fingerprint")
        if not all(isinstance(value, str) and value for value in (proposal_id, run_id, fingerprint)):
            return {}
        if not re.fullmatch(r"[A-Za-z0-9_-]{12,128}", proposal_id):
            return {}
        if not _RUN_ID_RE.fullmatch(run_id) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
            return {}
        restart_step = raw.get("restart_step")
        stopped_step = raw.get("stopped_step")
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            or not 1 <= value <= STEP_REGISTRY.total_steps
            for value in (restart_step, stopped_step)
        ):
            return {}
        changes = raw.get("changes")
        if not isinstance(changes, list) or not 1 <= len(changes) <= 16:
            return {}
        clean_changes: list[dict[str, Any]] = []
        for item in changes:
            if not isinstance(item, Mapping):
                return {}
            path = item.get("path")
            value = item.get("value")
            if not isinstance(path, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,159}", path):
                return {}
            if isinstance(value, bool) or isinstance(value, (Mapping, list)) or value is None:
                return {}
            if not isinstance(value, (str, int, float)):
                return {}
            clean_changes.append({"path": path, "value": value})
        paths = raw.get("parameter_paths")
        if not isinstance(paths, list) or sorted(paths) != sorted(item["path"] for item in clean_changes):
            return {}
        return {
            "proposal_id": proposal_id,
            "run_id": run_id,
            "status": "awaiting_confirmation",
            "config_fingerprint": fingerprint,
            "stopped_step": stopped_step,
            "restart_step": restart_step,
            "parameter_paths": sorted(paths),
            "changes": clean_changes,
        }

    @staticmethod
    def _public_completion_scope(extra: object) -> dict[str, Any]:
        """Keep the single non-executable EQ-only acceptance scope public."""
        if not isinstance(extra, Mapping):
            return {}
        raw = extra.get("completion_scope")
        if not isinstance(raw, Mapping):
            return {}
        if (
            raw.get("mode") == "through_eq"
            and raw.get("stage") == "eq"
            and raw.get("step") == EQ_STEP
        ):
            return {"mode": "through_eq", "step": EQ_STEP, "stage": "eq"}
        return {}

    @staticmethod
    def _public_repair(status: Mapping[str, Any]) -> dict[str, Any]:
        """Whitelist compact repair progress and applied config changes."""
        raw = status.get("repair")
        if not isinstance(raw, Mapping):
            raw = {
                "attempt": status.get("retry_n", 0),
                "max_attempts": status.get("retry_max", 0),
                "adjustments": status.get("adjustments", []),
            }
        try:
            attempt = max(0, int(raw.get("attempt", 0)))
            max_attempts = max(0, int(raw.get("max_attempts", 0)))
        except (TypeError, ValueError):
            attempt = max_attempts = 0
        adjustments = []
        for item in raw.get("adjustments", []) if isinstance(raw.get("adjustments"), list) else []:
            if not isinstance(item, Mapping):
                continue
            values = {key: str(item.get(key, "")).replace("\n", " ").replace("\r", " ").strip()
                      for key in ("name", "before", "after")}
            if (
                all(values.values())
                and all(len(value) <= 80 and "/" not in value and "\\" not in value and ".." not in value
                        for value in values.values())
                and values["before"] != values["after"]
            ):
                adjustments.append(values)
        if status.get("state") != "retrying" and not adjustments:
            return {}
        return {
            "attempt": attempt,
            "max_attempts": max_attempts,
            "adjustments": adjustments[-8:],
        }

    @staticmethod
    def _public_escalation(value: object) -> dict[str, Any]:
        """Whitelist a compact human-actionable recovery conclusion."""
        if not isinstance(value, Mapping):
            return {}

        def clean(item: object, limit: int) -> str:
            text = str(item or "").replace("\n", " ").replace("\r", " ").strip()
            if not text or "/" in text or "\\" in text or ".." in text:
                return ""
            return text[:limit]

        attempts = value.get("attempts_made", 0)
        if isinstance(attempts, bool):
            attempts = 0
        try:
            attempts = max(0, min(int(attempts), 99))
        except (TypeError, ValueError):
            attempts = 0
        actions = [
            text for text in (clean(item, 160) for item in value.get("actions_tried", []))
            if text
        ][:8] if isinstance(value.get("actions_tried"), list) else []
        public = {
            "layer": clean(value.get("layer"), 80),
            "step": clean(value.get("step"), 120),
            "error_kind": clean(value.get("error_kind"), 80),
            "attempts_made": attempts,
            "actions_tried": actions,
            "recommendation": clean(value.get("recommendation"), 300),
            "backup_plan": clean(value.get("backup_plan"), 300),
        }
        return {key: item for key, item in public.items() if item not in ("", [])}

    def _read_json(self, path: Path, label: str) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise RunRegistryError(f"无法读取{label}: {exc}") from exc
        if not isinstance(payload, dict):
            raise RunRegistryError(f"{label}格式无效")
        return payload

    @staticmethod
    def _public_timestamp(value: Any) -> str | None:
        if not isinstance(value, str) or len(value) > 64:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()

    @classmethod
    def _public_local_timestamp(cls, value: Any) -> str | None:
        """Format a validated UTC instant in the host's local timezone for UI use."""
        utc_value = cls._public_timestamp(value)
        if utc_value is None:
            return None
        local = datetime.fromisoformat(utc_value).astimezone()
        offset = local.strftime("%z")
        offset_label = "UTC"
        if len(offset) == 5:
            offset_label = f"UTC{offset[:3]}:{offset[3:]}"
        return f"{local:%Y-%m-%d %H:%M:%S} 本地时间（{offset_label}）"

    @classmethod
    def _add_local_timestamp(cls, target: dict[str, Any], field: str) -> None:
        formatted = cls._public_local_timestamp(target.get(field))
        if formatted is not None:
            target[f"{field}_local"] = formatted

    def _add_live_mdrun_facts(self, payload: Mapping[str, Any], result: dict[str, Any]) -> None:
        """Copy only bounded liveness fields from a private ETA snapshot."""
        if isinstance(payload.get("process_alive"), bool):
            result["process_alive"] = payload["process_alive"]
        source = payload.get("source")
        if source in {"startup", "mdrun_process", "stage_artifacts", "gmx_verbose"}:
            result["source"] = source
        progress_at = self._public_timestamp(payload.get("last_progress_at"))
        if progress_at is not None:
            result["last_progress_at"] = progress_at
            self._add_local_timestamp(result, "last_progress_at")
        progress_step = payload.get("last_progress_step")
        if isinstance(progress_step, int) and progress_step >= 0:
            result["last_progress_step"] = progress_step

    def _read_events(self, directory: Path) -> list[dict[str, Any]]:
        path = directory / EVENTS_FILENAME
        if not path.is_file():
            return []
        events = []
        for line in path.read_text(errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        return events

    def _update_index(self, directory: Path, manifest: Mapping[str, Any], status: Mapping[str, Any] | None = None) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                payload = {"version": 1, "runs": []}
                if self.index_path.is_file():
                    try:
                        payload = self._read_json(self.index_path, "运行索引")
                    except RunRegistryError:
                        payload = {"version": 1, "runs": []}
                entries = [item for item in payload.get("runs", []) if item.get("run_id") != directory.name]
                snapshot = dict(status or {})
                entries.append({
                    "run_id": directory.name,
                    "display_name": manifest.get("display_name", ""),
                    "created_at": manifest.get("created_at", ""),
                    "updated_at": snapshot.get("updated_at", manifest.get("updated_at", _now())),
                    "backend": manifest.get("backend", ""),
                    "state": snapshot.get("state", "unknown"),
                    "step": snapshot.get("step", 0),
                    "total_steps": snapshot.get("total_steps", manifest.get("total_steps", 0)),
                    "parent_run_id": manifest.get("parent_run_id"),
                })
                _atomic_write(self.index_path, {"version": 1, "runs": entries})
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _discover_runs(self) -> list[dict[str, Any]]:
        """Read legacy manifests without creating an index as a query side effect."""
        if not self.runs_dir.is_dir():
            return []
        entries = []
        for directory in self.runs_dir.iterdir():
            if not directory.is_dir() or not _RUN_ID_RE.fullmatch(directory.name):
                continue
            manifest_file = directory / MANIFEST_FILENAME
            unified_file = directory / RUN_MANIFEST_FILENAME
            if not manifest_file.is_file() and not unified_file.is_file():
                continue
            try:
                manifest = self._read_registry_manifest(directory)
                status = self._read_json(directory / STATUS_FILENAME, "运行状态") if (directory / STATUS_FILENAME).is_file() else {}
            except RunRegistryError:
                continue
            entries.append({
                "run_id": directory.name,
                "display_name": manifest.get("display_name", ""),
                "created_at": manifest.get("created_at", ""),
                "updated_at": status.get("updated_at", manifest.get("updated_at", "")),
                "backend": manifest.get("backend", ""),
                "state": status.get("state", "unknown"),
                "step": status.get("step", 0),
                "total_steps": status.get("total_steps", manifest.get("total_steps", 0)),
                "parent_run_id": manifest.get("parent_run_id"),
            })
        return sorted(
            entries,
            key=lambda item: (str(item.get("updated_at", "")), str(item.get("run_id", ""))),
            reverse=True,
        )

    @staticmethod
    def _redact(text: str) -> str:
        return re.sub(r"(?i)(api[_-]?key|token|password)\s*[=:]\s*\S+", r"\1=[REDACTED]", text)
