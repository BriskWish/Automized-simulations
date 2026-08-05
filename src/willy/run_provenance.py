"""One redacted, reproducible provenance record for every pipeline run."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping
import json
import platform
import subprocess
import sys

from willy.config_store import write_json


RUN_PROVENANCE_FILENAME = "provenance.json"
RUN_PROVENANCE_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_fingerprint(path: Path, root: Path) -> dict[str, Any]:
    digest = sha256(path.read_bytes()).hexdigest()
    return {
        "path": path.resolve().relative_to(root.resolve()).as_posix(),
        "sha256": digest,
        "size_bytes": path.stat().st_size,
    }


def _source_revision(project_root: Path) -> dict[str, Any]:
    """Capture repository identity, never file names from a dirty worktree."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project_root,
            capture_output=True, text=True, timeout=3, check=False,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=project_root,
            capture_output=True, text=True, timeout=3, check=False,
        ).stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return {"commit": "unavailable", "dirty": None}
    return {"commit": commit if len(commit) == 40 else "unavailable", "dirty": dirty}


def _package_version() -> str:
    try:
        return metadata.version("willy")
    except metadata.PackageNotFoundError:
        return "source"


def _runtime() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": f"{platform.system()}-{platform.release()}",
        "willy": _package_version(),
    }


def _input_fingerprints(run_dir: Path) -> list[dict[str, Any]]:
    suffixes = {".gjf", ".fchk", ".molden", ".mol2", ".chg", ".itp", ".gro", ".top", ".mdp", ".pdb"}
    records = []
    for path in sorted(run_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in suffixes:
            records.append(_file_fingerprint(path, run_dir))
    return records


def _safe_capabilities(capabilities: Mapping[str, Mapping[str, object]]) -> dict[str, dict[str, object]]:
    allowed = {"tool_id", "label", "status", "source", "reason", "version"}
    return {
        str(tool_id): {key: value for key, value in value.items() if key in allowed}
        for tool_id, value in capabilities.items()
        if isinstance(tool_id, str) and isinstance(value, Mapping)
    }


def create_or_refresh_provenance(
    run_dir: str | Path,
    *,
    project_root: str | Path,
    backend: str,
    config_path: str | Path,
    random_seed: int,
    capabilities: Mapping[str, Mapping[str, object]],
    llm_model: str | None,
    prompt_versions: Mapping[str, str],
) -> dict[str, Any]:
    """Create or refresh one run-local record without storing secret material."""
    directory = Path(run_dir).resolve()
    root = Path(project_root).resolve()
    config = Path(config_path).resolve()
    if config.parent != directory or config.name != "config.json":
        raise ValueError("provenance 只能引用当前 run 的 config.json")
    if not config.is_file():
        raise ValueError("provenance 缺少运行配置快照")
    path = directory / RUN_PROVENANCE_FILENAME
    existing: dict[str, Any] = {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("schema_version") == RUN_PROVENANCE_SCHEMA_VERSION:
            existing = raw
    except (OSError, json.JSONDecodeError):
        pass
    config_fp = _file_fingerprint(config, directory)
    revisions = [item for item in existing.get("config_revisions", []) if isinstance(item, dict)]
    if not any(item.get("sha256") == config_fp["sha256"] for item in revisions):
        revisions.append({"recorded_at": _now(), **config_fp})
    payload = {
        "schema_version": RUN_PROVENANCE_SCHEMA_VERSION,
        "run_id": directory.name,
        "created_at": existing.get("created_at", _now()),
        "updated_at": _now(),
        "backend": backend,
        "source_revision": _source_revision(root),
        "runtime": _runtime(),
        "config": config_fp,
        "config_revisions": revisions,
        "input_fingerprints": _input_fingerprints(directory),
        "random_seed": int(random_seed),
        "capabilities": _safe_capabilities(capabilities),
        "llm": {
            "model": str(llm_model or ""),
            "prompt_versions": {str(key): str(value) for key, value in prompt_versions.items()},
        },
    }
    write_json(path, payload)
    return payload
