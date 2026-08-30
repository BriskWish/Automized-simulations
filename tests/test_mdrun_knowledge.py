"""Boundaries for the curated GROMACS mdrun knowledge lookup."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from willy.agent_simulation import SimulationAgent
from willy.simulation.mdrun_knowledge import lookup_mdrun_knowledge, public_entry_index
from willy.simulation.pending_action import create_eq_pending_action, public_pending_action
from willy.simulation.protocol import default_md_config
from willy.toolist_simulation import handle_simulation_tool_call


def _tool_call(call_id: str, entries: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name="tools_lookup_mdrun_knowledge",
            arguments=json.dumps({"entries": entries}, ensure_ascii=False),
        ),
    )


def _response(*, content: str | None = None, tool_calls=None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))]
    )


def test_index_and_lookup_require_matching_number_and_name():
    index = public_entry_index()
    assert len(index) == 13
    assert {"number", "name"} == set(index[0])
    assert lookup_mdrun_knowledge([index[9]])["lookup_status"] == "retrieved"
    mismatch = lookup_mdrun_knowledge([{"number": index[9]["number"], "name": "wrong"}])
    assert mismatch["lookup_status"] == "not_matched"
    assert lookup_mdrun_knowledge(index[:4])["errors"] == ["单次最多读取 3 条知识条目"]


def test_tool_handler_is_read_only_and_bounded():
    result = json.loads(handle_simulation_tool_call(
        "tools_lookup_mdrun_knowledge",
        {"entries": public_entry_index()[:4]},
    ))
    assert result["ok"] is False
    assert result["lookup_status"] == "not_matched"


def test_eq_proposal_uses_only_kb_tool_and_two_lookup_calls(tmp_path):
    config = {"residues": {"Li": 1}, "molecules": {"Li": {"charge": 0, "spin": 1}}, "md": default_md_config()}
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    index = public_entry_index()
    final = json.dumps({
        "summary": "降低积分步长并重新验收。",
        "adjustments": [{"field": "dt", "after": 0.0005}],
        "knowledge_entries": [index[9]],
        "knowledge_status": "retrieved",
        "advice_source": "knowledge_base",
        "compatibility_notice": "需核对本机版本",
    }, ensure_ascii=False)
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _response(tool_calls=[_tool_call("one", [index[9]])]),
        _response(tool_calls=[_tool_call("two", [index[0], index[1]])]),
        _response(content=final),
    ]
    agent = SimulationAgent(client)
    proposal = agent._request_eq_recovery_proposal(
        config_path=str(config_path), error_kind="eq_not_converged",
        error_message="EQ 未通过", evidence={"vacuum_detected": False},
    )
    assert client.chat.completions.create.call_count == 3
    assert client.chat.completions.create.call_args_list[0].kwargs["tools"]
    assert client.chat.completions.create.call_args_list[0].kwargs["tools"][0]["function"]["name"] == "tools_lookup_mdrun_knowledge"
    assert proposal["_knowledge_lookup_status"] == "retrieved"
    assert proposal["_retrieved_entries"][0]["number"] == index[9]["number"]


def test_pending_action_exposes_verified_or_unverified_source(tmp_path):
    run_dir = tmp_path / "md__knowledge"
    run_dir.mkdir()
    (run_dir / "config.json").write_text(json.dumps({
        "residues": {"Li": 1},
        "molecules": {"Li": {"charge": 0, "spin": 1}},
        "md": default_md_config(),
    }))
    index = public_entry_index()
    action = create_eq_pending_action(run_dir, proposal={
        "adjustments": [{"field": "dt", "after": 0.0005}],
        "knowledge_entries": [index[9]],
        "_retrieved_entries": [{"number": index[9]["number"], "name": index[9]["name"]}],
        "_knowledge_lookup_status": "retrieved",
    })
    public = public_pending_action(action)
    assert public["advice_source"] == "knowledge_base"
    assert public["knowledge_entries"] == [index[9]]
