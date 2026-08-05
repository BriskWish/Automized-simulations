"""Regression tests for deterministic text-based pipeline confirmation."""

from types import SimpleNamespace

import willy.agent_config as agent_config


def _pending_plan() -> list[dict]:
    return [{"role": "assistant", "content": "**模拟方案确认**\n\n组成: Li 1"}]


def _plan(config):
    return agent_config.create_pending_launch_plan(config, _pending_plan()[-1]["content"])


def test_text_confirmation_starts_the_pending_plan_without_an_llm(monkeypatch):
    config = {"backend": "g16", "residues": {"Li": 1}}
    calls = []
    monkeypatch.setattr(
        agent_config,
        "start_pipeline",
        lambda launch_config: calls.append(launch_config) or SimpleNamespace(
            message="流水线已启动。", run_id="md_demo", state="started"),
    )
    monkeypatch.setattr(agent_config, "_DS", None)

    updates = list(agent_config.chat("开始运行", _pending_plan(), _plan(config)))

    assert calls == [config]
    assert updates[-1][3] is None
    assert updates[-1][1] == [
        *_pending_plan(),
        {"role": "user", "content": "开始运行"},
        {"role": "assistant", "content": "流水线已启动。"},
    ]


def test_text_confirmation_without_a_pending_plan_does_not_start(monkeypatch):
    calls = []
    monkeypatch.setattr(agent_config, "start_pipeline", lambda config: calls.append(config))

    updates = list(agent_config.chat("运行", []))

    assert calls == []
    assert "当前没有待确认的模拟方案" in updates[-1][1][-1]["content"]


def test_start_pipeline_rejects_validation_issues_before_reserving_a_run(monkeypatch):
    reserved = []
    monkeypatch.setattr(agent_config, "write_startup_audit", lambda *_args: None)
    monkeypatch.setattr(
        agent_config,
        "reserve_pipeline_launch",
        lambda *_args: reserved.append(True),
    )

    receipt = agent_config.start_pipeline({
        "residues": {"Li": "many"},
        "molecules": {"Li": {}},
    })

    assert receipt.state == "failed"
    assert reserved == []


def test_new_failed_request_clears_an_older_pending_plan(monkeypatch):
    class Replies:
        def create(self, **_kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(tool_calls=None, content="{}"),
            )])

    old_config = {"backend": "g16", "residues": {"Li": 1}}
    calls = []
    monkeypatch.setattr(agent_config, "_available_residues", lambda: {"Li": {}})
    monkeypatch.setattr(agent_config, "_DS", SimpleNamespace(chat=SimpleNamespace(completions=Replies())))
    monkeypatch.setattr(agent_config, "start_pipeline", lambda config: calls.append(config))

    failed = list(agent_config.chat("新的模拟需求", _pending_plan(), _plan(old_config)))

    assert failed[-1][3] is None
    assert "LLM 多次返回异常" in failed[-1][1][-1]["content"]

    confirmation = list(agent_config.chat("确认运行", failed[-1][1], failed[-1][3]))
    assert calls == []
    assert "当前没有待确认的模拟方案" in confirmation[-1][1][-1]["content"]


def test_pending_plan_requires_its_own_latest_summary():
    config = {"backend": "g16", "residues": {"Li": 1}}
    plan = _plan(config)
    foreign_history = [{"role": "assistant", "content": "**模拟方案确认**\n\n组成: EC 10"}]

    assert agent_config._pending_launch_config(plan, _pending_plan()) == config
    assert agent_config._pending_launch_config(plan, foreign_history) is None


def test_summary_uses_mass_density_default_prompt():
    summary = agent_config.summarize({
        "residues": {"Li": 1},
        "molecules": {"Li": {"charge": 1}},
        "md": {"eq": {"target_temperature": 298, "segments_ns": {}}, "prod": {"duration_ns": 2}},
        "box": {"target_mass_density_g_cm3": 1.5},
    })

    assert "初始体积将由使用默认1.5g/cm3的密度猜测" in summary
    assert "分子数密度" not in summary
