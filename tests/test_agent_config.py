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


def test_pending_plan_revision_sends_frozen_context_and_replaces_plan(monkeypatch):
    class Replies:
        def __init__(self):
            self.calls = []
            self.count = 0

        def create(self, **kwargs):
            self.calls.append(kwargs)
            self.count += 1
            if self.count == 1:
                tool_call = SimpleNamespace(
                    id="audit-revision",
                    function=SimpleNamespace(
                        name="tools_inspect_quantum_inputs",
                        arguments='{"backend":"g16","components":[{"name":"Li","count":2}]}',
                    ),
                )
                return SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(tool_calls=[tool_call], content=""),
                )])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                tool_calls=None,
                content='{"backend":"g16","residues":{"Li":2},"molecules":{"Li":{"charge":1,"spin":1}}}',
            ))])

    old_config = {"backend": "g16", "residues": {"Li": 1}}
    replies = Replies()
    audit = {
        "ok": True,
        "backend": "g16",
        "components": [{"name": "Li", "count": 2, "status": "valid"}],
        "charge_balance": "imbalanced",
        "net_charge": 2,
    }
    monkeypatch.setattr(agent_config, "_available_residues", lambda: {"Li": {}})
    monkeypatch.setattr(agent_config, "_DS", SimpleNamespace(chat=SimpleNamespace(completions=replies)))
    monkeypatch.setattr(
        agent_config,
        "handle_tool_call",
        lambda _name, _args: __import__("json").dumps(audit),
    )
    monkeypatch.setattr(agent_config, "_audit_candidate_config", lambda config: (dict(config), []))

    history = _pending_plan()
    plan = _plan(old_config)
    updates = list(agent_config.chat("把 Li 改为 2 个", history, plan))

    assert replies.calls[0]["messages"][1]["content"].find("上一轮冻结配置") >= 0
    assert '"Li": 1' in replies.calls[0]["messages"][1]["content"]
    assert "把 Li 改为 2 个" in replies.calls[0]["messages"][1]["content"]
    assert updates[-1][3] is not plan
    assert updates[-1][3]["config"]["residues"] == {"Li": 2}
    assert "模拟方案确认" in updates[-1][1][-1]["content"]


def test_pending_plan_question_keeps_plan_and_sends_context_to_llm(monkeypatch):
    calls = []

    class Replies:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                tool_calls=None, content="当前方案的 EQ 和 PROD 时长彼此独立。",
            ))])

    config = {"backend": "g16", "residues": {"Li": 1}}
    plan = _plan(config)
    monkeypatch.setattr(agent_config, "_DS", SimpleNamespace(chat=SimpleNamespace(completions=Replies())))

    updates = list(agent_config.chat("为什么要先做 EQ？", _pending_plan(), plan))

    assert updates[-1][3] == plan
    assert "当前方案的 EQ 和 PROD" in updates[-1][1][-1]["content"]
    assert "冻结配置" in calls[0]["messages"][1]["content"]
    assert '"Li": 1' in calls[0]["messages"][1]["content"]


def test_confirmation_after_pending_plan_question_still_uses_original_plan(monkeypatch):
    config = {"backend": "g16", "residues": {"Li": 1}}
    plan = _plan(config)
    monkeypatch.setattr(agent_config, "_DS", None)
    question_updates = list(agent_config.chat("为什么要先做 EQ？", _pending_plan(), plan))
    monkeypatch.setattr(
        agent_config,
        "start_pipeline",
        lambda launch_config: SimpleNamespace(message="流水线已启动。", run_id="md_demo", state="started"),
    )

    confirmation = list(agent_config.chat("确认运行", question_updates[-1][1], question_updates[-1][3]))

    assert confirmation[-1][3] is None
    assert confirmation[-1][1][-1]["content"] == "流水线已启动。"


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
        "box": {"target_mass_density_g_cm3": 0.7},
    })

    assert "初始体积将由使用默认0.7g/cm3的密度猜测" in summary
    assert "分子数密度" not in summary


def test_remote_selection_is_server_validated_and_frozen_into_new_plan(monkeypatch):
    class Replies:
        def __init__(self):
            self.calls = []
            self.count = 0

        def create(self, **kwargs):
            self.calls.append(kwargs)
            self.count += 1
            if self.count == 1:
                tool_call = SimpleNamespace(
                    id="audit-remote",
                    function=SimpleNamespace(
                        name="tools_inspect_quantum_inputs",
                        arguments='{"backend":"g16","components":[{"name":"Li","count":2}]}',
                    ),
                )
                return SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(tool_calls=[tool_call], content=""),
                )])
            # The model attempts to replace the selected remote profile.  The
            # server must discard this field before freezing the proposal.
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                tool_calls=None,
                content=(
                    '{"backend":"g16","residues":{"Li":2},'
                    '"molecules":{"Li":{"charge":1,"spin":1}},'
                    '"execution":{"md":{"backend":"ssh","profile":"invented"}}}'
                ),
            ))])

    audit = {
        "ok": True,
        "backend": "g16",
        "components": [{"name": "Li", "count": 2, "status": "valid"}],
        "charge_balance": "imbalanced",
        "net_charge": 2,
    }
    replies = Replies()
    monkeypatch.setattr(agent_config, "_available_residues", lambda: {"Li": {}})
    monkeypatch.setattr(agent_config, "_DS", SimpleNamespace(chat=SimpleNamespace(completions=replies)))
    monkeypatch.setattr(
        agent_config,
        "handle_tool_call",
        lambda _name, _args: __import__("json").dumps(audit),
    )
    monkeypatch.setattr(agent_config, "_audit_candidate_config", lambda config: (dict(config), []))
    monkeypatch.setattr(
        agent_config,
        "parse_execution_md",
        lambda _payload: SimpleNamespace(as_dict=lambda: {
            "backend": "ssh", "profile": "lab_gpu", "retain_remote_run": True,
        }),
    )

    updates = list(agent_config.chat(
        "Li 2",
        [],
        execution_context={
            "execution_mode": "ssh",
            "execution_profile_id": "lab_gpu",
            "available": False,
        },
    ))

    frozen = updates[-1][3]["config"]
    assert frozen["execution"] == {
        "md": {"backend": "ssh", "profile": "lab_gpu", "retain_remote_run": True}
    }
    assert "profile: lab_gpu" in updates[-1][1][-1]["content"]
    assert "SSH 直连" in replies.calls[0]["messages"][0]["content"]


def test_unregistered_remote_selection_blocks_plan_generation(monkeypatch):
    monkeypatch.setattr(
        agent_config,
        "parse_execution_md",
        lambda _payload: (_ for _ in ()).throw(
            agent_config.RemoteRegistryError("未配置远程 profile 注册表")
        ),
    )
    monkeypatch.setattr(agent_config, "_DS", None)

    updates = list(agent_config.chat(
        "Li 2",
        [],
        execution_context={
            "execution_mode": "ssh",
            "execution_profile_id": "lab_gpu",
            "available": True,
        },
    ))

    assert "所选远程执行配置不可用" in updates[-1][1][-1]["content"]
    assert "未配置远程 profile 注册表" in updates[-1][1][-1]["content"]


def test_confirmation_rejects_remote_selection_changed_after_plan(monkeypatch):
    config = {
        "backend": "g16",
        "residues": {"Li": 1},
        "execution": {
            "md": {"backend": "ssh", "profile": "lab_gpu", "retain_remote_run": True},
        },
    }
    calls = []
    monkeypatch.setattr(agent_config, "start_pipeline", lambda launch: calls.append(launch))

    updates = list(agent_config.chat(
        "确认运行",
        _pending_plan(),
        _plan(config),
        execution_context={"execution_mode": "local", "execution_profile_id": "local-default"},
    ))

    assert calls == []
    assert updates[-1][3] is not None
    assert "执行选择已在方案生成后变化" in updates[-1][1][-1]["content"]


def test_remote_profile_never_silently_falls_back_to_local_launch(monkeypatch):
    config = {
        "backend": "g16",
        "residues": {"Li": 1},
        "execution": {
            "md": {"backend": "ssh", "profile": "lab_gpu", "retain_remote_run": True},
        },
    }
    audit_events = []
    monkeypatch.setattr(agent_config, "audit_config_quantum_inputs", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(agent_config, "quantum_input_contract_issues", lambda *_args: [])
    monkeypatch.setattr(agent_config, "apply_audited_quantum_properties", lambda value, _audit: value)
    monkeypatch.setattr(agent_config, "validate_config", lambda _config: [])
    monkeypatch.setattr(agent_config, "write_startup_audit", lambda _root, event: audit_events.append(event))
    monkeypatch.setattr(
        agent_config,
        "reserve_pipeline_launch",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not reserve a local run")),
    )

    receipt = agent_config.start_pipeline(config)

    assert receipt.state == "remote_executor_unavailable"
    assert "未启动本机或远程进程" in receipt.message
    assert audit_events == ["remote_executor_unavailable"]
