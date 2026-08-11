# Willy 测试用例集

> 维护角色：6 号测试工程师
> 来源：`python3 -m pytest --collect-only -q` 收集到 825 条 pytest 用例；另有 18 条离线 LLM mock eval 场景；本台账共 843 条记录。

## 使用说明

- `T001` 起的记录与 pytest node ID 一一对应；参数化变体按独立用例编号。
- `L001` 起的记录是 `tests.llm_eval.scenarios` 中的受控 LLM 行为场景，不计入 pytest 收集数。
- 测试方式由用例源码中的参数化、`tmp_path`、`monkeypatch`、mock/patch、异常断言与 external 标记归纳；详细断言以 node ID 对应源码为准。
- 重新生成：`python3 -m tests.tools.generate_test_case_catalog --output tests/reports/test_case_catalog.md`。每次增删测试或 LLM 场景后必须重新生成并核对总数。

## 分类总览

| 分类 | 记录数 | 覆盖范围 |
|---|---:|---|
| A | 103 | UI 与前端交互：Gradio 布局、确认式操作、可视化、状态展示、公开错误边界与 fake-executor 浏览器验收。 |
| B | 112 | 环境与基础错误模型：依赖发现、环境变量优先级、vendor 完整性/发布证据、能力报告脱敏与结构化错误协议。 |
| C | 72 | 配置与全局工具：config v2、迁移、分子知识库与 Layer 0 工具 schema/handler。 |
| D | 76 | Agent 与 LLM 行为：OpenAI-compatible 配置、LayerAgent 的上下文、受控动作、预算、重试、升级，以及 18 个离线 mock LLM 场景。 |
| E | 76 | 量子与跨层 Tool 契约：量子/引擎日志解析、ORCA 结构产物、量子工具、后端原始输入审计与跨层 tool 返回协议。 |
| F | 47 | 拓扑后端与组装：Sobtop/OPLS 后端、manifest、重试账本、ITP 校验和主拓扑组装。 |
| G | 108 | 模拟执行、ETA 与后处理：EM/EQ/PROD 协议、GROMACS 适配、ETA、阶段产物和分析流程。 |
| H | 123 | 流水线编排与状态机：步骤构建、唯一步骤注册、失败修复、run workspace、公开状态和恢复边界。 |
| I | 73 | 运行管理、启动控制与运行助理：运行预留、远程 profile/transport、互斥启动、进程生命周期、保留清理、审计、provenance、RunStore 与只读助理。 |
| J | 22 | 外部 Smoke 与发布证据：外部工具预检、fixture 完整性、脱敏批次报告、证据归档和 required 发布门禁。 |
| K | 31 | 托管 LLM 网关：服务端限额的设备自助申请、客户端私钥、短期令牌、签名/nonce、防重放、模型白名单、额度账本、管理员控制面和离线 fake-upstream 脱敏边界。 |

## A. UI 与前端交互

Gradio 布局、确认式操作、可视化、状态展示、公开错误边界与 fake-executor 浏览器验收。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T001 | tests/e2e/test_browser_workflow.py::test_browser_workflow_uses_fake_executor_without_touching_a_real_run | app.py 浏览器端到端 fake executor 验收 | 验证 `browser_workflow_uses_fake_executor_without_touching_a_real_run` 行为 | tmp_path 隔离工作区 |
| T021 | tests/test_app_ui.py::test_visualization_panel_refreshes_current_run_structures_on_focus | app.py UI 结构与交互状态 | 验证 `visualization_panel_refreshes_current_run_structures_on_focus` 行为 | 进程内行为断言 |
| T022 | tests/test_app_ui.py::test_visualization_refresh_keeps_a_current_filename_or_uses_the_first_choice | app.py UI 结构与交互状态 | 验证 `visualization_refresh_keeps_a_current_filename_or_uses_the_first_choice` 行为 | monkeypatch |
| T023 | tests/test_app_ui.py::test_visualization_run_and_file_selectors_refresh_independently | app.py UI 结构与交互状态 | 验证 `visualization_run_and_file_selectors_refresh_independently` 行为 | monkeypatch |
| T024 | tests/test_app_ui.py::test_proposal_example_is_a_textbox_placeholder_only | app.py UI 结构与交互状态 | 验证 `proposal_example_is_a_textbox_placeholder_only` 行为 | 进程内行为断言 |
| T025 | tests/test_app_ui.py::test_proposal_welcome_bubble_includes_execution_mode | app.py UI 结构与交互状态 | 验证 `proposal_welcome_bubble_includes_execution_mode` 行为 | 进程内行为断言 |
| T026 | tests/test_app_ui.py::test_assistant_chat_and_input_rows_share_bottom_alignment_contract | app.py UI 结构与交互状态 | 验证 `assistant_chat_and_input_rows_share_bottom_alignment_contract` 行为 | 进程内行为断言 |
| T027 | tests/test_app_ui.py::test_run_assistant_disables_gradio_consecutive_message_grouping | app.py UI 结构与交互状态 | 验证 `run_assistant_disables_gradio_consecutive_message_grouping` 行为 | 进程内行为断言 |
| T028 | tests/test_app_ui.py::test_run_assistant_filters_visual_events_from_llm_history | app.py UI 结构与交互状态 | 验证 `run_assistant_filters_visual_events_from_llm_history` 行为 | 进程内行为断言 |
| T029 | tests/test_app_ui.py::test_stop_control_uses_server_side_confirmation_without_cleanup_choices | app.py UI 结构与交互状态 | 验证 `stop_control_uses_server_side_confirmation_without_cleanup_choices` 行为 | 进程内行为断言 |
| T030 | tests/test_app_ui.py::test_stop_control_only_dispatches_after_second_server_side_confirmation | app.py UI 结构与交互状态 | 验证 `stop_control_only_dispatches_after_second_server_side_confirmation` 行为 | monkeypatch |
| T031 | tests/test_app_ui.py::test_stop_control_stays_interactive_when_backend_does_not_acknowledge | app.py UI 结构与交互状态 | 验证 `stop_control_stays_interactive_when_backend_does_not_acknowledge` 行为 | monkeypatch |
| T032 | tests/test_app_ui.py::test_task_workspace_has_four_equal_height_panels_and_chart_placeholder | app.py UI 结构与交互状态 | 验证 `task_workspace_has_four_equal_height_panels_and_chart_placeholder` 行为 | 进程内行为断言 |
| T033 | tests/test_app_ui.py::test_application_title_is_consistent_across_ui_surfaces | app.py UI 结构与交互状态 | 验证 `application_title_is_consistent_across_ui_surfaces` 行为 | 进程内行为断言 |
| T034 | tests/test_app_ui.py::test_about_tab_exposes_project_summary_and_official_account_qr_code | app.py UI 结构与交互状态 | 验证 `about_tab_exposes_project_summary_and_official_account_qr_code` 行为 | 进程内行为断言 |
| T035 | tests/test_app_ui.py::test_beginner_guide_sits_between_configuration_and_about_tabs | app.py UI 结构与交互状态 | 验证 `beginner_guide_sits_between_configuration_and_about_tabs` 行为 | 进程内行为断言 |
| T036 | tests/test_app_ui.py::test_configuration_tab_exposes_generic_llm_fields | app.py UI 结构与交互状态 | 验证 `configuration_tab_exposes_generic_llm_fields` 行为 | 进程内行为断言 |
| T037 | tests/test_app_ui.py::test_llm_connection_action_shows_loading_and_public_success_or_failure | app.py UI 结构与交互状态 | 验证 `llm_connection_action_shows_loading_and_public_success_or_failure` 行为 | monkeypatch |
| T038 | tests/test_app_ui.py::test_saving_llm_config_does_not_trigger_connection_test | app.py UI 结构与交互状态 | 验证 `saving_llm_config_does_not_trigger_connection_test` 行为 | monkeypatch |
| T039 | tests/test_app_ui.py::test_llm_configuration_exposes_mutually_exclusive_managed_controls | app.py UI 结构与交互状态 | 验证 `llm_configuration_exposes_mutually_exclusive_managed_controls` 行为 | 进程内行为断言 |
| T040 | tests/test_app_ui.py::test_managed_mode_switch_and_self_service_registration | app.py UI 结构与交互状态 | 验证 `managed_mode_switch_and_self_service_registration` 行为 | monkeypatch |
| T041 | tests/test_app_ui.py::test_theme_toggle_is_a_borderless_fixed_control_at_the_bottom_right | app.py UI 结构与交互状态 | 验证 `theme_toggle_is_a_borderless_fixed_control_at_the_bottom_right` 行为 | 进程内行为断言 |
| T042 | tests/test_app_ui.py::test_ui_controls_use_one_cjk_capable_font_stack_for_chinese_and_latin_text | app.py UI 结构与交互状态 | 验证 `ui_controls_use_one_cjk_capable_font_stack_for_chinese_and_latin_text` 行为 | 进程内行为断言 |
| T043 | tests/test_app_ui.py::test_header_reserves_scrollbar_space_to_keep_its_title_fixed_across_tabs | app.py UI 结构与交互状态 | 验证 `header_reserves_scrollbar_space_to_keep_its_title_fixed_across_tabs` 行为 | 进程内行为断言 |
| T044 | tests/test_app_ui.py::test_proposal_chat_locks_controls_until_streaming_response_finishes | app.py UI 结构与交互状态 | 验证 `proposal_chat_locks_controls_until_streaming_response_finishes` 行为 | monkeypatch |
| T045 | tests/test_app_ui.py::test_proposal_chat_is_text_only_and_does_not_mutate_history | app.py UI 结构与交互状态 | 验证 `proposal_chat_is_text_only_and_does_not_mutate_history` 行为 | 进程内行为断言 |
| T046 | tests/test_app_ui.py::test_task_page_uses_text_confirmation_without_a_click_bridge | app.py UI 结构与交互状态 | 验证 `task_page_uses_text_confirmation_without_a_click_bridge` 行为 | 进程内行为断言 |
| T047 | tests/test_app_ui.py::test_structure_upload_accepts_orca_input_files | app.py UI 结构与交互状态 | 验证 `structure_upload_accepts_orca_input_files` 行为 | 进程内行为断言 |
| T048 | tests/test_app_ui.py::test_run_assistant_shows_message_before_waiting_for_reply | app.py UI 结构与交互状态 | 验证 `run_assistant_shows_message_before_waiting_for_reply` 行为 | monkeypatch |
| T049 | tests/test_app_ui.py::test_run_assistant_resets_browser_history_when_run_changes | app.py UI 结构与交互状态 | 验证 `run_assistant_resets_browser_history_when_run_changes` 行为 | monkeypatch |
| T050 | tests/test_app_ui.py::test_run_assistant_refresh_updates_one_status_bubble_without_growing_history | app.py UI 结构与交互状态 | 验证 `run_assistant_refresh_updates_one_status_bubble_without_growing_history` 行为 | monkeypatch |
| T051 | tests/test_app_ui.py::test_run_assistant_seals_status_and_appends_distinct_failure_events | app.py UI 结构与交互状态 | 验证 `run_assistant_seals_status_and_appends_distinct_failure_events` 行为 | monkeypatch |
| T052 | tests/test_app_ui.py::test_run_assistant_appends_a_new_status_after_confirmed_repair | app.py UI 结构与交互状态 | 验证 `run_assistant_appends_a_new_status_after_confirmed_repair` 行为 | 进程内行为断言 |
| T053 | tests/test_app_ui.py::test_run_assistant_refresh_resets_old_dialogue_on_run_switch | app.py UI 结构与交互状态 | 验证 `run_assistant_refresh_resets_old_dialogue_on_run_switch` 行为 | monkeypatch |
| T054 | tests/test_app_ui.py::test_run_assistant_text_stop_and_pause_never_call_stop_pipeline_or_llm | app.py UI 结构与交互状态 | 验证 `run_assistant_text_stop_and_pause_never_call_stop_pipeline_or_llm` 行为 | monkeypatch |
| T055 | tests/test_app_ui.py::test_run_assistant_renders_status_and_adjustment_as_separate_safe_bubbles | app.py UI 结构与交互状态 | 验证 `run_assistant_renders_status_and_adjustment_as_separate_safe_bubbles` 行为 | 进程内行为断言 |
| T056 | tests/test_app_ui.py::test_pending_action_approval_uses_adapter_without_llm | app.py UI 结构与交互状态 | 验证 `pending_action_approval_uses_adapter_without_llm` 行为 | monkeypatch |
| T057 | tests/test_app_ui.py::test_pending_action_natural_approval_uses_adapter_without_llm | app.py UI 结构与交互状态 | 验证 `pending_action_natural_approval_uses_adapter_without_llm` 行为 | monkeypatch |
| T058 | tests/test_app_ui.py::test_pending_action_controls_require_awaiting_confirmation | app.py UI 结构与交互状态 | 验证 `pending_action_controls_require_awaiting_confirmation` 行为 | monkeypatch |
| T059 | tests/test_app_ui.py::test_pending_action_revision_uses_backend_and_remains_pending | app.py UI 结构与交互状态 | 验证 `pending_action_revision_uses_backend_and_remains_pending` 行为 | monkeypatch |
| T060 | tests/test_app_ui.py::test_parameter_shorthand_is_revision_request_but_question_is_not | app.py UI 结构与交互状态 | 验证 `parameter_shorthand_is_revision_request_but_question_is_not` 行为 | 进程内行为断言 |
| T061 | tests/test_app_ui.py::test_remote_task_tab_is_frozen_and_local_task_is_the_active_entry | app.py UI 结构与交互状态 | 验证 `remote_task_tab_is_frozen_and_local_task_is_the_active_entry` 行为 | 进程内行为断言 |
| T193 | tests/test_frontend_api.py::test_stop_during_gromacs_requests_checkpoint_first_shutdown | willy.frontend_api | 验证 `stop_during_gromacs_requests_checkpoint_first_shutdown` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T194 | tests/test_frontend_api.py::test_clean_stop_unlinks_only_known_root_artifacts | willy.frontend_api | 验证 `clean_stop_unlinks_only_known_root_artifacts` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T195 | tests/test_frontend_api.py::test_reconcile_stale_latest_run_marks_transient_status_aborted_with_audit | willy.frontend_api | 验证 `reconcile_stale_latest_run_marks_transient_status_aborted_with_audit` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T196 | tests/test_frontend_api.py::test_frontend_polling_does_not_reconcile_or_mutate_status | willy.frontend_api | 验证 `frontend_polling_does_not_reconcile_or_mutate_status` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T197 | tests/test_frontend_api.py::test_legacy_pipeline_group_keeps_controls_in_running_state | willy.frontend_api | 验证 `legacy_pipeline_group_keeps_controls_in_running_state` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T198 | tests/test_frontend_api.py::test_reconcile_skips_run_with_fresh_gromacs_eta_heartbeat | willy.frontend_api | 验证 `reconcile_skips_run_with_fresh_gromacs_eta_heartbeat` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T199 | tests/test_frontend_api.py::test_reconcile_abandons_abort_when_revision_advances_during_check | willy.frontend_api | 验证 `reconcile_abandons_abort_when_revision_advances_during_check` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T200 | tests/test_frontend_api.py::test_stopping_status_uses_public_checkpoint_message | willy.frontend_api | 验证 `stopping_status_uses_public_checkpoint_message` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T201 | tests/test_frontend_api.py::test_escalated_protocol_change_tells_user_the_run_was_not_modified | willy.frontend_api | 验证 `escalated_protocol_change_tells_user_the_run_was_not_modified` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T202 | tests/test_frontend_api.py::test_escalated_runtime_failure_shows_recovery_conclusion | willy.frontend_api | 验证 `escalated_runtime_failure_shows_recovery_conclusion` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T203 | tests/test_frontend_api.py::test_awaiting_confirmation_summary_explains_the_llm_and_user_boundary | willy.frontend_api | 验证 `awaiting_confirmation_summary_explains_the_llm_and_user_boundary` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T204 | tests/test_frontend_api.py::test_get_pending_action_reads_only_the_waiting_public_summary | willy.frontend_api | 验证 `get_pending_action_reads_only_the_waiting_public_summary` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T205 | tests/test_frontend_api.py::test_pending_action_never_leaks_from_old_run_to_newest_run | willy.frontend_api | 验证 `pending_action_never_leaks_from_old_run_to_newest_run` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T206 | tests/test_frontend_api.py::test_run_panel_snapshot_resolves_the_run_once_for_status_and_action | willy.frontend_api | 验证 `run_panel_snapshot_resolves_the_run_once_for_status_and_action` 行为 | monkeypatch |
| T207 | tests/test_frontend_api.py::test_active_pipeline_run_wins_over_newer_historical_index_entry | willy.frontend_api | 验证 `active_pipeline_run_wins_over_newer_historical_index_entry` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T208 | tests/test_frontend_api.py::test_confirm_pending_action_reserves_the_same_run_and_spawns_controlled_retry | willy.frontend_api | 验证 `confirm_pending_action_reserves_the_same_run_and_spawns_controlled_retry` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T209 | tests/test_frontend_api.py::test_confirm_pending_action_restores_waiting_state_when_runner_cannot_start | willy.frontend_api | 验证 `confirm_pending_action_restores_waiting_state_when_runner_cannot_start` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T210 | tests/test_frontend_api.py::test_run_status_cas_rejects_stale_revision_and_terminal_regression | willy.frontend_api | 验证 `run_status_cas_rejects_stale_revision_and_terminal_regression` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T211 | tests/test_frontend_api.py::test_revising_pending_action_keeps_run_waiting_and_config_unchanged | willy.frontend_api | 验证 `revising_pending_action_keeps_run_waiting_and_config_unchanged` 行为 | tmp_path 隔离工作区 + monkeypatch + 异常断言 |
| T212 | tests/test_frontend_api.py::test_invalid_revised_proposal_preserves_original_waiting_action | willy.frontend_api | 验证 `invalid_revised_proposal_preserves_original_waiting_action` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T213 | tests/test_frontend_api.py::test_pipeline_launch_receipt_does_not_repeat_the_proposed_plan | willy.frontend_api | 验证 `pipeline_launch_receipt_does_not_repeat_the_proposed_plan` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T214 | tests/test_frontend_api.py::test_run_assistant_defaults_to_the_latest_run | willy.frontend_api | 验证 `run_assistant_defaults_to_the_latest_run` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T215 | tests/test_frontend_api.py::test_run_assistant_fast_status_query_does_not_require_llm | willy.frontend_api | 验证 `run_assistant_fast_status_query_does_not_require_llm` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T216 | tests/test_frontend_api.py::test_run_assistant_pending_message_distinguishes_fact_and_explanation_paths | willy.frontend_api | 验证 `run_assistant_pending_message_distinguishes_fact_and_explanation_paths` 行为 | 进程内行为断言 |
| T217 | tests/test_frontend_api.py::test_llm_config_is_persisted_locally_without_leaking_to_ui | willy.frontend_api | 验证 `llm_config_is_persisted_locally_without_leaking_to_ui` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T218 | tests/test_frontend_api.py::test_llm_config_rejects_blank_or_multiline_values | willy.frontend_api | 验证 `llm_config_rejects_blank_or_multiline_values` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T219 | tests/test_frontend_api.py::test_llm_config_notice_shows_the_loaded_model_or_a_safe_fallback | willy.frontend_api | 验证 `llm_config_notice_shows_the_loaded_model_or_a_safe_fallback` 行为 | monkeypatch |
| T220 | tests/test_frontend_api.py::test_managed_mode_persists_only_the_mode_and_never_an_endpoint_or_registration_credential | willy.frontend_api | 验证 `managed_mode_persists_only_the_mode_and_never_an_endpoint_or_registration_credential` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T221 | tests/test_frontend_api.py::test_selecting_managed_mode_does_not_create_device_identity_until_ui_registration | willy.frontend_api | 验证 `selecting_managed_mode_does_not_create_device_identity_until_ui_registration` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T222 | tests/test_frontend_api.py::test_managed_status_and_notice_are_redacted_when_profile_is_unavailable | willy.frontend_api | 验证 `managed_status_and_notice_are_redacted_when_profile_is_unavailable` 行为 | monkeypatch |
| T223 | tests/test_frontend_api.py::test_managed_connection_check_uses_the_device_provider_and_hides_protocol_details | willy.frontend_api | 验证 `managed_connection_check_uses_the_device_provider_and_hides_protocol_details` 行为 | monkeypatch |
| T224 | tests/test_frontend_api.py::test_llm_connection_uses_transient_form_values_and_forces_a_tool_call | willy.frontend_api | 验证 `llm_connection_uses_transient_form_values_and_forces_a_tool_call` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T225 | tests/test_frontend_api.py::test_llm_connection_does_not_append_v1_to_the_entered_url | willy.frontend_api | 验证 `llm_connection_does_not_append_v1_to_the_entered_url` 行为 | monkeypatch |
| T226 | tests/test_frontend_api.py::test_llm_connection_rejects_invalid_url_without_constructing_a_client | willy.frontend_api | 验证 `llm_connection_rejects_invalid_url_without_constructing_a_client` 行为 | monkeypatch |
| T227 | tests/test_frontend_api.py::test_llm_connection_explains_missing_transient_key_and_v1_paths | willy.frontend_api | 验证 `llm_connection_explains_missing_transient_key_and_v1_paths` 行为 | monkeypatch |
| T228 | tests/test_frontend_api.py::test_llm_connection_maps_network_and_http_failures_to_public_results[html_response] | willy.frontend_api | 验证 `llm_connection_maps_network_and_http_failures_to_public_results` 行为；参数集 `html_response` | pytest 参数化 + monkeypatch |
| T229 | tests/test_frontend_api.py::test_llm_connection_maps_network_and_http_failures_to_public_results[status_401] | willy.frontend_api | 验证 `llm_connection_maps_network_and_http_failures_to_public_results` 行为；参数集 `status_401` | pytest 参数化 + monkeypatch |
| T230 | tests/test_frontend_api.py::test_llm_connection_maps_network_and_http_failures_to_public_results[status_403] | willy.frontend_api | 验证 `llm_connection_maps_network_and_http_failures_to_public_results` 行为；参数集 `status_403` | pytest 参数化 + monkeypatch |
| T231 | tests/test_frontend_api.py::test_llm_connection_maps_network_and_http_failures_to_public_results[status_404] | willy.frontend_api | 验证 `llm_connection_maps_network_and_http_failures_to_public_results` 行为；参数集 `status_404` | pytest 参数化 + monkeypatch |
| T232 | tests/test_frontend_api.py::test_llm_connection_maps_network_and_http_failures_to_public_results[status_429] | willy.frontend_api | 验证 `llm_connection_maps_network_and_http_failures_to_public_results` 行为；参数集 `status_429` | pytest 参数化 + monkeypatch |
| T233 | tests/test_frontend_api.py::test_llm_connection_maps_network_and_http_failures_to_public_results[timeout] | willy.frontend_api | 验证 `llm_connection_maps_network_and_http_failures_to_public_results` 行为；参数集 `timeout` | pytest 参数化 + monkeypatch |
| T234 | tests/test_frontend_api.py::test_llm_connection_maps_network_and_http_failures_to_public_results[network_failure] | willy.frontend_api | 验证 `llm_connection_maps_network_and_http_failures_to_public_results` 行为；参数集 `network_failure` | pytest 参数化 + monkeypatch |
| T235 | tests/test_frontend_api.py::test_llm_connection_maps_missing_choices_to_protocol_error | willy.frontend_api | 验证 `llm_connection_maps_missing_choices_to_protocol_error` 行为 | monkeypatch |
| T236 | tests/test_frontend_api.py::test_llm_connection_maps_missing_tool_call_and_hides_exception_details | willy.frontend_api | 验证 `llm_connection_maps_missing_tool_call_and_hides_exception_details` 行为 | monkeypatch |
| T237 | tests/test_frontend_api.py::test_real_llm_connection_is_explicitly_opt_in | willy.frontend_api | 验证：Exercise the configured endpoint only under --run-llm-connection. | 进程内行为断言 |
| T238 | tests/test_frontend_api.py::test_visualization_scans_only_pdb_and_mol2_from_current_run_directory | willy.frontend_api | 验证 `visualization_scans_only_pdb_and_mol2_from_current_run_directory` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T239 | tests/test_frontend_api.py::test_visualization_prefers_active_lock_then_newest_numbered_run_directory | willy.frontend_api | 验证 `visualization_prefers_active_lock_then_newest_numbered_run_directory` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T240 | tests/test_frontend_api.py::test_visualization_run_and_file_choices_are_scoped_to_the_selected_run | willy.frontend_api | 验证 `visualization_run_and_file_choices_are_scoped_to_the_selected_run` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T241 | tests/test_frontend_api.py::test_visualization_style_uses_balanced_defaults_and_clamps_user_values | willy.frontend_api | 验证 `visualization_style_uses_balanced_defaults_and_clamps_user_values` 行为 | 进程内行为断言 |
| T242 | tests/test_frontend_api.py::test_visualization_legend_lists_only_elements_present_in_pdb_and_mol2 | willy.frontend_api | 验证 `visualization_legend_lists_only_elements_present_in_pdb_and_mol2` 行为 | 进程内行为断言 |
| T243 | tests/test_frontend_api.py::test_run_assistant_status_renders_only_public_chinese_activity | willy.frontend_api | 验证 `run_assistant_status_renders_only_public_chinese_activity` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T244 | tests/test_frontend_api.py::test_run_summary_renders_local_mdrun_heartbeat_without_inventing_eta | willy.frontend_api | 验证 `run_summary_renders_local_mdrun_heartbeat_without_inventing_eta` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T245 | tests/test_frontend_api.py::test_run_summary_uses_compact_current_stage_eta_label | willy.frontend_api | 验证 `run_summary_uses_compact_current_stage_eta_label` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T246 | tests/test_frontend_api.py::test_run_assistant_status_animates_completion_marker | willy.frontend_api | 验证 `run_assistant_status_animates_completion_marker` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T247 | tests/test_frontend_api.py::test_run_assistant_status_shows_public_repair_attempt_and_adjustments | willy.frontend_api | 验证 `run_assistant_status_shows_public_repair_attempt_and_adjustments` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T248 | tests/test_frontend_api.py::test_run_assistant_status_uses_only_public_error_summary | willy.frontend_api | 验证 `run_assistant_status_uses_only_public_error_summary` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T249 | tests/test_frontend_api.py::test_run_assistant_status_ignores_an_orphan_root_status | willy.frontend_api | 验证 `run_assistant_status_ignores_an_orphan_root_status` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T250 | tests/test_frontend_api.py::test_execution_profile_snapshot_degrades_without_remote_registry | willy.frontend_api | 验证 `execution_profile_snapshot_degrades_without_remote_registry` 行为 | monkeypatch |
| T251 | tests/test_frontend_api.py::test_execution_profile_snapshot_redacts_remote_registry_fields | willy.frontend_api | 验证 `execution_profile_snapshot_redacts_remote_registry_fields` 行为 | monkeypatch |
| T252 | tests/test_frontend_api.py::test_execution_profile_snapshot_adapts_initial_capability_map | willy.frontend_api | 验证 `execution_profile_snapshot_adapts_initial_capability_map` 行为 | monkeypatch |
| T253 | tests/test_frontend_api.py::test_execution_profile_context_is_non_persistent_and_selection_scoped | willy.frontend_api | 验证 `execution_profile_context_is_non_persistent_and_selection_scoped` 行为 | monkeypatch |

## B. 环境与基础错误模型

依赖发现、环境变量优先级、vendor 完整性/发布证据、能力报告脱敏与结构化错误协议。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T069 | tests/test_env_checker.py::TestDepResult::test_construction | willy.env_checker / TestDepResult | 验证 `construction` 行为 | 进程内行为断言 |
| T070 | tests/test_env_checker.py::TestDepResult::test_needed_by_is_list | willy.env_checker / TestDepResult | 验证：needed_by 应为字符串列表 | 进程内行为断言 |
| T071 | tests/test_env_checker.py::TestDepResult::test_default_status_is_ok | willy.env_checker / TestDepResult | 验证 `default_status_is_ok` 行为 | 进程内行为断言 |
| T072 | tests/test_env_checker.py::TestDepResult::test_status_values | willy.env_checker / TestDepResult | 验证 `status_values` 行为 | 进程内行为断言 |
| T073 | tests/test_env_checker.py::TestEnvReport::test_empty_report | willy.env_checker / TestEnvReport | 验证 `empty_report` 行为 | 进程内行为断言 |
| T074 | tests/test_env_checker.py::TestEnvReport::test_all_ok | willy.env_checker / TestEnvReport | 验证 `all_ok` 行为 | 进程内行为断言 |
| T075 | tests/test_env_checker.py::TestEnvReport::test_some_missing | willy.env_checker / TestEnvReport | 验证 `some_missing` 行为 | 进程内行为断言 |
| T076 | tests/test_env_checker.py::TestEnvReport::test_failed_strs_format | willy.env_checker / TestEnvReport | 验证：failed_strs 应为人类可读的字符串 | 进程内行为断言 |
| T077 | tests/test_env_checker.py::TestEnvReport::test_is_ok_with_module_filter | willy.env_checker / TestEnvReport | 验证：is_ok 应按模块过滤 | 进程内行为断言 |
| T078 | tests/test_env_checker.py::TestEnvReport::test_format_method | willy.env_checker / TestEnvReport | 验证：format() 应返回人类可读的表格 | 进程内行为断言 |
| T079 | tests/test_env_checker.py::TestEnvReport::test_failed_strs_handles_no_exec | willy.env_checker / TestEnvReport | 验证：failed_strs 应处理 no_exec 状态 | 进程内行为断言 |
| T080 | tests/test_env_checker.py::TestDependenciesDefinition::test_dependencies_defined | willy.env_checker / TestDependenciesDefinition | 验证 `dependencies_defined` 行为 | 进程内行为断言 |
| T081 | tests/test_env_checker.py::TestDependenciesDefinition::test_dependency_names_unique | willy.env_checker / TestDependenciesDefinition | 验证 `dependency_names_unique` 行为 | 进程内行为断言 |
| T082 | tests/test_env_checker.py::TestDependenciesDefinition::test_dependencies_are_depresults | willy.env_checker / TestDependenciesDefinition | 验证：_DEPENDENCIES 中的每个条目都应是 DepResult 实例 | 进程内行为断言 |
| T083 | tests/test_env_checker.py::TestDependenciesDefinition::test_dependencies_have_kinds | willy.env_checker / TestDependenciesDefinition | 验证 `dependencies_have_kinds` 行为 | 进程内行为断言 |
| T084 | tests/test_env_checker.py::TestDependenciesDefinition::test_dependencies_grouped_by_module | willy.env_checker / TestDependenciesDefinition | 验证：依赖项应按实际执行步骤的规范名称分组 | 进程内行为断言 |
| T085 | tests/test_env_checker.py::TestDependenciesDefinition::test_dependency_ownership_is_derived_from_execution_registry | willy.env_checker / TestDependenciesDefinition | 验证 `dependency_ownership_is_derived_from_execution_registry` 行为 | 进程内行为断言 |
| T086 | tests/test_env_checker.py::TestDependenciesDefinition::test_envvar_dependencies_exist | willy.env_checker / TestDependenciesDefinition | 验证：BOSSdir 作为过渡期兼容变量保留在依赖报告中 | 进程内行为断言 |
| T087 | tests/test_env_checker.py::TestCheckAll::test_check_all_returns_env_report | willy.env_checker / TestCheckAll | 验证：check_all 应返回 EnvReport —— 不会崩溃 | 进程内行为断言 |
| T088 | tests/test_env_checker.py::TestCheckAll::test_check_all_has_results | willy.env_checker / TestCheckAll | 验证 `check_all_has_results` 行为 | 进程内行为断言 |
| T089 | tests/test_env_checker.py::TestCheckAll::test_check_all_result_statuses_valid | willy.env_checker / TestCheckAll | 验证：每个结果的状态应为有效值 | 进程内行为断言 |
| T090 | tests/test_env_checker.py::TestCheckModule::test_check_known_module | willy.env_checker / TestCheckModule | 验证：已知模块应返回 EnvReport | 进程内行为断言 |
| T091 | tests/test_env_checker.py::TestCheckModule::test_check_unknown_module | willy.env_checker / TestCheckModule | 验证：未知模块应返回空报告 | 进程内行为断言 |
| T092 | tests/test_env_checker.py::TestCheckModule::test_all_modules_work | willy.env_checker / TestCheckModule | 验证：所有 referenced 模块应可检查且不崩溃 | 进程内行为断言 |
| T093 | tests/test_env_checker.py::TestCheckModule::test_boss_default_does_not_mutate_process_environment | willy.env_checker / TestCheckModule | 验证 `boss_default_does_not_mutate_process_environment` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T094 | tests/test_env_checker.py::TestCheckModule::test_gaussian_helper_uses_registered_step_name | willy.env_checker / TestCheckModule | 验证：兼容 helper 也必须查询 env_checker 中真实注册的步骤名 | mock/patch |
| T095 | tests/test_env_checker.py::TestCheckModule::test_g09_gaussian_helper_uses_its_registered_step_name | willy.env_checker / TestCheckModule | 验证 `g09_gaussian_helper_uses_its_registered_step_name` 行为 | mock/patch |
| T096 | tests/test_env_checker.py::TestCheckModule::test_g09_modules_require_only_g09_tools | willy.env_checker / TestCheckModule | 验证 `g09_modules_require_only_g09_tools` 行为 | 进程内行为断言 |
| T097 | tests/test_env_checker.py::TestEnsure::test_ensure_passes_when_all_ok | willy.env_checker / TestEnsure | 验证：当所有依赖就绪时，ensure 不应抛出异常 | mock/patch |
| T098 | tests/test_env_checker.py::TestEnsure::test_ensure_raises_runtime_error_when_missing | willy.env_checker / TestEnsure | 验证：当依赖缺失时，ensure 应抛出 RuntimeError | mock/patch + 异常断言 |
| T099 | tests/test_env_checker.py::TestEnvCheckerEdgeCases::test_envvar_dependency_handling | willy.env_checker / TestEnvCheckerEdgeCases | 验证：旧 BOSSdir 显示名仍用于兼容既有预检界面 | 进程内行为断言 |
| T100 | tests/test_env_checker.py::TestEnvCheckerEdgeCases::test_file_exec_dependency_handling | willy.env_checker / TestEnvCheckerEdgeCases | 验证：file_exec 类型的依赖应存在 | 进程内行为断言 |
| T101 | tests/test_env_checker.py::TestEnvCheckerEdgeCases::test_vendor_paths_exist_in_dependencies | willy.env_checker / TestEnvCheckerEdgeCases | 验证：vendor/ 路径应存在于某些依赖中 | 进程内行为断言 |
| T102 | tests/test_env_checker.py::TestEnvCheckerEdgeCases::test_check_all_does_not_crash | willy.env_checker / TestEnvCheckerEdgeCases | 验证：check_all 即使依赖不可用也不应引发异常 | 进程内行为断言 |
| T103 | tests/test_env_registry.py::test_standard_binary_override_precedes_path | willy.env_registry | 验证 `standard_binary_override_precedes_path` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T104 | tests/test_env_registry.py::test_dotenv_standard_override_is_loaded_from_project_root | willy.env_registry | 验证 `dotenv_standard_override_is_loaded_from_project_root` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T105 | tests/test_env_registry.py::test_dotenv_value_uses_process_environment_before_dotenv | willy.env_registry | 验证 `dotenv_value_uses_process_environment_before_dotenv` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T106 | tests/test_env_registry.py::test_invalid_explicit_binary_is_configuration_error | willy.env_registry | 验证 `invalid_explicit_binary_is_configuration_error` 行为 | monkeypatch |
| T107 | tests/test_env_registry.py::test_project_root_never_falls_back_to_current_working_directory | willy.env_registry | 验证 `project_root_never_falls_back_to_current_working_directory` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T108 | tests/test_env_registry.py::test_orca_home_resolves_companions_and_child_library_path | willy.env_registry | 验证 `orca_home_resolves_companions_and_child_library_path` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T109 | tests/test_env_registry.py::test_g09_uses_its_own_configured_executables_and_gaussian_environment | willy.env_registry | 验证 `g09_uses_its_own_configured_executables_and_gaussian_environment` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T110 | tests/test_env_registry.py::test_ligpargen_child_gets_bossdir_without_global_mutation | willy.env_registry | 验证 `ligpargen_child_gets_bossdir_without_global_mutation` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T111 | tests/test_env_registry.py::test_openbabel_uses_standard_binary_override | willy.env_registry | 验证 `openbabel_uses_standard_binary_override` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T112 | tests/test_env_registry.py::test_multiwfn_uses_bundled_binary_and_ignores_external_configuration | willy.env_registry | 验证 `multiwfn_uses_bundled_binary_and_ignores_external_configuration` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T113 | tests/test_env_registry.py::test_boss_loader_failure_is_runtime_unavailable | willy.env_registry | 验证 `boss_loader_failure_is_runtime_unavailable` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T114 | tests/test_env_registry.py::test_boss_signal_termination_is_runtime_unavailable | willy.env_registry | 验证 `boss_signal_termination_is_runtime_unavailable` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T115 | tests/test_env_registry.py::test_bundled_packmol_loader_failure_is_runtime_unavailable | willy.env_registry | 验证 `bundled_packmol_loader_failure_is_runtime_unavailable` 行为 | tmp_path 隔离工作区 |
| T116 | tests/test_env_registry.py::test_bundled_packmol_usage_exit_still_proves_runtime_started | willy.env_registry | 验证 `bundled_packmol_usage_exit_still_proves_runtime_started` 行为 | tmp_path 隔离工作区 |
| T117 | tests/test_env_registry.py::test_capability_report_is_redacted_and_run_local | willy.env_registry | 验证 `capability_report_is_redacted_and_run_local` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T118 | tests/test_errors.py::TestErrorKind::test_all_kinds_have_unique_values | willy.errors / TestErrorKind | 验证：所有 ErrorKind 成员的值必须唯一 | 进程内行为断言 |
| T119 | tests/test_errors.py::TestErrorKind::test_retryable_property_exists_for_all | willy.errors / TestErrorKind | 验证：每个 ErrorKind 都应有 retryable 属性 | 进程内行为断言 |
| T120 | tests/test_errors.py::TestErrorKind::test_rollback_required_property_exists_for_all | willy.errors / TestErrorKind | 验证：每个 ErrorKind 都应有 rollback_required 属性 | 进程内行为断言 |
| T121 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.SCF_NOT_CONVERGED] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.SCF_NOT_CONVERGED` | pytest 参数化 |
| T122 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.GEOM_NOT_CONVERGED] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.GEOM_NOT_CONVERGED` | pytest 参数化 |
| T123 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.GAUSSIAN_CRASH] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.GAUSSIAN_CRASH` | pytest 参数化 |
| T124 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.ORCA_CRASH] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.ORCA_CRASH` | pytest 参数化 |
| T125 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.FORMCHK_FAILED] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.FORMCHK_FAILED` | pytest 参数化 |
| T126 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.GROMPP_FAILED] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.GROMPP_FAILED` | pytest 参数化 |
| T127 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.MDRUN_FAILED] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.MDRUN_FAILED` | pytest 参数化 |
| T128 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.PACKMOL_FAILED] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.PACKMOL_FAILED` | pytest 参数化 |
| T129 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.EM_NOT_CONVERGED] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.EM_NOT_CONVERGED` | pytest 参数化 |
| T130 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.EQ_NOT_CONVERGED] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.EQ_NOT_CONVERGED` | pytest 参数化 |
| T131 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.TIMEOUT] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.TIMEOUT` | pytest 参数化 |
| T132 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.SOBTOP_FAILED] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.SOBTOP_FAILED` | pytest 参数化 |
| T133 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.LIGPARGEN_FAILED] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.LIGPARGEN_FAILED` | pytest 参数化 |
| T134 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.UNKNOWN] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.UNKNOWN` | pytest 参数化 |
| T135 | tests/test_errors.py::TestErrorKind::test_non_retryable_errors[ErrorKind.DEPENDENCY_MISSING] | willy.errors / TestErrorKind | 验证：不可自动修复的错误应返回 retryable=False；参数集 `ErrorKind.DEPENDENCY_MISSING` | pytest 参数化 |
| T136 | tests/test_errors.py::TestErrorKind::test_non_retryable_errors[ErrorKind.DEPENDENCY_NO_EXEC] | willy.errors / TestErrorKind | 验证：不可自动修复的错误应返回 retryable=False；参数集 `ErrorKind.DEPENDENCY_NO_EXEC` | pytest 参数化 |
| T137 | tests/test_errors.py::TestErrorKind::test_non_retryable_errors[ErrorKind.RESP_FAILED] | willy.errors / TestErrorKind | 验证：不可自动修复的错误应返回 retryable=False；参数集 `ErrorKind.RESP_FAILED` | pytest 参数化 |
| T138 | tests/test_errors.py::TestErrorKind::test_non_retryable_errors[ErrorKind.FILE_NOT_FOUND] | willy.errors / TestErrorKind | 验证：不可自动修复的错误应返回 retryable=False；参数集 `ErrorKind.FILE_NOT_FOUND` | pytest 参数化 |
| T139 | tests/test_errors.py::TestErrorKind::test_non_retryable_errors[ErrorKind.CONFIG_INVALID] | willy.errors / TestErrorKind | 验证：不可自动修复的错误应返回 retryable=False；参数集 `ErrorKind.CONFIG_INVALID` | pytest 参数化 |
| T140 | tests/test_errors.py::TestErrorKind::test_rollback_required_errors[ErrorKind.GROMPP_FAILED] | willy.errors / TestErrorKind | 验证：回滚类错误应返回 rollback_required=True；参数集 `ErrorKind.GROMPP_FAILED` | pytest 参数化 |
| T141 | tests/test_errors.py::TestErrorKind::test_rollback_required_errors[ErrorKind.MDRUN_FAILED] | willy.errors / TestErrorKind | 验证：回滚类错误应返回 rollback_required=True；参数集 `ErrorKind.MDRUN_FAILED` | pytest 参数化 |
| T142 | tests/test_errors.py::TestErrorKind::test_rollback_required_errors[ErrorKind.FORMCHK_FAILED] | willy.errors / TestErrorKind | 验证：回滚类错误应返回 rollback_required=True；参数集 `ErrorKind.FORMCHK_FAILED` | pytest 参数化 |
| T143 | tests/test_errors.py::TestErrorKind::test_rollback_required_errors[ErrorKind.SOBTOP_FAILED] | willy.errors / TestErrorKind | 验证：回滚类错误应返回 rollback_required=True；参数集 `ErrorKind.SOBTOP_FAILED` | pytest 参数化 |
| T144 | tests/test_errors.py::TestErrorKind::test_rollback_required_errors[ErrorKind.LIGPARGEN_FAILED] | willy.errors / TestErrorKind | 验证：回滚类错误应返回 rollback_required=True；参数集 `ErrorKind.LIGPARGEN_FAILED` | pytest 参数化 |
| T145 | tests/test_errors.py::TestErrorKind::test_rollback_required_errors[ErrorKind.ORCA_CRASH] | willy.errors / TestErrorKind | 验证：回滚类错误应返回 rollback_required=True；参数集 `ErrorKind.ORCA_CRASH` | pytest 参数化 |
| T146 | tests/test_errors.py::TestErrorKind::test_rollback_required_errors[ErrorKind.GAUSSIAN_CRASH] | willy.errors / TestErrorKind | 验证：回滚类错误应返回 rollback_required=True；参数集 `ErrorKind.GAUSSIAN_CRASH` | pytest 参数化 |
| T147 | tests/test_errors.py::TestErrorKind::test_no_rollback_errors[ErrorKind.SCF_NOT_CONVERGED] | willy.errors / TestErrorKind | 验证：非回滚类错误应返回 rollback_required=False；参数集 `ErrorKind.SCF_NOT_CONVERGED` | pytest 参数化 |
| T148 | tests/test_errors.py::TestErrorKind::test_no_rollback_errors[ErrorKind.GEOM_NOT_CONVERGED] | willy.errors / TestErrorKind | 验证：非回滚类错误应返回 rollback_required=False；参数集 `ErrorKind.GEOM_NOT_CONVERGED` | pytest 参数化 |
| T149 | tests/test_errors.py::TestErrorKind::test_no_rollback_errors[ErrorKind.RESP_FAILED] | willy.errors / TestErrorKind | 验证：非回滚类错误应返回 rollback_required=False；参数集 `ErrorKind.RESP_FAILED` | pytest 参数化 |
| T150 | tests/test_errors.py::TestErrorKind::test_no_rollback_errors[ErrorKind.EM_NOT_CONVERGED] | willy.errors / TestErrorKind | 验证：非回滚类错误应返回 rollback_required=False；参数集 `ErrorKind.EM_NOT_CONVERGED` | pytest 参数化 |
| T151 | tests/test_errors.py::TestErrorKind::test_no_rollback_errors[ErrorKind.EQ_NOT_CONVERGED] | willy.errors / TestErrorKind | 验证：非回滚类错误应返回 rollback_required=False；参数集 `ErrorKind.EQ_NOT_CONVERGED` | pytest 参数化 |
| T152 | tests/test_errors.py::TestErrorKind::test_no_rollback_errors[ErrorKind.TIMEOUT] | willy.errors / TestErrorKind | 验证：非回滚类错误应返回 rollback_required=False；参数集 `ErrorKind.TIMEOUT` | pytest 参数化 |
| T153 | tests/test_errors.py::TestErrorKind::test_retryable_set_matches_all_retryable | willy.errors / TestErrorKind | 验证：_RETRYABLE 集合中不应有未知成员 | 进程内行为断言 |
| T154 | tests/test_errors.py::TestErrorKind::test_rollback_set_matches_all_rollback | willy.errors / TestErrorKind | 验证：_ROLLBACK_REQUIRED 集合中不应有未知成员 | 进程内行为断言 |
| T155 | tests/test_errors.py::TestErrorKind::test_unused_error_kinds | willy.errors / TestErrorKind | 验证：BUG: SOBTOP_EXIT_24 和 LOCK_CONFLICT 在 ErrorKind 中定义， 但在整个代码库中从未引用或抛出 | 进程内行为断言 |
| T156 | tests/test_errors.py::TestStepError::test_minimal_construction | willy.errors / TestStepError | 验证：仅 kind 必填 | 进程内行为断言 |
| T157 | tests/test_errors.py::TestStepError::test_full_construction | willy.errors / TestStepError | 验证：所有字段均应正确存储 | 进程内行为断言 |
| T158 | tests/test_errors.py::TestStepError::test_raw_output_truncation | willy.errors / TestStepError | 验证：raw_output 可接受长字符串（截断由调用者负责） | 进程内行为断言 |
| T159 | tests/test_errors.py::TestStepResult::test_success_result | willy.errors / TestStepResult | 验证 `success_result` 行为 | 进程内行为断言 |
| T160 | tests/test_errors.py::TestStepResult::test_failure_result | willy.errors / TestStepResult | 验证 `failure_result` 行为 | 进程内行为断言 |
| T161 | tests/test_errors.py::TestStepResult::test_escalated_result | willy.errors / TestStepResult | 验证：升级结果应同时设置 success=False 和 escalated=True | 进程内行为断言 |
| T162 | tests/test_errors.py::TestStepResult::test_defaults | willy.errors / TestStepResult | 验证：默认值应与文档一致 | 进程内行为断言 |
| T163 | tests/test_errors.py::TestStepResult::test_to_dict_preserves_protocol_fields_and_is_json_safe | willy.errors / TestStepResult | 验证 `to_dict_preserves_protocol_fields_and_is_json_safe` 行为 | 进程内行为断言 |
| T164 | tests/test_errors.py::TestDiagnosisResult::test_to_dict_marker | willy.errors / TestDiagnosisResult | 验证：to_dict() 必须包含 _diagnosis=True 标记 | 进程内行为断言 |
| T165 | tests/test_errors.py::TestDiagnosisResult::test_default_severity | willy.errors / TestDiagnosisResult | 验证：默认严重级别应为 'info' | 进程内行为断言 |
| T166 | tests/test_errors.py::TestDiagnosisResult::test_all_severity_levels | willy.errors / TestDiagnosisResult | 验证：所有严重级别均应支持 | 进程内行为断言 |
| T167 | tests/test_errors.py::TestDiagnosisResult::test_extra_fields_preserved | willy.errors / TestDiagnosisResult | 验证：extra 字段应在 to_dict() 中完整保留 | 进程内行为断言 |
| T168 | tests/test_errors.py::TestDiagnosisResult::test_empty_issues_and_evidence | willy.errors / TestDiagnosisResult | 验证：issues 和 evidence 默认为空列表 | 进程内行为断言 |
| T169 | tests/test_errors.py::TestRetryContext::test_not_exhausted_initially | willy.errors / TestRetryContext | 验证 `not_exhausted_initially` 行为 | 进程内行为断言 |
| T170 | tests/test_errors.py::TestRetryContext::test_exhausted_when_attempts_equal_max | willy.errors / TestRetryContext | 验证 `exhausted_when_attempts_equal_max` 行为 | 进程内行为断言 |
| T171 | tests/test_errors.py::TestRetryContext::test_exhausted_when_attempts_exceed_max | willy.errors / TestRetryContext | 验证：即使 attempts > max_attempts 也应返回 True | 进程内行为断言 |
| T172 | tests/test_errors.py::TestRetryContext::test_actions_tried_accumulation | willy.errors / TestRetryContext | 验证 `actions_tried_accumulation` 行为 | 进程内行为断言 |
| T173 | tests/test_errors.py::TestRetryContext::test_max_attempts_default | willy.errors / TestRetryContext | 验证：默认 max_attempts 应为 3 | 进程内行为断言 |
| T174 | tests/test_errors.py::TestRetryContext::test_last_raw_output_default | willy.errors / TestRetryContext | 验证 `last_raw_output_default` 行为 | 进程内行为断言 |
| T175 | tests/test_errors.py::TestErrorPropagation::test_step_error_from_result_extraction | willy.errors / TestErrorPropagation | 验证：从 StepResult 中提取 StepError 应保持完整性 | 进程内行为断言 |
| T176 | tests/test_errors.py::TestErrorPropagation::test_chain_of_failures | willy.errors / TestErrorPropagation | 验证：多个步骤的失败链应保留各个错误 | 进程内行为断言 |
| T177 | tests/test_errors.py::TestErrorPropagation::test_error_kind_determines_agent_behavior | willy.errors / TestErrorPropagation | 验证：Agent 行为应基于 ErrorKind，不应解析裸字符串 | 进程内行为断言 |
| T783 | tests/test_vendor_manifest.py::test_repository_vendor_inventory_has_no_integrity_drift | willy.vendor_manifest / bundled vendor release inventory | 验证 `repository_vendor_inventory_has_no_integrity_drift` 行为 | 进程内行为断言 |
| T784 | tests/test_vendor_manifest.py::test_vendor_inventory_reports_hash_drift | willy.vendor_manifest / bundled vendor release inventory | 验证 `vendor_inventory_reports_hash_drift` 行为 | tmp_path 隔离工作区 |
| T785 | tests/test_vendor_manifest.py::test_vendor_inventory_keeps_unverified_distribution_as_release_blocker | willy.vendor_manifest / bundled vendor release inventory | 验证 `vendor_inventory_keeps_unverified_distribution_as_release_blocker` 行为 | tmp_path 隔离工作区 |

## C. 配置与全局工具

config v2、迁移、分子知识库与 Layer 0 工具 schema/handler。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T642 | tests/test_toolist_global.py::TestToolDefinitions::test_all_tools_have_required_format | willy.toolist_global / TestToolDefinitions | 验证：每个工具定义必须遵循 OpenAI function calling 格式 | 进程内行为断言 |
| T643 | tests/test_toolist_global.py::TestToolDefinitions::test_tool_names_unique | willy.toolist_global / TestToolDefinitions | 验证：工具名称必须唯一 | 进程内行为断言 |
| T644 | tests/test_toolist_global.py::TestToolDefinitions::test_tool_names_follow_convention | willy.toolist_global / TestToolDefinitions | 验证：工具名称必须遵循 tools_{action}_{target} 命名规范 | 进程内行为断言 |
| T645 | tests/test_toolist_global.py::TestToolDefinitions::test_required_tools_present | willy.toolist_global / TestToolDefinitions | 验证：所有必需的 Config Agent 工具必须存在 | 进程内行为断言 |
| T646 | tests/test_toolist_global.py::TestToolsValidateConfig::test_handler_validates_config | willy.toolist_global / TestToolsValidateConfig | 验证 `handler_validates_config` 行为 | 进程内行为断言 |
| T647 | tests/test_toolist_global.py::TestToolsValidateConfig::test_json_parse_error_handling | willy.toolist_global / TestToolsValidateConfig | 验证 `json_parse_error_handling` 行为 | 进程内行为断言 |
| T648 | tests/test_toolist_global.py::TestMoleculeRegistry::test_registry_has_entries | willy.toolist_global / TestMoleculeRegistry | 验证：注册表必须提供知识库或内置兜底分子 | 进程内行为断言 |
| T649 | tests/test_toolist_global.py::TestMoleculeRegistry::test_lookup_exact_registered_name | willy.toolist_global / TestMoleculeRegistry | 验证：精确注册的离子名必须返回其结构化条目 | 进程内行为断言 |
| T650 | tests/test_toolist_global.py::TestMoleculeRegistry::test_lookup_case_insensitive | willy.toolist_global / TestMoleculeRegistry | 验证：ASCII 分子名应不区分大小写 | 进程内行为断言 |
| T651 | tests/test_toolist_global.py::TestMoleculeRegistry::test_lookup_unknown_returns_none | willy.toolist_global / TestMoleculeRegistry | 验证：完全未知的分子应返回 None | 进程内行为断言 |
| T652 | tests/test_toolist_global.py::TestMoleculeRegistry::test_alias_lookup | willy.toolist_global / TestMoleculeRegistry | 验证：知识库中的中文别名应返回对应离子 | 进程内行为断言 |
| T653 | tests/test_toolist_global.py::TestMoleculeRegistry::test_imported_species_aliases_are_indexed[\u94a0\u79bb\u5b50-Na-1-1-1] | willy.toolist_global / TestMoleculeRegistry | 验证：导入物种的中英文/带电别名必须进入表格与 TF-IDF 索引；参数集 `\u94a0\u79bb\u5b50-Na-1-1-1` | pytest 参数化 |
| T654 | tests/test_toolist_global.py::TestMoleculeRegistry::test_imported_species_aliases_are_indexed[\u516d\u6c1f\u7837\u9178\u6839-AsF6--1-1-7] | willy.toolist_global / TestMoleculeRegistry | 验证：导入物种的中英文/带电别名必须进入表格与 TF-IDF 索引；参数集 `\u516d\u6c1f\u7837\u9178\u6839-AsF6--1-1-7` | pytest 参数化 |
| T655 | tests/test_toolist_global.py::TestMoleculeRegistry::test_imported_species_aliases_are_indexed[B(CN)4--BCN4--1-1-9] | willy.toolist_global / TestMoleculeRegistry | 验证：导入物种的中英文/带电别名必须进入表格与 TF-IDF 索引；参数集 `B(CN)4--BCN4--1-1-9` | pytest 参数化 |
| T656 | tests/test_toolist_global.py::TestMoleculeRegistry::test_imported_species_aliases_are_indexed[FTFSI--FTFSI--1-2-13] | willy.toolist_global / TestMoleculeRegistry | 验证：导入物种的中英文/带电别名必须进入表格与 TF-IDF 索引；参数集 `FTFSI--FTFSI--1-2-13` | pytest 参数化 |
| T657 | tests/test_toolist_global.py::TestMoleculeRegistry::test_imported_species_aliases_are_indexed[\u56db\u4e59\u4e8c\u9187\u4e8c\u7532\u919a-T4GM-0-1-37] | willy.toolist_global / TestMoleculeRegistry | 验证：导入物种的中英文/带电别名必须进入表格与 TF-IDF 索引；参数集 `\u56db\u4e59\u4e8c\u9187\u4e8c\u7532\u919a-T4GM-0-1-37` | pytest 参数化 |
| T658 | tests/test_toolist_global.py::TestCompoundResolution::test_known_compounds_can_be_split | willy.toolist_global / TestCompoundResolution | 验证 `known_compounds_can_be_split` 行为 | 进程内行为断言 |
| T659 | tests/test_toolist_global.py::TestCompoundResolution::test_compound_split_preserves_stoichiometry | willy.toolist_global / TestCompoundResolution | 验证 `compound_split_preserves_stoichiometry` 行为 | 进程内行为断言 |
| T660 | tests/test_toolist_global.py::TestErrorDiagnosisPresets::test_errors_dict_has_entries | willy.toolist_global / TestErrorDiagnosisPresets | 验证 `errors_dict_has_entries` 行为 | 进程内行为断言 |
| T661 | tests/test_toolist_global.py::TestErrorDiagnosisPresets::test_error_diagnosis_returns_hints | willy.toolist_global / TestErrorDiagnosisPresets | 验证：每个错误条目应至少包含一条 hint | 进程内行为断言 |
| T662 | tests/test_toolist_global.py::TestHandleToolCall::test_unknown_tool_returns_error | willy.toolist_global / TestHandleToolCall | 验证：未知工具名应返回错误 JSON | 进程内行为断言 |
| T663 | tests/test_toolist_global.py::TestHandleToolCall::test_refresh_structs | willy.toolist_global / TestHandleToolCall | 验证：tools_refresh_structs 应重新加载注册表 | monkeypatch |
| T664 | tests/test_toolist_global.py::TestHandleToolCall::test_refresh_structs_is_idempotent | willy.toolist_global / TestHandleToolCall | 验证：重复刷新不得复制表格条目或向量文档 | 进程内行为断言 |
| T665 | tests/test_toolist_global.py::TestHandleToolCall::test_get_box_density_known_system | willy.toolist_global / TestHandleToolCall | 验证：tools_get_box_density returns the mass-density default contract. | 进程内行为断言 |
| T666 | tests/test_toolist_global.py::TestHandleToolCall::test_lookup_md_defaults_for_electrolyte | willy.toolist_global / TestHandleToolCall | 验证：tools_lookup_md_defaults 对 electrolyte 返回合理默认值 | 进程内行为断言 |
| T667 | tests/test_toolist_global.py::TestHandleToolCall::test_all_md_default_presets_use_one_fs[ionic_liquid] | willy.toolist_global / TestHandleToolCall | 验证：所有 Agent 体系预设统一使用 1 fs，避免新任务回退到 2 fs；参数集 `ionic_liquid` | pytest 参数化 |
| T668 | tests/test_toolist_global.py::TestHandleToolCall::test_all_md_default_presets_use_one_fs[solvent_mix] | willy.toolist_global / TestHandleToolCall | 验证：所有 Agent 体系预设统一使用 1 fs，避免新任务回退到 2 fs；参数集 `solvent_mix` | pytest 参数化 |
| T669 | tests/test_toolist_global.py::TestHandleToolCall::test_all_md_default_presets_use_one_fs[aqueous] | willy.toolist_global / TestHandleToolCall | 验证：所有 Agent 体系预设统一使用 1 fs，避免新任务回退到 2 fs；参数集 `aqueous` | pytest 参数化 |
| T670 | tests/test_toolist_global.py::TestHandleToolCall::test_all_md_default_presets_use_one_fs[organic] | willy.toolist_global / TestHandleToolCall | 验证：所有 Agent 体系预设统一使用 1 fs，避免新任务回退到 2 fs；参数集 `organic` | pytest 参数化 |
| T671 | tests/test_toolist_global.py::TestHandleToolCall::test_set_backend_quantum | willy.toolist_global / TestHandleToolCall | 验证：tools_set_backend_quantum 应更新 config.json | monkeypatch |
| T672 | tests/test_toolist_global.py::TestHandleToolCall::test_set_g09_backend_quantum | willy.toolist_global / TestHandleToolCall | 验证 `set_g09_backend_quantum` 行为 | monkeypatch |
| T673 | tests/test_toolist_global.py::TestHandleToolCall::test_lookup_basis_set_returns_recommendation | willy.toolist_global / TestHandleToolCall | 验证：tools_lookup_basis_set 应返回基组推荐 | 进程内行为断言 |
| T786 | tests/test_workflow_config.py::test_valid_v2_config_passes | willy.workflow_config 与 MD 配置协议 | 验证 `valid_v2_config_passes` 行为 | 进程内行为断言 |
| T787 | tests/test_workflow_config.py::test_nproc_must_be_a_bounded_positive_integer[0] | willy.workflow_config 与 MD 配置协议 | 验证 `nproc_must_be_a_bounded_positive_integer` 行为；参数集 `0` | pytest 参数化 |
| T788 | tests/test_workflow_config.py::test_nproc_must_be_a_bounded_positive_integer[-1] | willy.workflow_config 与 MD 配置协议 | 验证 `nproc_must_be_a_bounded_positive_integer` 行为；参数集 `-1` | pytest 参数化 |
| T789 | tests/test_workflow_config.py::test_nproc_must_be_a_bounded_positive_integer[1.5] | willy.workflow_config 与 MD 配置协议 | 验证 `nproc_must_be_a_bounded_positive_integer` 行为；参数集 `1.5` | pytest 参数化 |
| T790 | tests/test_workflow_config.py::test_nproc_must_be_a_bounded_positive_integer[8] | willy.workflow_config 与 MD 配置协议 | 验证 `nproc_must_be_a_bounded_positive_integer` 行为；参数集 `8` | pytest 参数化 |
| T791 | tests/test_workflow_config.py::test_nproc_must_be_a_bounded_positive_integer[True] | willy.workflow_config 与 MD 配置协议 | 验证 `nproc_must_be_a_bounded_positive_integer` 行为；参数集 `True` | pytest 参数化 |
| T792 | tests/test_workflow_config.py::test_nproc_must_be_a_bounded_positive_integer[4097] | willy.workflow_config 与 MD 配置协议 | 验证 `nproc_must_be_a_bounded_positive_integer` 行为；参数集 `4097` | pytest 参数化 |
| T793 | tests/test_workflow_config.py::test_molecule_nproc_can_override_global_default | willy.workflow_config 与 MD 配置协议 | 验证 `molecule_nproc_can_override_global_default` 行为 | 进程内行为断言 |
| T794 | tests/test_workflow_config.py::test_execution_defaults_to_explicit_local_profileless_mode | willy.workflow_config 与 MD 配置协议 | 验证 `execution_defaults_to_explicit_local_profileless_mode` 行为 | 进程内行为断言 |
| T795 | tests/test_workflow_config.py::test_outer_schema_keeps_unknown_extensions_but_reports_them_structurally | willy.workflow_config 与 MD 配置协议 | 验证 `outer_schema_keeps_unknown_extensions_but_reports_them_structurally` 行为 | 进程内行为断言 |
| T796 | tests/test_workflow_config.py::test_outer_schema_rejects_malformed_nested_sections_before_defaults | willy.workflow_config 与 MD 配置协议 | 验证 `outer_schema_rejects_malformed_nested_sections_before_defaults` 行为 | 进程内行为断言 |
| T797 | tests/test_workflow_config.py::test_execution_schema_rejects_private_connection_fields | willy.workflow_config 与 MD 配置协议 | 验证 `execution_schema_rejects_private_connection_fields` 行为 | 进程内行为断言 |
| T798 | tests/test_workflow_config.py::test_execution_can_explicitly_stop_a_trial_after_eq | willy.workflow_config 与 MD 配置协议 | 验证 `execution_can_explicitly_stop_a_trial_after_eq` 行为 | 进程内行为断言 |
| T799 | tests/test_workflow_config.py::test_execution_rejects_any_stop_scope_other_than_eq[prod] | willy.workflow_config 与 MD 配置协议 | 验证 `execution_rejects_any_stop_scope_other_than_eq` 行为；参数集 `prod` | pytest 参数化 |
| T800 | tests/test_workflow_config.py::test_execution_rejects_any_stop_scope_other_than_eq[em] | willy.workflow_config 与 MD 配置协议 | 验证 `execution_rejects_any_stop_scope_other_than_eq` 行为；参数集 `em` | pytest 参数化 |
| T801 | tests/test_workflow_config.py::test_execution_rejects_any_stop_scope_other_than_eq[] | willy.workflow_config 与 MD 配置协议 | 验证 `execution_rejects_any_stop_scope_other_than_eq` 行为 | 进程内行为断言 |
| T802 | tests/test_workflow_config.py::test_execution_rejects_any_stop_scope_other_than_eq[9] | willy.workflow_config 与 MD 配置协议 | 验证 `execution_rejects_any_stop_scope_other_than_eq` 行为；参数集 `9` | pytest 参数化 |
| T803 | tests/test_workflow_config.py::test_unknown_remote_profile_is_rejected_without_affecting_local_mode | willy.workflow_config 与 MD 配置协议 | 验证 `unknown_remote_profile_is_rejected_without_affecting_local_mode` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T804 | tests/test_workflow_config.py::test_apply_config_rejects_invalid_outer_shape_without_writing | willy.workflow_config 与 MD 配置协议 | 验证 `apply_config_rejects_invalid_outer_shape_without_writing` 行为 | monkeypatch + 异常断言 |
| T805 | tests/test_workflow_config.py::test_apply_config_preserves_forward_compatible_extension | willy.workflow_config 与 MD 配置协议 | 验证 `apply_config_preserves_forward_compatible_extension` 行为 | monkeypatch |
| T806 | tests/test_workflow_config.py::test_malformed_config_returns_displayable_issues[None-config \u5fc5\u987b\u662f\u5bf9\u8c61] | willy.workflow_config 与 MD 配置协议 | 验证 `malformed_config_returns_displayable_issues` 行为；参数集 `None-config \u5fc5\u987b\u662f\u5bf9\u8c61` | pytest 参数化 |
| T807 | tests/test_workflow_config.py::test_malformed_config_returns_displayable_issues[config1-residues \u5fc5\u987b\u662f\u5bf9\u8c61] | willy.workflow_config 与 MD 配置协议 | 验证 `malformed_config_returns_displayable_issues` 行为；参数集 `config1-residues \u5fc5\u987b\u662f\u5bf9\u8c61` | pytest 参数化 |
| T808 | tests/test_workflow_config.py::test_malformed_config_returns_displayable_issues[config2-residues.A \u5fc5\u987b\u662f\u6570\u503c] | willy.workflow_config 与 MD 配置协议 | 验证 `malformed_config_returns_displayable_issues` 行为；参数集 `config2-residues.A \u5fc5\u987b\u662f\u6570\u503c` | pytest 参数化 |
| T809 | tests/test_workflow_config.py::test_malformed_config_returns_displayable_issues[config3-molecules \u5fc5\u987b\u662f\u5bf9\u8c61] | willy.workflow_config 与 MD 配置协议 | 验证 `malformed_config_returns_displayable_issues` 行为；参数集 `config3-molecules \u5fc5\u987b\u662f\u5bf9\u8c61` | pytest 参数化 |
| T810 | tests/test_workflow_config.py::test_legacy_durations_are_rejected_without_migration | willy.workflow_config 与 MD 配置协议 | 验证 `legacy_durations_are_rejected_without_migration` 行为 | 进程内行为断言 |
| T811 | tests/test_workflow_config.py::test_non_neutral_system_requires_confirmation | willy.workflow_config 与 MD 配置协议 | 验证 `non_neutral_system_requires_confirmation` 行为 | 进程内行为断言 |
| T812 | tests/test_workflow_config.py::test_invalid_eq_and_prod_boundaries_are_reported | willy.workflow_config 与 MD 配置协议 | 验证 `invalid_eq_and_prod_boundaries_are_reported` 行为 | 进程内行为断言 |
| T813 | tests/test_workflow_config.py::test_prod_temperature_must_match_eq_target | willy.workflow_config 与 MD 配置协议 | 验证 `prod_temperature_must_match_eq_target` 行为 | 进程内行为断言 |
| T814 | tests/test_workflow_config.py::test_special_system_rejects_isotropic_pressure_coupling | willy.workflow_config 与 MD 配置协议 | 验证 `special_system_rejects_isotropic_pressure_coupling` 行为 | 进程内行为断言 |
| T815 | tests/test_workflow_config.py::test_defaults_create_schema_v2_without_legacy_fields | willy.workflow_config 与 MD 配置协议 | 验证 `defaults_create_schema_v2_without_legacy_fields` 行为 | 进程内行为断言 |
| T816 | tests/test_workflow_config.py::test_defaults_preserve_existing_number_density_snapshot | willy.workflow_config 与 MD 配置协议 | 验证 `defaults_preserve_existing_number_density_snapshot` 行为 | 进程内行为断言 |
| T817 | tests/test_workflow_config.py::test_defaults_preserve_an_explicit_trr_output_request | willy.workflow_config 与 MD 配置协议 | 验证 `defaults_preserve_an_explicit_trr_output_request` 行为 | 进程内行为断言 |
| T818 | tests/test_workflow_config.py::test_defaults_preserve_an_explicit_oplsaa_force_field_request | willy.workflow_config 与 MD 配置协议 | 验证 `defaults_preserve_an_explicit_oplsaa_force_field_request` 行为 | 进程内行为断言 |
| T819 | tests/test_workflow_config.py::test_config_prompt_describes_explicit_trr_and_oplsaa_requests | willy.workflow_config 与 MD 配置协议 | 验证 `config_prompt_describes_explicit_trr_and_oplsaa_requests` 行为 | 进程内行为断言 |
| T820 | tests/test_workflow_config.py::test_defaults_preserve_legacy_fields_for_visible_rejection | willy.workflow_config 与 MD 配置协议 | 验证 `defaults_preserve_legacy_fields_for_visible_rejection` 行为 | 进程内行为断言 |
| T821 | tests/test_workflow_config.py::test_explicit_migration_writes_v2_and_mapping_without_overwriting_source | willy.workflow_config 与 MD 配置协议 | 验证 `explicit_migration_writes_v2_and_mapping_without_overwriting_source` 行为 | tmp_path 隔离工作区 |
| T822 | tests/test_workflow_config.py::test_confirmed_migration_adoption_replaces_active_config_with_valid_protocol | willy.workflow_config 与 MD 配置协议 | 验证 `confirmed_migration_adoption_replaces_active_config_with_valid_protocol` 行为 | tmp_path 隔离工作区 |
| T823 | tests/test_workflow_config.py::test_annealing_temperatures_must_descend_strictly | willy.workflow_config 与 MD 配置协议 | 验证 `annealing_temperatures_must_descend_strictly` 行为 | 进程内行为断言 |
| T824 | tests/test_workflow_config.py::test_apply_config_writes_v2_snapshot | willy.workflow_config 与 MD 配置协议 | 验证 `apply_config_writes_v2_snapshot` 行为 | monkeypatch |
| T825 | tests/test_workflow_config.py::test_apply_config_replaces_active_file_only_after_atomic_backup | willy.workflow_config 与 MD 配置协议 | 验证 `apply_config_replaces_active_file_only_after_atomic_backup` 行为 | monkeypatch |

## D. Agent 与 LLM 行为

OpenAI-compatible 配置、LayerAgent 的上下文、受控动作、预算、重试、升级，以及 18 个离线 mock LLM 场景。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T002 | tests/test_action_contract.py::test_default_catalog_covers_every_schema_and_declares_effects | willy.action_contract | 验证 `default_catalog_covers_every_schema_and_declares_effects` 行为 | 进程内行为断言 |
| T003 | tests/test_action_contract.py::test_proposal_is_json_safe_and_nested_arguments_are_immutable | willy.action_contract | 验证 `proposal_is_json_safe_and_nested_arguments_are_immutable` 行为 | 异常断言 |
| T004 | tests/test_action_contract.py::test_validation_requires_matching_enabled_declaration | willy.action_contract | 验证 `validation_requires_matching_enabled_declaration` 行为 | 异常断言 |
| T005 | tests/test_action_contract.py::test_executed_action_round_trip_does_not_accept_raw_output | willy.action_contract | 验证 `executed_action_round_trip_does_not_accept_raw_output` 行为 | 进程内行为断言 |
| T006 | tests/test_action_contract.py::test_read_only_declaration_cannot_claim_mutation | willy.action_contract | 验证 `read_only_declaration_cannot_claim_mutation` 行为 | 异常断言 |
| T007 | tests/test_action_contract.py::test_parameter_effect_raises_the_effect_of_a_specific_call | willy.action_contract | 验证 `parameter_effect_raises_the_effect_of_a_specific_call` 行为 | 进程内行为断言 |
| T008 | tests/test_agent_config.py::test_text_confirmation_starts_the_pending_plan_without_an_llm | willy.agent_config | 验证 `text_confirmation_starts_the_pending_plan_without_an_llm` 行为 | monkeypatch |
| T009 | tests/test_agent_config.py::test_text_confirmation_without_a_pending_plan_does_not_start | willy.agent_config | 验证 `text_confirmation_without_a_pending_plan_does_not_start` 行为 | monkeypatch |
| T010 | tests/test_agent_config.py::test_start_pipeline_rejects_validation_issues_before_reserving_a_run | willy.agent_config | 验证 `start_pipeline_rejects_validation_issues_before_reserving_a_run` 行为 | monkeypatch |
| T011 | tests/test_agent_config.py::test_new_failed_request_clears_an_older_pending_plan | willy.agent_config | 验证 `new_failed_request_clears_an_older_pending_plan` 行为 | monkeypatch |
| T012 | tests/test_agent_config.py::test_pending_plan_revision_sends_frozen_context_and_replaces_plan | willy.agent_config | 验证 `pending_plan_revision_sends_frozen_context_and_replaces_plan` 行为 | monkeypatch |
| T013 | tests/test_agent_config.py::test_pending_plan_question_keeps_plan_and_sends_context_to_llm | willy.agent_config | 验证 `pending_plan_question_keeps_plan_and_sends_context_to_llm` 行为 | monkeypatch |
| T014 | tests/test_agent_config.py::test_confirmation_after_pending_plan_question_still_uses_original_plan | willy.agent_config | 验证 `confirmation_after_pending_plan_question_still_uses_original_plan` 行为 | monkeypatch |
| T015 | tests/test_agent_config.py::test_pending_plan_requires_its_own_latest_summary | willy.agent_config | 验证 `pending_plan_requires_its_own_latest_summary` 行为 | 进程内行为断言 |
| T016 | tests/test_agent_config.py::test_summary_uses_mass_density_default_prompt | willy.agent_config | 验证 `summary_uses_mass_density_default_prompt` 行为 | 进程内行为断言 |
| T017 | tests/test_agent_config.py::test_remote_selection_is_server_validated_and_frozen_into_new_plan | willy.agent_config | 验证 `remote_selection_is_server_validated_and_frozen_into_new_plan` 行为 | monkeypatch |
| T018 | tests/test_agent_config.py::test_unregistered_remote_selection_blocks_plan_generation | willy.agent_config | 验证 `unregistered_remote_selection_blocks_plan_generation` 行为 | monkeypatch |
| T019 | tests/test_agent_config.py::test_confirmation_rejects_remote_selection_changed_after_plan | willy.agent_config | 验证 `confirmation_rejects_remote_selection_changed_after_plan` 行为 | monkeypatch |
| T020 | tests/test_agent_config.py::test_remote_profile_never_silently_falls_back_to_local_launch | willy.agent_config | 验证 `remote_profile_never_silently_falls_back_to_local_launch` 行为 | monkeypatch |
| T276 | tests/test_layer_agent.py::TestEscalation::test_to_dict_truncates_raw_output | willy.layer_agent.LayerAgent / TestEscalation | 验证：to_dict() 应将 last_raw_output 截断到 500 字符 | 进程内行为断言 |
| T277 | tests/test_layer_agent.py::TestEscalation::test_empty_actions_and_output | willy.layer_agent.LayerAgent / TestEscalation | 验证：升级可能没有动作或输出 | 进程内行为断言 |
| T278 | tests/test_layer_agent.py::TestLayerAgentConstruction::test_minimal_construction | willy.layer_agent.LayerAgent / TestLayerAgentConstruction | 验证：最小有效构造应成功 | mock/patch |
| T279 | tests/test_layer_agent.py::TestLayerAgentConstruction::test_custom_max_retries | willy.layer_agent.LayerAgent / TestLayerAgentConstruction | 验证：自定义 max_retries 应被尊重 | mock/patch |
| T280 | tests/test_layer_agent.py::TestLayerAgentConstruction::test_on_action_callback | willy.layer_agent.LayerAgent / TestLayerAgentConstruction | 验证：on_action 回调应被存储 | mock/patch |
| T281 | tests/test_layer_agent.py::TestBuildContext::test_context_includes_step_info | willy.layer_agent.LayerAgent / TestBuildContext | 验证 `context_includes_step_info` 行为 | mock/patch |
| T282 | tests/test_layer_agent.py::TestBuildContext::test_context_truncates_long_config | willy.layer_agent.LayerAgent / TestBuildContext | 验证：config_text 应被截断到约 2000 字符 | mock/patch |
| T283 | tests/test_layer_agent.py::TestBuildContext::test_context_includes_artifacts | willy.layer_agent.LayerAgent / TestBuildContext | 验证 `context_includes_artifacts` 行为 | mock/patch |
| T284 | tests/test_layer_agent.py::TestBuildContext::test_context_includes_failed_step_outputs | willy.layer_agent.LayerAgent / TestBuildContext | 验证：失败步骤保留的中间产物也必须展示给 Agent | mock/patch |
| T285 | tests/test_layer_agent.py::TestBuildContext::test_context_includes_structured_execution_evidence | willy.layer_agent.LayerAgent / TestBuildContext | 验证 `context_includes_structured_execution_evidence` 行为 | mock/patch |
| T286 | tests/test_layer_agent.py::TestEscalate::test_escalate_returns_failed_step_result | willy.layer_agent.LayerAgent / TestEscalate | 验证 `escalate_returns_failed_step_result` 行为 | mock/patch |
| T287 | tests/test_layer_agent.py::TestHandleFailure::test_handle_failure_returns_step_result | willy.layer_agent.LayerAgent / TestHandleFailure | 验证：handle_failure 应返回 StepResult | mock/patch |
| T288 | tests/test_layer_agent.py::TestHandleFailure::test_successful_tool_result_keeps_its_actual_step_identity | willy.layer_agent.LayerAgent / TestHandleFailure | 验证：Upstream repair success must not be relabelled as the failed EQ step. | mock/patch |
| T289 | tests/test_layer_agent.py::TestHandleFailure::test_handle_failure_exhausts_retries | willy.layer_agent.LayerAgent / TestHandleFailure | 验证：当超过 max_retries 时，应升级 | mock/patch |
| T290 | tests/test_layer_agent.py::TestHandleFailure::test_tool_failures_stop_at_hard_retry_limit | willy.layer_agent.LayerAgent / TestHandleFailure | 验证 `tool_failures_stop_at_hard_retry_limit` 行为 | mock/patch |
| T291 | tests/test_layer_agent.py::TestHandleFailure::test_protocol_change_request_escalates_without_a_second_llm_call | willy.layer_agent.LayerAgent / TestHandleFailure | 验证：A tool cannot turn a model-generated flag into user confirmation. | mock/patch |
| T292 | tests/test_layer_agent.py::TestHandleFailure::test_simulation_agent_blocks_protocol_change_before_tool_dispatch | willy.layer_agent.LayerAgent / TestHandleFailure | 验证 `simulation_agent_blocks_protocol_change_before_tool_dispatch` 行为 | mock/patch |
| T293 | tests/test_layer_agent.py::TestHandleFailure::test_simulation_agent_rejects_box_retry_without_geometry_evidence | willy.layer_agent.LayerAgent / TestHandleFailure | 验证 `simulation_agent_rejects_box_retry_without_geometry_evidence` 行为 | tmp_path 隔离工作区 + mock/patch |
| T294 | tests/test_layer_agent.py::TestHandleFailure::test_handle_failure_detects_escalation_keyword | willy.layer_agent.LayerAgent / TestHandleFailure | 验证：当 LLM 在响应中说 'escalat' 时，应立即升级 | mock/patch |
| T295 | tests/test_layer_agent.py::TestHandleFailure::test_handle_failure_llm_exception_retry | willy.layer_agent.LayerAgent / TestHandleFailure | 验证：当 LLM API 抛出异常时，应重试一次 | mock/patch |
| T296 | tests/test_layer_agent.py::TestHandleFailure::test_llm_exceptions_consume_retry_budget_and_update_state | willy.layer_agent.LayerAgent / TestHandleFailure | 验证 `llm_exceptions_consume_retry_budget_and_update_state` 行为 | mock/patch |
| T297 | tests/test_layer_agent.py::TestLayerAgentEdgeCases::test_empty_tool_list | willy.layer_agent.LayerAgent / TestLayerAgentEdgeCases | 验证：零工具的 Agent 应能工作（纯文本响应） | mock/patch |
| T298 | tests/test_layer_agent.py::TestLayerAgentEdgeCases::test_tool_handler_returning_non_json | willy.layer_agent.LayerAgent / TestLayerAgentEdgeCases | 验证：工具处理程序可能返回非 JSON —— LLM 应处理它 | mock/patch |
| T299 | tests/test_layer_agent.py::TestLayerAgentEdgeCases::test_long_error_message | willy.layer_agent.LayerAgent / TestLayerAgentEdgeCases | 验证：极长的错误消息不应崩溃 Agent | mock/patch |
| T300 | tests/test_layer_agent.py::TestRecoveryAuthorization::test_model_confirmation_flag_cannot_dispatch_a_method_change | willy.layer_agent.LayerAgent / TestRecoveryAuthorization | 验证 `model_confirmation_flag_cannot_dispatch_a_method_change` 行为 | tmp_path 隔离工作区 + mock/patch |
| T301 | tests/test_layer_agent.py::TestRecoveryAuthorization::test_safe_dispatch_records_policy_and_execution_trace | willy.layer_agent.LayerAgent / TestRecoveryAuthorization | 验证 `safe_dispatch_records_policy_and_execution_trace` 行为 | tmp_path 隔离工作区 + mock/patch |
| T302 | tests/test_llm_budget.py::test_budget_limits_calls_and_reports_state | willy.llm_budget | 验证 `budget_limits_calls_and_reports_state` 行为 | 异常断言 |
| T303 | tests/test_llm_budget.py::test_circuit_breaker_opens_after_consecutive_failures | willy.llm_budget | 验证 `circuit_breaker_opens_after_consecutive_failures` 行为 | 异常断言 |
| T304 | tests/test_llm_config.py::test_no_key_means_llm_is_not_configured | willy.llm_config OpenAI-compatible 配置 | 验证 `no_key_means_llm_is_not_configured` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T305 | tests/test_llm_config.py::test_legacy_deepseek_key_uses_existing_defaults | willy.llm_config OpenAI-compatible 配置 | 验证 `legacy_deepseek_key_uses_existing_defaults` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T306 | tests/test_llm_config.py::test_generic_values_override_legacy_and_read_dotenv | willy.llm_config OpenAI-compatible 配置 | 验证 `generic_values_override_legacy_and_read_dotenv` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T307 | tests/test_llm_config.py::test_invalid_values_are_rejected_without_client_construction[not-a-url-model] | willy.llm_config OpenAI-compatible 配置 | 验证 `invalid_values_are_rejected_without_client_construction` 行为；参数集 `not-a-url-model` | pytest 参数化 + 异常断言 |
| T308 | tests/test_llm_config.py::test_invalid_values_are_rejected_without_client_construction[ftp://example.com-model] | willy.llm_config OpenAI-compatible 配置 | 验证 `invalid_values_are_rejected_without_client_construction` 行为；参数集 `ftp://example.com-model` | pytest 参数化 + 异常断言 |
| T309 | tests/test_llm_config.py::test_invalid_values_are_rejected_without_client_construction[https://example.com?token=x-model] | willy.llm_config OpenAI-compatible 配置 | 验证 `invalid_values_are_rejected_without_client_construction` 行为；参数集 `https://example.com?token=x-model` | pytest 参数化 + 异常断言 |
| T310 | tests/test_llm_config.py::test_invalid_values_are_rejected_without_client_construction[https://example.com-model\nother] | willy.llm_config OpenAI-compatible 配置 | 验证 `invalid_values_are_rejected_without_client_construction` 行为；参数集 `https://example.com-model\nother` | pytest 参数化 + 异常断言 |
| T311 | tests/test_llm_config.py::test_configured_client_uses_resolved_endpoint_and_model | willy.llm_config OpenAI-compatible 配置 | 验证 `configured_client_uses_resolved_endpoint_and_model` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T312 | tests/test_llm_config.py::test_managed_mode_uses_a_fixed_deployment_profile_not_a_user_api_key | willy.llm_config OpenAI-compatible 配置 | 验证 `managed_mode_uses_a_fixed_deployment_profile_not_a_user_api_key` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T313 | tests/test_llm_config.py::test_invalid_llm_mode_is_rejected_before_any_client_is_constructed | willy.llm_config OpenAI-compatible 配置 | 验证 `invalid_llm_mode_is_rejected_before_any_client_is_constructed` 行为 | tmp_path 隔离工作区 + monkeypatch + 异常断言 |
| T314 | tests/test_llm_eval_report.py::test_mock_eval_public_report_excludes_prompts_traces_and_tool_arguments | tests.llm_eval 脱敏基线报告 | 验证 `mock_eval_public_report_excludes_prompts_traces_and_tool_arguments` 行为 | tmp_path 隔离工作区 + mock/patch |
| L001 | LLM Eval::q_scf_001 | LayerAgent / quantum | 注入 `scf_not_converged`：SCF 不收敛 → 重试2次 → 第2次添加 scf=xqc 后成功 | 预录 MockLLMResponse + mock tool executor + 评分阈值 |
| L002 | LLM Eval::q_crash_002 | LayerAgent / quantum | 注入 `gaussian_crash`：Gaussian segfault 崩溃 → 5次重试无果 → 升级 | 预录 MockLLMResponse + mock tool executor + 评分阈值 |
| L003 | LLM Eval::q_formchk_003 | LayerAgent / quantum | 注入 `formchk_failed`：Formchk 失败 → 重试1次 → 成功 | 预录 MockLLMResponse + mock tool executor + 评分阈值 |
| L004 | LLM Eval::q_orca_004 | LayerAgent / quantum | 注入 `orca_crash`：ORCA 内存不足崩溃 → 增加内存重试 → 成功 | 预录 MockLLMResponse + mock tool executor + 评分阈值 |
| L005 | LLM Eval::q_geom_005 | LayerAgent / quantum | 注入 `geom_not_converged`：几何优化不收敛 → 添加 opt=calcfc → 成功 | 预录 MockLLMResponse + mock tool executor + 评分阈值 |
| L006 | LLM Eval::q_resp_006 | LayerAgent / quantum | 注入 `resp_failed`：RESP 电荷拟合失败 → 尝试更换溶剂 → 仍失败 → 升级 | 预录 MockLLMResponse + mock tool executor + 评分阈值 |
| L007 | LLM Eval::t_sobtop_007 | LayerAgent / topology | 注入 `sobtop_failed`：Sobtop 失败 → 使用 manifest 中同一 Sobtop 后端重试2次 → 第3次成功 | 预录 MockLLMResponse + mock tool executor + 评分阈值 |
| L008 | LLM Eval::t_rc24_008 | LayerAgent / topology | 注入 `sobtop_failed`：Sobtop rc=24 且 ITP/GRO 完整有效 → 接受当前结果 → 继续流程 | 预录 MockLLMResponse + mock tool executor + 评分阈值 |
| L009 | LLM Eval::t_ligpargen_009 | LayerAgent / topology | 注入 `ligpargen_failed`：LigParGen 失败 → 在同一 OPLS-AA 后端重试 → 成功 | 预录 MockLLMResponse + mock tool executor + 评分阈值 |
| L010 | LLM Eval::t_atomtype_010 | LayerAgent / topology | 注入 `atomtype_conflict`：atomtype CX 参数冲突 → 停止组装并升级 | 预录 MockLLMResponse + mock tool executor + 评分阈值 |
| L011 | LLM Eval::t_mol2_corrupt_011 | LayerAgent / topology | 注入 `file_not_found`：mol2 文件损坏无法修复 → 请求上游修复并升级 | 预录 MockLLMResponse + mock tool executor + 评分阈值 |
| L012 | LLM Eval::t_assembly_012 | LayerAgent / topology | 注入 `config_invalid`：主拓扑组装 itp 冲突 → 多次重试 → 升级 | 预录 MockLLMResponse + mock tool executor + 评分阈值 |
| L013 | LLM Eval::s_em_013 | LayerAgent / simulation | 注入 `em_not_converged`：EM 未收敛 → 增加 nsteps → 降低 emtol → 成功 | 预录 MockLLMResponse + mock tool executor + 评分阈值 |
| L014 | LLM Eval::s_eq_014 | LayerAgent / simulation | 注入 `eq_not_converged`：EQ 密度不收敛 → 调整 tau_p → 成功 | 预录 MockLLMResponse + mock tool executor + 评分阈值 |
| L015 | LLM Eval::s_nan_015 | LayerAgent / simulation | 注入 `mdrun_failed`：PROD NaN 异常 → 减小 dt → 从 checkpoint 恢复 → 成功 | 预录 MockLLMResponse + mock tool executor + 评分阈值 |
| L016 | LLM Eval::s_temp_016 | LayerAgent / simulation | 注入 `mdrun_failed`：温度爆炸 9999K → 调整退火协议 → 成功 | 预录 MockLLMResponse + mock tool executor + 评分阈值 |
| L017 | LLM Eval::s_grompp_017 | LayerAgent / simulation | 注入 `grompp_failed`：grompp atomtype 错误 → 诊断 → 修复 itp → 成功 | 预录 MockLLMResponse + mock tool executor + 评分阈值 |
| L018 | LLM Eval::s_eq_persist_018 | LayerAgent / simulation | 注入 `eq_not_converged`：EQ 持续失败 → 3次重试用尽 → 升级 | 预录 MockLLMResponse + mock tool executor + 评分阈值 |

## E. 量子与跨层 Tool 契约

量子/引擎日志解析、ORCA 结构产物、量子工具、后端原始输入审计与跨层 tool 返回协议。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T315 | tests/test_log_parsers.py::TestParseGaussianLog::test_normal_termination | willy.log_parsers / TestParseGaussianLog | 验证 `normal_termination` 行为 | tmp_path 隔离工作区 |
| T316 | tests/test_log_parsers.py::TestParseGaussianLog::test_scf_not_converged | willy.log_parsers / TestParseGaussianLog | 验证 `scf_not_converged` 行为 | tmp_path 隔离工作区 |
| T317 | tests/test_log_parsers.py::TestParseGaussianLog::test_geom_not_converged | willy.log_parsers / TestParseGaussianLog | 验证 `geom_not_converged` 行为 | tmp_path 隔离工作区 |
| T318 | tests/test_log_parsers.py::TestParseGaussianLog::test_gaussian_crash | willy.log_parsers / TestParseGaussianLog | 验证 `gaussian_crash` 行为 | tmp_path 隔离工作区 |
| T319 | tests/test_log_parsers.py::TestParseGaussianLog::test_missing_file | willy.log_parsers / TestParseGaussianLog | 验证 `missing_file` 行为 | 进程内行为断言 |
| T320 | tests/test_log_parsers.py::TestParseGaussianLog::test_empty_file | willy.log_parsers / TestParseGaussianLog | 验证 `empty_file` 行为 | tmp_path 隔离工作区 |
| T321 | tests/test_log_parsers.py::TestParseGaussianLog::test_evidence_lines_truncated | willy.log_parsers / TestParseGaussianLog | 验证：证据行应被截断以控制上下文大小 | tmp_path 隔离工作区 |
| T322 | tests/test_log_parsers.py::TestParseGaussianLog::test_optimization_cycles_detected | willy.log_parsers / TestParseGaussianLog | 验证：优化周期数应被检测 | tmp_path 隔离工作区 |
| T323 | tests/test_log_parsers.py::TestParseOrcaOutput::test_normal_termination | willy.log_parsers / TestParseOrcaOutput | 验证 `normal_termination` 行为 | tmp_path 隔离工作区 |
| T324 | tests/test_log_parsers.py::TestParseOrcaOutput::test_scf_not_converged | willy.log_parsers / TestParseOrcaOutput | 验证 `scf_not_converged` 行为 | tmp_path 隔离工作区 |
| T325 | tests/test_log_parsers.py::TestParseOrcaOutput::test_missing_file | willy.log_parsers / TestParseOrcaOutput | 验证 `missing_file` 行为 | 进程内行为断言 |
| T326 | tests/test_log_parsers.py::TestParseOrcaOutput::test_orca_crash_detection | willy.log_parsers / TestParseOrcaOutput | 验证 `orca_crash_detection` 行为 | tmp_path 隔离工作区 |
| T327 | tests/test_log_parsers.py::TestParseGromacsLog::test_em_converged | willy.log_parsers / TestParseGromacsLog | 验证 `em_converged` 行为 | tmp_path 隔离工作区 |
| T328 | tests/test_log_parsers.py::TestParseGromacsLog::test_lincs_warnings_detected | willy.log_parsers / TestParseGromacsLog | 验证 `lincs_warnings_detected` 行为 | tmp_path 隔离工作区 |
| T329 | tests/test_log_parsers.py::TestParseGromacsLog::test_missing_file | willy.log_parsers / TestParseGromacsLog | 验证 `missing_file` 行为 | 进程内行为断言 |
| T330 | tests/test_log_parsers.py::TestParseGromacsLog::test_stage_specific_hints | willy.log_parsers / TestParseGromacsLog | 验证：不同阶段的提示应不同 | tmp_path 隔离工作区 |
| T331 | tests/test_log_parsers.py::TestDiagnoseLog::test_explicit_gaussian_engine | willy.log_parsers / TestDiagnoseLog | 验证 `explicit_gaussian_engine` 行为 | 进程内行为断言 |
| T332 | tests/test_log_parsers.py::TestDiagnoseLog::test_explicit_orca_engine | willy.log_parsers / TestDiagnoseLog | 验证 `explicit_orca_engine` 行为 | 进程内行为断言 |
| T333 | tests/test_log_parsers.py::TestDiagnoseLog::test_explicit_gromacs_engine | willy.log_parsers / TestDiagnoseLog | 验证 `explicit_gromacs_engine` 行为 | 进程内行为断言 |
| T334 | tests/test_log_parsers.py::TestDiagnoseLog::test_unknown_engine_fallback | willy.log_parsers / TestDiagnoseLog | 验证 `unknown_engine_fallback` 行为 | 进程内行为断言 |
| T335 | tests/test_log_parsers.py::TestEvidenceTruncation::test_very_long_lines_handled | willy.log_parsers / TestEvidenceTruncation | 验证：极长行不应导致解析器崩溃 | tmp_path 隔离工作区 |
| T336 | tests/test_log_parsers.py::TestEvidenceTruncation::test_binary_content_handled | willy.log_parsers / TestEvidenceTruncation | 验证：二进制内容不应导致解析器崩溃 | tmp_path 隔离工作区 |
| T362 | tests/test_multiwfn_bundled.py::test_molden_mol2_preserves_multiwfn_connectivity_and_atom_order | bundled Multiwfn 运行契约 | 验证 `molden_mol2_preserves_multiwfn_connectivity_and_atom_order` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T363 | tests/test_multiwfn_bundled.py::test_molden_mol2_allows_single_atom_without_bonds | bundled Multiwfn 运行契约 | 验证 `molden_mol2_allows_single_atom_without_bonds` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T364 | tests/test_multiwfn_bundled.py::test_molden_mol2_rejects_missing_connectivity_for_multi_atom | bundled Multiwfn 运行契约 | 验证 `molden_mol2_rejects_missing_connectivity_for_multi_atom` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T365 | tests/test_multiwfn_bundled.py::test_extract_xyz_rejects_nonzero_multiwfn_exit | bundled Multiwfn 运行契约 | 验证 `extract_xyz_rejects_nonzero_multiwfn_exit` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T366 | tests/test_multiwfn_bundled.py::test_resp_uses_clean_exit_sequence_and_publishes_charge | bundled Multiwfn 运行契约 | 验证 `resp_uses_clean_exit_sequence_and_publishes_charge` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T367 | tests/test_multiwfn_bundled.py::test_resp_maps_nonzero_multiwfn_exit_to_resp_failure | bundled Multiwfn 运行契约 | 验证 `resp_maps_nonzero_multiwfn_exit_to_resp_failure` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T368 | tests/test_multiwfn_bundled.py::test_sobtop_optional_multiwfn_command_targets_bundled_runtime | bundled Multiwfn 运行契约 | 验证：Sobtop 的可选 Multiwfn 菜单不得回退到系统 PATH | 进程内行为断言 |
| T369 | tests/test_multiwfn_bundled.py::test_bundled_payload_matches_manifest_and_has_no_machine_paths | bundled Multiwfn 运行契约 | 验证：发布包必须可校验，且配置不能嵌入构建机的量子程序路径 | 进程内行为断言 |
| T499 | tests/test_quantum_input_audit.py::test_g16_audit_reads_charge_and_spin_from_raw_input | willy.quantum.input_audit / backend-specific raw inputs | 验证 `g16_audit_reads_charge_and_spin_from_raw_input` 行为 | tmp_path 隔离工作区 |
| T500 | tests/test_quantum_input_audit.py::test_g09_audit_uses_the_same_gjf_contract_without_falling_back_to_inp | willy.quantum.input_audit / backend-specific raw inputs | 验证 `g09_audit_uses_the_same_gjf_contract_without_falling_back_to_inp` 行为 | tmp_path 隔离工作区 |
| T501 | tests/test_quantum_input_audit.py::test_imported_ionic_gjf_inputs_match_the_registered_filename_charges | willy.quantum.input_audit / backend-specific raw inputs | 验证：迁入 struct 的带电文件必须以文件名电荷写入 GJF 头部 | 进程内行为断言 |
| T502 | tests/test_quantum_input_audit.py::test_struct_gjf_headers_are_canonical_and_dmaa_is_removed | willy.quantum.input_audit / backend-specific raw inputs | 验证：每个保留 GJF 都必须有相对 checkpoint、同名标题和 charge 行 | 进程内行为断言 |
| T503 | tests/test_quantum_input_audit.py::test_every_retained_gjf_has_a_native_orca_input | willy.quantum.input_audit / backend-specific raw inputs | 验证：每个保留 GJF 都应有可被 ORCA 原生审计器读取的同名 INP | 进程内行为断言 |
| T504 | tests/test_quantum_input_audit.py::test_orca_audit_requires_native_optimized_inp_and_never_falls_back_to_gjf | willy.quantum.input_audit / backend-specific raw inputs | 验证 `orca_audit_requires_native_optimized_inp_and_never_falls_back_to_gjf` 行为 | tmp_path 隔离工作区 |
| T505 | tests/test_quantum_input_audit.py::test_audit_properties_replace_an_untrusted_neutral_default | willy.quantum.input_audit / backend-specific raw inputs | 验证 `audit_properties_replace_an_untrusted_neutral_default` 行为 | tmp_path 隔离工作区 |
| T506 | tests/test_quantum_input_audit.py::test_orca_workspace_requires_inp_and_snapshots_it | willy.quantum.input_audit / backend-specific raw inputs | 验证 `orca_workspace_requires_inp_and_snapshots_it` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T507 | tests/test_quantum_input_audit.py::test_g09_workspace_requires_gjf_and_snapshots_it | willy.quantum.input_audit / backend-specific raw inputs | 验证 `g09_workspace_requires_gjf_and_snapshots_it` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T508 | tests/test_quantum_input_audit.py::test_start_pipeline_rejects_input_changed_after_plan_freeze | willy.quantum.input_audit / backend-specific raw inputs | 验证 `start_pipeline_rejects_input_changed_after_plan_freeze` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T509 | tests/test_quantum_input_audit.py::test_config_agent_requires_an_input_audit_and_freezes_audited_charge | willy.quantum.input_audit / backend-specific raw inputs | 验证 `config_agent_requires_an_input_audit_and_freezes_audited_charge` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T634 | tests/test_struct_orca.py::test_single_atom_opt_uses_private_sp_input_and_preserves_raw_input | willy.quantum.struct_orca | 验证 `single_atom_opt_uses_private_sp_input_and_preserves_raw_input` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T635 | tests/test_struct_orca.py::test_multiatom_opt_keeps_original_orca_input | willy.quantum.struct_orca | 验证 `multiatom_opt_keeps_original_orca_input` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T674 | tests/test_toolist_quantum_topology.py::TestQuantumToolDefinitions::test_quantum_tools_defined | willy.toolist_quantum / toolist_topology / TestQuantumToolDefinitions | 验证：G16/G09/ORCA 镜像链路应完整暴露量子工具 | 进程内行为断言 |
| T675 | tests/test_toolist_quantum_topology.py::TestQuantumToolDefinitions::test_all_tool_names | willy.toolist_quantum / toolist_topology / TestQuantumToolDefinitions | 验证 `all_tool_names` 行为 | 进程内行为断言 |
| T676 | tests/test_toolist_quantum_topology.py::TestQuantumToolDefinitions::test_tools_have_valid_json_schema | willy.toolist_quantum / toolist_topology / TestQuantumToolDefinitions | 验证 `tools_have_valid_json_schema` 行为 | 进程内行为断言 |
| T677 | tests/test_toolist_quantum_topology.py::TestQuantumToolDefinitions::test_retry_tools_use_current_fchk_contract | willy.toolist_quantum / toolist_topology / TestQuantumToolDefinitions | 验证：mol2 和 RESP 重试都必须消费 Step 2 的 *_opt.fchk | 进程内行为断言 |
| T678 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_unknown_tool_returns_error | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证 `unknown_tool_returns_error` 行为 | 进程内行为断言 |
| T679 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_diagnose_tool_returns_result | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：tools_diagnose_error_quantum 应返回诊断结果 | 进程内行为断言 |
| T680 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_modify_config_molecule | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：tools_modify_config_molecule 应更新 config.json | 进程内行为断言 |
| T681 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_modify_config_uses_the_active_run_snapshot | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：Agent 调整只影响当前运行，不得改写项目根配置 | tmp_path 隔离工作区 |
| T682 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_skip_molecule_quantum | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：tools_skip_molecule_quantum 应将分子加入跳过列表 | 进程内行为断言 |
| T683 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_mol2_conversion_error_on_missing_file | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：当文件不存在时，mol2 转换应优雅失败 | 进程内行为断言 |
| T684 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_mol2_conversion_returns_step_result_for_malformed_fchk | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：fchk 解析异常不得从公开工具路径泄露 | tmp_path 隔离工作区 |
| T685 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_mol2_conversion_rejects_missing_bond_connectivity[NBond] | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：缺失任一键连接字段时，不得生成可被 Sobtop 消费的 mol2；参数集 `NBond` | pytest 参数化 + tmp_path 隔离工作区 |
| T686 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_mol2_conversion_rejects_missing_bond_connectivity[IBond] | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：缺失任一键连接字段时，不得生成可被 Sobtop 消费的 mol2；参数集 `IBond` | pytest 参数化 + tmp_path 隔离工作区 |
| T687 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_mol2_conversion_rejects_missing_bond_connectivity[RBond] | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：缺失任一键连接字段时，不得生成可被 Sobtop 消费的 mol2；参数集 `RBond` | pytest 参数化 + tmp_path 隔离工作区 |
| T688 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_mol2_conversion_rejects_incomplete_coordinates | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：原子数与坐标数组长度不一致时必须失败 | tmp_path 隔离工作区 |
| T689 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_chg_retry_reports_missing_explicit_fchk[tools_retry_chg_g16] | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：两种后端重试都基于同一显式 fchk 输入；参数集 `tools_retry_chg_g16` | pytest 参数化 + tmp_path 隔离工作区 |
| T690 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_chg_retry_reports_missing_explicit_fchk[tools_retry_chg_g09] | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：两种后端重试都基于同一显式 fchk 输入；参数集 `tools_retry_chg_g09` | pytest 参数化 + tmp_path 隔离工作区 |
| T691 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_chg_retry_reports_missing_explicit_fchk[tools_retry_chg_orca] | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：两种后端重试都基于同一显式 fchk 输入；参数集 `tools_retry_chg_orca` | pytest 参数化 + tmp_path 隔离工作区 |
| T692 | tests/test_toolist_quantum_topology.py::TestTopologyToolDefinitions::test_six_tools_defined | willy.toolist_quantum / toolist_topology / TestTopologyToolDefinitions | 验证：应有 6 个拓扑工具 | 进程内行为断言 |
| T693 | tests/test_toolist_quantum_topology.py::TestTopologyToolDefinitions::test_all_tool_names | willy.toolist_quantum / toolist_topology / TestTopologyToolDefinitions | 验证 `all_tool_names` 行为 | 进程内行为断言 |
| T694 | tests/test_toolist_quantum_topology.py::TestTopologyToolDefinitions::test_topology_config_tool_has_no_unused_default_charge | willy.toolist_quantum / toolist_topology / TestTopologyToolDefinitions | 验证 `topology_config_tool_has_no_unused_default_charge` 行为 | 进程内行为断言 |
| T695 | tests/test_toolist_quantum_topology.py::TestTopologyToolHandler::test_unknown_tool_returns_error | willy.toolist_quantum / toolist_topology / TestTopologyToolHandler | 验证 `unknown_tool_returns_error` 行为 | 进程内行为断言 |
| T696 | tests/test_toolist_quantum_topology.py::TestTopologyToolHandler::test_diagnose_tool_returns_result | willy.toolist_quantum / toolist_topology / TestTopologyToolHandler | 验证：tools_diagnose_error_topology 应返回诊断结果 | 进程内行为断言 |
| T697 | tests/test_toolist_quantum_topology.py::TestTopologyToolHandler::test_diagnose_detects_atomtype_error | willy.toolist_quantum / toolist_topology / TestTopologyToolHandler | 验证：应从 raw_output 中检测 atomtype 缺失 | 进程内行为断言 |
| T698 | tests/test_toolist_quantum_topology.py::TestTopologyToolHandler::test_diagnose_detects_sobtop_exit_24 | willy.toolist_quantum / toolist_topology / TestTopologyToolHandler | 验证：应从 raw_output 中检测 Sobtop 退出码 24 | 进程内行为断言 |
| T699 | tests/test_toolist_quantum_topology.py::TestTopologyToolHandler::test_diagnose_detects_ligpargen_error | willy.toolist_quantum / toolist_topology / TestTopologyToolHandler | 验证：应检测 LigParGen 相关错误 | 进程内行为断言 |
| T700 | tests/test_toolist_quantum_topology.py::TestTopologyToolHandler::test_modify_config_requires_active_run | willy.toolist_quantum / toolist_topology / TestTopologyToolHandler | 验证：Topology config tools may only mutate an explicit run snapshot. | 进程内行为断言 |
| T701 | tests/test_toolist_quantum_topology.py::TestTopologyToolHandler::test_skip_molecule_topology_is_disabled | willy.toolist_quantum / toolist_topology / TestTopologyToolHandler | 验证：Skip must not leave manifest and residues inconsistent. | 进程内行为断言 |
| T702 | tests/test_toolist_quantum_topology.py::TestCrossToolistConsistency::test_all_tool_names_use_tools_prefix | willy.toolist_quantum / toolist_topology / TestCrossToolistConsistency | 验证：所有工具名称必须以 tools_ 开头 | 进程内行为断言 |
| T703 | tests/test_toolist_quantum_topology.py::TestCrossToolistConsistency::test_all_handlers_return_json_strings | willy.toolist_quantum / toolist_topology / TestCrossToolistConsistency | 验证：诊断处理程序应返回 JSON 字符串 | 进程内行为断言 |
| T704 | tests/test_toolist_quantum_topology.py::TestCrossToolistConsistency::test_diagnosis_tools_all_use_diagnosis_marker | willy.toolist_quantum / toolist_topology / TestCrossToolistConsistency | 验证：所有 diagnose_* 工具应返回 _diagnosis=True | 进程内行为断言 |
| T705 | tests/test_toolist_quantum_topology.py::test_quantum_tool_catalog_exposes_g09_structure_and_charge_actions | willy.toolist_quantum / toolist_topology | 验证 `quantum_tool_catalog_exposes_g09_structure_and_charge_actions` 行为 | 进程内行为断言 |
| T706 | tests/test_toolist_quantum_topology.py::test_g09_structure_retry_dispatches_to_g09_module | willy.toolist_quantum / toolist_topology | 验证 `g09_structure_retry_dispatches_to_g09_module` 行为 | tmp_path 隔离工作区 + monkeypatch |

## F. 拓扑后端与组装

Sobtop/OPLS 后端、manifest、重试账本、ITP 校验和主拓扑组装。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T274 | tests/test_itp_namespace.py::test_namespace_itp_rewrites_atom_and_parameter_references | willy.topology.itp_namespace / OPLS-AA assembly namespace | 验证 `namespace_itp_rewrites_atom_and_parameter_references` 行为 | tmp_path 隔离工作区 |
| T275 | tests/test_itp_namespace.py::test_opls_assembly_namespaces_conflicting_local_types | willy.topology.itp_namespace / OPLS-AA assembly namespace | 验证 `opls_assembly_namespaces_conflicting_local_types` 行为 | tmp_path 隔离工作区 |
| T738 | tests/test_top_assembly.py::test_build_revises_itps_in_requested_topology_directory | willy.topology.top_assembly | 验证 `build_revises_itps_in_requested_topology_directory` 行为 | tmp_path 隔离工作区 |
| T739 | tests/test_top_assembly.py::test_build_rejects_a_shared_topology_directory | willy.topology.top_assembly | 验证 `build_rejects_a_shared_topology_directory` 行为 | 进程内行为断言 |
| T740 | tests/test_topology_contract.py::TestUnifiedTopologyManifest::test_write_uses_private_topology_section_when_unified_manifest_exists | willy.topology 后端与产物契约 / TestUnifiedTopologyManifest | 验证 `write_uses_private_topology_section_when_unified_manifest_exists` 行为 | tmp_path 隔离工作区 |
| T741 | tests/test_topology_contract.py::TestUnifiedTopologyManifest::test_legacy_topology_manifest_is_read_when_unified_manifest_is_absent | willy.topology 后端与产物契约 / TestUnifiedTopologyManifest | 验证 `legacy_topology_manifest_is_read_when_unified_manifest_is_absent` 行为 | tmp_path 隔离工作区 |
| T742 | tests/test_topology_contract.py::TestUnifiedTopologyManifest::test_invalid_unified_manifest_never_falls_back_to_legacy_topology_data | willy.topology 后端与产物契约 / TestUnifiedTopologyManifest | 验证 `invalid_unified_manifest_never_falls_back_to_legacy_topology_data` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T743 | tests/test_topology_contract.py::TestUnifiedTopologyManifest::test_topology_assembly_reads_and_updates_unified_section | willy.topology 后端与产物契约 / TestUnifiedTopologyManifest | 验证 `topology_assembly_reads_and_updates_unified_section` 行为 | tmp_path 隔离工作区 |
| T744 | tests/test_topology_contract.py::TestTopologyConfig::test_amber_and_unregistered_values_are_rejected | willy.topology 后端与产物契约 / TestTopologyConfig | 验证 `amber_and_unregistered_values_are_rejected` 行为 | 进程内行为断言 |
| T745 | tests/test_topology_contract.py::TestTopologyConfig::test_legacy_contradictory_ligpargen_gaff_migrates_to_sobtop | willy.topology 后端与产物契约 / TestTopologyConfig | 验证 `legacy_contradictory_ligpargen_gaff_migrates_to_sobtop` 行为 | 进程内行为断言 |
| T746 | tests/test_topology_contract.py::TestTopologyConfig::test_legacy_ligpargen_opls_migrates_to_oplsaa | willy.topology 后端与产物契约 / TestTopologyConfig | 验证 `legacy_ligpargen_opls_migrates_to_oplsaa` 行为 | 进程内行为断言 |
| T747 | tests/test_topology_contract.py::TestTopologyDispatcher::test_dispatch_rejects_path_like_residue_name | willy.topology 后端与产物契约 / TestTopologyDispatcher | 验证 `dispatch_rejects_path_like_residue_name` 行为 | tmp_path 隔离工作区 |
| T748 | tests/test_topology_contract.py::TestTopologyDispatcher::test_sobtop_dispatch_uses_only_config_registered_components | willy.topology 后端与产物契约 / TestTopologyDispatcher | 验证 `sobtop_dispatch_uses_only_config_registered_components` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T749 | tests/test_topology_contract.py::TestTopologyDispatcher::test_dispatch_does_not_reset_existing_retry_ledger | willy.topology 后端与产物契约 / TestTopologyDispatcher | 验证 `dispatch_does_not_reset_existing_retry_ledger` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T750 | tests/test_topology_contract.py::TestTopologyDispatcher::test_opls_dispatch_passes_each_component_real_charge | willy.topology 后端与产物契约 / TestTopologyDispatcher | 验证 `opls_dispatch_passes_each_component_real_charge` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T751 | tests/test_topology_contract.py::TestTopologyDispatcher::test_opls_backend_passes_charge_and_opls_options_to_ligpargen | willy.topology 后端与产物契约 / TestTopologyDispatcher | 验证 `opls_backend_passes_charge_and_opls_options_to_ligpargen` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T752 | tests/test_topology_contract.py::TestSobtopExecutor::test_frozen_menu_sequence | willy.topology 后端与产物契约 / TestSobtopExecutor | 验证 `frozen_menu_sequence` 行为 | tmp_path 隔离工作区 |
| T753 | tests/test_topology_contract.py::TestSobtopExecutor::test_old_vendor_output_cannot_make_failed_run_succeed | willy.topology 后端与产物契约 / TestSobtopExecutor | 验证 `old_vendor_output_cannot_make_failed_run_succeed` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T754 | tests/test_topology_contract.py::TestSobtopExecutor::test_rc24_requires_complete_valid_current_outputs | willy.topology 后端与产物契约 / TestSobtopExecutor | 验证 `rc24_requires_complete_valid_current_outputs` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T755 | tests/test_topology_contract.py::TestSobtopExecutor::test_rc24_with_invalid_outputs_fails_and_cleans_vendor | willy.topology 后端与产物契约 / TestSobtopExecutor | 验证 `rc24_with_invalid_outputs_fails_and_cleans_vendor` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T756 | tests/test_topology_contract.py::TestSobtopExecutor::test_startup_oserror_is_a_dependency_step_result | willy.topology 后端与产物契约 / TestSobtopExecutor | 验证 `startup_oserror_is_a_dependency_step_result` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T757 | tests/test_topology_contract.py::TestSobtopExecutor::test_output_directory_must_be_explicit | willy.topology 后端与产物契约 / TestSobtopExecutor | 验证 `output_directory_must_be_explicit` 行为 | tmp_path 隔离工作区 |
| T758 | tests/test_topology_contract.py::TestSobtopExecutor::test_real_sobtop_minimal_integration_when_available | willy.topology 后端与产物契约 / TestSobtopExecutor | 验证：Exercise Sobtop only with a hash-verified external EC fixture bundle. | tmp_path 隔离工作区 |
| T759 | tests/test_topology_contract.py::TestOplsExecutor::test_same_residue_concurrent_runs_are_isolated | willy.topology 后端与产物契约 / TestOplsExecutor | 验证 `same_residue_concurrent_runs_are_isolated` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T760 | tests/test_topology_contract.py::TestOplsExecutor::test_ligpargen_gro_restores_five_column_residue_name | willy.topology 后端与产物契约 / TestOplsExecutor | 验证 `ligpargen_gro_restores_five_column_residue_name` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T761 | tests/test_topology_contract.py::TestOplsExecutor::test_ligpargen_gets_a_private_legacy_babel_compatibility_entry | willy.topology 后端与产物契约 / TestOplsExecutor | 验证 `ligpargen_gets_a_private_legacy_babel_compatibility_entry` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T762 | tests/test_topology_contract.py::TestOplsExecutor::test_startup_oserror_is_a_dependency_step_result | willy.topology 后端与产物契约 / TestOplsExecutor | 验证 `startup_oserror_is_a_dependency_step_result` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T763 | tests/test_topology_contract.py::TestOplsExecutor::test_output_directory_must_be_explicit | willy.topology 后端与产物契约 / TestOplsExecutor | 验证 `output_directory_must_be_explicit` 行为 | 进程内行为断言 |
| T764 | tests/test_topology_contract.py::TestOplsExecutor::test_path_like_output_name_is_rejected_before_any_write | willy.topology 后端与产物契约 / TestOplsExecutor | 验证 `path_like_output_name_is_rejected_before_any_write` 行为 | tmp_path 隔离工作区 |
| T765 | tests/test_topology_contract.py::TestOplsExecutor::test_existing_output_symlink_cannot_escape_run_directory | willy.topology 后端与产物契约 / TestOplsExecutor | 验证 `existing_output_symlink_cannot_escape_run_directory` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T766 | tests/test_topology_contract.py::TestOplsExecutor::test_moleculetype_restore_failure_is_structured_and_cleans_outputs | willy.topology 后端与产物契约 / TestOplsExecutor | 验证 `moleculetype_restore_failure_is_structured_and_cleans_outputs` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T767 | tests/test_topology_contract.py::TestTopologyRetryLedger::test_config_tool_updates_opls_options_used_by_retry | willy.topology 后端与产物契约 / TestTopologyRetryLedger | 验证 `config_tool_updates_opls_options_used_by_retry` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T768 | tests/test_topology_contract.py::TestTopologyRetryLedger::test_retry_budget_is_persisted_and_enforced_by_backend_tool | willy.topology 后端与产物契约 / TestTopologyRetryLedger | 验证 `retry_budget_is_persisted_and_enforced_by_backend_tool` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T769 | tests/test_topology_contract.py::TestTopologyRetryLedger::test_total_retry_budget_rejects_fifth_claim | willy.topology 后端与产物契约 / TestTopologyRetryLedger | 验证 `total_retry_budget_rejects_fifth_claim` 行为 | 进程内行为断言 |
| T770 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_successful_assembly_retry_keeps_atomtypes | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `successful_assembly_retry_keeps_atomtypes` 行为 | tmp_path 隔离工作区 |
| T771 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_assembly_namespace_does_not_overwrite_similarly_named_source | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `assembly_namespace_does_not_overwrite_similarly_named_source` 行为 | tmp_path 隔离工作区 |
| T772 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_missing_atomtypes_prevents_assembly | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `missing_atomtypes_prevents_assembly` 行为 | tmp_path 隔离工作区 |
| T773 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_undefined_atomtype_reference_prevents_assembly | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `undefined_atomtype_reference_prevents_assembly` 行为 | tmp_path 隔离工作区 |
| T774 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_moleculetype_must_match_manifest_residue | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `moleculetype_must_match_manifest_residue` 行为 | tmp_path 隔离工作区 |
| T775 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_missing_sections_and_atom_count_mismatch_fail | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `missing_sections_and_atom_count_mismatch_fail` 行为 | tmp_path 隔离工作区 |
| T776 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_validate_moleculetype_against_expected_residue | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `validate_moleculetype_against_expected_residue` 行为 | tmp_path 隔离工作区 |
| T777 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_manifest_missing_gro_prevents_assembly | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `manifest_missing_gro_prevents_assembly` 行为 | tmp_path 隔离工作区 |
| T778 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_manifest_artifact_tampering_prevents_assembly | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `manifest_artifact_tampering_prevents_assembly` 行为 | tmp_path 隔离工作区 |
| T779 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_gaff_uff_components_assemble_but_cross_family_is_rejected | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `gaff_uff_components_assemble_but_cross_family_is_rejected` 行为 | tmp_path 隔离工作区 |
| T780 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_atomtype_parameter_conflict_is_fatal | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `atomtype_parameter_conflict_is_fatal` 行为 | tmp_path 隔离工作区 |
| T781 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_itp_revision_is_section_aware_and_idempotent | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `itp_revision_is_section_aware_and_idempotent` 行为 | tmp_path 隔离工作区 |
| T782 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_atomtype_conflict_escalates_without_llm_retry | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `atomtype_conflict_escalates_without_llm_retry` 行为 | mock/patch |

## G. 模拟执行、ETA 与后处理

EM/EQ/PROD 协议、GROMACS 适配、ETA、阶段产物和分析流程。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T348 | tests/test_mdrun_eta.py::test_verbose_carriage_return_output_updates_eta_snapshot | willy.simulation.mdrun_eta | 验证 `verbose_carriage_return_output_updates_eta_snapshot` 行为 | tmp_path 隔离工作区 |
| T349 | tests/test_mdrun_eta.py::test_eta_snapshot_stays_waiting_without_a_gromacs_prediction | willy.simulation.mdrun_eta | 验证 `eta_snapshot_stays_waiting_without_a_gromacs_prediction` 行为 | tmp_path 隔离工作区 |
| T350 | tests/test_mdrun_eta.py::test_heartbeat_refreshes_liveness_and_reads_gromacs_2025_log_progress | willy.simulation.mdrun_eta | 验证 `heartbeat_refreshes_liveness_and_reads_gromacs_2025_log_progress` 行为 | tmp_path 隔离工作区 |
| T351 | tests/test_mdrun_eta.py::test_gromacs_duration_variant_updates_eta_without_deriving_from_step_rate | willy.simulation.mdrun_eta | 验证 `gromacs_duration_variant_updates_eta_without_deriving_from_step_rate` 行为 | tmp_path 隔离工作区 |
| T352 | tests/test_mdrun_eta.py::test_gromacs_2025_will_finish_eta_is_parsed_from_chunked_verbose_output | willy.simulation.mdrun_eta | 验证：GROMACS 2025 prints an absolute ctime ETA after PME tuning. | tmp_path 隔离工作区 + monkeypatch |
| T353 | tests/test_mdrun_eta.py::test_eta_snapshot_closes_when_mdrun_ends | willy.simulation.mdrun_eta | 验证 `eta_snapshot_closes_when_mdrun_ends` 行为 | tmp_path 隔离工作区 |
| T354 | tests/test_mdrun_knowledge.py::test_index_and_lookup_require_matching_number_and_name | willy.simulation.mdrun_knowledge / SimulationAgent knowledge boundary | 验证 `index_and_lookup_require_matching_number_and_name` 行为 | 进程内行为断言 |
| T355 | tests/test_mdrun_knowledge.py::test_tool_handler_is_read_only_and_bounded | willy.simulation.mdrun_knowledge / SimulationAgent knowledge boundary | 验证 `tool_handler_is_read_only_and_bounded` 行为 | 进程内行为断言 |
| T356 | tests/test_mdrun_knowledge.py::test_eq_proposal_uses_only_kb_tool_and_two_lookup_calls | willy.simulation.mdrun_knowledge / SimulationAgent knowledge boundary | 验证 `eq_proposal_uses_only_kb_tool_and_two_lookup_calls` 行为 | tmp_path 隔离工作区 + mock/patch |
| T357 | tests/test_mdrun_knowledge.py::test_pending_action_exposes_verified_or_unverified_source | willy.simulation.mdrun_knowledge / SimulationAgent knowledge boundary | 验证 `pending_action_exposes_verified_or_unverified_source` 行为 | tmp_path 隔离工作区 |
| T358 | tests/test_mdrun_knowledge.py::test_ui_labels_unverified_advice_source | willy.simulation.mdrun_knowledge / SimulationAgent knowledge boundary | 验证 `ui_labels_unverified_advice_source` 行为 | 进程内行为断言 |
| T484 | tests/test_postprocess.py::test_postprocess_generates_traceable_analysis_outputs | willy.simulation.postprocess | 验证 `postprocess_generates_traceable_analysis_outputs` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T485 | tests/test_postprocess.py::test_postprocess_uses_system_for_pbc_and_requested_group_for_msd | willy.simulation.postprocess | 验证 `postprocess_uses_system_for_pbc_and_requested_group_for_msd` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T486 | tests/test_postprocess.py::test_postprocess_rejects_incomplete_trajectory | willy.simulation.postprocess | 验证 `postprocess_rejects_incomplete_trajectory` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T487 | tests/test_postprocess.py::test_postprocess_refuses_to_overwrite_existing_analysis | willy.simulation.postprocess | 验证 `postprocess_refuses_to_overwrite_existing_analysis` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T488 | tests/test_postprocess.py::test_postprocess_rejects_invalid_discard_fraction | willy.simulation.postprocess | 验证 `postprocess_rejects_invalid_discard_fraction` 行为 | tmp_path 隔离工作区 |
| T489 | tests/test_postprocess.py::test_postprocess_reports_missing_production_artifacts | willy.simulation.postprocess | 验证 `postprocess_reports_missing_production_artifacts` 行为 | tmp_path 隔离工作区 |
| T490 | tests/test_postprocess.py::test_postprocess_rejects_sources_without_completed_prod_manifest | willy.simulation.postprocess | 验证 `postprocess_rejects_sources_without_completed_prod_manifest` 行为 | tmp_path 隔离工作区 |
| T491 | tests/test_postprocess.py::test_postprocess_rejects_artifact_fingerprint_mismatch | willy.simulation.postprocess | 验证 `postprocess_rejects_artifact_fingerprint_mismatch` 行为 | tmp_path 隔离工作区 |
| T572 | tests/test_simulation_execution.py::test_grompp_and_mdrun_returns_required_stage_artifacts | willy.simulation 执行适配层 | 验证 `grompp_and_mdrun_returns_required_stage_artifacts` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T573 | tests/test_simulation_execution.py::test_grompp_allows_only_small_single_net_charge_warning | willy.simulation 执行适配层 | 验证 `grompp_allows_only_small_single_net_charge_warning` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T574 | tests/test_simulation_execution.py::test_grompp_charge_warning_above_tolerance_remains_fatal | willy.simulation 执行适配层 | 验证 `grompp_charge_warning_above_tolerance_remains_fatal` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T575 | tests/test_simulation_execution.py::test_em_result_preserves_grompp_warning_policy_evidence | willy.simulation 执行适配层 | 验证 `em_result_preserves_grompp_warning_policy_evidence` 行为 | 进程内行为断言 |
| T576 | tests/test_simulation_execution.py::test_grompp_and_mdrun_uses_run_config_cpu_default | willy.simulation 执行适配层 | 验证 `grompp_and_mdrun_uses_run_config_cpu_default` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T577 | tests/test_simulation_execution.py::test_gromacs_reports_preprocess_and_run_as_public_activities | willy.simulation 执行适配层 | 验证 `gromacs_reports_preprocess_and_run_as_public_activities` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T578 | tests/test_simulation_execution.py::test_gromacs_routes_liveness_heartbeats_without_redefining_activity | willy.simulation 执行适配层 | 验证 `gromacs_routes_liveness_heartbeats_without_redefining_activity` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T579 | tests/test_simulation_execution.py::test_mdp_reports_each_stage_as_public_progress | willy.simulation 执行适配层 | 验证 `mdp_reports_each_stage_as_public_progress` 行为 | tmp_path 隔离工作区 |
| T580 | tests/test_simulation_execution.py::test_grompp_and_mdrun_passes_custom_tpr_to_mdrun | willy.simulation 执行适配层 | 验证 `grompp_and_mdrun_passes_custom_tpr_to_mdrun` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T581 | tests/test_simulation_execution.py::test_grompp_and_mdrun_rejects_missing_required_input | willy.simulation 执行适配层 | 验证 `grompp_and_mdrun_rejects_missing_required_input` 行为 | tmp_path 隔离工作区 |
| T582 | tests/test_simulation_execution.py::test_grompp_and_mdrun_rejects_success_without_required_output | willy.simulation 执行适配层 | 验证 `grompp_and_mdrun_rejects_success_without_required_output` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T583 | tests/test_simulation_execution.py::test_mdrun_failure_has_private_process_evidence_and_mdrun_kind | willy.simulation 执行适配层 | 验证 `mdrun_failure_has_private_process_evidence_and_mdrun_kind` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T584 | tests/test_simulation_execution.py::test_detect_vacuum_region_from_final_eq_structure | willy.simulation 执行适配层 | 验证 `detect_vacuum_region_from_final_eq_structure` 行为 | tmp_path 隔离工作区 |
| T585 | tests/test_simulation_execution.py::test_eq_acceptance_uses_temperature_and_potential_slope_only | willy.simulation 执行适配层 | 验证 `eq_acceptance_uses_temperature_and_potential_slope_only` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T586 | tests/test_simulation_execution.py::test_eq_acceptance_rejects_excessive_potential_slope | willy.simulation 执行适配层 | 验证 `eq_acceptance_rejects_excessive_potential_slope` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T587 | tests/test_simulation_execution.py::test_eq_vacuum_and_density_are_observations_not_blockers | willy.simulation 执行适配层 | 验证 `eq_vacuum_and_density_are_observations_not_blockers` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T588 | tests/test_simulation_protocol.py::test_default_eq_mdp_has_six_segment_cumulative_points_and_actual_time | willy.simulation.protocol / manifest | 验证 `default_eq_mdp_has_six_segment_cumulative_points_and_actual_time` 行为 | tmp_path 隔离工作区 |
| T589 | tests/test_simulation_protocol.py::test_run_mdp_metadata_is_embedded_in_md_manifest | willy.simulation.protocol / manifest | 验证 `run_mdp_metadata_is_embedded_in_md_manifest` 行为 | tmp_path 隔离工作区 |
| T590 | tests/test_simulation_protocol.py::test_unified_run_manifest_keeps_md_evidence_and_protocol_in_private_sections | willy.simulation.protocol / manifest | 验证 `unified_run_manifest_keeps_md_evidence_and_protocol_in_private_sections` 行为 | tmp_path 隔离工作区 |
| T591 | tests/test_simulation_protocol.py::test_partial_mdp_regeneration_preserves_other_stage_metadata | willy.simulation.protocol / manifest | 验证 `partial_mdp_regeneration_preserves_other_stage_metadata` 行为 | tmp_path 隔离工作区 |
| T592 | tests/test_simulation_protocol.py::test_eq_duration_boundaries_are_valid[7.0] | willy.simulation.protocol / manifest | 验证 `eq_duration_boundaries_are_valid` 行为；参数集 `7.0` | pytest 参数化 |
| T593 | tests/test_simulation_protocol.py::test_eq_duration_boundaries_are_valid[100.0] | willy.simulation.protocol / manifest | 验证 `eq_duration_boundaries_are_valid` 行为；参数集 `100.0` | pytest 参数化 |
| T594 | tests/test_simulation_protocol.py::test_eq_duration_outside_boundaries_is_rejected[6.999] | willy.simulation.protocol / manifest | 验证 `eq_duration_outside_boundaries_is_rejected` 行为；参数集 `6.999` | pytest 参数化 |
| T595 | tests/test_simulation_protocol.py::test_eq_duration_outside_boundaries_is_rejected[100.001] | willy.simulation.protocol / manifest | 验证 `eq_duration_outside_boundaries_is_rejected` 行为；参数集 `100.001` | pytest 参数化 |
| T596 | tests/test_simulation_protocol.py::test_prod_duration_boundaries_are_valid[2.0] | willy.simulation.protocol / manifest | 验证 `prod_duration_boundaries_are_valid` 行为；参数集 `2.0` | pytest 参数化 |
| T597 | tests/test_simulation_protocol.py::test_prod_duration_boundaries_are_valid[200.0] | willy.simulation.protocol / manifest | 验证 `prod_duration_boundaries_are_valid` 行为；参数集 `200.0` | pytest 参数化 |
| T598 | tests/test_simulation_protocol.py::test_prod_duration_outside_boundaries_is_rejected[1.999] | willy.simulation.protocol / manifest | 验证 `prod_duration_outside_boundaries_is_rejected` 行为；参数集 `1.999` | pytest 参数化 |
| T599 | tests/test_simulation_protocol.py::test_prod_duration_outside_boundaries_is_rejected[200.001] | willy.simulation.protocol / manifest | 验证 `prod_duration_outside_boundaries_is_rejected` 行为；参数集 `200.001` | pytest 参数化 |
| T600 | tests/test_simulation_protocol.py::test_short_final_hold_warns_and_disables_auto_acceptance | willy.simulation.protocol / manifest | 验证 `short_final_hold_warns_and_disables_auto_acceptance` 行为 | tmp_path 隔离工作区 |
| T601 | tests/test_simulation_protocol.py::test_potential_slope_acceptance_threshold_is_validated | willy.simulation.protocol / manifest | 验证 `potential_slope_acceptance_threshold_is_validated` 行为 | 进程内行为断言 |
| T602 | tests/test_simulation_protocol.py::test_eq_cannot_run_without_accepted_em | willy.simulation.protocol / manifest | 验证 `eq_cannot_run_without_accepted_em` 行为 | tmp_path 隔离工作区 |
| T603 | tests/test_simulation_protocol.py::test_prod_requires_the_accepted_eq_checkpoint | willy.simulation.protocol / manifest | 验证 `prod_requires_the_accepted_eq_checkpoint` 行为 | tmp_path 隔离工作区 |
| T604 | tests/test_simulation_protocol.py::test_stage_manifest_keeps_mdrun_evidence_private_to_the_run | willy.simulation.protocol / manifest | 验证 `stage_manifest_keeps_mdrun_evidence_private_to_the_run` 行为 | tmp_path 隔离工作区 |
| T605 | tests/test_simulation_protocol.py::test_prod_append_requires_matching_manifest_contract_and_checkpoint | willy.simulation.protocol / manifest | 验证 `prod_append_requires_matching_manifest_contract_and_checkpoint` 行为 | tmp_path 隔离工作区 |
| T606 | tests/test_simulation_protocol.py::test_upstream_protocol_change_invalidates_downstream_acceptance | willy.simulation.protocol / manifest | 验证 `upstream_protocol_change_invalidates_downstream_acceptance` 行为 | tmp_path 隔离工作区 |
| T607 | tests/test_simulation_protocol.py::test_config_edit_invalidates_changed_stage_and_all_downstream | willy.simulation.protocol / manifest | 验证 `config_edit_invalidates_changed_stage_and_all_downstream` 行为 | tmp_path 隔离工作区 |
| T608 | tests/test_simulation_protocol.py::test_manifest_archives_each_config_revision_used_by_a_stage | willy.simulation.protocol / manifest | 验证 `manifest_archives_each_config_revision_used_by_a_stage` 行为 | tmp_path 隔离工作区 |
| T609 | tests/test_simulation_protocol.py::test_packing_number_density_changes_packmol_box_size_and_input | willy.simulation.protocol / manifest | 验证 `packing_number_density_changes_packmol_box_size_and_input` 行为 | 进程内行为断言 |
| T610 | tests/test_simulation_protocol.py::test_mass_density_box_uses_topology_mass_and_explicit_pbc | willy.simulation.protocol / manifest | 验证 `mass_density_box_uses_topology_mass_and_explicit_pbc` 行为 | 进程内行为断言 |
| T611 | tests/test_simulation_protocol.py::test_auto_box_config_derives_mass_from_run_local_itp | willy.simulation.protocol / manifest | 验证 `auto_box_config_derives_mass_from_run_local_itp` 行为 | tmp_path 隔离工作区 |
| T612 | tests/test_simulation_protocol.py::test_packmol_uses_seekable_input_file | willy.simulation.protocol / manifest | 验证 `packmol_uses_seekable_input_file` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T613 | tests/test_simulation_protocol.py::test_packmol_failure_removes_stale_output_and_classifies_process_exit | willy.simulation.protocol / manifest | 验证 `packmol_failure_removes_stale_output_and_classifies_process_exit` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T614 | tests/test_simulation_protocol.py::test_packmol_runtime_preflight_blocks_loader_incompatibility | willy.simulation.protocol / manifest | 验证 `packmol_runtime_preflight_blocks_loader_incompatibility` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T615 | tests/test_simulation_protocol.py::test_box_preflight_reports_missing_inputs_as_structured_evidence | willy.simulation.protocol / manifest | 验证 `box_preflight_reports_missing_inputs_as_structured_evidence` 行为 | tmp_path 隔离工作区 |
| T616 | tests/test_simulation_protocol.py::test_grompp_default_does_not_force_warning_bypass | willy.simulation.protocol / manifest | 验证 `grompp_default_does_not_force_warning_bypass` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T617 | tests/test_simulation_protocol.py::test_em_zero_step_convergence_gets_a_one_frame_xtc | willy.simulation.protocol / manifest | 验证 `em_zero_step_convergence_gets_a_one_frame_xtc` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T618 | tests/test_simulation_protocol.py::test_prod_end_time_prefers_physical_progress_over_wall_clock | willy.simulation.protocol / manifest | 验证 `prod_end_time_prefers_physical_progress_over_wall_clock` 行为 | tmp_path 隔离工作区 |
| T619 | tests/test_simulation_protocol.py::test_prod_frame_count_uses_gmx_check_summary | willy.simulation.protocol / manifest | 验证 `prod_frame_count_uses_gmx_check_summary` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T620 | tests/test_stage_visualization.py::test_stage_visualization_converts_gro_after_acceptance | willy.simulation.visualization | 验证 `stage_visualization_converts_gro_after_acceptance` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T621 | tests/test_stage_visualization.py::test_stale_conversion_cannot_publish_after_stage_source_changes | willy.simulation.visualization | 验证 `stale_conversion_cannot_publish_after_stage_source_changes` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T622 | tests/test_stage_visualization.py::test_schedule_runs_a_daemon_conversion_without_blocking_pipeline | willy.simulation.visualization | 验证 `schedule_runs_a_daemon_conversion_without_blocking_pipeline` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T623 | tests/test_stage_visualization.py::test_stage_regeneration_removes_its_derived_viewer_artifact | willy.simulation.visualization | 验证 `stage_regeneration_removes_its_derived_viewer_artifact` 行为 | tmp_path 隔离工作区 |
| T624 | tests/test_stage_visualization.py::test_orchestrator_schedules_only_md_stage_visualizations | willy.simulation.visualization | 验证 `orchestrator_schedules_only_md_stage_visualizations` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T625 | tests/test_stage_visualization.py::test_prod_visualization_is_a_finalization_contract | willy.simulation.visualization | 验证 `prod_visualization_is_a_finalization_contract` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T626 | tests/test_stage_visualization.py::test_prod_visualization_failure_blocks_terminal_completion | willy.simulation.visualization | 验证 `prod_visualization_failure_blocks_terminal_completion` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T627 | tests/test_stage_visualization.py::test_orchestrator_does_not_enter_done_without_prod_pdb | willy.simulation.visualization | 验证 `orchestrator_does_not_enter_done_without_prod_pdb` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T628 | tests/test_stage_visualization.py::test_prod_final_cleanup_removes_only_requested_intermediates | willy.simulation.visualization | 验证 `prod_final_cleanup_removes_only_requested_intermediates` 行为 | tmp_path 隔离工作区 |
| T629 | tests/test_stage_visualization.py::test_prod_final_cleanup_rejects_unsafe_molecule_names | willy.simulation.visualization | 验证 `prod_final_cleanup_rejects_unsafe_molecule_names` 行为 | tmp_path 隔离工作区 |
| T707 | tests/test_toolist_simulation.py::TestSimulationToolDefinitions::test_fourteen_tools_defined | willy.toolist_simulation / TestSimulationToolDefinitions | 验证：应有 14 个工具定义，不暴露不完整的跳过分子功能 | 进程内行为断言 |
| T708 | tests/test_toolist_simulation.py::TestSimulationToolDefinitions::test_all_tool_names_present | willy.toolist_simulation / TestSimulationToolDefinitions | 验证：所有必需工具应存在 | 进程内行为断言 |
| T709 | tests/test_toolist_simulation.py::TestSimulationToolDefinitions::test_tool_meta_covers_all_tools | willy.toolist_simulation / TestSimulationToolDefinitions | 验证：TOOL_META 应覆盖所有已定义工具 | 进程内行为断言 |
| T710 | tests/test_toolist_simulation.py::TestSimulationToolDefinitions::test_skip_molecule_is_not_exposed | willy.toolist_simulation / TestSimulationToolDefinitions | 验证 `skip_molecule_is_not_exposed` 行为 | 进程内行为断言 |
| T711 | tests/test_toolist_simulation.py::TestSimulationToolDefinitions::test_eq_schema_exposes_all_three_annealing_temperatures | willy.toolist_simulation / TestSimulationToolDefinitions | 验证 `eq_schema_exposes_all_three_annealing_temperatures` 行为 | 进程内行为断言 |
| T712 | tests/test_toolist_simulation.py::TestSimulationToolDefinitions::test_gromacs_run_tools_expose_file_contract | willy.toolist_simulation / TestSimulationToolDefinitions | 验证 `gromacs_run_tools_expose_file_contract` 行为 | 进程内行为断言 |
| T713 | tests/test_toolist_simulation.py::TestSimulationToolDefinitions::test_protocol_tools_do_not_expose_model_confirmed_flag | willy.toolist_simulation / TestSimulationToolDefinitions | 验证：User approval is server-side, never a tool argument the model can forge. | 进程内行为断言 |
| T714 | tests/test_toolist_simulation.py::TestHandleSimulationToolCall::test_unknown_tool_returns_error | willy.toolist_simulation / TestHandleSimulationToolCall | 验证 `unknown_tool_returns_error` 行为 | 进程内行为断言 |
| T715 | tests/test_toolist_simulation.py::TestHandleSimulationToolCall::test_step_to_dict_format | willy.toolist_simulation / TestHandleSimulationToolCall | 验证：StepResult.to_dict 应包含 _step_result 标记 | 进程内行为断言 |
| T716 | tests/test_toolist_simulation.py::TestHandleSimulationToolCall::test_migration_adoption_requires_confirmation_and_switches_active_config | willy.toolist_simulation / TestHandleSimulationToolCall | 验证 `migration_adoption_requires_confirmation_and_switches_active_config` 行为 | tmp_path 隔离工作区 |
| T717 | tests/test_toolist_simulation.py::TestHandleSimulationToolCall::test_model_supplied_confirmed_flag_cannot_authorize_protocol_change | willy.toolist_simulation / TestHandleSimulationToolCall | 验证：Even an explicit LLM ``confirmed=true`` must leave config untouched. | 进程内行为断言 |
| T718 | tests/test_toolist_simulation.py::TestRetryProd::test_run_prod_accepts_extra_mdrun | willy.toolist_simulation / TestRetryProd | 验证 `run_prod_accepts_extra_mdrun` 行为 | 进程内行为断言 |
| T719 | tests/test_toolist_simulation.py::TestRetryProd::test_grompp_and_mdrun_does_accept_extra_mdrun | willy.toolist_simulation / TestRetryProd | 验证：用于对比：grompp_and_mdrun 确实接受 extra_mdrun | 进程内行为断言 |
| T720 | tests/test_toolist_simulation.py::TestRetryProd::test_handler_does_not_inject_unverified_restart_flags | willy.toolist_simulation / TestRetryProd | 验证 `handler_does_not_inject_unverified_restart_flags` 行为 | tmp_path 隔离工作区 + mock/patch |
| T721 | tests/test_toolist_simulation.py::TestRunGromacsTools::test_run_em_uses_bound_workspace | willy.toolist_simulation / TestRunGromacsTools | 验证 `run_em_uses_bound_workspace` 行为 | tmp_path 隔离工作区 + mock/patch |
| T722 | tests/test_toolist_simulation.py::TestRunGromacsTools::test_run_tool_rejects_path_outside_workspace | willy.toolist_simulation / TestRunGromacsTools | 验证 `run_tool_rejects_path_outside_workspace` 行为 | tmp_path 隔离工作区 |
| T723 | tests/test_toolist_simulation.py::TestRetryMdp::test_stage_is_forwarded_to_mdp_builder | willy.toolist_simulation / TestRetryMdp | 验证 `stage_is_forwarded_to_mdp_builder` 行为 | tmp_path 隔离工作区 + mock/patch |
| T724 | tests/test_toolist_simulation.py::TestRetryMdp::test_all_stage_requests_all_mdp_files | willy.toolist_simulation / TestRetryMdp | 验证 `all_stage_requests_all_mdp_files` 行为 | tmp_path 隔离工作区 + mock/patch |
| T725 | tests/test_toolist_simulation.py::TestRetryMdp::test_eq_change_rebuilds_eq_and_prod | willy.toolist_simulation / TestRetryMdp | 验证 `eq_change_rebuilds_eq_and_prod` 行为 | tmp_path 隔离工作区 + mock/patch |
| T726 | tests/test_toolist_simulation.py::TestRetryMdp::test_build_all_signature_accepts_overrides | willy.toolist_simulation / TestRetryMdp | 验证：build_all 按关键字接受 overrides 参数 | 进程内行为断言 |
| T727 | tests/test_toolist_simulation.py::TestRetryMdp::test_keyword_override_call_is_correct | willy.toolist_simulation / TestRetryMdp | 验证：build_all(overrides=overrides) 在 Python 中是合法的， 因为前两个参数有默认值 | 进程内行为断言 |
| T728 | tests/test_toolist_simulation.py::TestModifyConfigSimulation::test_writes_to_config_json | willy.toolist_simulation / TestModifyConfigSimulation | 验证：应更新 config.json 中的 md 字段 | 进程内行为断言 |
| T729 | tests/test_toolist_simulation.py::TestModifyConfigSimulation::test_reports_config_delta_after_the_run_snapshot_is_updated | willy.toolist_simulation / TestModifyConfigSimulation | 验证 `reports_config_delta_after_the_run_snapshot_is_updated` 行为 | 进程内行为断言 |
| T730 | tests/test_toolist_simulation.py::TestModifyConfigSimulation::test_missing_config_json | willy.toolist_simulation / TestModifyConfigSimulation | 验证：config.json 不存在时应有错误 | tmp_path 隔离工作区 |
| T731 | tests/test_toolist_simulation.py::TestModifyConfigSimulation::test_preserves_non_md_fields | willy.toolist_simulation / TestModifyConfigSimulation | 验证：修改 MD 字段不应影响其他 config 段 | 进程内行为断言 |
| T732 | tests/test_toolist_simulation.py::test_removed_skip_handler_does_not_mutate_configuration | willy.toolist_simulation | 验证 `removed_skip_handler_does_not_mutate_configuration` 行为 | 进程内行为断言 |
| T733 | tests/test_toolist_simulation.py::TestDiagnoseErrorSimulation::test_returns_diagnosis_result_format | willy.toolist_simulation / TestDiagnoseErrorSimulation | 验证：结果应包含 _diagnosis=True 标记 | 进程内行为断言 |
| T734 | tests/test_toolist_simulation.py::TestDiagnoseErrorSimulation::test_analyzes_grompp_stderr_for_atomtype | willy.toolist_simulation / TestDiagnoseErrorSimulation | 验证：应从 grompp stderr 中检测 atomtype 问题 | 进程内行为断言 |
| T735 | tests/test_toolist_simulation.py::TestDiagnoseErrorSimulation::test_analyzes_mdrun_stderr_for_nan | willy.toolist_simulation / TestDiagnoseErrorSimulation | 验证：应从 mdrun stderr 中检测 NaN | 进程内行为断言 |
| T736 | tests/test_toolist_simulation.py::TestStepResultSerialization::test_success_result_format | willy.toolist_simulation / TestStepResultSerialization | 验证 `success_result_format` 行为 | 进程内行为断言 |
| T737 | tests/test_toolist_simulation.py::TestStepResultSerialization::test_failure_result_format | willy.toolist_simulation / TestStepResultSerialization | 验证 `failure_result_format` 行为 | 进程内行为断言 |

## H. 流水线编排与状态机

步骤构建、唯一步骤注册、失败修复、run workspace、公开状态和恢复边界。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T359 | tests/test_multi_factor_pending_action.py::test_multi_factor_action_requires_selection_and_applies_only_selected_option | willy.simulation.pending_action multi-factor confirmation | 验证 `multi_factor_action_requires_selection_and_applies_only_selected_option` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T360 | tests/test_multi_factor_pending_action.py::test_frontend_option_selection_keeps_status_waiting_and_config_unchanged | willy.simulation.pending_action multi-factor confirmation | 验证 `frontend_option_selection_keeps_status_waiting_and_config_unchanged` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T361 | tests/test_multi_factor_pending_action.py::test_text_option_parser_accepts_selection_and_confirmation_variants | willy.simulation.pending_action multi-factor confirmation | 验证 `text_option_parser_accepts_selection_and_confirmation_variants` 行为 | 进程内行为断言 |
| T370 | tests/test_pending_action.py::test_pending_action_does_not_change_config_until_applied | willy.simulation.pending_action | 验证 `pending_action_does_not_change_config_until_applied` 行为 | tmp_path 隔离工作区 |
| T371 | tests/test_pending_action.py::test_pending_action_rejects_changed_config_before_launch | willy.simulation.pending_action | 验证 `pending_action_rejects_changed_config_before_launch` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T372 | tests/test_pending_action.py::test_pending_action_applies_only_its_validated_fields | willy.simulation.pending_action | 验证 `pending_action_applies_only_its_validated_fields` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T373 | tests/test_pending_action.py::test_pending_action_normalizes_eq_pressure_and_hold_aliases | willy.simulation.pending_action | 验证 `pending_action_normalizes_eq_pressure_and_hold_aliases` 行为 | tmp_path 隔离工作区 |
| T374 | tests/test_pending_action.py::test_pending_action_reports_unsupported_replacement_field | willy.simulation.pending_action | 验证 `pending_action_reports_unsupported_replacement_field` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T375 | tests/test_pending_action.py::test_replacing_pending_action_preserves_config_until_new_confirmation | willy.simulation.pending_action | 验证 `replacing_pending_action_preserves_config_until_new_confirmation` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T376 | tests/test_pending_action.py::test_vacuum_recovery_requires_box_rebuild_from_step_seven | willy.simulation.pending_action | 验证 `vacuum_recovery_requires_box_rebuild_from_step_seven` 行为 | tmp_path 隔离工作区 |
| T377 | tests/test_pending_action.py::test_public_pending_action_exposes_only_bounded_editable_parameters | willy.simulation.pending_action | 验证 `public_pending_action_exposes_only_bounded_editable_parameters` 行为 | tmp_path 隔离工作区 |
| T387 | tests/test_pipeline_orchestrator.py::test_cli_accepts_g09_backend | willy.pipeline_orchestrator | 验证 `cli_accepts_g09_backend` 行为 | 进程内行为断言 |
| T388 | tests/test_pipeline_orchestrator.py::TestStepRegistryLayerMapping::test_step_layer_mapping_is_complete | willy.pipeline_orchestrator / TestStepRegistryLayerMapping | 验证：步骤 1-10 应全部映射 | 进程内行为断言 |
| T389 | tests/test_pipeline_orchestrator.py::TestStepRegistryLayerMapping::test_step_layers_correct | willy.pipeline_orchestrator / TestStepRegistryLayerMapping | 验证：各步骤的层映射应正确 | 进程内行为断言 |
| T390 | tests/test_pipeline_orchestrator.py::TestStepRegistryLayerMapping::test_no_steps_beyond_10 | willy.pipeline_orchestrator / TestStepRegistryLayerMapping | 验证：步骤 11+ 尚未定义 | 异常断言 |
| T391 | tests/test_pipeline_orchestrator.py::TestPipelineOrchestratorInit::test_no_api_key_disables_llm | willy.pipeline_orchestrator / TestPipelineOrchestratorInit | 验证：没有任何 LLM API Key 时，应禁用 LLM | monkeypatch |
| T392 | tests/test_pipeline_orchestrator.py::TestPipelineOrchestratorInit::test_use_llm_false_skips_init | willy.pipeline_orchestrator / TestPipelineOrchestratorInit | 验证：use_llm=False 时不应初始化 LLM 客户端 | 进程内行为断言 |
| T393 | tests/test_pipeline_orchestrator.py::TestPipelineOrchestratorInit::test_backend_stored | willy.pipeline_orchestrator / TestPipelineOrchestratorInit | 验证 `backend_stored` 行为 | 进程内行为断言 |
| T394 | tests/test_pipeline_orchestrator.py::TestPipelineOrchestratorInit::test_g09_backend_is_a_supported_distinct_backend | willy.pipeline_orchestrator / TestPipelineOrchestratorInit | 验证 `g09_backend_is_a_supported_distinct_backend` 行为 | 进程内行为断言 |
| T395 | tests/test_pipeline_orchestrator.py::TestPipelineOrchestratorInit::test_unknown_quantum_backend_is_rejected | willy.pipeline_orchestrator / TestPipelineOrchestratorInit | 验证 `unknown_quantum_backend_is_rejected` 行为 | 异常断言 |
| T396 | tests/test_pipeline_orchestrator.py::TestPipelineOrchestratorInit::test_configured_model_is_injected_into_all_repair_agents | willy.pipeline_orchestrator / TestPipelineOrchestratorInit | 验证 `configured_model_is_injected_into_all_repair_agents` 行为 | monkeypatch + mock/patch |
| T397 | tests/test_pipeline_orchestrator.py::TestPipelineOrchestratorInit::test_new_run_never_inherits_previous_done_steps | willy.pipeline_orchestrator / TestPipelineOrchestratorInit | 验证：A fresh launch must not infer a resume from the global status file. | tmp_path 隔离工作区 + monkeypatch |
| T398 | tests/test_pipeline_orchestrator.py::TestPipelineOrchestratorInit::test_explicit_resume_requires_the_original_run_directory | willy.pipeline_orchestrator / TestPipelineOrchestratorInit | 验证 `explicit_resume_requires_the_original_run_directory` 行为 | tmp_path 隔离工作区 + monkeypatch + 异常断言 |
| T399 | tests/test_pipeline_orchestrator.py::TestPublicRepairUpdates::test_simulation_config_delta_is_labeled_and_unitized | willy.pipeline_orchestrator / TestPublicRepairUpdates | 验证 `simulation_config_delta_is_labeled_and_unitized` 行为 | 进程内行为断言 |
| T400 | tests/test_pipeline_orchestrator.py::TestBuildSteps::test_g16_backend_builds_10_steps | willy.pipeline_orchestrator / TestBuildSteps | 验证 `g16_backend_builds_10_steps` 行为 | 进程内行为断言 |
| T401 | tests/test_pipeline_orchestrator.py::TestBuildSteps::test_orca_backend_builds_10_steps | willy.pipeline_orchestrator / TestBuildSteps | 验证 `orca_backend_builds_10_steps` 行为 | 进程内行为断言 |
| T402 | tests/test_pipeline_orchestrator.py::TestBuildSteps::test_g09_backend_builds_its_own_quantum_modules | willy.pipeline_orchestrator / TestBuildSteps | 验证 `g09_backend_builds_its_own_quantum_modules` 行为 | 进程内行为断言 |
| T403 | tests/test_pipeline_orchestrator.py::TestBuildSteps::test_final_three_steps_are_gromacs_execution | willy.pipeline_orchestrator / TestBuildSteps | 验证 `final_three_steps_are_gromacs_execution` 行为 | 进程内行为断言 |
| T404 | tests/test_pipeline_orchestrator.py::TestBuildSteps::test_step_structure | willy.pipeline_orchestrator / TestBuildSteps | 验证：每个步骤应为 5 元组: (label, func, dep_module, is_batch, layer_index) | 进程内行为断言 |
| T405 | tests/test_pipeline_orchestrator.py::TestBuildSteps::test_step_1_is_batch | willy.pipeline_orchestrator / TestBuildSteps | 验证：第一步（结构优化）应是批量的 | 进程内行为断言 |
| T406 | tests/test_pipeline_orchestrator.py::TestBuildSteps::test_step_5_is_not_batch | willy.pipeline_orchestrator / TestBuildSteps | 验证：第五步（主拓扑）不应是批量的 | 进程内行为断言 |
| T407 | tests/test_pipeline_orchestrator.py::TestBuildSteps::test_all_pipeline_steps_receive_the_same_run_directory | willy.pipeline_orchestrator / TestBuildSteps | 验证：正常流水线不得回退到项目根目录的共享输入或拓扑目录 | tmp_path 隔离工作区 + monkeypatch + mock/patch |
| T408 | tests/test_pipeline_orchestrator.py::TestPublicQuantumProgress::test_g09_progress_callbacks_are_labeled_g09 | willy.pipeline_orchestrator / TestPublicQuantumProgress | 验证 `g09_progress_callbacks_are_labeled_g09` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T409 | tests/test_pipeline_orchestrator.py::TestPublicQuantumProgress::test_g16_orca_resp_and_step2_callbacks_are_structured | willy.pipeline_orchestrator / TestPublicQuantumProgress | 验证 `g16_orca_resp_and_step2_callbacks_are_structured` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T410 | tests/test_pipeline_orchestrator.py::TestPublicQuantumProgress::test_resp_rejects_an_incomplete_configured_molecule_set | willy.pipeline_orchestrator / TestPublicQuantumProgress | 验证：Step 3 must not silently accept 4/5 configured molecules. | tmp_path 隔离工作区 |
| T411 | tests/test_pipeline_orchestrator.py::TestRunWorkspace::test_prepare_workspace_copies_precomputed_g16_input | willy.pipeline_orchestrator / TestRunWorkspace | 验证 `prepare_workspace_copies_precomputed_g16_input` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T412 | tests/test_pipeline_orchestrator.py::TestRunWorkspace::test_prepare_workspace_accepts_gjf_without_fchk | willy.pipeline_orchestrator / TestRunWorkspace | 验证：A new molecule reaches Step 1 silently when only its .gjf exists. | tmp_path 隔离工作区 + monkeypatch |
| T413 | tests/test_pipeline_orchestrator.py::TestRunWorkspace::test_prepare_workspace_rejects_molecule_without_quantum_input | willy.pipeline_orchestrator / TestRunWorkspace | 验证 `prepare_workspace_rejects_molecule_without_quantum_input` 行为 | tmp_path 隔离工作区 + monkeypatch + 异常断言 |
| T414 | tests/test_pipeline_orchestrator.py::TestSinglePointMol2Contract::test_g16_singlepoint_inherits_molecule_resource_override | willy.pipeline_orchestrator / TestSinglePointMol2Contract | 验证 `g16_singlepoint_inherits_molecule_resource_override` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T415 | tests/test_pipeline_orchestrator.py::TestSinglePointMol2Contract::test_g16_mol2_failure_marks_step_2_failed | willy.pipeline_orchestrator / TestSinglePointMol2Contract | 验证 `g16_mol2_failure_marks_step_2_failed` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T416 | tests/test_pipeline_orchestrator.py::TestHandleBatchResult::test_all_success | willy.pipeline_orchestrator / TestHandleBatchResult | 验证 `all_success` 行为 | 进程内行为断言 |
| T417 | tests/test_pipeline_orchestrator.py::TestHandleBatchResult::test_some_fail_no_llm | willy.pipeline_orchestrator / TestHandleBatchResult | 验证：在没有 LLM 的情况下，部分失败应返回 False | 进程内行为断言 |
| T418 | tests/test_pipeline_orchestrator.py::TestHandleBatchResult::test_molecule_failure_is_not_silently_filtered | willy.pipeline_orchestrator / TestHandleBatchResult | 验证：模拟层不能跳过分子而保留旧拓扑和建盒结果 | 进程内行为断言 |
| T419 | tests/test_pipeline_orchestrator.py::TestHandleBatchResult::test_artifacts_collected_for_success | willy.pipeline_orchestrator / TestHandleBatchResult | 验证 `artifacts_collected_for_success` 行为 | 进程内行为断言 |
| T420 | tests/test_pipeline_orchestrator.py::TestHandleBatchResult::test_empty_batch_is_a_failure | willy.pipeline_orchestrator / TestHandleBatchResult | 验证 `empty_batch_is_a_failure` 行为 | 进程内行为断言 |
| T421 | tests/test_pipeline_orchestrator.py::TestHandleBatchResult::test_each_failed_molecule_is_repaired_before_step_succeeds | willy.pipeline_orchestrator / TestHandleBatchResult | 验证 `each_failed_molecule_is_repaired_before_step_succeeds` 行为 | mock/patch |
| T422 | tests/test_pipeline_orchestrator.py::TestHandleSingleResult::test_success_collects_artifacts | willy.pipeline_orchestrator / TestHandleSingleResult | 验证 `success_collects_artifacts` 行为 | 进程内行为断言 |
| T423 | tests/test_pipeline_orchestrator.py::TestHandleSingleResult::test_failure_no_llm | willy.pipeline_orchestrator / TestHandleSingleResult | 验证 `failure_no_llm` 行为 | 进程内行为断言 |
| T424 | tests/test_pipeline_orchestrator.py::TestHandleSingleResult::test_user_stop_request_bypasses_error_and_agent_retry | willy.pipeline_orchestrator / TestHandleSingleResult | 验证 `user_stop_request_bypasses_error_and_agent_retry` 行为 | tmp_path 隔离工作区 |
| T425 | tests/test_pipeline_orchestrator.py::TestHandleSingleResult::test_failure_without_error_details | willy.pipeline_orchestrator / TestHandleSingleResult | 验证：错误为 None 时不应崩溃 | 进程内行为断言 |
| T426 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_no_agent_available | willy.pipeline_orchestrator / TestInvokeAgent | 验证：agent 为 None 时必须升级，不能悬挂在 retrying | 进程内行为断言 |
| T427 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_agent_exception_is_escalated_and_decision_is_persisted | willy.pipeline_orchestrator / TestInvokeAgent | 验证 `agent_exception_is_escalated_and_decision_is_persisted` 行为 | tmp_path 隔离工作区 + mock/patch |
| T428 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_runtime_unavailable_skips_llm_parameter_repair | willy.pipeline_orchestrator / TestInvokeAgent | 验证 `runtime_unavailable_skips_llm_parameter_repair` 行为 | mock/patch |
| T429 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_agent_repair_success | willy.pipeline_orchestrator / TestInvokeAgent | 验证 `agent_repair_success` 行为 | mock/patch |
| T430 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_step_two_upstream_repair_requests_a_rerun | willy.pipeline_orchestrator / TestInvokeAgent | 验证：Creating only {name}.fchk cannot complete SP + mol2. | tmp_path 隔离工作区 + mock/patch |
| T431 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_eq_upstream_mdp_repair_rolls_back_to_em_without_marking_eq_done | willy.pipeline_orchestrator / TestInvokeAgent | 验证：A configuration repair cannot turn an EQ failure into EQ success. | tmp_path 隔离工作区 + mock/patch |
| T432 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_eq_failure_waits_for_user_confirmation_before_prod | willy.pipeline_orchestrator / TestInvokeAgent | 验证：An EQ failure creates a proposal; it must never auto-enter PROD. | tmp_path 隔离工作区 + monkeypatch + mock/patch |
| T433 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_confirmed_eq_action_reruns_eq_before_prod | willy.pipeline_orchestrator / TestInvokeAgent | 验证：An approved action rewrites MDPs then needs accepted EQ before PROD. | tmp_path 隔离工作区 + monkeypatch |
| T434 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_eq_success_without_manifest_acceptance_is_blocked_before_prod | willy.pipeline_orchestrator / TestInvokeAgent | 验证：An EQ tool result alone is insufficient to progress into PROD. | tmp_path 隔离工作区 + monkeypatch + mock/patch |
| T435 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_eq_only_scope_stops_after_accepted_eq_without_starting_prod | willy.pipeline_orchestrator / TestInvokeAgent | 验证：A declared EQ-only trial remains an accepted partial workflow. | tmp_path 隔离工作区 + monkeypatch |
| T436 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_run_registry_projects_only_the_fixed_eq_completion_scope | willy.pipeline_orchestrator / TestInvokeAgent | 验证：RunRegistry must retain the scoped completion marker for the UI. | tmp_path 隔离工作区 |
| T437 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_run_reexecutes_step_two_after_upstream_repair | willy.pipeline_orchestrator / TestInvokeAgent | 验证：The rerun runs Step 2 itself and only then permits downstream steps. | tmp_path 隔离工作区 + monkeypatch + mock/patch |
| T438 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_agent_receives_failed_step_intermediate_artifacts | willy.pipeline_orchestrator / TestInvokeAgent | 验证：Step 2 mol2 失败时，Agent 必须收到本次生成的 *_opt.fchk | mock/patch |
| T439 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_agent_escalation | willy.pipeline_orchestrator / TestInvokeAgent | 验证 `agent_escalation` 行为 | mock/patch |
| T440 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_agent_escalation_uses_current_failed_molecule | willy.pipeline_orchestrator / TestInvokeAgent | 验证：The final public error must not retain an earlier batch item's target. | mock/patch |
| T441 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_agent_repair_failure_no_escalation | willy.pipeline_orchestrator / TestInvokeAgent | 验证：Agent 未升级的失败应中止流水线 | mock/patch |
| T442 | tests/test_pipeline_state.py::TestStateEnum::test_all_states_defined | willy.pipeline_state / TestStateEnum | 验证 `all_states_defined` 行为 | 进程内行为断言 |
| T443 | tests/test_pipeline_state.py::TestStateEnum::test_state_values_are_strings | willy.pipeline_state / TestStateEnum | 验证 `state_values_are_strings` 行为 | 进程内行为断言 |
| T444 | tests/test_pipeline_state.py::TestStateEnum::test_state_transitions_well_defined | willy.pipeline_state / TestStateEnum | 验证：典型状态流转: IDLE → RUNNING → DONE | 进程内行为断言 |
| T445 | tests/test_pipeline_state.py::TestPipelineStatus::test_default_values | willy.pipeline_state / TestPipelineStatus | 验证 `default_values` 行为 | 进程内行为断言 |
| T446 | tests/test_pipeline_state.py::TestPipelineStatus::test_all_fields_serializable | willy.pipeline_state / TestPipelineStatus | 验证：所有字段应对 JSON 可序列化 | 进程内行为断言 |
| T447 | tests/test_pipeline_state.py::TestPipelineStatus::test_escalation_field_defaults_to_dict | willy.pipeline_state / TestPipelineStatus | 验证 `escalation_field_defaults_to_dict` 行为 | 进程内行为断言 |
| T448 | tests/test_pipeline_state.py::TestPipelineStatus::test_escalation_field_with_dict | willy.pipeline_state / TestPipelineStatus | 验证 `escalation_field_with_dict` 行为 | 进程内行为断言 |
| T449 | tests/test_pipeline_state.py::TestPipelineStatus::test_actions_list_accumulation | willy.pipeline_state / TestPipelineStatus | 验证 `actions_list_accumulation` 行为 | 进程内行为断言 |
| T450 | tests/test_pipeline_state.py::TestPipelineStatus::test_error_field | willy.pipeline_state / TestPipelineStatus | 验证：error 字段存储最近的错误消息 | 进程内行为断言 |
| T451 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_initial_state_is_idle | willy.pipeline_state / TestPipelineStateMachine | 验证 `initial_state_is_idle` 行为 | 进程内行为断言 |
| T452 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_transition_updates_state | willy.pipeline_state / TestPipelineStateMachine | 验证 `transition_updates_state` 行为 | 进程内行为断言 |
| T453 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_awaiting_confirmation_cannot_skip_directly_to_running | willy.pipeline_state / TestPipelineStateMachine | 验证 `awaiting_confirmation_cannot_skip_directly_to_running` 行为 | 异常断言 |
| T454 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_terminal_state_cannot_resume | willy.pipeline_state / TestPipelineStateMachine | 验证 `terminal_state_cannot_resume` 行为 | 异常断言 |
| T455 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_scoped_done_preserves_the_last_completed_step | willy.pipeline_state / TestPipelineStateMachine | 验证 `scoped_done_preserves_the_last_completed_step` 行为 | 进程内行为断言 |
| T456 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_public_status_rejects_an_unrecognized_completion_scope | willy.pipeline_state / TestPipelineStateMachine | 验证 `public_status_rejects_an_unrecognized_completion_scope` 行为 | 进程内行为断言 |
| T457 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_state_revision_is_monotonic_and_persisted | willy.pipeline_state / TestPipelineStateMachine | 验证 `state_revision_is_monotonic_and_persisted` 行为 | tmp_path 隔离工作区 |
| T458 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_controlled_resume_preserves_revision_before_next_write | willy.pipeline_state / TestPipelineStateMachine | 验证 `controlled_resume_preserves_revision_before_next_write` 行为 | 进程内行为断言 |
| T459 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_external_stop_revision_cannot_be_overwritten_by_old_heartbeat | willy.pipeline_state / TestPipelineStateMachine | 验证 `external_stop_revision_cannot_be_overwritten_by_old_heartbeat` 行为 | tmp_path 隔离工作区 |
| T460 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_set_step | willy.pipeline_state / TestPipelineStateMachine | 验证 `set_step` 行为 | 进程内行为断言 |
| T461 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_mark_done | willy.pipeline_state / TestPipelineStateMachine | 验证 `mark_done` 行为 | 进程内行为断言 |
| T462 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_rollback_to_invalidates_downstream_steps | willy.pipeline_state / TestPipelineStateMachine | 验证 `rollback_to_invalidates_downstream_steps` 行为 | 进程内行为断言 |
| T463 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_controlled_restart_withdraws_only_the_restarted_suffix | willy.pipeline_state / TestPipelineStateMachine | 验证 `controlled_restart_withdraws_only_the_restarted_suffix` 行为 | 进程内行为断言 |
| T464 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_set_error | willy.pipeline_state / TestPipelineStateMachine | 验证 `set_error` 行为 | 进程内行为断言 |
| T465 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_start_retry | willy.pipeline_state / TestPipelineStateMachine | 验证 `start_retry` 行为 | 进程内行为断言 |
| T466 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_add_action | willy.pipeline_state / TestPipelineStateMachine | 验证 `add_action` 行为 | 进程内行为断言 |
| T467 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_public_repair_snapshot_includes_only_safe_adjustments | willy.pipeline_state / TestPipelineStateMachine | 验证 `public_repair_snapshot_includes_only_safe_adjustments` 行为 | tmp_path 隔离工作区 |
| T468 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_set_escalated | willy.pipeline_state / TestPipelineStateMachine | 验证 `set_escalated` 行为 | 进程内行为断言 |
| T469 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_set_aborted | willy.pipeline_state / TestPipelineStateMachine | 验证 `set_aborted` 行为 | 进程内行为断言 |
| T470 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_user_requested_abort_clears_stale_failure | willy.pipeline_state / TestPipelineStateMachine | 验证 `user_requested_abort_clears_stale_failure` 行为 | 进程内行为断言 |
| T471 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_full_pipeline_flow | willy.pipeline_state / TestPipelineStateMachine | 验证：完整的流水线状态流转: IDLE → RUNNING → (每个步骤) → DONE | 进程内行为断言 |
| T472 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_error_then_retry_then_done_flow | willy.pipeline_state / TestPipelineStateMachine | 验证：错误 → 重试 → 恢复 → 完成流程 | 进程内行为断言 |
| T473 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_final_escalation_flow | willy.pipeline_state / TestPipelineStateMachine | 验证：所有重试用尽 → 升级流程 | 进程内行为断言 |
| T474 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_writes_status_json | willy.pipeline_state / TestPipelineStateMachine | 验证：状态机应在初始化时写入 status.json | tmp_path 隔离工作区 + monkeypatch |
| T475 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_atomic_write | willy.pipeline_state / TestPipelineStateMachine | 验证：写入应为原子操作（先 .tmp，再 os.replace） | tmp_path 隔离工作区 + monkeypatch |
| T476 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_read_static_method | willy.pipeline_state / TestPipelineStateMachine | 验证：read() 应读取当前的 status.json | tmp_path 隔离工作区 + monkeypatch |
| T477 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_read_missing_file_returns_idle | willy.pipeline_state / TestPipelineStateMachine | 验证：status.json 不存在时应返回 IDLE | tmp_path 隔离工作区 + monkeypatch |
| T478 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_read_corrupted_file_returns_idle | willy.pipeline_state / TestPipelineStateMachine | 验证：status.json 损坏时应返回 IDLE | tmp_path 隔离工作区 + monkeypatch |
| T479 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_error_message_handling | willy.pipeline_state / TestPipelineStateMachine | 验证：错误消息处理 | tmp_path 隔离工作区 + monkeypatch |
| T480 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_rapid_state_transitions | willy.pipeline_state / TestPipelineStateMachine | 验证：快速状态转换不应崩溃 | tmp_path 隔离工作区 + monkeypatch |
| T481 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_custom_total_steps | willy.pipeline_state / TestPipelineStateMachine | 验证：自定义 total_steps 应被存储 | tmp_path 隔离工作区 + monkeypatch |
| T482 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_activity_is_strictly_structured_and_persisted_atomically | willy.pipeline_state / TestPipelineStateMachine | 验证 `activity_is_strictly_structured_and_persisted_atomically` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T483 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_escalation_public_snapshot_drops_raw_output | willy.pipeline_state / TestPipelineStateMachine | 验证 `escalation_public_snapshot_drops_raw_output` 行为 | tmp_path 隔离工作区 |
| T510 | tests/test_recovery_contracts.py::test_pending_action_adapts_to_generic_contract | willy.action_contract / recovery contract | 验证 `pending_action_adapts_to_generic_contract` 行为 | 进程内行为断言 |
| T511 | tests/test_recovery_contracts.py::test_decision_trace_redacts_paths_and_secrets | willy.action_contract / recovery contract | 验证 `decision_trace_redacts_paths_and_secrets` 行为 | tmp_path 隔离工作区 |
| T512 | tests/test_recovery_policy.py::test_eq_policy_keeps_prod_out_and_requires_confirmation | willy.recovery_policy | 验证 `eq_policy_keeps_prod_out_and_requires_confirmation` 行为 | 进程内行为断言 |
| T513 | tests/test_recovery_policy.py::test_policy_allows_confirmed_safe_retry_with_bounded_attempts | willy.recovery_policy | 验证 `policy_allows_confirmed_safe_retry_with_bounded_attempts` 行为 | 进程内行为断言 |
| T514 | tests/test_recovery_policy.py::test_quantum_parameter_effects_require_confirmation_or_fork | willy.recovery_policy | 验证 `quantum_parameter_effects_require_confirmation_or_fork` 行为 | 进程内行为断言 |
| T630 | tests/test_step_registry.py::test_registry_is_contiguous_and_covers_the_current_pipeline | willy.step_registry | 验证 `registry_is_contiguous_and_covers_the_current_pipeline` 行为 | 进程内行为断言 |
| T631 | tests/test_step_registry.py::test_registry_rejects_gaps_and_duplicate_ids | willy.step_registry | 验证 `registry_rejects_gaps_and_duplicate_ids` 行为 | 异常断言 |
| T632 | tests/test_step_registry.py::test_execution_modules_own_normalized_tool_and_step_dependencies | willy.step_registry | 验证 `execution_modules_own_normalized_tool_and_step_dependencies` 行为 | 进程内行为断言 |
| T633 | tests/test_step_registry.py::test_execution_module_registry_rejects_unknown_steps_and_duplicate_tools | willy.step_registry | 验证 `execution_module_registry_rejects_unknown_steps_and_duplicate_tools` 行为 | 异常断言 |
| T636 | tests/test_structured_log.py::test_append_creates_run_local_log_and_shared_sequence | willy.structured_log | 验证 `append_creates_run_local_log_and_shared_sequence` 行为 | tmp_path 隔离工作区 |
| T637 | tests/test_structured_log.py::test_sensitive_paths_and_command_like_values_are_redacted | willy.structured_log | 验证 `sensitive_paths_and_command_like_values_are_redacted` 行为 | tmp_path 隔离工作区 |
| T638 | tests/test_structured_log.py::test_malformed_fields_are_omitted_without_rejecting_event | willy.structured_log | 验证 `malformed_fields_are_omitted_without_rejecting_event` 行为 | tmp_path 隔离工作区 |
| T639 | tests/test_structured_log.py::test_invalid_required_event_is_non_fatal_and_does_not_write | willy.structured_log | 验证 `invalid_required_event_is_non_fatal_and_does_not_write` 行为 | tmp_path 隔离工作区 |
| T640 | tests/test_structured_log.py::test_logging_failure_is_non_blocking | willy.structured_log | 验证 `logging_failure_is_non_blocking` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T641 | tests/test_structured_log_integration.py::test_pipeline_step_result_is_written_to_the_bound_run | willy.structured_log integration | 验证 `pipeline_step_result_is_written_to_the_bound_run` 行为 | tmp_path 隔离工作区 |

## I. 运行管理、启动控制与运行助理

运行预留、远程 profile/transport、互斥启动、进程生命周期、保留清理、审计、provenance、RunStore 与只读助理。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T378 | tests/test_pipeline_launch.py::test_reservation_is_atomic_and_allocates_a_run_id | willy.pipeline_launch | 验证 `reservation_is_atomic_and_allocates_a_run_id` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T379 | tests/test_pipeline_launch.py::test_reservation_derives_the_next_sequence_from_existing_run_directories | willy.pipeline_launch | 验证 `reservation_derives_the_next_sequence_from_existing_run_directories` 行为 | tmp_path 隔离工作区 |
| T380 | tests/test_pipeline_launch.py::test_legacy_live_lock_remains_authoritative_until_its_owner_exits | willy.pipeline_launch | 验证 `legacy_live_lock_remains_authoritative_until_its_owner_exits` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T381 | tests/test_pipeline_launch.py::test_running_lock_remains_active_when_managed_process_group_survives | willy.pipeline_launch | 验证 `running_lock_remains_active_when_managed_process_group_survives` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T382 | tests/test_pipeline_launch.py::test_frontend_recovers_the_current_run_from_a_live_legacy_lock | willy.pipeline_launch | 验证 `frontend_recovers_the_current_run_from_a_live_legacy_lock` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T383 | tests/test_pipeline_launch.py::test_unbound_startup_audit_has_only_public_messages | willy.pipeline_launch | 验证 `unbound_startup_audit_has_only_public_messages` 行为 | tmp_path 隔离工作区 |
| T384 | tests/test_pipeline_launch.py::test_frontend_launch_conflict_does_not_spawn_a_second_child | willy.pipeline_launch | 验证 `frontend_launch_conflict_does_not_spawn_a_second_child` 行为 | tmp_path 隔离工作区 + monkeypatch + mock/patch |
| T385 | tests/test_pipeline_launch.py::test_deferred_state_machine_does_not_write_root_before_run_binding | willy.pipeline_launch | 验证 `deferred_state_machine_does_not_write_root_before_run_binding` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T386 | tests/test_pipeline_launch.py::test_orchestrator_binds_state_to_run_before_any_status_write | willy.pipeline_launch | 验证 `orchestrator_binds_state_to_run_before_any_status_write` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T492 | tests/test_process_lifecycle.py::test_termination_escalates_the_process_group | willy.process_lifecycle | 验证 `termination_escalates_the_process_group` 行为 | monkeypatch |
| T493 | tests/test_process_lifecycle.py::test_lifecycle_record_excludes_command_paths | willy.process_lifecycle | 验证 `lifecycle_record_excludes_command_paths` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T494 | tests/test_process_lifecycle.py::test_managed_command_honors_stop_request_and_records_audit | willy.process_lifecycle | 验证 `managed_command_honors_stop_request_and_records_audit` 行为 | tmp_path 隔离工作区 |
| T495 | tests/test_process_lifecycle.py::test_managed_command_timeout_terminates_and_records_audit | willy.process_lifecycle | 验证 `managed_command_timeout_terminates_and_records_audit` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T496 | tests/test_process_lifecycle.py::test_managed_command_emits_low_frequency_heartbeat | willy.process_lifecycle | 验证 `managed_command_emits_low_frequency_heartbeat` 行为 | tmp_path 隔离工作区 + monkeypatch + 异常断言 |
| T497 | tests/test_prune_runs.py::test_prune_runs_requires_apply_and_updates_the_index | scripts.prune_runs | 验证 `prune_runs_requires_apply_and_updates_the_index` 行为 | tmp_path 隔离工作区 |
| T498 | tests/test_prune_runs.py::test_prune_runs_refuses_when_a_pipeline_lock_is_active | scripts.prune_runs | 验证 `prune_runs_refuses_when_a_pipeline_lock_is_active` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T515 | tests/test_remote_execution.py::test_preflight_normalizes_private_transport_failure_and_never_exposes_details | willy.remote_execution | 验证 `preflight_normalizes_private_transport_failure_and_never_exposes_details` 行为 | 进程内行为断言 |
| T516 | tests/test_remote_execution.py::test_preflight_requires_declared_resources_without_transport_details | willy.remote_execution | 验证 `preflight_requires_declared_resources_without_transport_details` 行为 | 进程内行为断言 |
| T517 | tests/test_remote_execution.py::test_gromacs_command_is_argv_only_and_rejects_control_characters | willy.remote_execution | 验证 `gromacs_command_is_argv_only_and_rejects_control_characters` 行为 | 异常断言 |
| T518 | tests/test_remote_execution.py::test_sync_manifest_rejects_escape_and_verifies_upload_and_download_hashes | willy.remote_execution | 验证 `sync_manifest_rejects_escape_and_verifies_upload_and_download_hashes` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T519 | tests/test_remote_execution.py::test_sync_receipt_hash_mismatch_is_rejected_before_stage_can_continue | willy.remote_execution | 验证 `sync_receipt_hash_mismatch_is_rejected_before_stage_can_continue` 行为 | tmp_path 隔离工作区 |
| T520 | tests/test_remote_execution.py::test_direct_ssh_lifecycle_preserves_start_query_checkpoint_first_stop_and_escalation | willy.remote_execution | 验证 `direct_ssh_lifecycle_preserves_start_query_checkpoint_first_stop_and_escalation` 行为 | 进程内行为断言 |
| T521 | tests/test_remote_execution.py::test_run_binding_preserves_local_behavior_and_preflights_remote_only | willy.remote_execution | 验证 `run_binding_preserves_local_behavior_and_preflights_remote_only` 行为 | 进程内行为断言 |
| T522 | tests/test_remote_registry.py::test_local_execution_never_requires_a_profile_file | willy.remote_registry | 验证 `local_execution_never_requires_a_profile_file` 行为 | tmp_path 隔离工作区 |
| T523 | tests/test_remote_registry.py::test_registry_uses_explicit_environment_override | willy.remote_registry | 验证 `registry_uses_explicit_environment_override` 行为 | tmp_path 隔离工作区 + monkeypatch + 异常断言 |
| T524 | tests/test_remote_registry.py::test_public_capabilities_are_redacted | willy.remote_registry | 验证 `public_capabilities_are_redacted` 行为 | tmp_path 隔离工作区 |
| T525 | tests/test_remote_registry.py::test_public_execution_snapshot_marks_registered_profile_unprobed | willy.remote_registry | 验证 `public_execution_snapshot_marks_registered_profile_unprobed` 行为 | tmp_path 隔离工作区 |
| T526 | tests/test_remote_registry.py::test_missing_or_insecure_profile_file_is_not_available | willy.remote_registry | 验证 `missing_or_insecure_profile_file_is_not_available` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T527 | tests/test_remote_registry.py::test_registry_rejects_duplicate_json_fields_and_symlinked_parent | willy.remote_registry | 验证 `registry_rejects_duplicate_json_fields_and_symlinked_parent` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T528 | tests/test_remote_registry.py::test_profile_schema_rejects_secrets_shell_fields_and_non_strict_host_policy | willy.remote_registry | 验证 `profile_schema_rejects_secrets_shell_fields_and_non_strict_host_policy` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T529 | tests/test_remote_registry.py::test_remote_execution_requires_registered_matching_launcher | willy.remote_registry | 验证 `remote_execution_requires_registered_matching_launcher` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T530 | tests/test_remote_registry.py::test_slurm_profile_requires_structured_resources | willy.remote_registry | 验证 `slurm_profile_requires_structured_resources` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T531 | tests/test_run_assistant.py::TestRunRegistry::test_v2_registry_section_owns_registration_and_artifacts | willy.run_registry / toolist_run / agent_run / TestRunRegistry | 验证 `v2_registry_section_owns_registration_and_artifacts` 行为 | tmp_path 隔离工作区 |
| T532 | tests/test_run_assistant.py::TestRunRegistry::test_register_run_hashes_reused_quantum_intermediate | willy.run_registry / toolist_run / agent_run / TestRunRegistry | 验证 `register_run_hashes_reused_quantum_intermediate` 行为 | tmp_path 隔离工作区 |
| T533 | tests/test_run_assistant.py::TestRunRegistry::test_refresh_config_fingerprint_tracks_final_snapshot | willy.run_registry / toolist_run / agent_run / TestRunRegistry | 验证 `refresh_config_fingerprint_tracks_final_snapshot` 行为 | tmp_path 隔离工作区 |
| T534 | tests/test_run_assistant.py::TestRunRegistry::test_register_status_result_and_artifact_contract | willy.run_registry / toolist_run / agent_run / TestRunRegistry | 验证 `register_status_result_and_artifact_contract` 行为 | tmp_path 隔离工作区 |
| T535 | tests/test_run_assistant.py::TestRunRegistry::test_registry_rejects_path_traversal_and_log_secrets_are_redacted | willy.run_registry / toolist_run / agent_run / TestRunRegistry | 验证 `registry_rejects_path_traversal_and_log_secrets_are_redacted` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T536 | tests/test_run_assistant.py::TestRunRegistry::test_box_parameters_expose_only_audited_geometry | willy.run_registry / toolist_run / agent_run / TestRunRegistry | 验证 `box_parameters_expose_only_audited_geometry` 行为 | tmp_path 隔离工作区 |
| T537 | tests/test_run_assistant.py::TestRunRegistry::test_status_observer_is_best_effort_and_event_typed | willy.run_registry / toolist_run / agent_run / TestRunRegistry | 验证 `status_observer_is_best_effort_and_event_typed` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T538 | tests/test_run_assistant.py::TestRunRegistry::test_public_status_events_and_reports_never_include_engine_output | willy.run_registry / toolist_run / agent_run / TestRunRegistry | 验证 `public_status_events_and_reports_never_include_engine_output` 行为 | tmp_path 隔离工作区 |
| T539 | tests/test_run_assistant.py::TestRunRegistry::test_active_eq_manifest_reconciles_a_status_that_wrongly_entered_prod | willy.run_registry / toolist_run / agent_run / TestRunRegistry | 验证：Only public state changes when a live EQ contradicts a PROD status. | tmp_path 隔离工作区 |
| T540 | tests/test_run_assistant.py::TestRunRegistry::test_stale_eq_manifest_does_not_reopen_a_stopped_run_as_live | willy.run_registry / toolist_run / agent_run / TestRunRegistry | 验证：A stale manifest alone cannot overwrite the public terminal path. | tmp_path 隔离工作区 |
| T541 | tests/test_run_assistant.py::TestRunTools::test_tools_are_all_read_only_and_server_binds_run_id | willy.run_registry / toolist_run / agent_run / TestRunTools | 验证 `tools_are_all_read_only_and_server_binds_run_id` 行为 | tmp_path 隔离工作区 |
| T542 | tests/test_run_assistant.py::TestRunTools::test_environment_tool_returns_only_redacted_capabilities | willy.run_registry / toolist_run / agent_run / TestRunTools | 验证 `environment_tool_returns_only_redacted_capabilities` 行为 | tmp_path 隔离工作区 |
| T543 | tests/test_run_assistant.py::TestRunTools::test_box_tool_returns_audited_geometry | willy.run_registry / toolist_run / agent_run / TestRunTools | 验证 `box_tool_returns_audited_geometry` 行为 | tmp_path 隔离工作区 |
| T544 | tests/test_run_assistant.py::TestRunTools::test_md_eta_tool_returns_only_validated_gromacs_prediction | willy.run_registry / toolist_run / agent_run / TestRunTools | 验证 `md_eta_tool_returns_only_validated_gromacs_prediction` 行为 | tmp_path 隔离工作区 |
| T545 | tests/test_run_assistant.py::TestRunTools::test_runtime_heartbeat_updates_status_without_appending_events | willy.run_registry / toolist_run / agent_run / TestRunTools | 验证 `runtime_heartbeat_updates_status_without_appending_events` 行为 | tmp_path 隔离工作区 |
| T546 | tests/test_run_assistant.py::TestRunTools::test_md_eta_tool_rejects_invalid_snapshot_and_handles_no_snapshot | willy.run_registry / toolist_run / agent_run / TestRunTools | 验证 `md_eta_tool_rejects_invalid_snapshot_and_handles_no_snapshot` 行为 | tmp_path 隔离工作区 |
| T547 | tests/test_run_assistant.py::TestRunTools::test_list_runs_does_not_create_an_index_for_legacy_runs | willy.run_registry / toolist_run / agent_run / TestRunTools | 验证 `list_runs_does_not_create_an_index_for_legacy_runs` 行为 | tmp_path 隔离工作区 |
| T548 | tests/test_run_assistant.py::TestRunAssistant::test_assistant_answers_status_fast_path_without_llm | willy.run_registry / toolist_run / agent_run / TestRunAssistant | 验证 `assistant_answers_status_fast_path_without_llm` 行为 | tmp_path 隔离工作区 + mock/patch |
| T549 | tests/test_run_assistant.py::TestRunAssistant::test_assistant_labels_waiting_confirmation_without_falling_back_to_unknown | willy.run_registry / toolist_run / agent_run / TestRunAssistant | 验证 `assistant_labels_waiting_confirmation_without_falling_back_to_unknown` 行为 | 进程内行为断言 |
| T550 | tests/test_run_assistant.py::TestRunAssistant::test_assistant_can_call_only_bound_read_tool_for_complex_question | willy.run_registry / toolist_run / agent_run / TestRunAssistant | 验证 `assistant_can_call_only_bound_read_tool_for_complex_question` 行为 | tmp_path 隔离工作区 + mock/patch |
| T551 | tests/test_run_assistant.py::TestRunAssistant::test_assistant_limits_history_to_six_compact_turns | willy.run_registry / toolist_run / agent_run / TestRunAssistant | 验证 `assistant_limits_history_to_six_compact_turns` 行为 | tmp_path 隔离工作区 + mock/patch |
| T552 | tests/test_run_assistant.py::TestRunAssistant::test_assistant_degrades_complex_question_to_local_facts_without_llm | willy.run_registry / toolist_run / agent_run / TestRunAssistant | 验证 `assistant_degrades_complex_question_to_local_facts_without_llm` 行为 | tmp_path 隔离工作区 |
| T553 | tests/test_run_assistant.py::TestRunAssistant::test_assistant_prefers_local_eta_and_waiting_heartbeat_times | willy.run_registry / toolist_run / agent_run / TestRunAssistant | 验证 `assistant_prefers_local_eta_and_waiting_heartbeat_times` 行为 | 进程内行为断言 |
| T554 | tests/test_run_assistant.py::TestOrchestratorRunRegistration::test_prepared_workspace_registers_run_and_binds_state | willy.run_registry / toolist_run / agent_run / TestOrchestratorRunRegistration | 验证 `prepared_workspace_registers_run_and_binds_state` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T555 | tests/test_run_metadata.py::test_load_legacy_sections_keeps_protocol_separate_and_does_not_write | willy.run_metadata schema-v2 / migration / section CAS | 验证 `load_legacy_sections_keeps_protocol_separate_and_does_not_write` 行为 | tmp_path 隔离工作区 |
| T556 | tests/test_run_metadata.py::test_section_reader_prefers_v2_and_falls_back_to_legacy | willy.run_metadata schema-v2 / migration / section CAS | 验证 `section_reader_prefers_v2_and_falls_back_to_legacy` 行为 | tmp_path 隔离工作区 |
| T557 | tests/test_run_metadata.py::test_explicit_legacy_migration_retains_old_files | willy.run_metadata schema-v2 / migration / section CAS | 验证 `explicit_legacy_migration_retains_old_files` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T558 | tests/test_run_metadata.py::test_section_update_uses_global_and_section_compare_and_swap | willy.run_metadata schema-v2 / migration / section CAS | 验证 `section_update_uses_global_and_section_compare_and_swap` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T559 | tests/test_run_metadata.py::test_multi_section_update_is_one_atomic_global_revision | willy.run_metadata schema-v2 / migration / section CAS | 验证 `multi_section_update_is_one_atomic_global_revision` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T560 | tests/test_run_metadata.py::test_migration_rejects_active_or_awaiting_runs[running] | willy.run_metadata schema-v2 / migration / section CAS | 验证 `migration_rejects_active_or_awaiting_runs` 行为；参数集 `running` | pytest 参数化 + tmp_path 隔离工作区 + 异常断言 |
| T561 | tests/test_run_metadata.py::test_migration_rejects_active_or_awaiting_runs[awaiting_confirmation] | willy.run_metadata schema-v2 / migration / section CAS | 验证 `migration_rejects_active_or_awaiting_runs` 行为；参数集 `awaiting_confirmation` | pytest 参数化 + tmp_path 隔离工作区 + 异常断言 |
| T562 | tests/test_run_metadata.py::test_migration_rejects_active_or_awaiting_runs[retrying] | willy.run_metadata schema-v2 / migration / section CAS | 验证 `migration_rejects_active_or_awaiting_runs` 行为；参数集 `retrying` | pytest 参数化 + tmp_path 隔离工作区 + 异常断言 |
| T563 | tests/test_run_metadata.py::test_migration_rejects_pending_action_even_if_status_is_terminal | willy.run_metadata schema-v2 / migration / section CAS | 验证 `migration_rejects_pending_action_even_if_status_is_terminal` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T564 | tests/test_run_metadata.py::test_migration_rejects_run_without_terminal_status_evidence | willy.run_metadata schema-v2 / migration / section CAS | 验证 `migration_rejects_run_without_terminal_status_evidence` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T565 | tests/test_run_metadata.py::test_active_migration_requires_explicit_opt_in | willy.run_metadata schema-v2 / migration / section CAS | 验证 `active_migration_requires_explicit_opt_in` 行为 | tmp_path 隔离工作区 |
| T566 | tests/test_run_provenance.py::test_provenance_is_run_local_redacted_and_tracks_config_revisions | willy.run_provenance | 验证 `provenance_is_run_local_redacted_and_tracks_config_revisions` 行为 | tmp_path 隔离工作区 |
| T567 | tests/test_run_provenance.py::test_provenance_rejects_config_outside_current_run | willy.run_provenance | 验证 `provenance_rejects_config_outside_current_run` 行为 | tmp_path 隔离工作区 |
| T568 | tests/test_run_provenance.py::test_provenance_uses_unified_private_section_when_enabled | willy.run_provenance | 验证 `provenance_uses_unified_private_section_when_enabled` 行为 | tmp_path 隔离工作区 |
| T569 | tests/test_run_store.py::test_manifest_read_modify_write_is_serialized | willy.run_store | 验证 `manifest_read_modify_write_is_serialized` 行为 | tmp_path 隔离工作区 |
| T570 | tests/test_run_store.py::test_events_and_decisions_share_one_monotonic_run_sequence | willy.run_store | 验证 `events_and_decisions_share_one_monotonic_run_sequence` 行为 | tmp_path 隔离工作区 |
| T571 | tests/test_run_store.py::test_pending_bundle_replays_missing_json_and_event_once | willy.run_store | 验证 `pending_bundle_replays_missing_json_and_event_once` 行为 | tmp_path 隔离工作区 |

## J. 外部 Smoke 与发布证据

外部工具预检、fixture 完整性、脱敏批次报告、证据归档和 required 发布门禁。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T062 | tests/test_batch_report.py::test_batch_report_contains_only_fingerprints_versions_and_public_acceptance | tests.reporting.batch_report | 验证 `batch_report_contains_only_fingerprints_versions_and_public_acceptance` 行为 | tmp_path 隔离工作区 |
| T063 | tests/test_batch_report.py::test_batch_report_rejects_logs_commands_and_secrets[raw_log-do not publish] | tests.reporting.batch_report | 验证 `batch_report_rejects_logs_commands_and_secrets` 行为；参数集 `raw_log-do not publish` | pytest 参数化 + tmp_path 隔离工作区 + 异常断言 |
| T064 | tests/test_batch_report.py::test_batch_report_rejects_logs_commands_and_secrets[api_key-secret] | tests.reporting.batch_report | 验证 `batch_report_rejects_logs_commands_and_secrets` 行为；参数集 `api_key-secret` | pytest 参数化 + tmp_path 隔离工作区 + 异常断言 |
| T065 | tests/test_batch_report.py::test_batch_report_rejects_logs_commands_and_secrets[command-gmx mdrun] | tests.reporting.batch_report | 验证 `batch_report_rejects_logs_commands_and_secrets` 行为；参数集 `command-gmx mdrun` | pytest 参数化 + tmp_path 隔离工作区 + 异常断言 |
| T066 | tests/test_batch_report.py::test_batch_report_rejects_logs_commands_and_secrets[note-Authorization: Bearer value] | tests.reporting.batch_report | 验证 `batch_report_rejects_logs_commands_and_secrets` 行为；参数集 `note-Authorization: Bearer value` | pytest 参数化 + tmp_path 隔离工作区 + 异常断言 |
| T067 | tests/test_batch_report.py::test_batch_report_writer_is_json_and_atomic | tests.reporting.batch_report | 验证 `batch_report_writer_is_json_and_atomic` 行为 | tmp_path 隔离工作区 |
| T068 | tests/test_batch_report.py::test_artifact_must_be_relative_to_the_report_root | tests.reporting.batch_report | 验证 `artifact_must_be_relative_to_the_report_root` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T178 | tests/test_external_smoke.py::test_smoke_registry_has_unique_case_ids_and_declared_timeout | willy.external_smoke / self-hosted CI gate | 验证 `smoke_registry_has_unique_case_ids_and_declared_timeout` 行为 | 进程内行为断言 |
| T179 | tests/test_external_smoke.py::test_smoke_preflight_is_read_only_and_reports_missing_fixture | willy.external_smoke / self-hosted CI gate | 验证 `smoke_preflight_is_read_only_and_reports_missing_fixture` 行为 | tmp_path 隔离工作区 |
| T180 | tests/test_external_smoke.py::test_smoke_preflight_accepts_only_hash_verified_declared_fixture | willy.external_smoke / self-hosted CI gate | 验证 `smoke_preflight_accepts_only_hash_verified_declared_fixture` 行为 | tmp_path 隔离工作区 |
| T181 | tests/test_external_smoke.py::test_smoke_preflight_rejects_tampered_fixture_bundle | willy.external_smoke / self-hosted CI gate | 验证 `smoke_preflight_rejects_tampered_fixture_bundle` 行为 | tmp_path 隔离工作区 |
| T182 | tests/test_external_smoke.py::test_smoke_evidence_is_opt_in_and_redacted | willy.external_smoke / self-hosted CI gate | 验证 `smoke_evidence_is_opt_in_and_redacted` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T183 | tests/test_external_smoke.py::test_evidence_verifier_rejects_preflight_record_as_execution | willy.external_smoke / self-hosted CI gate | 验证 `evidence_verifier_rejects_preflight_record_as_execution` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T184 | tests/test_external_smoke.py::test_evidence_verifier_requires_declared_outputs_for_successful_execution | willy.external_smoke / self-hosted CI gate | 验证 `evidence_verifier_requires_declared_outputs_for_successful_execution` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T185 | tests/test_external_smoke.py::test_evidence_verifier_rejects_path_traversal_in_tampered_artifact | willy.external_smoke / self-hosted CI gate | 验证 `evidence_verifier_rejects_path_traversal_in_tampered_artifact` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T186 | tests/test_external_smoke.py::test_selected_smoke_cases_rejects_duplicate_ids | willy.external_smoke / self-hosted CI gate | 验证 `selected_smoke_cases_rejects_duplicate_ids` 行为 | 异常断言 |
| T187 | tests/test_external_smoke.py::test_external_smoke_preflight_gate[gromacs_minimal] | willy.external_smoke / self-hosted CI gate | 验证：An acceptance machine can make missing tools/fixtures a hard failure.；参数集 `gromacs_minimal` | pytest 参数化 |
| T188 | tests/test_external_smoke.py::test_external_smoke_preflight_gate[sobtop_ec] | willy.external_smoke / self-hosted CI gate | 验证：An acceptance machine can make missing tools/fixtures a hard failure.；参数集 `sobtop_ec` | pytest 参数化 |
| T189 | tests/test_external_smoke.py::test_external_smoke_preflight_gate[g16_minimal] | willy.external_smoke / self-hosted CI gate | 验证：An acceptance machine can make missing tools/fixtures a hard failure.；参数集 `g16_minimal` | pytest 参数化 |
| T190 | tests/test_external_smoke.py::test_external_smoke_preflight_gate[orca_minimal] | willy.external_smoke / self-hosted CI gate | 验证：An acceptance machine can make missing tools/fixtures a hard failure.；参数集 `orca_minimal` | pytest 参数化 |
| T191 | tests/test_external_smoke.py::test_external_smoke_preflight_gate[multiwfn_minimal] | willy.external_smoke / self-hosted CI gate | 验证：An acceptance machine can make missing tools/fixtures a hard failure.；参数集 `multiwfn_minimal` | pytest 参数化 |
| T192 | tests/test_external_smoke.py::test_external_smoke_preflight_gate[ligpargen_minimal] | willy.external_smoke / self-hosted CI gate | 验证：An acceptance machine can make missing tools/fixtures a hard failure.；参数集 `ligpargen_minimal` | pytest 参数化 |

## K. 托管 LLM 网关

服务端限额的设备自助申请、客户端私钥、短期令牌、签名/nonce、防重放、模型白名单、额度账本、管理员控制面和离线 fake-upstream 脱敏边界。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T254 | tests/test_gateway.py::test_gateway_default_auto_approval_and_grant_limits_are_configured | willy_gateway FastAPI/SQLite gateway contract | 验证 `gateway_default_auto_approval_and_grant_limits_are_configured` 行为 | tmp_path 隔离工作区 |
| T255 | tests/test_gateway.py::test_managed_flow_forwards_alias_and_tool_calls_without_prompt_persistence | willy_gateway FastAPI/SQLite gateway contract | 验证 `managed_flow_forwards_alias_and_tool_calls_without_prompt_persistence` 行为 | tmp_path 隔离工作区 |
| T256 | tests/test_gateway.py::test_device_signature_nonce_replay_and_revocation_are_denied | willy_gateway FastAPI/SQLite gateway contract | 验证 `device_signature_nonce_replay_and_revocation_are_denied` 行为 | tmp_path 隔离工作区 |
| T257 | tests/test_gateway.py::test_token_request_body_tampering_and_expiry_are_denied | willy_gateway FastAPI/SQLite gateway contract | 验证 `token_request_body_tampering_and_expiry_are_denied` 行为 | tmp_path 隔离工作区 |
| T258 | tests/test_gateway.py::test_upstream_failure_is_redacted_and_timeout_keeps_reservation | willy_gateway FastAPI/SQLite gateway contract | 验证 `upstream_failure_is_redacted_and_timeout_keeps_reservation` 行为 | tmp_path 隔离工作区 |
| T259 | tests/test_gateway.py::test_model_allowlist_quota_and_request_policy_prevent_proxy_abuse | willy_gateway FastAPI/SQLite gateway contract | 验证 `model_allowlist_quota_and_request_policy_prevent_proxy_abuse` 行为 | tmp_path 隔离工作区 |
| T260 | tests/test_gateway.py::test_self_service_registration_uses_server_counted_slots_and_reuses_the_same_public_key | willy_gateway FastAPI/SQLite gateway contract | 验证 `self_service_registration_uses_server_counted_slots_and_reuses_the_same_public_key` 行为 | tmp_path 隔离工作区 |
| T261 | tests/test_gateway.py::test_first_registered_devices_are_auto_approved_with_default_grants | willy_gateway FastAPI/SQLite gateway contract | 验证 `first_registered_devices_are_auto_approved_with_default_grants` 行为 | tmp_path 隔离工作区 |
| T262 | tests/test_gateway.py::test_existing_manual_registration_consumes_the_automatic_approval_window | willy_gateway FastAPI/SQLite gateway contract | 验证 `existing_manual_registration_consumes_the_automatic_approval_window` 行为 | tmp_path 隔离工作区 |
| T263 | tests/test_gateway.py::test_self_service_registration_is_rate_limited_before_it_can_fill_slots | willy_gateway FastAPI/SQLite gateway contract | 验证 `self_service_registration_is_rate_limited_before_it_can_fill_slots` 行为 | tmp_path 隔离工作区 |
| T264 | tests/test_gateway.py::test_gateway_startup_assigns_expiry_to_legacy_pending_devices | willy_gateway FastAPI/SQLite gateway contract | 验证 `gateway_startup_assigns_expiry_to_legacy_pending_devices` 行为 | tmp_path 隔离工作区 |
| T265 | tests/test_gateway_admin.py::test_loopback_admin_requires_a_separate_secret_and_controls_device_grants | willy_gateway loopback administrator control plane | 验证 `loopback_admin_requires_a_separate_secret_and_controls_device_grants` 行为 | tmp_path 隔离工作区 |
| T266 | tests/test_gateway_admin.py::test_admin_page_preselects_the_only_allowed_model_for_a_pending_device | willy_gateway loopback administrator control plane | 验证 `admin_page_preselects_the_only_allowed_model_for_a_pending_device` 行为 | 进程内行为断言 |
| T267 | tests/test_gateway_admin.py::test_admin_snapshot_reports_automatic_approval_capacity | willy_gateway loopback administrator control plane | 验证 `admin_snapshot_reports_automatic_approval_capacity` 行为 | tmp_path 隔离工作区 |
| T268 | tests/test_gateway_admin.py::test_admin_grants_reject_unknown_models_and_nonloopback_gateway_requires_tls | willy_gateway loopback administrator control plane | 验证 `admin_grants_reject_unknown_models_and_nonloopback_gateway_requires_tls` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T269 | tests/test_gateway_admin.py::test_expired_pending_registration_releases_its_slot_and_cannot_be_approved | willy_gateway loopback administrator control plane | 验证 `expired_pending_registration_releases_its_slot_and_cannot_be_approved` 行为 | tmp_path 隔离工作区 |
| T270 | tests/test_gateway_offline_acceptance.py::test_device_token_replay_and_revocation_are_enforced_offline | willy_gateway 离线 fake-upstream 验收 | 验证 `device_token_replay_and_revocation_are_enforced_offline` 行为 | tmp_path 隔离工作区 |
| T271 | tests/test_gateway_offline_acceptance.py::test_quota_and_streaming_guard_block_fake_upstream_before_forwarding | willy_gateway 离线 fake-upstream 验收 | 验证 `quota_and_streaming_guard_block_fake_upstream_before_forwarding` 行为 | tmp_path 隔离工作区 |
| T272 | tests/test_gateway_offline_acceptance.py::test_timeout_keeps_then_expires_unknown_reservation_without_sleeping | willy_gateway 离线 fake-upstream 验收 | 验证 `timeout_keeps_then_expires_unknown_reservation_without_sleeping` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T273 | tests/test_gateway_offline_acceptance.py::test_tool_call_forwarding_and_audit_redaction_are_observable_offline | willy_gateway 离线 fake-upstream 验收 | 验证 `tool_call_forwarding_and_audit_redaction_are_observable_offline` 行为 | tmp_path 隔离工作区 |
| T337 | tests/test_managed_gateway.py::test_profile_is_fixed_to_https_and_cannot_accept_a_remote_plain_http_endpoint | willy.managed_gateway device identity and token-refreshing client | 验证 `profile_is_fixed_to_https_and_cannot_accept_a_remote_plain_http_endpoint` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T338 | tests/test_managed_gateway.py::test_identity_status_does_not_create_a_private_key_until_managed_mode_is_selected | willy.managed_gateway device identity and token-refreshing client | 验证 `identity_status_does_not_create_a_private_key_until_managed_mode_is_selected` 行为 | tmp_path 隔离工作区 |
| T339 | tests/test_managed_gateway.py::test_registration_sends_only_a_public_key_and_persists_a_private_machine_identity | willy.managed_gateway device identity and token-refreshing client | 验证 `registration_sends_only_a_public_key_and_persists_a_private_machine_identity` 行为 | tmp_path 隔离工作区 |
| T340 | tests/test_managed_gateway.py::test_gateway_registration_does_not_inherit_environment_proxies | willy.managed_gateway device identity and token-refreshing client | 验证 `gateway_registration_does_not_inherit_environment_proxies` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T341 | tests/test_managed_gateway.py::test_token_exchange_is_signed_cached_and_refreshed_before_expiry | willy.managed_gateway device identity and token-refreshing client | 验证 `token_exchange_is_signed_cached_and_refreshed_before_expiry` 行为 | tmp_path 隔离工作区 |
| T342 | tests/test_managed_gateway.py::test_usage_snapshot_checks_the_gateway_without_calling_chat_completions | willy.managed_gateway device identity and token-refreshing client | 验证 `usage_snapshot_checks_the_gateway_without_calling_chat_completions` 行为 | tmp_path 隔离工作区 |
| T343 | tests/test_managed_gateway.py::test_managed_client_gets_a_token_for_each_expired_openai_call | willy.managed_gateway device identity and token-refreshing client | 验证 `managed_client_gets_a_token_for_each_expired_openai_call` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T344 | tests/test_managed_gateway.py::test_client_registration_and_signed_token_exchange_reach_the_real_gateway_asgi_app | willy.managed_gateway device identity and token-refreshing client | 验证 `client_registration_and_signed_token_exchange_reach_the_real_gateway_asgi_app` 行为 | tmp_path 隔离工作区 |
| T345 | tests/test_managed_gateway_bundle.py::test_client_bundle_includes_profile_and_excludes_local_runtime_state | willy.managed_gateway_bundle release client packaging | 验证 `client_bundle_includes_profile_and_excludes_local_runtime_state` 行为 | tmp_path 隔离工作区 |
| T346 | tests/test_managed_gateway_bundle.py::test_client_bundle_rejects_loopback_or_plain_http_profile | willy.managed_gateway_bundle release client packaging | 验证 `client_bundle_rejects_loopback_or_plain_http_profile` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T347 | tests/test_managed_gateway_bundle.py::test_client_bundle_rejects_profile_inside_source_tree | willy.managed_gateway_bundle release client packaging | 验证 `client_bundle_rejects_profile_inside_source_tree` 行为 | tmp_path 隔离工作区 + 异常断言 |
