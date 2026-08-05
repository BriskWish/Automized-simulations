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
    gro: str | None = None
    success: bool = False
    validated: bool = False
    error: str = ""


def manifest_path(workspace: str | Path) -> Path:
    return Path(workspace) / MANIFEST_FILENAME


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
    """Atomically persist the Step 4 input/output record in the run directory."""
    path = manifest_path(workspace)
    payload = {
        "version": 3,
        "backend": backend,
        "forcefield_family": forcefield_family,
        "components": [asdict(component) for component in components],
        "retry_ledger": retry_ledger or {},
    }
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
    path = manifest_path(workspace)
    with path.open() as handle:
        return json.load(handle)


def component_for_name(manifest: dict[str, Any], molecule_name: str) -> dict[str, Any] | None:
    for component in manifest.get("components", []):
        if molecule_name in {component.get("molecule_id"), component.get("residue_name")}:
            return component
    return None
