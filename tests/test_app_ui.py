"""Regression coverage for the Gradio conversation event adapters."""

from pathlib import Path
import app as app_module


def test_visualization_panel_refreshes_current_run_structures_on_focus():
    config = app_module.app.get_config_file()
    elem_ids = {
        component["props"].get("elem_id")
        for component in config["components"]
    }

    assert {
        "visualization-panel",
        "structure-run-selector",
        "structure-selector",
        "structure-viewer",
        "structure-legend",
    }.issubset(elem_ids)
    assert "可视化" in str(config)
    assert "运行目录" in str(config)
    assert "结构文件" in str(config)
    assert "focus" in str(config["dependencies"])
    assert "_refresh_visualization_run_choices" in app_module.__dict__
    assert "_refresh_visualization_file_choices" in app_module.__dict__
    component_order = [
        component["props"].get("elem_id")
        for component in config["components"]
    ]
    assert component_order.index("structure-run-selector") < component_order.index("structure-legend")
    assert component_order.index("structure-selector") < component_order.index("structure-legend")
    assert component_order.index("structure-legend") < component_order.index("structure-viewer")
    assert "legend-area-title" not in str(config)
    dropdowns = {
        component["props"].get("elem_id"): component["props"]
        for component in config["components"]
        if component["props"].get("elem_id") in {
            "structure-run-selector", "structure-selector",
        }
    }
    assert dropdowns["structure-run-selector"]["min_width"] == 0
    assert dropdowns["structure-selector"]["min_width"] == 0
    assert "flex-wrap: nowrap !important;" in app_module.APP_CSS


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


def test_visualization_run_and_file_selectors_refresh_independently(monkeypatch):
    rendered = []
    legends = []
    monkeypatch.setattr(
        app_module.frontend_api,
        "get_run_visualization_run_choices",
        lambda: ["md__202608080002", "md__202608080001"],
    )
    monkeypatch.setattr(
        app_module.frontend_api,
        "get_run_visualization_file_choices",
        lambda run_id: {
            "md__202608080002": ["prod.pdb"],
            "md__202608080001": ["eq.pdb", "model.mol2"],
        }.get(run_id, []),
    )
    monkeypatch.setattr(
        app_module.frontend_api,
        "render_run_visualization_html",
        lambda choice, **kwargs: rendered.append((choice, kwargs.get("run_id"))) or f"viewer:{choice}",
    )
    monkeypatch.setattr(
        app_module.frontend_api,
        "render_run_visualization_legend_html",
        lambda choice, **kwargs: legends.append((choice, kwargs.get("run_id"))) or f"legend:{choice}",
    )

    run_update, file_update, viewer, legend = app_module._refresh_visualization_run_choices(
        "removed-run", "model.mol2"
    )

    assert run_update["value"] == "md__202608080002"
    assert file_update["choices"] == ["prod.pdb"]
    assert file_update["value"] == "prod.pdb"
    assert viewer == "viewer:prod.pdb"
    assert legend == "legend:prod.pdb"
    assert rendered == [("prod.pdb", "md__202608080002")]
    assert legends == [("prod.pdb", "md__202608080002")]

    file_update, viewer, legend = app_module._refresh_visualization_file_choices(
        "md__202608080001", "model.mol2"
    )
    assert file_update["value"] == "model.mol2"
    assert viewer == "viewer:model.mol2"
    assert legend == "legend:model.mol2"


def test_proposal_example_is_a_textbox_placeholder_only():
    config = app_module.app.get_config_file()
    proposal_input = next(
        component for component in config["components"]
        if component["type"] == "textbox"
        and component["props"].get("placeholder") == app_module.PROPOSAL_EXAMPLE
    )
    assert proposal_input["props"]["lines"] == 3
    assert "启动示例" not in str(config)


def test_proposal_welcome_bubble_includes_execution_mode():
    welcome = app_module._proposal_welcome_history(app_module._LOCAL_EXECUTION_CONTEXT)

    assert len(welcome) == 1
    assert welcome[0]["role"] == "assistant"
    assert "你好！我是 **Willy-方案助理**" in welcome[0]["content"]
    assert "**当前方案执行方式**：本版本仅支持本机 GROMACS" in welcome[0]["content"]
    assert "G09 未经可靠全链路验证，使用时可能出现运行问题" in welcome[0]["content"]
    assert "proposal-execution-summary" not in str(app_module.app.get_config_file())


def test_assistant_chat_and_input_rows_share_bottom_alignment_contract():
    config = app_module.app.get_config_file()
    elem_ids = {
        component["props"].get("elem_id")
        for component in config["components"]
    }

    assert {
        "proposal-header",
        "run-header",
        "proposal-chat",
        "run-chat",
        "proposal-input-row",
        "run-input-row",
    }.issubset(elem_ids)
    assert "#proposal-chat,\n#run-chat {\n    flex: 0 0 auto;" in app_module.APP_CSS
    assert "height: 650px !important;" in app_module.APP_CSS
    assert "#proposal-header,\n#run-header {" in app_module.APP_CSS
    assert "#proposal-input-row,\n#run-input-row {\n    margin-bottom: 0;\n    margin-top: auto;\n}" in app_module.APP_CSS
    assert "#proposal-assistant { overflow: hidden; }" in app_module.APP_CSS
    textboxes = [
        component for component in config["components"]
        if component["type"] == "textbox"
    ]
    assert all(component["props"]["lines"] == 3 for component in textboxes[:2])


def test_run_assistant_disables_gradio_consecutive_message_grouping():
    config = app_module.app.get_config_file()
    run_chat = next(
        component for component in config["components"]
        if component["props"].get("elem_id") == "run-chat"
    )

    assert run_chat["props"]["group_consecutive_messages"] is False


def test_run_assistant_filters_visual_events_from_llm_history():
    history = app_module._run_assistant_welcome_history() + [
        app_module._run_assistant_event(
            "status", "md_current:status:running:1:none:none", "### 工程状态\n\n运行中"
        ),
        app_module._run_assistant_event(
            "proposal", "md_current:pending_action:eq-1", "#### 待确认的模拟调整"
        ),
        {"role": "user", "content": "现在到哪一步了？"},
        {"role": "assistant", "content": "当前正在运行。"},
    ]

    assert app_module._run_assistant_llm_history(history) == [
        app_module._run_assistant_welcome_history()[0],
        {"role": "user", "content": "现在到哪一步了？"},
        {"role": "assistant", "content": "当前正在运行。"},
    ]


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
    assert "--workbench-panel-height: 63.4rem" in app_module.APP_CSS
    assert ":root { --workbench-panel-height: 57.6rem; }" in app_module.APP_CSS
    assert "height: var(--workbench-panel-height)" in app_module.APP_CSS
    assert "min-height: var(--workbench-panel-height);" in app_module.APP_CSS
    assert "height: min(43.2rem, calc(var(--workbench-panel-height) - 12rem));" in app_module.APP_CSS
    assert ".structure-viewer-frame" in app_module.APP_CSS
    assert ".structure-viewer-name" in app_module.APP_CSS
    assert "#structure-legend" in app_module.APP_CSS
    chatboxes = [
        component for component in config["components"]
        if component["type"] == "chatbot"
    ]
    assert app_module.ASSISTANT_CHAT_HEIGHT == 650
    assert all(
        component["props"]["height"] == app_module.ASSISTANT_CHAT_HEIGHT
        for component in chatboxes
    )


def test_application_title_is_consistent_across_ui_surfaces():
    title = "Willy : AI驱动的小分子Gromacs模拟工具"
    config = app_module.app.get_config_file()

    assert title in str(config)
    assert "height=400" not in app_module.APP_CSS
    assert "#run-chat .pipeline-status-indicator" in app_module.APP_CSS
    assert ".gradio-container.willy-dark #run-assistant code" in app_module.APP_CSS
    assert "color: #f0f5f1 !important;" in app_module.APP_CSS
    assert "#structure-selector {" in app_module.APP_CSS
    assert "#structure-viewer .structure-viewer-frame iframe" in app_module.APP_CSS
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
    assert "架构、交付、质量、网关：ChatGPT 5.6-terra" in str(config)
    assert "重跑续跑计划" in str(config)
    assert "服务器上的GROMACS软件远程控制功能" in str(config)
    assert {
        "about-page",
        "about-qr-section",
        "official-account-code",
        "third-party-notices",
    }.issubset(elem_ids)
    assert Path(app_module.OFFICIAL_ACCOUNT_QR).is_file()
    assert "第三方组件引用与著作权" in str(config)
    assert "10.1002/jcc.22885" in str(config)
    assert "10.1002/jcc.21224" in str(config)
    assert "grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr)" in app_module.APP_CSS
    assert "font-size: 2.25rem" in app_module.APP_CSS
    assert "border-top: 1px solid #aeb4b0" in app_module.APP_CSS
    assert "#third-party-notices" in app_module.APP_CSS
    assert "border-top: 1px dashed var(--input-border)" in app_module.APP_CSS


def test_beginner_guide_sits_between_configuration_and_about_tabs():
    config = app_module.app.get_config_file()
    tab_labels = [
        component["props"].get("label")
        for component in config["components"]
        if component["type"] == "tabitem"
    ]
    elem_ids = {
        component["props"].get("elem_id")
        for component in config["components"]
    }

    assert tab_labels.index("配置") < tab_labels.index("新手指南") < tab_labels.index("关于")
    assert "beginner-guide-page" in elem_ids
    for heading in (
        "零、Agent 的概况",
        "一、运行 Agent 最少需要的外置依赖",
        "二、体系的初始确定",
        "三、可供修改的参数",
        "3.1 量子层",
        "3.2 拓扑层",
        "3.3 模拟层",
        "四、LLM 的报错处理",
        "五、获取结构",
        "六、其他",
    ):
        assert heading in app_module.BEGINNER_GUIDE_HTML
    for guide_text in (
        "Willy 基于Linux/WSL2系统",
        "二者功能、聊天记录不互通",
        "结构优化-电荷设置-拓扑生成-模拟参数生成-进行模拟",
        "GROMACS 2022.0 或更高版本",
        "ORCA 6.x",
        "https://orcaforum.kofo.mpg.de/",
        "WILLY_ORCA_HOME",
        "LigParGen（仅 OPLS-AA 路径）",
        "完整 Open Babel 3",
        "WILLY_BOSS_HOME",
        "RDF、RMSD等可视化图表、SSH服务器连接",
    ):
        assert guide_text in app_module.BEGINNER_GUIDE_HTML
    assert "#beginner-guide-page .guide-section" in app_module.APP_CSS
    assert "border-top: 1px solid var(--input-border)" in app_module.APP_CSS


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


def test_llm_configuration_exposes_mutually_exclusive_managed_controls():
    config = app_module.app.get_config_file()
    elem_ids = {component["props"].get("elem_id") for component in config["components"]}

    assert {
        "llm-provider-mode",
        "managed-gateway-status",
        "managed-gateway-register",
        "managed-gateway-test",
        "test-llm-connection-button",
    }.issubset(elem_ids)
    assert "托管网关（服务器）" in str(config)
    assert "确认接入" in str(config)
    assert "一次性邀请码" not in str(config)
    assert "#llm-provider-mode .wrap" in app_module.APP_CSS
    assert "grid-template-columns: minmax(0, 1fr) !important;" in app_module.APP_CSS
    assert 'input[type="radio"]:checked' in app_module.APP_CSS
    assert "radial-gradient(circle at center, #111111" in app_module.APP_CSS
    assert '#llm-provider-mode .wrap > label:has(input[type="radio"]:checked)' in app_module.APP_CSS
    assert "box-shadow: 0 0.3rem 0.85rem" in app_module.APP_CSS


def test_managed_mode_switch_and_self_service_registration(monkeypatch):
    mode_calls = []
    monkeypatch.setattr(app_module, "save_llm_mode", lambda mode: mode_calls.append(mode) or "模式已保存")
    monkeypatch.setattr(app_module, "get_llm_config_notice", lambda: "托管提示")

    managed_column, byok_column, notice, status = app_module._select_llm_mode("managed")
    assert mode_calls == ["managed"]
    assert managed_column["visible"] is True
    assert byok_column["visible"] is False
    assert notice["value"] == "托管提示"
    assert status["value"] == "模式已保存"

    monkeypatch.setattr(app_module, "request_managed_gateway_registration", lambda: "设备已申请")
    monkeypatch.setattr(app_module, "_managed_gateway_status_markdown", lambda: "等待管理员批准")
    registration_status, gateway_status = app_module._request_managed_gateway_registration()
    assert registration_status == "设备已申请"
    assert gateway_status == "等待管理员批准"


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
    assert ".gradio-container [role=\"tab\"]" in app_module.APP_CSS
    assert "font-size: 1.1rem;" in app_module.APP_CSS
    assert "font-weight: 700;" in app_module.APP_CSS


def test_header_reserves_scrollbar_space_to_keep_its_title_fixed_across_tabs():
    assert "overflow-y: scroll" in app_module.APP_CSS
    assert "scrollbar-gutter: stable" in app_module.APP_CSS
    assert "grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr)" in app_module.APP_CSS


def test_proposal_chat_locks_controls_until_streaming_response_finishes(monkeypatch):
    received_contexts = []

    def fake_chat(message, history, pending_plan, *, execution_context=None):
        received_contexts.append(execution_context)
        user_history = list(history) + [{"role": "user", "content": message}]
        yield "", user_history, user_history, pending_plan, "", {"visible": False}
        completed = user_history + [{"role": "assistant", "content": "方案已生成。"}]
        yield "", completed, completed, {"plan_id": "demo"}, "", {"visible": True}

    monkeypatch.setattr(app_module, "chat", fake_chat)

    execution_context = {
        "execution_mode": "ssh",
        "execution_profile_id": "Lab_GPU",
        "available": True,
        "summary": "已登记，尚未进行连接预检。",
    }
    updates = list(app_module._chat_wrapper("生成方案", [], None, execution_context))

    assert updates[0][0]["interactive"] is False
    assert updates[0][4]["interactive"] is False
    assert updates[0][5]["interactive"] is False
    assert updates[-1][0]["interactive"] is True
    assert updates[-1][1][-1]["content"] == "方案已生成。"
    assert updates[-1][3] == {"plan_id": "demo"}
    assert updates[-1][5]["interactive"] is True
    assert updates[-1][4]["interactive"] is True
    assert received_contexts == [execution_context]


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


def test_structure_upload_accepts_orca_input_files():
    upload_props = next(
        component["props"]
        for component in app_module.app.get_config_file()["components"]
        if component.get("type") == "uploadbutton"
        and component.get("props", {}).get("label") == "上传结构"
    )

    assert ".inp" in upload_props["file_types"]


def test_run_assistant_shows_message_before_waiting_for_reply(monkeypatch):
    history = app_module._run_assistant_welcome_history()
    assert "md_run/最新编号/" in history[0]["content"]
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


def test_run_assistant_refresh_updates_one_status_bubble_without_growing_history(monkeypatch):
    first_snapshot = {
        "run_id": "md_current",
        "summary": "### 工程状态\n\n状态：运行中",
        "live_summary": "### 工程状态\n\n状态：运行中",
        "status_event_id": "md_current:status:running:8:none:none",
    }
    history = app_module._sync_run_assistant_history(
        app_module._run_assistant_welcome_history(), first_snapshot
    )
    snapshot = {
        **first_snapshot,
        "live_summary": "### 工程状态\n\n状态：运行中\n\n当前工序预计结束：10:00",
    }
    monkeypatch.setattr(app_module.frontend_api, "get_run_panel_snapshot", lambda: snapshot)
    monkeypatch.setattr(app_module, "_refresh_stop_button", lambda *_args: {"visible": True})

    rendered, persisted, run_id, _button = app_module._refresh_run_assistant_view(
        history, "md_current", False, False
    )

    assert run_id == "md_current"
    assert len(rendered) == 2
    assert rendered[0] == history[0]
    assert rendered[1]["content"] == snapshot["live_summary"]
    assert persisted[1]["_run_assistant_event_id"] == first_snapshot["status_event_id"]
    assert persisted is not history


def test_run_assistant_seals_status_and_appends_distinct_failure_events(monkeypatch):

    def snapshot(revision, action_id, error):
        return {
            "run_id": "md_current",
            "summary": f"### 工程状态\n\n×{error}",
            "live_summary": "### 工程状态\n\n状态：等待用户确认调整方案",
            "status_event_id": f"md_current:status:awaiting_confirmation:9:{action_id}:error:{revision}",
            "error_event": {
                "event_id": f"md_current:error:{revision}",
                "content": f"#### 工程错误\n\n×{error}",
            },
            "timeline_events": True,
            "pending_action": {
                "run_id": "md_current",
                "action_id": action_id,
                "status": "awaiting_confirmation",
                "summary": f"{error} 的调整方案",
                "restart_step": 9,
                "adjustments": [],
            },
        }

    running = {
        "run_id": "md_current",
        "summary": "### 工程状态\n\n状态：运行中",
        "live_summary": "### 工程状态\n\n状态：运行中",
        "status_event_id": "md_current:status:running:9:none:none",
    }
    history = app_module._sync_run_assistant_history(
        app_module._run_assistant_welcome_history(), running
    )
    current = snapshot(49, "eq-first", "第一次 EQ 失败")
    monkeypatch.setattr(app_module.frontend_api, "get_run_panel_snapshot", lambda: current)
    monkeypatch.setattr(app_module, "_refresh_stop_button", lambda *_args: {"visible": True})

    rendered, persisted, run_id, _button = app_module._refresh_run_assistant_view(
        history, "md_current", False, False
    )

    assert run_id == "md_current"
    assert rendered[0] == history[0]
    assert rendered[1]["content"] == running["live_summary"]
    assert rendered[2]["content"] == current["live_summary"]
    assert "第一次 EQ 失败" in rendered[3]["content"]
    assert "待确认的模拟调整" in rendered[4]["content"]

    current = snapshot(69, "eq-second", "第二次 EQ 失败")
    rendered, persisted, run_id, _button = app_module._refresh_run_assistant_view(
        persisted, run_id, False, False
    )

    status_cards = [
        message for message in persisted
        if message.get("_run_assistant_event_kind") == "status"
    ]
    assert len(status_cards) == 3
    assert "第二次 EQ 失败" in rendered[-2]["content"]
    assert "第二次 EQ 失败" in rendered[-1]["content"]
    repeated = app_module._refresh_run_assistant_view(
        persisted, run_id, False, False
    )
    assert repeated[1] == persisted


def test_run_assistant_appends_a_new_status_after_confirmed_repair():
    waiting = {
        "run_id": "md_current",
        "summary": "### 工程状态\n\n状态：等待确认",
        "live_summary": "### 工程状态\n\n状态：等待确认",
        "status_event_id": "md_current:status:awaiting_confirmation:9:eq-first:none",
        "pending_action": {
            "run_id": "md_current",
            "action_id": "eq-first",
            "status": "awaiting_confirmation",
            "summary": "EQ 需要确认。",
            "restart_step": 9,
            "adjustments": [],
        },
    }
    history = app_module._sync_run_assistant_history(
        app_module._run_assistant_welcome_history(), waiting
    )
    confirmed = history + [
        {"role": "user", "content": "确认重跑"},
        {"role": "assistant", "content": "已确认，将从第 9 步重跑。"},
    ]
    retrying = {
        "run_id": "md_current",
        "summary": "### 工程状态\n\n状态：重跑中",
        "live_summary": "### 工程状态\n\n状态：重跑中",
        "status_event_id": "md_current:status:retrying:9:none:none",
    }

    persisted = app_module._sync_run_assistant_history(confirmed, retrying)
    rendered = app_module._render_run_assistant_chat(persisted)

    assert [
        message["content"] for message in rendered
        if message["content"].startswith("### 工程状态")
    ] == [waiting["live_summary"], retrying["live_summary"]]
    assert "待确认的模拟调整" in rendered[2]["content"]
    assert rendered[-1]["content"] == retrying["live_summary"]


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
    assert persisted[0] == app_module._run_assistant_welcome_history()[0]
    assert persisted[1]["_run_assistant_event_kind"] == "status"
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
    history = app_module._run_assistant_welcome_history()
    history = app_module._sync_run_assistant_history(history, {
        "run_id": "md_current",
        "summary": "### 工程状态\n\n状态：运行中",
        "live_summary": "### 工程状态\n\n状态：运行中",
        "status_event_id": "md_current:status:running:8:none:none",
    })
    history.append({"role": "assistant", "content": "运行助理的普通回答。"})
    history = app_module._sync_run_assistant_history(history, {
        "run_id": "md_current",
        "summary": "### 工程状态\n\n状态：等待确认",
        "live_summary": "### 工程状态\n\n状态：等待确认",
        "status_event_id": "md_current:status:awaiting_confirmation:9:eq-repair-1:none",
        "pending_action": action,
    })
    rendered = app_module._render_run_assistant_chat(history)

    assert rendered[0] == history[0]
    assert rendered[1] == {
        "role": "assistant", "content": "### 工程状态\n\n状态：运行中",
    }
    assert rendered[2]["content"] == "运行助理的普通回答。"
    assert rendered[3]["content"] == "### 工程状态\n\n状态：等待确认"
    assert "EQ 验收未通过" in rendered[4]["content"]
    assert "第 9 步" in rendered[4]["content"]
    assert "时间步长：0.001 ps -> 0.0005 ps" in rendered[4]["content"]
    assert "降低数值不稳定风险" in rendered[4]["content"]
    assert "must never render" not in rendered[4]["content"]


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


def test_remote_task_tab_is_frozen_and_local_task_is_the_active_entry():
    config = app_module.app.get_config_file()
    tab_labels = [
        component["props"].get("label")
        for component in config["components"]
        if component["type"] == "tabitem"
    ]
    elem_ids = {
        component["props"].get("elem_id")
        for component in config["components"]
    }
    radio_labels = {
        component["props"].get("label")
        for component in config["components"]
        if component["type"] == "radio"
    }
    dropdown_labels = {
        component["props"].get("label")
        for component in config["components"]
        if component["type"] == "dropdown"
    }

    assert tab_labels.index("本地任务") < tab_labels.index("远程任务") < tab_labels.index("配置")
    assert {
        "remote-task-page",
        "remote-mode-selector",
        "remote-profile-selector",
        "remote-capability-summary",
        "remote-task-unavailable-notice",
    }.issubset(elem_ids)
    assert "远程执行方式（已冻结）" in radio_labels
    assert "远程执行配置（已冻结）" in dropdown_labels
    assert "主机地址" not in str(config)
    assert "私钥路径" not in str(config)
    assert "远程命令" not in str(config)
    assert "#remote-capability-summary" in app_module.APP_CSS
    assert "本版本暂不支持远程执行" in str(config)
    controls = {
        component["props"].get("elem_id"): component["props"]
        for component in config["components"]
    }
    assert controls["remote-mode-selector"]["interactive"] is False
    assert controls["remote-profile-selector"]["interactive"] is False
    assert "value" not in controls["remote-mode-selector"]
    assert "refresh-remote-status-button" not in elem_ids
    assert app_module._LOCAL_EXECUTION_CONTEXT["execution_mode"] == "local"
