"""Run-local MD manifest, provenance fingerprints, locking, and stop requests."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import asdict, is_dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterable, Mapping, Any
import fcntl
import json
import os
import shutil
import subprocess

from willy.simulation.protocol import canonical_json_fingerprint
from willy.env_registry import require_tool
from willy._paths import get_project_root
from willy.run_store import run_transaction


MANIFEST_FILENAME = "md_manifest.json"
LOCK_FILENAME = ".md.lock"
STOP_REQUEST_FILENAME = "stop.request"
_STAGE_OUTPUT_SUFFIXES = ("tpr", "gro", "xtc", "edr", "log", "cpt", "trr")
_STAGE_ORDER = ("em", "eq", "prod")


class ManifestError(ValueError):
    """A run-local manifest is absent, inconsistent, or not permitted."""


class RunLockError(RuntimeError):
    """Raised when another process currently owns the MD run lock."""


def _json_safe(value: Any) -> Any:
    """Convert stage evidence dataclasses and paths before manifest writes."""
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_fingerprint(path: str | Path, root: str | Path | None = None) -> dict[str, Any]:
    """Return a content fingerprint with a run-relative path when possible."""
    file_path = Path(path).resolve()
    if not file_path.is_file():
        raise ManifestError(f"无法为不存在的文件生成指纹: {file_path}")
    display_path = str(file_path)
    if root is not None:
        try:
            display_path = file_path.relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            pass
    return {
        "path": display_path,
        "sha256": _sha256_file(file_path),
        "size_bytes": file_path.stat().st_size,
    }


def archive_config_revision(run_dir: str | Path, config_path: str | Path) -> dict[str, Any]:
    """Keep a content-addressed copy of every config used to start a stage."""
    directory = Path(run_dir)
    source = Path(config_path)
    fingerprint = file_fingerprint(source, directory)
    archive_dir = directory / "config_revisions"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive = archive_dir / f"{fingerprint['sha256']}.json"
    if not archive.exists():
        shutil.copy2(source, archive)
    return {
        **fingerprint,
        "archive": archive.relative_to(directory).as_posix(),
    }


def _record_config_revision(manifest: dict[str, Any], evidence: Mapping[str, Any]) -> None:
    revisions = manifest.setdefault("config_revisions", [])
    if any(item.get("sha256") == evidence.get("sha256") for item in revisions):
        return
    revisions.append({"recorded_at": _now(), **dict(evidence)})


def _command_version(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    text = (result.stdout or result.stderr or "").strip()
    return text.splitlines()[0][:240] if text else f"exit={result.returncode}"


def resource_snapshot(run_dir: str | Path) -> dict[str, Any]:
    directory = Path(run_dir)
    disk = shutil.disk_usage(directory)
    return {
        "cpu_count": os.cpu_count() or 1,
        "gpu_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
        "disk_free_bytes": disk.free,
        "disk_total_bytes": disk.total,
    }


def tool_versions() -> dict[str, str]:
    """Collect versions once per run; failure is evidence, not a crash."""
    try:
        gmx_command = [str(require_tool("gmx").executable), "--version"]
    except RuntimeError:
        gmx_command = ["__willy_missing_gmx__"]
    packmol_command = [str(get_project_root() / "vendor" / "packmol"), "--version"]
    return {
        "gromacs": _command_version(gmx_command),
        "packmol": _command_version(packmol_command),
    }


def manifest_path(run_dir: str | Path) -> Path:
    return Path(run_dir) / MANIFEST_FILENAME


def load_manifest(run_dir: str | Path) -> dict[str, Any]:
    path = manifest_path(run_dir)
    if not path.is_file():
        raise ManifestError(f"缺少 MD manifest: {path}")
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ManifestError(f"MD manifest 格式无效: {exc}") from exc
    return _validate_manifest_payload(payload)


def _validate_manifest_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ManifestError("MD manifest schema_version 无效")
    payload.setdefault("stages", {})
    payload.setdefault("events", [])
    return payload


def initialize_manifest(
    run_dir: str | Path,
    config_path: str | Path,
    *,
    random_seed: int,
    versions: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Create a run-local manifest or refresh immutable run-level facts."""
    directory = Path(run_dir)
    config = Path(config_path)
    config_fp = archive_config_revision(directory, config)
    with run_transaction(directory) as store:
        raw = store.read_json(MANIFEST_FILENAME)
        if raw is not None:
            payload = _validate_manifest_payload(raw)
            payload["updated_at"] = _now()
            payload.setdefault("config", config_fp)
            _record_config_revision(payload, config_fp)
            payload.setdefault("random_seed", int(random_seed))
            payload.setdefault("tool_versions", dict(versions or tool_versions()))
            payload.setdefault("resources", resource_snapshot(directory))
        else:
            payload = {
                "schema_version": 1,
                "created_at": _now(),
                "updated_at": _now(),
                "config": config_fp,
                "config_revisions": [{"recorded_at": _now(), **config_fp}],
                "random_seed": int(random_seed),
                "tool_versions": dict(versions or tool_versions()),
                "resources": resource_snapshot(directory),
                "stages": {},
                "box_attempts": [],
                "events": [{"time": _now(), "type": "manifest_initialized"}],
            }
        store.write_json(MANIFEST_FILENAME, payload)
        return payload


def stage_contract(
    run_dir: str | Path,
    stage: str,
    *,
    config_path: str | Path,
    topol: str | Path,
    itps: Iterable[str | Path],
    mdp: str | Path,
    coordinates: str | Path,
    parent_checkpoint: str | Path | None = None,
) -> dict[str, Any]:
    """Fingerprint every input that determines one stage's physical protocol."""
    directory = Path(run_dir)
    inputs = {
        "config": archive_config_revision(directory, config_path),
        "topol": file_fingerprint(topol, directory),
        "itps": [file_fingerprint(path, directory) for path in sorted(map(Path, itps))],
        "mdp": file_fingerprint(mdp, directory),
        "coordinates": file_fingerprint(coordinates, directory),
    }
    if parent_checkpoint is not None:
        inputs["parent_checkpoint"] = file_fingerprint(parent_checkpoint, directory)
    contract = {
        "stage": stage,
        "inputs": inputs,
        "fingerprint": canonical_json_fingerprint(inputs),
    }
    return contract


def require_prior_stage(run_dir: str | Path, stage: str) -> dict[str, Any] | None:
    """Require accepted upstream stages before every dynamic phase."""
    parent = {"eq": "em", "prod": "eq"}.get(stage)
    if parent is None:
        return None
    manifest = load_manifest(run_dir)
    record = manifest.get("stages", {}).get(parent, {})
    if record.get("status") != "accepted":
        raise ManifestError(f"{stage.upper()} 只能消费已验收的 {parent.upper()}，文件存在本身不构成许可")
    return record


def stage_can_resume(run_dir: str | Path, stage: str, contract: Mapping[str, Any]) -> bool:
    """Only permit ``-cpi -append`` with a matching interrupted stage record."""
    manifest = load_manifest(run_dir)
    record = manifest.get("stages", {}).get(stage, {})
    checkpoint = Path(run_dir) / f"{stage}.cpt"
    return (
        record.get("status") in {"running", "failed", "interrupted"}
        and record.get("contract", {}).get("fingerprint") == contract.get("fingerprint")
        and checkpoint.is_file()
        and checkpoint.stat().st_size > 0
    )


def prepare_stage_attempt(
    run_dir: str | Path,
    stage: str,
    contract: Mapping[str, Any],
    *,
    estimated_output_bytes: int = 0,
) -> dict[str, Any]:
    """Record a new attempt and clear incompatible same-stage artifacts.

    This implements the allowed cleanup branch strategy: a changed protocol
    never shares ``.xtc/.edr/.cpt`` files with the previous protocol.
    """
    directory = Path(run_dir)
    with run_transaction(directory) as store:
        manifest = _validate_manifest_payload(store.read_json(MANIFEST_FILENAME))
        stages = manifest.setdefault("stages", {})
        old = stages.get(stage, {})
        same_contract = old.get("contract", {}).get("fingerprint") == contract.get("fingerprint")
        cleaned: list[str] = []
        if old and not same_contract:
            cleaned = clear_stage_outputs(directory, stage)
            _invalidate_downstream_stages(manifest, directory, stage)
        history = list(old.get("history", []))
        if old:
            history.append({
                "attempt": old.get("attempt", 0),
                "status": old.get("status", "unknown"),
                "contract_fingerprint": old.get("contract", {}).get("fingerprint", ""),
                "completed_at": old.get("completed_at", ""),
                "superseded": not same_contract,
            })
        record = {
            "status": "running",
            "attempt": int(old.get("attempt", 0)) + 1,
            "started_at": _now(),
            "contract": dict(contract),
            "estimated_output_bytes": int(max(0, estimated_output_bytes)),
            "history": history,
        }
        stages[stage] = record
        _record_config_revision(manifest, contract["inputs"]["config"])
        manifest["updated_at"] = _now()
        manifest.setdefault("events", []).append({
            "time": _now(),
            "type": "stage_started",
            "stage": stage,
            "attempt": record["attempt"],
            "cleared_outputs": cleaned,
        })
        store.write_json(MANIFEST_FILENAME, manifest)
        return record


def _invalidate_downstream_stages(manifest: dict[str, Any], directory: Path, stage: str) -> None:
    """Withdraw downstream acceptance when an upstream protocol changes."""
    try:
        stage_index = _STAGE_ORDER.index(stage)
    except ValueError:
        return
    stages = manifest.setdefault("stages", {})
    for dependent in _STAGE_ORDER[stage_index + 1:]:
        old = stages.get(dependent)
        if not old:
            continue
        cleaned = clear_stage_outputs(directory, dependent)
        history = list(old.get("history", []))
        history.append({
            "attempt": old.get("attempt", 0),
            "status": old.get("status", "unknown"),
            "contract_fingerprint": old.get("contract", {}).get("fingerprint", ""),
            "completed_at": old.get("completed_at", ""),
            "invalidated_by": stage,
        })
        stages[dependent] = {
            "status": "invalidated",
            "attempt": old.get("attempt", 0),
            "invalidated_at": _now(),
            "invalidated_by": stage,
            "history": history,
        }
        manifest.setdefault("events", []).append({
            "time": _now(),
            "type": "stage_invalidated",
            "stage": dependent,
            "invalidated_by": stage,
            "cleared_outputs": cleaned,
        })


def invalidate_stages_from(
    run_dir: str | Path,
    stage: str,
    *,
    reason: str,
) -> list[str]:
    """Invalidate one changed stage and every downstream stage in a run.

    A global protocol edit cannot leave a stale downstream MDP or accepted
    output eligible for reuse.  This function clears only regenerable stage
    outputs and leaves the prior contract in history for auditability.
    """
    directory = Path(run_dir)
    try:
        start = _STAGE_ORDER.index(stage)
    except ValueError as exc:
        raise ManifestError(f"未知 MD 阶段: {stage}") from exc
    with run_transaction(directory) as store:
        manifest = _validate_manifest_payload(store.read_json(MANIFEST_FILENAME))
        stages = manifest.setdefault("stages", {})
        invalidated: list[str] = []
        for affected in _STAGE_ORDER[start:]:
            old = stages.get(affected)
            if not old:
                continue
            cleaned = clear_stage_outputs(directory, affected)
            history = list(old.get("history", []))
            history.append({
                "attempt": old.get("attempt", 0),
                "status": old.get("status", "unknown"),
                "contract_fingerprint": old.get("contract", {}).get("fingerprint", ""),
                "completed_at": old.get("completed_at", ""),
                "invalidated_by": "configuration_change",
                "reason": reason,
            })
            stages[affected] = {
                "status": "invalidated",
                "attempt": old.get("attempt", 0),
                "invalidated_at": _now(),
                "invalidated_by": "configuration_change",
                "reason": reason,
                "history": history,
            }
            invalidated.append(affected)
            manifest.setdefault("events", []).append({
                "time": _now(),
                "type": "stage_invalidated",
                "stage": affected,
                "invalidated_by": "configuration_change",
                "reason": reason,
                "cleared_outputs": cleaned,
            })
        if invalidated:
            manifest["updated_at"] = _now()
            store.write_json(MANIFEST_FILENAME, manifest)
        return invalidated


def record_stage_result(
    run_dir: str | Path,
    stage: str,
    *,
    success: bool,
    contract: Mapping[str, Any],
    outputs: Mapping[str, str] | None = None,
    details: Mapping[str, Any] | None = None,
    error_kind: str = "",
    error_message: str = "",
    error_evidence: Mapping[str, Any] | None = None,
) -> None:
    """Persist stage completion and private failure evidence for one run."""
    directory = Path(run_dir)
    with run_transaction(directory) as store:
        manifest = _validate_manifest_payload(store.read_json(MANIFEST_FILENAME))
        record = manifest.setdefault("stages", {}).setdefault(stage, {})
        record["status"] = "accepted" if success and stage in {"em", "eq"} else ("completed" if success else "failed")
        record["completed_at"] = _now()
        record["contract"] = dict(contract)
        if outputs:
            record["outputs"] = {
                key: file_fingerprint(value, directory)
                for key, value in outputs.items()
                if Path(value).is_file()
            }
        if details:
            record["details"] = _json_safe(details)
        if success:
            record.pop("error", None)
            record.pop("error_evidence", None)
        else:
            record["error"] = {"kind": error_kind, "message": error_message[:500]}
            if error_evidence:
                record["error_evidence"] = _json_safe(error_evidence)
            else:
                record.pop("error_evidence", None)
        manifest["updated_at"] = _now()
        manifest.setdefault("events", []).append({
            "time": _now(),
            "type": "stage_accepted" if success else "stage_failed",
            "stage": stage,
            "status": record["status"],
        })
        store.write_json(MANIFEST_FILENAME, manifest)


def record_box_attempt(run_dir: str | Path, parameters: Mapping[str, Any]) -> None:
    """Store requested and observed Packmol geometry for rollback and diagnosis."""
    directory = Path(run_dir)
    with run_transaction(directory) as store:
        manifest = _validate_manifest_payload(store.read_json(MANIFEST_FILENAME))
        attempts = manifest.setdefault("box_attempts", [])
        attempts.append({"time": _now(), **dict(parameters)})
        manifest["updated_at"] = _now()
        store.write_json(MANIFEST_FILENAME, manifest)


def box_parameters_changed(run_dir: str | Path) -> bool:
    manifest = load_manifest(run_dir)
    attempts = manifest.get("box_attempts", [])
    if len(attempts) < 2:
        return True
    previous, current = attempts[-2], attempts[-1]
    keys = (
        "box_strategy", "target_mass_density_g_cm3", "packing_number_density_nm3",
        "actual_box_vectors_angstrom", "box_size_angstrom", "box_volume_nm3",
        "tolerance_angstrom",
    )
    return any(previous.get(key) != current.get(key) for key in keys)


def clear_stage_outputs(run_dir: str | Path, stage: str) -> list[str]:
    """Remove only regenerable artifacts for one stage from one run directory."""
    directory = Path(run_dir)
    names = [f"{stage}.{suffix}" for suffix in _STAGE_OUTPUT_SUFFIXES]
    names.append(f"{stage}_out.mdp")
    if stage == "eq":
        names.extend(["density.xvg", "temp.xvg", "pressure.xvg", "potential.xvg"])
    removed: list[str] = []
    for name in names:
        path = directory / name
        if path.is_file():
            path.unlink()
            removed.append(name)
    return removed


def estimate_output_bytes(atom_count: int, nsteps: int, *, trr_enabled: bool) -> int:
    """Conservative lightweight estimate used only for preflight disk checks."""
    frames = max(1, nsteps // 1000 + 1)
    xtc = frames * max(atom_count, 1) * 12
    edr = frames * 2048
    checkpoint_and_logs = max(atom_count, 1) * 256 + 8 * 1024 * 1024
    trr = frames * max(atom_count, 1) * 36 if trr_enabled else 0
    return int((xtc + edr + checkpoint_and_logs + trr) * 1.25)


def has_disk_capacity(run_dir: str | Path, estimated_output_bytes: int) -> bool:
    return shutil.disk_usage(run_dir).free >= max(estimated_output_bytes, 32 * 1024 * 1024)


class RunLock:
    """A non-blocking advisory lock scoped to exactly one MD run directory."""

    def __init__(self, run_dir: str | Path):
        self.path = Path(run_dir) / LOCK_FILENAME
        self._handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._handle.close()
            self._handle = None
            raise RunLockError("该 run 已被另一进程占用") from exc
        self._handle.seek(0)
        self._handle.truncate()
        self._handle.write(json.dumps({"pid": os.getpid(), "acquired_at": _now()}) + "\n")
        self._handle.flush()

    def release(self) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "RunLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def request_safe_stop(run_dir: str | Path) -> Path:
    """Ask the running GROMACS wrapper to send SIGINT and write a checkpoint."""
    path = Path(run_dir) / STOP_REQUEST_FILENAME
    path.write_text(json.dumps({"requested_at": _now(), "mode": "checkpoint_then_stop"}) + "\n")
    return path


def stop_requested(run_dir: str | Path) -> bool:
    return (Path(run_dir) / STOP_REQUEST_FILENAME).is_file()


def clear_stop_request(run_dir: str | Path) -> None:
    (Path(run_dir) / STOP_REQUEST_FILENAME).unlink(missing_ok=True)
