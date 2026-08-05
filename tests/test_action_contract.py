from __future__ import annotations

import pytest

from willy.action_contract import (
    ActionContractError,
    ActionEffect,
    ActionToolCatalog,
    ActionProposal,
    ExecutedAction,
    ParameterChange,
    ToolDeclaration,
    ValidatedAction,
    build_default_tool_catalog,
)


def _proposal(**overrides) -> ActionProposal:
    values = {
        "run_id": "md__202608050001",
        "layer": "simulation",
        "tool_name": "tools_retry_eq",
        "arguments": {"segments_ns": {"cool_target": 4}},
        "failed_step": 9,
        "parameter_changes": (ParameterChange("md.eq.segments_ns.cool_target", 2, 4),),
        "config_fingerprint": "a" * 64,
        "model_id": "test-model",
        "prompt_version": "simulation-recovery-v1",
        "decision_id": "act_test_1",
        "created_at": "2026-08-05T00:00:00+00:00",
    }
    values.update(overrides)
    return ActionProposal(**values)


def test_default_catalog_covers_every_schema_and_declares_effects():
    catalog = build_default_tool_catalog()
    assert len(catalog.declarations()) == 46
    names = {declaration.tool_name for declaration in catalog.declarations()}
    assert len(names) == 46
    assert all(isinstance(declaration.effect, ActionEffect) for declaration in catalog.declarations())
    assert catalog.require("tools_get_status_run").is_read_only
    assert catalog.require("tools_retry_eq").requires_confirmation
    assert catalog.require("tools_modify_config_topology").requires_fork
    assert not catalog.require("tools_skip_molecule_topology").enabled


def test_proposal_is_json_safe_and_nested_arguments_are_immutable():
    arguments = {"nested": {"values": [1, 2]}}
    proposal = _proposal(arguments=arguments)
    arguments["nested"]["values"].append(3)
    assert proposal.to_dict()["arguments"] == {"nested": {"values": [1, 2]}}
    with pytest.raises(TypeError):
        proposal.arguments["nested"]["values"] += (3,)


def test_validation_requires_matching_enabled_declaration():
    catalog = ActionToolCatalog((
        ToolDeclaration("tools_retry_eq", "simulation", ActionEffect.REQUIRES_CONFIRMATION),
        ToolDeclaration("tools_disabled", "simulation", ActionEffect.READ_ONLY, enabled=False),
    ))
    validated = catalog.validate(_proposal(), policy_id="recovery-v1")
    assert isinstance(validated, ValidatedAction)
    assert validated.requires_confirmation

    with pytest.raises(ActionContractError, match="层级不一致"):
        catalog.validate(_proposal(layer="quantum"), policy_id="recovery-v1")
    with pytest.raises(ActionContractError, match="已禁用"):
        catalog.validate(
            _proposal(tool_name="tools_disabled"),
            policy_id="recovery-v1",
        )


def test_executed_action_round_trip_does_not_accept_raw_output():
    declaration = ToolDeclaration("tools_retry_eq", "simulation", ActionEffect.REQUIRES_CONFIRMATION)
    action = ValidatedAction(_proposal(), declaration, policy_id="recovery-v1", validated_at="2026-08-05T00:00:01+00:00")
    executed = ExecutedAction(
        action=action,
        success=False,
        result_summary="EQ 验收未通过",
        output_keys=("eq_log", "eq_edr"),
        error_kind="equilibration_failed",
        executed_at="2026-08-05T00:00:02+00:00",
    )
    payload = executed.to_dict()
    assert "raw_output" not in payload
    restored = ExecutedAction.from_dict(payload)
    assert restored.decision_id == "act_test_1"
    assert restored.output_keys == ("eq_log", "eq_edr")


def test_read_only_declaration_cannot_claim_mutation():
    with pytest.raises(ActionContractError, match="read_only 工具"):
        ToolDeclaration(
            "tools_status",
            "run",
            ActionEffect.READ_ONLY,
            invalidates_stages=("prod",),
        )


def test_parameter_effect_raises_the_effect_of_a_specific_call():
    declaration = ToolDeclaration(
        "tools_retry_struct_g16",
        "quantum",
        ActionEffect.RETRY_SAFE,
        parameter_effects={"basis": "requires_confirmation"},
    )
    assert declaration.effect_for({"molecule_name": "Li"}) is ActionEffect.RETRY_SAFE
    assert declaration.effect_for({"molecule_name": "Li", "basis": "def2-SVP"}) is ActionEffect.REQUIRES_CONFIRMATION
