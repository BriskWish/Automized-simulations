"""Topology run manifest.

The manifest is the only hand-off from Step 4 to topology assembly and retry
tools.  It prevents stale files in a run directory from becoming inputs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from contextlib import contextmanager
import fcntl
import json
import os
import tempfile

from willy.run_metadata import (
    RUN_MANIFEST_FILENAME,
    load_run_metadata_section,
    load_run_manifest,
    update_run_manifest_section,
)


MANIFEST_FILENAME = "topology_manifest.json"
_MANIFEST_LOCK_FILENAME = ".topology_manifest.lock"
MAX_RETRIES_PER_ACTION = 2
MAX_TOPOLOGY_RETRIES = 4


@dataclass
class TopologyManifestComponent:
    molecule_id: str
    residue_name: str
    quantity: int
    mol2: str
    chg: str | None
    charge: int
    spin: int
    smiles: str | None
    backend: str
    forcefield_family: str
    lbcc: bool = False
    opt_steps: int = 0
    itp: str | None = None
    assembly_itp: str | None = None
    atomtype_namespace: dict[str, str] | None = None
    gro: str | None = None
    success: bool = False
    validated: bool = False
    error: str = ""


def manifest_path(workspace: str | Path) -> Path:
    """Return the legacy topology-only manifest location.

    This name remains part of the compatibility surface for old runs.  New
    unified runs keep the same payload in the private ``topology`` section of
    ``run_manifest.json`` instead.
    """
    return Path(workspace) / MANIFEST_FILENAME


def unified_manifest_path(workspace: str | Path) -> Path:
    """Return the schema-v2 unified run metadata location."""
    return Path(workspace) / RUN_MANIFEST_FILENAME


def manifest_exists(workspace: str | Path) -> bool:
    """Whether either supported topology manifest representation is present."""
    return unified_manifest_path(workspace).is_file() or manifest_path(workspace).is_file()


@contextmanager
def manifest_lock(workspace: str | Path):
    """Serialize manifest read-modify-write operations for one run directory."""
    path = Path(workspace) / _MANIFEST_LOCK_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def claim_retry(
    manifest: dict[str, Any],
    *,
    backend: str,
    molecule_id: str,
    action: str,
) -> tuple[bool, str]:
    """Claim one persisted topology retry slot.

    Call this while holding :func:`manifest_lock` and persist the modified
    manifest before starting the external backend.  This prevents a new agent
    process from bypassing the per-component or per-run retry budget.
    """
    ledger = manifest.setdefault("retry_ledger", {})
    key = f"{backend}:{molecule_id}:{action}"
    used = int(ledger.get(key, 0))
    total = sum(int(value) for value in ledger.values())
    if used >= MAX_RETRIES_PER_ACTION:
        return False, f"{molecule_id} 的 {action} 已达到 {MAX_RETRIES_PER_ACTION} 次上限"
    if total >= MAX_TOPOLOGY_RETRIES:
        return False, f"拓扑层已达到 {MAX_TOPOLOGY_RETRIES} 次重试总上限"
    ledger[key] = used + 1
    return True, f"{key} 第 {used + 1} 次重试"


def write_manifest(
    workspace: str | Path,
    *,
    backend: str,
    forcefield_family: str,
    components: list[TopologyManifestComponent],
    retry_ledger: dict[str, int] | None = None,
) -> Path:
    """Persist the Step 4 input/output record in the active representation.

    During rollout a run without ``run_manifest.json`` remains wholly legacy.
    Once a unified manifest exists, topology owns only its private section and
    uses its revision as a compare-and-swap guard.  The legacy file is never
    created or overwritten for a unified run.
    """
    directory = Path(workspace)
    payload = {
        "version": 3,
        "backend": backend,
        "forcefield_family": forcefield_family,
        "components": [asdict(component) for component in components],
        "retry_ledger": retry_ledger or {},
    }

    if unified_manifest_path(directory).is_file():
        current = load_run_manifest(directory)
        section = current["sections"]["topology"]
        update_run_manifest_section(
            directory,
            "topology",
            payload,
            expected_section_revision=section["revision"],
        )
        return unified_manifest_path(directory)

    return _write_legacy_manifest(directory, payload)


def _write_legacy_manifest(directory: Path, payload: dict[str, Any]) -> Path:
    """Atomically persist a legacy topology-only manifest."""
    path = manifest_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".topology_manifest.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path


def load_manifest(workspace: str | Path) -> dict[str, Any]:
    """Load topology data, preferring schema-v2 with legacy read fallback.

    The shared compatibility reader permits legacy fallback only when the
    unified manifest is absent.  A malformed or incomplete unified document
    remains authoritative rather than silently reviving stale topology data.
    """
    return load_run_metadata_section(workspace, "topology")


def component_for_name(manifest: dict[str, Any], molecule_name: str) -> dict[str, Any] | None:
    for component in manifest.get("components", []):
        if molecule_name in {component.get("molecule_id"), component.get("residue_name")}:
            return component
    return None
