"""Regression coverage for the Gradio conversation event adapters."""

from pathlib import Path
import app as app_module


def test_visualization_panel_refreshes_current_run_structures_on_focus():
    config = app_module.app.get_config_file()
    elem_ids = {
        component["props"].get("elem_id")
        for component in config["components"]
    }

    assert {"visualization-panel", "structure-selector", "structure-viewer"}.issubset(elem_ids)
    assert "可视化" in str(config)
    assert "打开下拉菜单时刷新当前运行目录" in str(config)
    assert "focus" in str(config["dependencies"])
    assert "_refresh_current_run_structure_choices" in app_module.__dict__


def test_visualization_refresh_keeps_a_current_filename_or_uses_the_first_choice(monkeypatch):
    rendered_choices = []
    monkeypatch.setattr(
        app_module.frontend_api,
        "get_run_visualization_choices",
        lambda: ["Li.mol2", "model.pdb"],
    )
    monkeypatch.setattr(
        app_module.frontend_api,
        "render_run_visualization_html",
        lambda choice: rendered_choices.append(choice) or f"viewer:{choice}",
    )

    update, viewer = app_module._refresh_current_run_structure_choices("model.pdb")

    assert update["choices"] == ["Li.mol2", "model.pdb"]
    assert update["value"] == "model.pdb"
    assert update["interactive"] is True
    assert viewer == "viewer:model.pdb"
    assert rendered_choices == ["model.pdb"]

    update, viewer = app_module._refresh_current_run_structure_choices("removed.pdb")

    assert update["value"] == "Li.mol2"
    assert viewer == "viewer:Li.mol2"


def test_proposal_example_is_a_textbox_placeholder_only():
    config = app_module.app.get_config_file()
    proposal_input = next(
        component for component in config["components"]
        if component["type"] == "textbox"
        and component["props"].get("placeholder") == app_module.PROPOSAL_EXAMPLE
    )
    assert proposal_input["props"]["lines"] == 3
    assert "启动示例" not in str(config)


def test_assistant_chat_and_input_rows_share_bottom_alignment_contract():
    config = app_module.app.get_config_file()
    elem_ids = {
        component["props"].get("elem_id")
        for component in config["components"]
    }

    assert {
        "proposal-header",
        "proposal-chat",
        "run-chat",
        "proposal-input-row",
        "run-input-row",
    }.issubset(elem_ids)
    assert "#proposal-chat,\n#run-chat {\n    margin-top: auto;\n}" in app_module.APP_CSS
    textboxes = [
        component for component in config["components"]
        if component["type"] == "textbox"
    ]
    assert all(component["props"]["lines"] == 3 for component in textboxes[:2])


def test_stop_control_uses_server_side_confirmation_without_cleanup_choices():
    config = app_module.app.get_config_file()

    assert not hasattr(app_module, "STOP_CONFIRM_JS")
    assert "清理产物并中止" not in str(config)
    assert "仅中止(保留产物)" not in str(config)
    assert "#stop-pipeline-button:disabled" in app_module.APP_CSS
    assert "window.confirm" not in str(config["dependencies"])


def test_stop_control_only_dispatches_after_second_server_side_confirmation(monkeypatch):
    stop_calls = []
    monkeypatch.setattr(
        app_module,
        "stop_pipeline",
        lambda *, clean: stop_calls.append(clean) or "已请求安全停止：等待 checkpoint",
    )

    first_update, confirmation_pending, stop_requested = app_module._handle_stop_button_click(False, False)
    assert stop_calls == []
    assert first_update["value"] == "确认中止"
    assert first_update["interactive"] is True
    assert confirmation_pending is True
    assert stop_requested is False

    confirmed_update, confirmation_pending, stop_requested = app_module._handle_stop_button_click(True, False)
    assert stop_calls == [False]
    assert confirmed_update["value"] == "正在安全停止"
    assert confirmed_update["interactive"] is False
    assert confirmation_pending is False
    assert stop_requested is True


def test_stop_control_stays_interactive_when_backend_does_not_acknowledge(monkeypatch):
    monkeypatch.setattr(app_module, "stop_pipeline", lambda *, clean: "当前没有运行中的流水线。")

    update, confirmation_pending, stop_requested = app_module._handle_stop_button_click(True, False)

    assert update["value"] == "中止请求未送达，请重试"
    assert update["interactive"] is True
    assert confirmation_pending is False
    assert stop_requested is False


def test_task_workspace_has_four_equal_height_panels_and_chart_placeholder():
    config = app_module.app.get_config_file()
    elem_ids = {
        component["props"].get("elem_id")
        for component in config["components"]
    }
    assert {
        "assistant-row",
        "workspace-row",
        "proposal-assistant",
        "run-assistant",
        "visualization-panel",
        "chart-panel",
    }.issubset(elem_ids)
    assert {"run-status", "pending-action-summary"}.isdisjoint(elem_ids)
    assert "--workbench-panel-height: 52.8rem" in app_module.APP_CSS
    assert "height: var(--workbench-panel-height)" in app_module.APP_CSS
    assert "height=400" not in app_module.APP_CSS
    assert "#run-chat .pipeline-status-indicator" in app_module.APP_CSS
    assert "#structure-selector {" in app_module.APP_CSS
    assert "#structure-viewer iframe" in app_module.APP_CSS
    assert "图表绘制" in str(config)
    assert "图表绘制栏" not in str(config)
    assert "可视化栏" not in str(config)
    assert "开发中..." in str(config)


def test_about_tab_exposes_project_summary_and_official_account_qr_code():
    config = app_module.app.get_config_file()
    elem_ids = {
        component["props"].get("elem_id")
        for component in config["components"]
    }

    assert "关于" in str(config)
    assert "Willy-方案助理" in str(config)
    assert {"about-page", "about-qr-section", "official-account-code"}.issubset(elem_ids)
    assert Path(app_module.OFFICIAL_ACCOUNT_QR).is_file()
    assert "grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr)" in app_module.APP_CSS
    assert "font-size: 2.25rem" in app_module.APP_CSS
    assert "border-top: 1px solid #aeb4b0" in app_module.APP_CSS


def test_configuration_tab_exposes_generic_llm_fields():
    config = app_module.app.get_config_file()
    components = config["components"]
    labels = {
        component["props"].get("label")
        for component in components
        if component["type"] == "textbox"
    }

    assert {
        "OpenAI-compatible API Key",
        "Base URL",
        "Model",
    }.issubset(labels)
    assert "Agent制作者不会以任何方式获取您的API-key" in str(config)
    assert "支持 OpenAI、DeepSeek、阿里云百炼、智谱 AI、月之暗面（Kimi）等厂商。" in str(config)
    assert "按服务提供商的URL文档填写。部分兼容/中转站服务不能使用网址直填，需在URL结尾添加 /v1。" in str(config)
    assert "只能填写服务提供商支持的模型，请注意横线、下划线或大小写格式。" in str(config)
    assert any(
        component["props"].get("placeholder") == "sk-***你的API key***"
        for component in components
        if component["type"] == "textbox"
    )
    assert {"llm-configuration-notice", "llm-configuration-status"}.issubset({
        component["props"].get("elem_id") for component in components
    })
    assert "DeepSeek API Key" not in labels
    assert any(
        component["props"].get("value") == "测试连接"
        and component["props"].get("elem_id") == "test-llm-connection-button"
        for component in config["components"]
        if component["type"] == "button"
    )


def test_llm_connection_action_shows_loading_and_public_success_or_failure(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "test_llm_connection",
        lambda *_args: {
            "ok": True,
            "code": "ok",
            "message": "连接与工具调用可用",
            "suggestion": "测试仅使用当前表单值，未保存任何配置。",
        },
    )

    updates = list(app_module._test_llm_connection("key", "https://example.test/v1", "model"))

    assert updates[0][0]["value"] == "正在测试 LLM 连接..."
    assert updates[0][1]["interactive"] is False
    assert updates[0][2]["interactive"] is False
    assert "连接与工具调用可用" in updates[-1][0]["value"]
    assert updates[-1][1]["interactive"] is True
    assert updates[-1][2]["interactive"] is True

    monkeypatch.setattr(
        app_module,
        "test_llm_connection",
        lambda *_args: {
            "ok": False,
            "code": "non_json",
            "message": "服务未返回 OpenAI API 响应",
            "suggestion": "检查 Base URL；常见修复是在末尾追加 /v1。",
        },
    )
    failed_update = list(app_module._test_llm_connection("key", "https://example.test", "model"))[-1]
    assert "服务未返回 OpenAI API 响应" in failed_update[0]["value"]
    assert "追加 /v1" in failed_update[0]["value"]


def test_saving_llm_config_does_not_trigger_connection_test(monkeypatch):
    test_calls = []
    monkeypatch.setattr(app_module, "test_llm_connection", lambda *_args: test_calls.append(True))
    monkeypatch.setattr(app_module, "save_llm_config", lambda *_args: "配置已保存")

    cleared_key, status = app_module._save_llm_config("key", "https://example.test/v1", "model")

    assert cleared_key == ""
    assert status == "配置已保存"
    assert test_calls == []


def test_theme_toggle_is_a_borderless_fixed_control_at_the_bottom_right():
    assert "position: fixed !important" in app_module.APP_CSS
    assert "bottom: 1rem" in app_module.APP_CSS
    assert "right: 1rem" in app_module.APP_CSS
    assert "#theme-toggle label" in app_module.APP_CSS
    assert "box-shadow: none !important" in app_module.APP_CSS


def test_ui_controls_use_one_cjk_capable_font_stack_for_chinese_and_latin_text():
    assert '--willy-ui-font: "Noto Sans CJK SC"' in app_module.APP_CSS
    assert "--font: var(--willy-ui-font)" in app_module.APP_CSS
    assert "font-family: var(--willy-ui-font) !important" in app_module.APP_CSS
    assert '[role="tab"]' in app_module.APP_CSS


def test_header_reserves_scrollbar_space_to_keep_its_title_fixed_across_tabs():
    assert "overflow-y: scroll" in app_module.APP_CSS
    assert "scrollbar-gutter: stable" in app_module.APP_CSS
    assert "grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr)" in app_module.APP_CSS


def test_proposal_chat_locks_controls_until_streaming_response_finishes(monkeypatch):
    def fake_chat(message, history, pending_plan):
        user_history = list(history) + [{"role": "user", "content": message}]
        yield "", user_history, user_history, pending_plan, "", {"visible": False}
        completed = user_history + [{"role": "assistant", "content": "方案已生成。"}]
        yield "", completed, completed, {"plan_id": "demo"}, "", {"visible": True}

    monkeypatch.setattr(app_module, "chat", fake_chat)

    updates = list(app_module._chat_wrapper("生成方案", []))

    assert updates[0][0]["interactive"] is False
    assert updates[0][4]["interactive"] is False
    assert updates[0][5]["interactive"] is False
    assert updates[-1][0]["interactive"] is True
    assert updates[-1][1][-1]["content"] == "方案已生成。"
    assert updates[-1][3] == {"plan_id": "demo"}
    assert updates[-1][5]["interactive"] is True
    assert updates[-1][4]["interactive"] is True


def test_proposal_chat_is_text_only_and_does_not_mutate_history():
    history = [
        {"role": "assistant", "content": "旧方案"},
        {"role": "user", "content": "调整温度"},
        {"role": "assistant", "content": "新方案"},
    ]

    rendered = app_module._render_proposal_chat(history)

    assert rendered == history
    assert rendered is not history
    assert rendered[-1] is not history[-1]


def test_task_page_uses_text_confirmation_without_a_click_bridge():
    config = app_module.app.get_config_file()
    elem_ids = {
        component["props"].get("elem_id")
        for component in config["components"]
    }

    assert "proposal-confirm-trigger" not in elem_ids
    assert not hasattr(app_module, "PIPELINE_LAUNCH_ACTION")
    assert not hasattr(app_module, "PIPELINE_LAUNCH_ACTION_JS")


def test_run_assistant_shows_message_before_waiting_for_reply(monkeypatch):
    history = [{"role": "assistant", "content": "欢迎"}]
    calls = []

    def fake_answer(run_id, message, prior_history):
        calls.append((run_id, message, prior_history))
        return "当前正在运行 NPT 退火。"

    monkeypatch.setattr(app_module, "chat_run_assistant", fake_answer)
    monkeypatch.setattr(app_module.frontend_api, "latest_run_id", lambda: "md_current")

    updates = list(app_module._ask_run_assistant("现在到哪一步了？", history))

    pending_history = updates[0][1]
    assert pending_history[-2] == {"role": "user", "content": "现在到哪一步了？"}
    assert pending_history[-1] == {"role": "assistant", "content": "正在读取当前运行事实..."}
    assert updates[0][0]["interactive"] is False
    assert updates[0][3]["interactive"] is False
    assert calls == [("md_current", "现在到哪一步了？", history)]
    assert updates[-1][1][-1] == {"role": "assistant", "content": "当前正在运行 NPT 退火。"}
    assert updates[-1][0]["interactive"] is True
    assert updates[-1][3]["interactive"] is True
    assert updates[-1][4] == "md_current"


def test_run_assistant_resets_browser_history_when_run_changes(monkeypatch):
    old_history = [
        {"role": "user", "content": "旧工程为什么失败？"},
        {"role": "assistant", "content": "旧工程的调整方案。"},
    ]
    received_history = []
    monkeypatch.setattr(app_module.frontend_api, "latest_run_id", lambda: "md_new")
    monkeypatch.setattr(
        app_module,
        "chat_run_assistant",
        lambda _run_id, _message, history: received_history.append(history) or "新工程正在运行。",
    )

    updates = list(
        app_module._ask_run_assistant("现在到哪一步了？", old_history, "md_old")
    )

    assert received_history == [app_module._run_assistant_welcome_history()]
    assert updates[-1][4] == "md_new"
    assert "旧工程" not in str(updates[-1][1])


def test_run_assistant_refresh_replaces_live_bubbles_without_growing_history(monkeypatch):
    history = app_module._run_assistant_welcome_history() + [
        {"role": "assistant", "content": "当前工程的普通解释。"},
    ]
    snapshot = {
        "run_id": "md_current",
        "summary": "### 工程状态\n\n状态：运行中",
        "pending_action": {
            "run_id": "md_current",
            "action_id": "eq-repair-1",
            "status": "awaiting_confirmation",
            "summary": "EQ 需要确认。",
            "restart_step": 9,
            "adjustments": [],
        },
    }
    monkeypatch.setattr(app_module.frontend_api, "get_run_panel_snapshot", lambda: snapshot)
    monkeypatch.setattr(app_module, "_refresh_stop_button", lambda *_args: {"visible": True})

    rendered, persisted, run_id, _button = app_module._refresh_run_assistant_view(
        history, "md_current", False, False
    )

    assert run_id == "md_current"
    assert len(rendered) == 4
    assert rendered[0] == history[0]
    assert rendered[1]["content"] == snapshot["summary"]
    assert "待确认的模拟调整" in rendered[2]["content"]
    assert rendered[3] == history[1]
    assert persisted == history
    assert persisted is not history


def test_run_assistant_refresh_resets_old_dialogue_on_run_switch(monkeypatch):
    monkeypatch.setattr(
        app_module.frontend_api,
        "get_run_panel_snapshot",
        lambda: {
            "run_id": "md_new",
            "summary": "### 工程状态 · 运行 md_new\n\n状态：运行中",
            "pending_action": None,
        },
    )
    monkeypatch.setattr(app_module, "_refresh_stop_button", lambda *_args: {"visible": True})

    rendered, persisted, run_id, _button = app_module._refresh_run_assistant_view(
        [{"role": "assistant", "content": "旧工程的调整方案。"}],
        "md_old",
        False,
        False,
    )

    assert run_id == "md_new"
    assert persisted == app_module._run_assistant_welcome_history()
    assert "旧工程" not in str(rendered)


def test_run_assistant_text_stop_and_pause_never_call_stop_pipeline_or_llm(monkeypatch):
    history = [{"role": "assistant", "content": "欢迎"}]
    stop_calls = []
    monkeypatch.setattr(
        app_module,
        "stop_pipeline",
        lambda *, clean: stop_calls.append(clean) or "已请求安全停止：等待 checkpoint",
    )
    monkeypatch.setattr(
        app_module,
        "chat_run_assistant",
        lambda *_args: (_ for _ in ()).throw(AssertionError("reserved command must not reach LLM")),
    )
    monkeypatch.setattr(
        app_module.frontend_api,
        "get_pending_action",
        lambda: None,
        raising=False,
    )

    for message in ("中止流水线", "确认中止", "暂停", "稍后"):
        update = list(
            app_module._handle_run_assistant_message(message, history, False, False)
        )[-1]
        assert update[1][-1]["content"]
        assert update[5] is False
        assert update[6] is False

    assert stop_calls == []


def test_run_assistant_renders_status_and_adjustment_as_separate_safe_bubbles():
    action = {
        "action_id": "eq-repair-1",
        "state": "pending",
        "summary": "EQ 验收未通过",
        "restart_step": 9,
        "adjustments": [{
            "name": "时间步长",
            "before": "0.001 ps",
            "after": "0.0005 ps",
            "reason": "降低数值不稳定风险",
            "raw_log": "must never render",
        }],
        "raw_log": "must never render",
    }
    history = app_module._run_assistant_welcome_history() + [
        {"role": "assistant", "content": "运行助理的普通回答。"},
    ]

    rendered = app_module._render_run_assistant_chat(history, {
        "run_id": "md_current",
        "summary": "### 工程状态\n\n状态：运行中",
        "pending_action": action,
    })

    assert rendered[0] == history[0]
    assert rendered[1] == {
        "role": "assistant", "content": "### 工程状态\n\n状态：运行中",
    }
    assert "EQ 验收未通过" in rendered[2]["content"]
    assert "第 9 步" in rendered[2]["content"]
    assert "时间步长：0.001 ps -> 0.0005 ps" in rendered[2]["content"]
    assert "降低数值不稳定风险" in rendered[2]["content"]
    assert "must never render" not in rendered[2]["content"]
    assert rendered[3] == history[1]
    assert rendered[3] is not history[1]
    assert history[1]["content"] == "运行助理的普通回答。"


def test_pending_action_approval_uses_adapter_without_llm(monkeypatch):
    history = [{"role": "assistant", "content": "欢迎"}]
    action = {
        "run_id": "md_current",
        "action_id": "eq-repair-1",
        "state_revision": 4,
        "config_fingerprint": "a" * 64,
        "status": "awaiting_confirmation",
        "summary": "EQ 验收未通过",
        "restart_step": 9,
        "adjustments": [],
    }
    confirmed_actions = []
    monkeypatch.setattr(
        app_module.frontend_api,
        "get_run_panel_snapshot",
        lambda: {
            "run_id": "md_current",
            "summary": "### 工程状态\n\n状态：等待确认",
            "pending_action": action,
        },
    )
    monkeypatch.setattr(
        app_module.frontend_api,
        "confirm_pending_action",
        lambda action_id, run_id, **kwargs: confirmed_actions.append((action_id, run_id, kwargs)) or "已确认，将从第 9 步重跑。",
        raising=False,
    )
    monkeypatch.setattr(
        app_module,
        "chat_run_assistant",
        lambda *_args: (_ for _ in ()).throw(AssertionError("approval must not reach LLM")),
    )

    update = list(
        app_module._handle_run_assistant_message("确认重跑", history, False, False)
    )[-1]

    assert confirmed_actions == [(
        "eq-repair-1",
        "md_current",
        {"state_revision": 4, "config_fingerprint": "a" * 64},
    )]
    assert update[1][-1]["content"] == "已确认，将从第 9 步重跑。"
    assert update[5] is False
    assert update[6] is False


def test_pending_action_natural_approval_uses_adapter_without_llm(monkeypatch):
    history = [{"role": "assistant", "content": "欢迎"}]
    action = {
        "run_id": "md_current",
        "action_id": "eq-repair-1",
        "status": "awaiting_confirmation",
        "summary": "EQ 验收未通过",
        "restart_step": 9,
        "adjustments": [],
    }
    confirmed_actions = []
    monkeypatch.setattr(
        app_module.frontend_api,
        "get_run_panel_snapshot",
        lambda: {"run_id": "md_current", "summary": "状态：等待确认", "pending_action": action},
    )
    monkeypatch.setattr(
        app_module.frontend_api,
        "confirm_pending_action",
        lambda action_id, run_id: confirmed_actions.append((action_id, run_id)) or "已确认，正在重跑。",
        raising=False,
    )
    monkeypatch.setattr(
        app_module,
        "chat_run_assistant",
        lambda *_args: (_ for _ in ()).throw(AssertionError("approval must not reach LLM")),
    )

    list(app_module._handle_run_assistant_message("我同意该方案并重跑", history, False, False))

    assert confirmed_actions == [("eq-repair-1", "md_current")]


def test_pending_action_controls_require_awaiting_confirmation(monkeypatch):
    history = [{"role": "assistant", "content": "欢迎"}]
    stale_action = {
        "run_id": "md_current",
        "action_id": "eq-repair-1",
        "status": "pending",
        "summary": "过期方案",
        "restart_step": 9,
        "adjustments": [],
    }
    confirmed_actions = []
    revisions = []
    monkeypatch.setattr(
        app_module.frontend_api,
        "get_run_panel_snapshot",
        lambda: {"run_id": "md_current", "summary": "状态：运行中", "pending_action": stale_action},
    )
    monkeypatch.setattr(
        app_module.frontend_api,
        "confirm_pending_action",
        lambda *args: confirmed_actions.append(args),
        raising=False,
    )
    monkeypatch.setattr(
        app_module.frontend_api,
        "revise_pending_action",
        lambda *args: revisions.append(args),
        raising=False,
    )
    monkeypatch.setattr(app_module, "chat_run_assistant", lambda *_args: "只读运行回答")

    approval_updates = list(
        app_module._handle_run_assistant_message("确认", history, False, False)
    )
    revision_updates = list(
        app_module._handle_run_assistant_message("将保温段改为 3 ns", history, False, False)
    )

    assert confirmed_actions == []
    assert revisions == []
    assert approval_updates[-1][2][-1]["content"] == "只读运行回答"
    assert revision_updates[-1][2][-1]["content"] == "只读运行回答"


def test_pending_action_revision_uses_backend_and_remains_pending(monkeypatch):
    history = [{"role": "assistant", "content": "欢迎"}]
    action = {
        "run_id": "md_current",
        "action_id": "eq-repair-1",
        "status": "awaiting_confirmation",
        "summary": "EQ 验收未通过",
        "restart_step": 9,
        "adjustments": [],
    }
    revisions = []
    monkeypatch.setattr(
        app_module.frontend_api,
        "get_run_panel_snapshot",
        lambda: {
            "run_id": "md_current",
            "summary": "### 工程状态\n\n状态：等待确认",
            "pending_action": action,
        },
    )
    monkeypatch.setattr(
        app_module.frontend_api,
        "revise_pending_action",
        lambda action_id, request, run_id: revisions.append((action_id, request, run_id))
        or "已按你的要求更新待确认方案；请审阅新方案后再明确确认。",
        raising=False,
    )
    monkeypatch.setattr(
        app_module,
        "chat_run_assistant",
        lambda *_args: (_ for _ in ()).throw(AssertionError("revision must not use read-only chat")),
    )

    updates = list(
        app_module._handle_run_assistant_message("将最终保温段改为 8 ns", history, False, False)
    )

    assert len(updates) == 2
    assert revisions == [("eq-repair-1", "将最终保温段改为 8 ns", "md_current")]
    assert updates[-1][2][-1]["content"].startswith("已按你的要求更新")
    assert updates[-1][5] is False
    assert updates[-1][6] is False


def test_parameter_shorthand_is_revision_request_but_question_is_not():
    assert app_module._requests_pending_action_revision("请把 tau_t 调到 0.5 ps") is True
    assert app_module._requests_pending_action_revision("为什么要调整 tau_t？") is False
