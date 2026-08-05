from willy.action_contract import ActionEffect, ToolDeclaration, build_default_tool_catalog
from willy.errors import ErrorKind
from willy.recovery_policy import default_recovery_policy


def test_eq_policy_keeps_prod_out_and_requires_confirmation():
    policy = default_recovery_policy(build_default_tool_catalog())
    eq = policy.authorize(
        layer="simulation", error_kind=ErrorKind.EQUILIBRATION_FAILED,
        step=9, tool=ToolDeclaration("tools_retry_eq", "simulation", ActionEffect.REQUIRES_CONFIRMATION),
        arguments={},
        attempt=0,
    )
    assert not eq.allowed
    assert eq.requires_confirmation
    prod = policy.authorize(
        layer="simulation", error_kind=ErrorKind.EQUILIBRATION_FAILED,
        step=9, tool=ToolDeclaration("tools_retry_prod", "simulation", ActionEffect.REQUIRES_CONFIRMATION),
        arguments={}, attempt=0,
    )
    assert not prod.allowed


def test_policy_allows_confirmed_safe_retry_with_bounded_attempts():
    policy = default_recovery_policy(build_default_tool_catalog())
    tool = ToolDeclaration("tools_retry_em", "simulation", ActionEffect.RETRY_SAFE)
    allowed = policy.authorize(
        layer="simulation", error_kind=ErrorKind.EM_NOT_CONVERGED,
        step=8, tool=tool, arguments={}, attempt=0,
    )
    assert allowed.allowed
    denied = policy.authorize(
        layer="simulation", error_kind=ErrorKind.EM_NOT_CONVERGED,
        step=8, tool=tool, arguments={}, attempt=3,
    )
    assert not denied.allowed


def test_quantum_parameter_effects_require_confirmation_or_fork():
    catalog = build_default_tool_catalog()
    policy = default_recovery_policy(catalog)

    basis = policy.authorize(
        layer="quantum", error_kind=ErrorKind.SCF_NOT_CONVERGED,
        step=1, tool=catalog.require("tools_retry_struct_g16"),
        arguments={"molecule_name": "Li", "basis": "def2-SVP"}, attempt=0,
    )
    assert not basis.allowed
    assert basis.requires_confirmation

    charge = policy.authorize(
        layer="quantum", error_kind=ErrorKind.RESP_FAILED,
        step=3, tool=catalog.require("tools_retry_chg_g16"),
        arguments={"fchk_path": "Li_opt.fchk", "charge": 1}, attempt=0,
    )
    assert not charge.allowed
    assert charge.fork_only
