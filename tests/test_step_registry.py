import pytest

from willy.step_registry import (
    EQ_STEP,
    PACKMOL_STEP,
    EXECUTION_MODULE_REGISTRY,
    ExecutionModuleDefinition,
    ExecutionModuleRegistry,
    STEP_REGISTRY,
    StepDefinition,
    StepRegistry,
)


def test_registry_is_contiguous_and_covers_the_current_pipeline():
    assert STEP_REGISTRY.total_steps == 10
    assert STEP_REGISTRY.step(EQ_STEP).simulation_stage == "eq"
    assert STEP_REGISTRY.index_for_stage("prod") == 10
    assert STEP_REGISTRY.indices_from(PACKMOL_STEP) == (7, 8, 9, 10)
    assert STEP_REGISTRY.controlled_restart_allowed(EQ_STEP, PACKMOL_STEP)
    assert STEP_REGISTRY.controlled_restart_allowed(EQ_STEP, EQ_STEP)
    assert not STEP_REGISTRY.controlled_restart_allowed(EQ_STEP, 8)
    assert STEP_REGISTRY.is_simulation_step(EQ_STEP)
    assert not STEP_REGISTRY.is_simulation_step(11)


def test_registry_rejects_gaps_and_duplicate_ids():
    with pytest.raises(ValueError):
        StepRegistry((StepDefinition("one", 1, "one", "x", "x"), StepDefinition("three", 3, "three", "x", "x")))
    with pytest.raises(ValueError):
        StepRegistry((StepDefinition("one", 1, "one", "x", "x"), StepDefinition("one", 2, "two", "x", "x")))


def test_execution_modules_own_normalized_tool_and_step_dependencies():
    from willy.env_registry import TOOL_SPECS

    assert EXECUTION_MODULE_REGISTRY.modules_for_tool("gmx") == (
        "box", "gromacs_em", "gromacs_eq", "gromacs_prod",
    )
    assert EXECUTION_MODULE_REGISTRY.modules_for_bundled_dependency("packmol") == ("box",)
    assert EXECUTION_MODULE_REGISTRY.step_for_module("gromacs_eq").index == EQ_STEP
    assert EXECUTION_MODULE_REGISTRY.has_module("topo_opls")
    assert not EXECUTION_MODULE_REGISTRY.has_module("unregistered")
    assert {
        tool_id
        for module in EXECUTION_MODULE_REGISTRY.definitions
        for tool_id in module.tool_ids
    } <= set(TOOL_SPECS)


def test_execution_module_registry_rejects_unknown_steps_and_duplicate_tools():
    with pytest.raises(ValueError, match="未知步骤标识"):
        ExecutionModuleRegistry(
            STEP_REGISTRY,
            (ExecutionModuleDefinition("bad", "missing_step"),),
        )
    with pytest.raises(ValueError, match="外部工具标识不可重复"):
        ExecutionModuleRegistry(
            STEP_REGISTRY,
            (ExecutionModuleDefinition("bad", "quantum_optimize", ("g16", "g16")),),
        )
