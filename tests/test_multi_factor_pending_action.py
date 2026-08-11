"""Multi-factor EQ recovery remains selectable and confirmation-gated."""

from __future__ import annotations

import json

import pytest

import app
import willy.frontend_api as frontend_api
from willy.run_registry import RunRegistry
from willy.simulation.pending_action import (
    PendingActionError,
    apply_pending_action,
    create_eq_pending_action,
    public_pending_action,
    select_pending_action_option,
    validate_pending_action_for_launch,
)
from willy.simulation.protocol import default_md_config


def _run_dir(tmp_path, run_id="md__202608070001"):
    directory = tmp_path / "md_run" / run_id
    directory.mkdir(parents=True)
    (directory / "config.json").write_text(json.dumps({
        "residues": {"Li": 1},
        "molecules": {"Li": {"charge": 0, "spin": 1}},
        "md": default_md_config(),
    }))
    return directory


def _proposal():
    return {
        "problem_summary": "温度与体积统计均异常，存在多个可能原因。",
        "options": [
            {
                "title": "方案一：减小积分步长",
                "cause": "高温段积分数值不稳定",
                "evidence": "温度持续偏离目标值",
                "summary": "以更小时间步重新执行 EQ。",
                "adjustments": [{"field": "dt", "after": 0.0005}],
            },
            {
                "title": "方案二：放缓压强耦合",
                "cause": "压浴对盒体积响应过强",
                "evidence": "体积在目标温度段仍显著波动",
                "summary": "延长 EQ 压强耦合时间后重跑。",
                "adjustments": [{"field": "eq_tau_p", "after": 2.5}],
            },
        ],
    }


def test_multi_factor_action_requires_selection_and_applies_only_selected_option(tmp_path):
    run_dir = _run_dir(tmp_path)
    before = (run_dir / "config.json").read_bytes()
    action = create_eq_pending_action(run_dir, proposal=_proposal())

    assert action["selection_required"] is True
    assert action["selected_option_id"] is None
    public = public_pending_action(action)
    assert len(public["options"]) == 2
    assert public["adjustments"] == []
    assert (run_dir / "config.json").read_bytes() == before
    with pytest.raises(PendingActionError, match="先选择方案"):
        validate_pending_action_for_launch(run_dir, action["action_id"])

    select_pending_action_option(run_dir, action["action_id"], "option_2")
    applied = apply_pending_action(run_dir, action["action_id"])
    config = json.loads((run_dir / "config.json").read_text())

    assert applied["selected_option_id"] == "option_2"
    assert config["md"]["dt"] == 0.001
    assert config["md"]["eq"]["tau_p"] == 2.5


def test_frontend_option_selection_keeps_status_waiting_and_config_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    run_id = "md__202608070002"
    run_dir = _run_dir(tmp_path, run_id)
    registry = RunRegistry(tmp_path)
    registry.register_run(run_dir, backend="g16", total_steps=10)
    action = create_eq_pending_action(run_dir, proposal=_proposal())
    registry.record_status(run_dir, {
        "state": "awaiting_confirmation", "step": 9, "activity": {}, "done_steps": list(range(1, 9)),
        "extra": {"run_id": run_id, "pending_action": public_pending_action(action)},
    }, "run_awaiting_confirmation")
    before = (run_dir / "config.json").read_bytes()
    pending = frontend_api.get_pending_action(run_id)

    assert pending and pending["selection_required"] is True
    reply = frontend_api.select_pending_action_option(
        action["action_id"], "option_1", run_id,
        state_revision=pending["state_revision"],
        config_fingerprint=pending["config_fingerprint"],
    )
    selected = frontend_api.get_pending_action(run_id)

    assert "已选择方案1" in reply
    assert selected and selected["selected_option_id"] == "option_1"
    assert selected["selection_required"] is False
    assert RunRegistry(tmp_path).get_run_status(run_id)["state"] == "awaiting_confirmation"
    assert (run_dir / "config.json").read_bytes() == before

    reselection = frontend_api.select_pending_action_option(
        action["action_id"], "option_2", run_id,
        state_revision=selected["state_revision"],
        config_fingerprint=selected["config_fingerprint"],
    )
    assert "已选择方案2" in reselection
    assert frontend_api.get_pending_action(run_id)["selected_option_id"] == "option_2"


def test_text_option_parser_accepts_selection_and_confirmation_variants():
    action = {"options": [{"option_id": "option_1"}, {"option_id": "option_2"}]}

    assert app._pending_option_id("方案1", action) == "option_1"
    assert app._pending_option_id("选择方案二", action) == "option_2"
    assert app._pending_option_id("确认方案一", action) == "option_1"
    assert app._is_option_confirmation("确认方案一") is True
    assert app._is_option_confirmation("方案1") is False
