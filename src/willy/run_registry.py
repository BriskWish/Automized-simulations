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
from willy.run_store import run_transaction
from willy.step_registry import EQ_STEP, STEP_REGISTRY


RUNS_DIRNAME = "md_run"
INDEX_FILENAME = "index.json"
MANIFEST_FILENAME = "manifest.json"
STATUS_FILENAME = "status.json"
STATUS_LOCK_FILENAME = ".status.lock"
EVENTS_FILENAME = "events.jsonl"
DECISION_TRACE_FILENAME = "decision_trace.jsonl"
ENVIRONMENT_REPORT_FILENAME = "environment_report.json"
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
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
        created = not manifest_file.exists()
        if created:
            config_file = directory / "config.json"
            input_files = [
                {"path": path.name, "sha256": _file_sha256(path)}
                for path in sorted(directory.iterdir())
                if path.is_file() and path.suffix in {".gjf", ".fchk", ".molden"}
            ]
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
            _atomic_write(manifest_file, manifest)
            self.append_event(directory, "run_created", {
                "backend": backend,
                "total_steps": total_steps,
                "parent_run_id": parent_run_id,
            })
        else:
            manifest = self._read_json(manifest_file, "运行 manifest")
            self.append_event(directory, "run_reopened", {"backend": backend})
        self._update_index(directory, manifest)
        return manifest

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
            manifest = self._read_json(directory / MANIFEST_FILENAME, "运行 manifest")
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
            with run_transaction(directory) as store:
                store.commit_bundle(
                    operation="compare_and_swap_status",
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
            manifest = self._read_json(directory / MANIFEST_FILENAME, "运行 manifest")
            self._update_index(directory, manifest, status=payload)
        except (OSError, RunRegistryError):
            pass
        return payload

    def request_stop(self, run_id: str) -> dict[str, Any]:
        """Persist a checkpoint-first user stop request for one active run."""
        return self._write_stop_state(run_id, state="stopping", event_type="run_stop_requested")

    def mark_aborted(self, run_id: str, *, event_type: str = "run_aborted") -> dict[str, Any]:
        """Persist a terminal user/interrupted-run outcome without engine details."""
        return self._write_stop_state(run_id, state="aborted", event_type=event_type)

    def _write_stop_state(self, run_id: str, *, state: str, event_type: str) -> dict[str, Any]:
        if state not in {"stopping", "aborted"}:
            raise ValueError("无效的运行停止状态")
        directory = self.resolve_run_id(run_id)
        current = self.get_run_status(run_id)
        if current.get("state") in {"done", "escalated", "aborted"}:
            return current
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
            expected_revision=_state_revision(current.get("state_revision")),
            status=updated,
            event_type=event_type,
        )
        return self.get_run_status(run_id)

    def record_environment_report(
        self,
        run_dir: str | Path,
        capabilities: Mapping[str, Mapping[str, Any]],
    ) -> None:
        """Store a redacted external-tool discovery snapshot for one run."""
        directory = self._validate_run_directory(run_dir)
        safe: dict[str, dict[str, Any]] = {}
        for tool_id, value in capabilities.items():
            if not isinstance(tool_id, str) or not isinstance(value, Mapping):
                continue
            version = value.get("version")
            safe[tool_id] = {
                "tool_id": str(value.get("tool_id", tool_id)),
                "label": str(value.get("label", tool_id)),
                "status": str(value.get("status", "unknown")),
                "source": str(value.get("source", "")),
                "reason": str(value.get("reason", "")),
                "version": None if version is None else str(version),
            }
        unavailable = sorted(tool_id for tool_id, value in safe.items() if value["status"] != "available")
        with run_transaction(directory) as store:
            store.commit_bundle(
                operation="record_environment_report",
                json_writes={ENVIRONMENT_REPORT_FILENAME: {
                    "schema_version": 1,
                    "capabilities": safe,
                }},
                jsonl_appends={EVENTS_FILENAME: [{
                    "timestamp": _now(),
                    "event_type": "environment_discovered",
                    "run_id": directory.name,
                    "details": {
                        "available_count": len(safe) - len(unavailable),
                        "unavailable_tools": unavailable,
                    },
                }]},
            )

    def get_environment_report(self, run_id: str) -> dict[str, Any]:
        """Read a run's redacted capability report with an injection-safe schema."""
        payload = self._read_json(
            self.resolve_run_id(run_id) / ENVIRONMENT_REPORT_FILENAME,
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

    def get_run_status(self, run_id: str) -> dict[str, Any]:
        directory = self.resolve_run_id(run_id)
        status_file = directory / STATUS_FILENAME
        if not status_file.is_file():
            return {"run_id": run_id, "state": "unknown", "message": "该 run 尚未写入状态快照"}
        status = self._read_json(status_file, "运行状态")
        status = self._reconcile_active_simulation_stage(directory, status)
        status["run_id"] = run_id
        self._add_local_timestamp(status, "started_at")
        self._add_local_timestamp(status, "updated_at")
        return status

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
        manifest_file = directory / "md_manifest.json"
        if not manifest_file.is_file():
            return status
        try:
            manifest = self._read_json(manifest_file, "MD manifest")
        except RunRegistryError:
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
                if observed is not None:
                    observed_at = datetime.fromisoformat(observed)
                    if (now - observed_at).total_seconds() <= freshness_s:
                        return True

        for suffix in ("log", "edr", "xtc", "cpt"):
            try:
                modified = datetime.fromtimestamp(
                    (directory / f"{stage}.{suffix}").stat().st_mtime,
                    timezone.utc,
                )
            except OSError:
                continue
            if (now - modified).total_seconds() <= freshness_s:
                return True
        return False

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
        status = self.get_run_status(run_id)
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
        manifest = self._read_json(directory / MANIFEST_FILENAME, "运行 manifest")
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
        path = directory / "md_manifest.json"
        if not path.is_file():
            return {"run_id": run_id, "status": "unavailable", "reason": "尚未创建 MD 建盒记录"}
        payload = self._read_json(path, "MD manifest")
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
        status = self.get_run_status(run_id)
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
            "done_steps": status.get("done_steps", []),
        }
        pending_action = self._public_pending_action(status.get("extra", {}))
        if pending_action:
            summary["pending_action"] = pending_action
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
        return {
            "run_id": run_id,
            "state": status.get("state", "unknown"),
            "step": status.get("step", 0),
            "step_label": status.get("step_label", ""),
            "layer": status.get("layer", ""),
            "error": status.get("error", ""),
            "error_kind": status.get("error_kind", ""),
            "repair": self._public_repair(status),
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
        return {
            "action_id": action_id,
            "state": "pending",
            "step_label": step_label,
            "restart_step": restart_step,
            "summary": summary,
            "adjustments": adjustments,
        }

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
            if not manifest_file.is_file():
                continue
            try:
                manifest = self._read_json(manifest_file, "运行 manifest")
                status = self._read_json(directory / STATUS_FILENAME, "运行状态") if (directory / STATUS_FILENAME).is_file() else {}
            except RunRegistryError:
                continue
            entries.append({
                "run_id": directory.name,
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
