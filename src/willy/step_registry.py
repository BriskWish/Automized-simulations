"""Authoritative definition of Willy's ordered pipeline steps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class StepDefinition:
    step_id: str
    index: int
    label: str
    layer: str
    artifact_contract: str
    simulation_stage: str = ""
    controlled_restart_steps: tuple[int, ...] = ()


class StepRegistry:
    def __init__(self, steps: Iterable[StepDefinition]):
        ordered = tuple(sorted(steps, key=lambda step: step.index))
        if not ordered or [step.index for step in ordered] != list(range(1, len(ordered) + 1)):
            raise ValueError("步骤注册表必须从 1 开始连续编号")
        if len({step.step_id for step in ordered}) != len(ordered):
            raise ValueError("步骤标识不可重复")
        stages = [step.simulation_stage for step in ordered if step.simulation_stage]
        if len(set(stages)) != len(stages):
            raise ValueError("模拟阶段标识不可重复")
        self._steps = ordered
        self._by_index = {step.index: step for step in ordered}
        self._by_id = {step.step_id: step for step in ordered}
        self._by_stage = {step.simulation_stage: step for step in ordered if step.simulation_stage}

    @property
    def total_steps(self) -> int:
        return len(self._steps)

    def step(self, index: int) -> StepDefinition:
        try:
            return self._by_index[index]
        except KeyError as exc:
            raise ValueError(f"未知步骤: {index}") from exc

    def by_id(self, step_id: str) -> StepDefinition:
        try:
            return self._by_id[step_id]
        except KeyError as exc:
            raise ValueError(f"未知步骤标识: {step_id}") from exc

    def by_stage(self, stage: str) -> StepDefinition | None:
        return self._by_stage.get(stage)

    def label_for(self, index: int) -> str:
        return self.step(index).label

    def layer_for(self, index: int) -> str:
        return self.step(index).layer

    def stage_for(self, index: int) -> str | None:
        return self.step(index).simulation_stage or None

    def index_for_stage(self, stage: str) -> int | None:
        definition = self.by_stage(stage)
        return definition.index if definition is not None else None

    def indices_from(self, index: int) -> tuple[int, ...]:
        self.step(index)
        return tuple(range(index, self.total_steps + 1))

    def controlled_restart_allowed(self, failed_step: int, restart_step: int) -> bool:
        return restart_step in self.step(failed_step).controlled_restart_steps

    def is_simulation_step(self, index: object) -> bool:
        """Return whether a validated pipeline index belongs to an MD stage."""
        return (
            isinstance(index, int)
            and not isinstance(index, bool)
            and 1 <= index <= self.total_steps
            and self.stage_for(index) is not None
        )


@dataclass(frozen=True)
class ExecutionModuleDefinition:
    """One executable unit within a pipeline step.

    A pipeline step may have several backend-specific executors.  The module
    identifier is therefore more granular than ``StepDefinition.step_id`` and
    is the canonical key for dependency preflight.  Tool IDs refer to
    ``env_registry.TOOL_SPECS``; bundled dependency IDs are resolved by the
    compatibility facade in ``env_checker``.
    """

    module_id: str
    step_id: str
    tool_ids: tuple[str, ...] = ()
    bundled_dependency_ids: tuple[str, ...] = ()


class ExecutionModuleRegistry:
    """Validate and query normalized execution-module dependency ownership."""

    def __init__(
        self,
        steps: StepRegistry,
        modules: Iterable[ExecutionModuleDefinition],
    ):
        ordered = tuple(modules)
        if not ordered:
            raise ValueError("执行模块注册表不能为空")
        module_ids = [module.module_id for module in ordered]
        if any(not module_id for module_id in module_ids):
            raise ValueError("执行模块标识不能为空")
        if len(set(module_ids)) != len(module_ids):
            raise ValueError("执行模块标识不可重复")
        for module in ordered:
            steps.by_id(module.step_id)
            if len(set(module.tool_ids)) != len(module.tool_ids):
                raise ValueError(f"{module.module_id} 的外部工具标识不可重复")
            if len(set(module.bundled_dependency_ids)) != len(module.bundled_dependency_ids):
                raise ValueError(f"{module.module_id} 的内置依赖标识不可重复")
        self._modules = ordered
        self._by_id = {module.module_id: module for module in ordered}
        self._steps = steps

    def module(self, module_id: str) -> ExecutionModuleDefinition:
        try:
            return self._by_id[module_id]
        except KeyError as exc:
            raise ValueError(f"未知执行模块: {module_id}") from exc

    def has_module(self, module_id: str) -> bool:
        return module_id in self._by_id

    @property
    def definitions(self) -> tuple[ExecutionModuleDefinition, ...]:
        """Expose immutable definitions for registry-consistency checks."""
        return self._modules

    def modules_for_tool(self, tool_id: str) -> tuple[str, ...]:
        return tuple(
            module.module_id for module in self._modules if tool_id in module.tool_ids
        )

    def modules_for_bundled_dependency(self, dependency_id: str) -> tuple[str, ...]:
        return tuple(
            module.module_id
            for module in self._modules
            if dependency_id in module.bundled_dependency_ids
        )

    def step_for_module(self, module_id: str) -> StepDefinition:
        return self._steps.by_id(self.module(module_id).step_id)


STEP_REGISTRY = StepRegistry((
    StepDefinition("quantum_optimize", 1, "结构优化", "quantum", "quantum_optimized_structure"),
    StepDefinition("quantum_singlepoint_mol2", 2, "单点计算与 mol2 转换", "quantum", "quantum_mol2"),
    StepDefinition("quantum_resp", 3, "RESP 电荷计算", "quantum", "quantum_chg"),
    StepDefinition("topology_parameterize", 4, "分子拓扑参数化", "topology", "topology_component"),
    StepDefinition("topology_assemble", 5, "主拓扑生成", "topology", "topology_system"),
    StepDefinition("simulation_mdp", 6, "MDP 参数生成", "simulation", "mdp_bundle"),
    StepDefinition("simulation_box", 7, "Packmol 盒子构建", "simulation", "packed_box"),
    StepDefinition("simulation_em", 8, "GROMACS 能量最小化", "simulation", "md_stage", "em"),
    StepDefinition("simulation_eq", 9, "GROMACS 三点式退火平衡", "simulation", "md_stage", "eq", (7, 9)),
    StepDefinition("simulation_prod", 10, "GROMACS 生产模拟", "simulation", "md_stage", "prod"),
))

PACKMOL_STEP = STEP_REGISTRY.by_id("simulation_box").index
MDP_STEP = STEP_REGISTRY.by_id("simulation_mdp").index
EM_STEP = STEP_REGISTRY.by_id("simulation_em").index
EQ_STEP = STEP_REGISTRY.by_id("simulation_eq").index
PROD_STEP = STEP_REGISTRY.by_id("simulation_prod").index


# This registry deliberately retains the historic module IDs used by execution
# adapters.  It gives those IDs one owner while keeping ``env_checker`` a
# compatible facade for callers that still invoke ``ensure(module_id)``.
EXECUTION_MODULE_REGISTRY = ExecutionModuleRegistry(STEP_REGISTRY, (
    ExecutionModuleDefinition("struct_g16", "quantum_optimize", ("g16", "formchk")),
    ExecutionModuleDefinition("struct_g09", "quantum_optimize", ("g09", "g09_formchk")),
    ExecutionModuleDefinition("struct_orca", "quantum_optimize", ("orca", "orca_2mkl")),
    ExecutionModuleDefinition(
        "sp_g16", "quantum_singlepoint_mol2", ("g16", "formchk"), ("multiwfn",),
    ),
    ExecutionModuleDefinition(
        "sp_g09", "quantum_singlepoint_mol2", ("g09", "g09_formchk"), ("multiwfn",),
    ),
    ExecutionModuleDefinition(
        "sp_orca", "quantum_singlepoint_mol2", ("orca", "orca_2mkl"), ("multiwfn",),
    ),
    ExecutionModuleDefinition("chg_resp", "quantum_resp", (), ("multiwfn",)),
    ExecutionModuleDefinition(
        "topo_gaff", "topology_parameterize", (),
        ("sobtop", "atomtype", "sobtop_ini", "sobtop_lj_parameters", "sobtop_bonded_parameters", "obabel", "openbabel", "coordgen"),
    ),
    ExecutionModuleDefinition(
        "topo_opls", "topology_parameterize", ("ligpargen", "boss", "obabel", "csh"),
    ),
    ExecutionModuleDefinition("mdp", "simulation_mdp"),
    ExecutionModuleDefinition("box", "simulation_box", ("gmx",), ("packmol",)),
    ExecutionModuleDefinition("gromacs_em", "simulation_em", ("gmx",)),
    ExecutionModuleDefinition("gromacs_eq", "simulation_eq", ("gmx",)),
    ExecutionModuleDefinition("gromacs_prod", "simulation_prod", ("gmx",)),
))
