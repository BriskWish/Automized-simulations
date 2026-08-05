"""Topology backend registry, configuration contract, and Step 4 dispatcher."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json

from willy.errors import ErrorKind, StepError, StepResult
from willy.config_store import write_json
from willy.topology.manifest import (
    TopologyManifestComponent,
    load_manifest,
    manifest_path,
    write_manifest,
)
from willy.topology.validation import validate_topology_files, validate_topology_output_name


_BACKEND_FORCEFIELDS = {
    "sobtop": "gaff_uff",
    "oplsaa": "oplsaa",
}


@dataclass(frozen=True)
class TopologyComponent:
    molecule_id: str
    residue_name: str
    quantity: int
    mol2: Path
    chg: Path | None
    charge: int
    spin: int
    smiles: str | None = None
    lbcc: bool = False
    opt_steps: int = 0


@dataclass(frozen=True)
class TopologyPlan:
    backend: str
    force_field: str
    components: tuple[TopologyComponent, ...]
    default_lbcc: bool = False
    default_opt_steps: int = 0

    @property
    def forcefield_family(self) -> str:
        return _BACKEND_FORCEFIELDS[self.backend]

    @classmethod
    def from_config(cls, config: dict[str, Any], workspace: str | Path) -> "TopologyPlan":
        topology, issues, _ = normalize_topology_config(config.get("topology", {}))
        if issues:
            raise ValueError("; ".join(issues))

        residues = config.get("residues", {})
        molecules = config.get("molecules", {})
        default_lbcc = topology.get("default_lbcc", False)
        default_opt_steps = topology.get("default_opt_steps", 0)
        components: list[TopologyComponent] = []
        for residue_name, quantity in residues.items():
            validate_topology_output_name(residue_name)
            if not isinstance(quantity, int) or quantity <= 0:
                raise ValueError(f"residues.{residue_name} 必须是正整数")
            molecule = molecules.get(residue_name)
            if not isinstance(molecule, dict):
                raise ValueError(f"residues 中的 '{residue_name}' 未在 molecules 中定义")
            molecule_id = str(molecule.get("molecule_id", residue_name))
            components.append(TopologyComponent(
                molecule_id=molecule_id,
                residue_name=residue_name,
                quantity=quantity,
                mol2=Path(workspace) / f"{molecule_id}.mol2",
                chg=(Path(workspace) / f"{molecule_id}.chg") if topology["backend"] == "sobtop" else None,
                charge=int(molecule.get("charge", 0)),
                spin=int(molecule.get("spin", 1)),
                smiles=molecule.get("smiles"),
                lbcc=default_lbcc,
                opt_steps=default_opt_steps,
            ))
        if not components:
            raise ValueError("residues 不能为空")
        return cls(
            topology["backend"], topology["force_field"], tuple(components),
            default_lbcc=default_lbcc, default_opt_steps=default_opt_steps,
        )


def normalize_topology_config(topology: object) -> tuple[dict[str, Any], list[str], bool]:
    """Normalize one documented legacy shape and validate the current contract.

    Older releases wrote ``backend=ligpargen, force_field=gaff`` even though
    their Step 4 always used Sobtop.  That exact contradictory pair migrates
    to the current Sobtop/GAFF+UFF contract.  A legacy LigParGen entry with an
    OPLS field maps to OPLS-AA.  All other unknown values are rejected.
    """
    if not isinstance(topology, dict):
        return {}, ["topology 必须是对象"], False

    normalized = dict(topology)
    backend = str(normalized.get("backend", "sobtop")).lower()
    force_field = str(normalized.get("force_field", "gaff_uff")).lower()
    migrated = False

    if backend == "ligpargen":
        migrated = True
        if force_field in {"", "gaff", "uff", "gaff_uff"}:
            backend, force_field = "sobtop", "gaff_uff"
        elif force_field in {"opls", "oplsaa"}:
            backend, force_field = "oplsaa", "oplsaa"
    elif backend == "opls" and force_field in {"opls", "oplsaa"}:
        backend, force_field, migrated = "oplsaa", "oplsaa", True

    normalized["backend"] = backend
    normalized["force_field"] = force_field
    issues: list[str] = []
    expected = _BACKEND_FORCEFIELDS.get(backend)
    if expected is None:
        issues.append("topology.backend 仅允许 sobtop 或 oplsaa")
    elif force_field != expected:
        issues.append(
            f"topology.backend={backend} 时 topology.force_field 必须为 {expected}"
        )
    if "default_lbcc" in normalized and not isinstance(normalized["default_lbcc"], bool):
        issues.append("topology.default_lbcc 必须是布尔值")
    if "default_opt_steps" in normalized and (
        isinstance(normalized["default_opt_steps"], bool)
        or not isinstance(normalized["default_opt_steps"], int)
        or normalized["default_opt_steps"] not in {0, 1, 2, 3}
    ):
        issues.append("topology.default_opt_steps 仅允许 0、1、2 或 3")
    return normalized, issues, migrated


class TopologyBackend(ABC):
    """Extension point for force-field backends.

    New backends register an implementation here; the pipeline always calls
    :func:`dispatch_topology` and never branches on a backend itself.
    """

    forcefield_family: str

    def prepare(self, plan: TopologyPlan) -> StepResult:
        return StepResult(step_name="topology_prepare", step_index=4, success=True)

    @abstractmethod
    def parameterize(self, component: TopologyComponent, workspace: Path) -> StepResult:
        raise NotImplementedError

    def validate(self, component: TopologyComponent, outputs: dict[str, str]) -> StepResult:
        return validate_topology_files(
            outputs.get("itp", ""),
            outputs.get("gro", ""),
            step_name="topology_validate",
            error_kind=ErrorKind.SOBTOP_FAILED if self.forcefield_family == "gaff_uff"
            else ErrorKind.LIGPARGEN_FAILED,
            expected_moleculetype=component.residue_name,
        )


class SobtopBackend(TopologyBackend):
    forcefield_family = "gaff_uff"

    def prepare(self, plan: TopologyPlan) -> StepResult:
        from willy.topology.topo_gaff import check_sobtop_ready

        issues = check_sobtop_ready()
        if issues:
            return StepResult(
                step_name="topo_gaff", step_index=4, success=False,
                error=StepError(ErrorKind.DEPENDENCY_MISSING, "Sobtop 环境未就绪", "\n".join(issues)),
            )
        return super().prepare(plan)

    def parameterize(self, component: TopologyComponent, workspace: Path) -> StepResult:
        if component.chg is None:
            return StepResult(
                step_name="topo_gaff", step_index=4, success=False,
                error=StepError(ErrorKind.FILE_NOT_FOUND, f"{component.molecule_id}: 缺少 Sobtop 所需的 .chg"),
            )
        from willy.topology.topo_gaff import SobtopInput, make_itp_gro

        return make_itp_gro(SobtopInput(
            mol2=str(component.mol2),
            chg=str(component.chg),
            output_name=component.residue_name,
        ), output_dir=str(workspace))


class OplsaaBackend(TopologyBackend):
    forcefield_family = "oplsaa"

    def prepare(self, plan: TopologyPlan) -> StepResult:
        from willy.topology.topo_opls import check_ligpargen_ready

        issues = check_ligpargen_ready()
        if issues:
            return StepResult(
                step_name="topo_opls", step_index=4, success=False,
                error=StepError(ErrorKind.DEPENDENCY_MISSING, "LigParGen/BOSS 环境未就绪", "\n".join(issues)),
            )
        return super().prepare(plan)

    def parameterize(self, component: TopologyComponent, workspace: Path) -> StepResult:
        from willy.topology.topo_opls import LigParGenInput, make_itp_gro_opls

        return make_itp_gro_opls(LigParGenInput(
            smiles=component.smiles,
            mol2=str(component.mol2),
            output_name=component.residue_name,
            net_charge=component.charge,
            lbcc=component.lbcc,
            opt_steps=component.opt_steps,
        ), output_dir=str(workspace))


_BACKENDS: dict[str, type[TopologyBackend]] = {
    "sobtop": SobtopBackend,
    "oplsaa": OplsaaBackend,
}


def dispatch_topology(
    config_path: str | Path,
    workspace: str | Path,
    on_progress=None,
) -> list[StepResult]:
    """Execute the configured backend and report one public activity per component."""
    config_file = Path(config_path)
    run_dir = Path(workspace)
    try:
        config = json.loads(config_file.read_text())
        topology, issues, migrated = normalize_topology_config(config.get("topology", {}))
        if issues:
            raise ValueError("; ".join(issues))
        if migrated or config.get("topology") != topology:
            config["topology"] = topology
            write_json(config_file, config)
        plan = TopologyPlan.from_config(config, run_dir)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [StepResult(
            step_name="topology_dispatch", step_index=4, success=False,
            error=StepError(ErrorKind.CONFIG_INVALID, f"拓扑配置无效: {exc}"),
        )]

    backend = _BACKENDS[plan.backend]()
    tool_name = "Sobtop" if plan.backend == "sobtop" else "LigParGen"
    total = len(plan.components)
    if on_progress:
        on_progress({
            "tool": tool_name, "operation": "拓扑参数化",
            "target_type": "system", "target": "当前体系",
            "current": 0, "total": total,
        })
    retry_ledger: dict[str, int] = {}
    if manifest_path(run_dir).is_file():
        try:
            existing_manifest = load_manifest(run_dir)
            if (existing_manifest.get("backend"), existing_manifest.get("forcefield_family")) == (
                plan.backend, backend.forcefield_family,
            ):
                retry_ledger = dict(existing_manifest.get("retry_ledger", {}))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            # A stale malformed manifest is never an input to this new plan.
            pass
    manifest_components = [TopologyManifestComponent(
        molecule_id=component.molecule_id,
        residue_name=component.residue_name,
        quantity=component.quantity,
        mol2=str(component.mol2),
        chg=str(component.chg) if component.chg is not None else None,
        charge=component.charge,
        spin=component.spin,
        smiles=component.smiles,
        backend=plan.backend,
        forcefield_family=backend.forcefield_family,
        lbcc=component.lbcc,
        opt_steps=component.opt_steps,
    ) for component in plan.components]
    write_manifest(run_dir, backend=plan.backend, forcefield_family=backend.forcefield_family,
                   components=manifest_components, retry_ledger=retry_ledger)

    prepared = backend.prepare(plan)
    if not prepared.success:
        for entry in manifest_components:
            entry.error = prepared.error.message if prepared.error else "后端预检失败"
        write_manifest(run_dir, backend=plan.backend, forcefield_family=backend.forcefield_family,
                       components=manifest_components, retry_ledger=retry_ledger)
        return [prepared]

    results: list[StepResult] = []
    for index, (component, entry) in enumerate(zip(plan.components, manifest_components), 1):
        if on_progress:
            on_progress({
                "tool": tool_name, "operation": "拓扑参数化",
                "target_type": "molecule", "target": component.molecule_id,
                "current": index, "total": total,
            })
        result = backend.parameterize(component, run_dir)
        if result.success:
            validated = backend.validate(component, result.outputs)
            if validated.success:
                entry.itp = result.outputs["itp"]
                entry.gro = result.outputs["gro"]
                entry.success = True
                entry.validated = True
                result.extra.update(validated.extra)
            else:
                result = validated
        if not result.success:
            entry.error = result.error.message if result.error else "拓扑生成失败"
        result.target_type = "molecule"
        result.target = component.molecule_id
        results.append(result)
        write_manifest(run_dir, backend=plan.backend, forcefield_family=backend.forcefield_family,
                       components=manifest_components, retry_ledger=retry_ledger)
    return results
