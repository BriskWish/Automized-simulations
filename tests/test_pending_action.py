"""EQ recovery proposals remain explicit, scoped, and one-time."""

from __future__ import annotations

import json

import pytest

from willy.simulation.pending_action import (
    PendingActionError,
    apply_pending_action,
    create_eq_pending_action,
    public_pending_action,
    replace_eq_pending_action,
    validate_pending_action_for_launch,
)
from willy.simulation.protocol import default_md_config


def _run_dir(tmp_path):
    run_dir = tmp_path / "md_run" / "md__202608030001"
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps({
        "residues": {"Li": 1},
        "molecules": {"Li": {"charge": 0, "spin": 1}},
        "md": default_md_config(),
    }))
    return run_dir


def test_pending_action_does_not_change_config_until_applied(tmp_path):
    run_dir = _run_dir(tmp_path)
    before = (run_dir / "config.json").read_bytes()
    action = create_eq_pending_action(run_dir, proposal={
        "summary": "降低时间步长后重新验收 EQ。",
        "adjustments": [{
            "field": "dt", "after": 0.0005,
            "purpose": "降低数值不稳定风险",
        }],
    })

    assert (run_dir / "config.json").read_bytes() == before
    public = public_pending_action(action)
    assert public["restart_step"] == 9
    assert public["adjustments"] == [{
        "name": "时间步长", "before": "0.001 ps", "after": "0.0005 ps",
        "purpose": "降低数值不稳定风险",
    }]
    assert "value" not in json.dumps(public)


def test_pending_action_rejects_changed_config_before_launch(tmp_path):
    run_dir = _run_dir(tmp_path)
    action = create_eq_pending_action(run_dir, proposal={
        "adjustments": [{"field": "dt", "after": 0.0005}],
    })
    config = json.loads((run_dir / "config.json").read_text())
    config["md"]["tau_t"] = 2.0
    (run_dir / "config.json").write_text(json.dumps(config))

    with pytest.raises(PendingActionError, match="配置已变化"):
        validate_pending_action_for_launch(run_dir, action["action_id"])


def test_pending_action_applies_only_its_validated_fields(tmp_path):
    run_dir = _run_dir(tmp_path)
    action = create_eq_pending_action(run_dir, proposal={
        "adjustments": [
            {"field": "dt", "after": 0.0005},
            {"field": "eq_segment.hold_target", "after": 4.0},
        ],
    })

    from willy.branching import preview_repair_config
    before = (run_dir / "config.json").read_bytes()
    applied, config = preview_repair_config(run_dir, action["action_id"])

    assert applied["state"] == "pending"
    assert (run_dir / "config.json").read_bytes() == before
    assert config["md"]["dt"] == 0.0005
    assert config["md"]["eq"]["segments_ns"]["hold_target"] == 4.0
    with pytest.raises(PendingActionError, match="不能写回父工程"):
        apply_pending_action(run_dir, action["action_id"])


def test_pending_action_normalizes_eq_pressure_and_hold_aliases(tmp_path):
    run_dir = _run_dir(tmp_path)
    action = create_eq_pending_action(run_dir, proposal={
        "adjustments": [
            {"field": "tau_p", "after": "2.5 ps"},
            {"field": "hold_time", "after": "4 ns"},
        ],
    }, allow_fallback=False)

    assert [item["field"] for item in action["adjustments"]] == [
        "eq_tau_p", "eq_segment.hold_target",
    ]
    assert [item["value"] for item in action["adjustments"]] == [2.5, 4.0]


def test_pending_action_reports_unsupported_replacement_field(tmp_path):
    run_dir = _run_dir(tmp_path)

    with pytest.raises(PendingActionError, match="不支持的调整字段"):
        create_eq_pending_action(run_dir, proposal={
            "adjustments": [{"field": "arbitrary_config_path", "after": 2.5}],
        }, allow_fallback=False)


def test_replacing_pending_action_preserves_config_until_new_confirmation(tmp_path):
    run_dir = _run_dir(tmp_path)
    before = (run_dir / "config.json").read_bytes()
    original = create_eq_pending_action(run_dir, proposal={
        "adjustments": [{"field": "dt", "after": 0.0005}],
    })

    replacement = replace_eq_pending_action(
        run_dir,
        action_id=original["action_id"],
        proposal={
            "summary": "改为延长最终保温段后重新验收 EQ。",
            "adjustments": [{
                "field": "eq_segment.hold_target",
                "after": 8.0,
                "purpose": "延长 298 K 末段采样",
            }],
        },
    )

    assert replacement["action_id"] != original["action_id"]
    assert replacement["restart_step"] == 9
    assert (run_dir / "config.json").read_bytes() == before
    with pytest.raises(PendingActionError, match="已失效"):
        validate_pending_action_for_launch(run_dir, original["action_id"])
    assert validate_pending_action_for_launch(run_dir, replacement["action_id"])["state"] == "pending"


def test_vacuum_recovery_requires_box_rebuild_from_step_seven(tmp_path):
    run_dir = _run_dir(tmp_path)
    config = json.loads((run_dir / "config.json").read_text())
    config["box"] = {"packing_number_density_nm3": 6.0}
    (run_dir / "config.json").write_text(json.dumps(config))

    action = create_eq_pending_action(
        run_dir,
        proposal={"adjustments": [{"field": "dt", "after": 0.0005}]},
        requires_box_rebuild=True,
    )

    assert action["restart_step"] == 7
    assert action["adjustments"][0]["field"] == "box_density"


def test_public_pending_action_exposes_only_bounded_editable_parameters(tmp_path):
    run_dir = _run_dir(tmp_path)
    action = create_eq_pending_action(run_dir, proposal={
        "adjustments": [{"field": "dt", "after": 0.0005}],
        "editable_fields": [
            {"field": "tau_t", "purpose": "重新评估恒温耦合"},
            {"field": "not_a_config_field", "purpose": "不得公开"},
        ],
    })

    public = public_pending_action(action)

    assert public["editable_parameters"] == [{
        "name": "恒温耦合时间",
        "current": "0.5 ps",
        "range": "0.0001 ps 至 20 ps",
        "purpose": "重新评估恒温耦合",
    }]
    assert "field" not in json.dumps(public, ensure_ascii=False)


def test_public_pending_action_contains_problem_two_paths_evidence_and_high_risk_gate(tmp_path):
    run_dir = _run_dir(tmp_path)
    config = json.loads((run_dir / "config.json").read_text())
    config["box"] = {"packing_number_density_nm3": 6.0}
    (run_dir / "config.json").write_text(json.dumps(config))

    action = create_eq_pending_action(run_dir, proposal={
        "problem": "EQ 末态温度与密度统计异常。",
        "evidence": ["末态温度未通过验收", "冻结配置的 dt 为 0.001 ps"],
        "options": [
            {
                "summary": "减小时间步长后在当前 EQ 步重试。",
                "adjustments": [{"field": "dt", "after": 0.0005, "purpose": "降低积分不稳定风险"}],
                "evidence_points": ["温度统计未通过验收"],
            },
            {
                "summary": "提高建盒数密度后从 Packmol 重新开始。",
                "adjustments": [{"field": "box_density", "after": 6.5, "purpose": "复核初始体积"}],
                "evidence_points": ["当前证据指向初始盒体积问题"],
            },
        ],
    })

    public = public_pending_action(action)
    report = public["recovery_plan"]

    assert report["problem"] == "EQ 末态温度与密度统计异常。"
    assert report["current_step_retry"]["applicable"] is True
    assert report["upstream_retry"]["applicable"] is True
    assert report["evidence"][:2] == ["末态温度未通过验收", "冻结配置的 dt 为 0.001 ps"]
    assert "温度统计未通过验收" in report["evidence"]
    assert report["risk_level"] == "high"
    assert report["manual_review_required"] is True
    assert "value" not in json.dumps(public, ensure_ascii=False)


def test_unknown_error_cannot_be_converted_to_a_fallback_eq_retry(tmp_path):
    run_dir = _run_dir(tmp_path)

    with pytest.raises(PendingActionError, match="不能生成猜测性"):
        create_eq_pending_action(run_dir, proposal={
            "unknown_error": True,
            "research_request": {"status": "approval_required"},
        })
