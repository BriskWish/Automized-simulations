# Willy 测试用例集

> 维护角色：6 号测试工程师
> 来源：`python3 -m pytest --collect-only -q` 收集到 594 条 pytest 用例；另有 18 条离线 LLM mock eval 场景；本台账共 612 条记录。

## 使用说明

- `T001` 起的记录与 pytest node ID 一一对应；参数化变体按独立用例编号。
- `L001` 起的记录是 `tests.llm_eval.scenarios` 中的受控 LLM 行为场景，不计入 pytest 收集数。
- 测试方式由用例源码中的参数化、`tmp_path`、`monkeypatch`、mock/patch、异常断言与 external 标记归纳；详细断言以 node ID 对应源码为准。
- 重新生成：`python3 scripts/generate_test_case_catalog.py`。每次增删测试或 LLM 场景后必须重新生成并核对总数。

## 分类总览

| 分类 | 记录数 | 覆盖范围 |
|---|---:|---|
| A | 75 | UI 与前端交互：Gradio 布局、确认式操作、可视化、状态展示和公开错误边界。 |
| B | 99 | 环境与基础错误模型：依赖发现、环境变量优先级、能力报告脱敏与结构化错误协议。 |
| C | 46 | 配置与全局工具：config v2、迁移、分子知识库与 Layer 0 工具 schema/handler。 |
| D | 64 | Agent 与 LLM 行为：OpenAI-compatible 配置、LayerAgent 的上下文、受控动作、预算、重试、升级，以及 18 个离线 mock LLM 场景。 |
| E | 52 | 量子与跨层 Tool 契约：量子/引擎日志解析、量子工具与跨层 tool 返回协议。 |
| F | 39 | 拓扑后端与组装：Sobtop/OPLS 后端、manifest、重试账本、ITP 校验和主拓扑组装。 |
| G | 81 | 模拟执行、ETA 与后处理：EM/EQ/PROD 协议、GROMACS 适配、ETA、阶段产物和分析流程。 |
| H | 100 | 流水线编排与状态机：步骤构建、唯一步骤注册、失败修复、run workspace、公开状态和恢复边界。 |
| I | 41 | 运行管理、启动控制与运行助理：运行预留、互斥启动、进程生命周期、保留清理、审计、provenance、RunStore 与只读助理。 |
| J | 15 | 外部 Smoke 与发布证据：外部工具预检、fixture 完整性、证据归档和 required 发布门禁。 |

## A. UI 与前端交互

Gradio 布局、确认式操作、可视化、状态展示和公开错误边界。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T013 | tests/test_app_ui.py::test_visualization_panel_refreshes_current_run_structures_on_focus | app.py UI 结构与交互状态 | 验证 `visualization_panel_refreshes_current_run_structures_on_focus` 行为 | 进程内行为断言 |
| T014 | tests/test_app_ui.py::test_visualization_refresh_keeps_a_current_filename_or_uses_the_first_choice | app.py UI 结构与交互状态 | 验证 `visualization_refresh_keeps_a_current_filename_or_uses_the_first_choice` 行为 | monkeypatch |
| T015 | tests/test_app_ui.py::test_proposal_example_is_a_textbox_placeholder_only | app.py UI 结构与交互状态 | 验证 `proposal_example_is_a_textbox_placeholder_only` 行为 | 进程内行为断言 |
| T016 | tests/test_app_ui.py::test_assistant_chat_and_input_rows_share_bottom_alignment_contract | app.py UI 结构与交互状态 | 验证 `assistant_chat_and_input_rows_share_bottom_alignment_contract` 行为 | 进程内行为断言 |
| T017 | tests/test_app_ui.py::test_stop_control_uses_server_side_confirmation_without_cleanup_choices | app.py UI 结构与交互状态 | 验证 `stop_control_uses_server_side_confirmation_without_cleanup_choices` 行为 | 进程内行为断言 |
| T018 | tests/test_app_ui.py::test_stop_control_only_dispatches_after_second_server_side_confirmation | app.py UI 结构与交互状态 | 验证 `stop_control_only_dispatches_after_second_server_side_confirmation` 行为 | monkeypatch |
| T019 | tests/test_app_ui.py::test_stop_control_stays_interactive_when_backend_does_not_acknowledge | app.py UI 结构与交互状态 | 验证 `stop_control_stays_interactive_when_backend_does_not_acknowledge` 行为 | monkeypatch |
| T020 | tests/test_app_ui.py::test_task_workspace_has_four_equal_height_panels_and_chart_placeholder | app.py UI 结构与交互状态 | 验证 `task_workspace_has_four_equal_height_panels_and_chart_placeholder` 行为 | 进程内行为断言 |
| T021 | tests/test_app_ui.py::test_about_tab_exposes_project_summary_and_official_account_qr_code | app.py UI 结构与交互状态 | 验证 `about_tab_exposes_project_summary_and_official_account_qr_code` 行为 | 进程内行为断言 |
| T022 | tests/test_app_ui.py::test_configuration_tab_exposes_generic_llm_fields | app.py UI 结构与交互状态 | 验证 `configuration_tab_exposes_generic_llm_fields` 行为 | 进程内行为断言 |
| T023 | tests/test_app_ui.py::test_llm_connection_action_shows_loading_and_public_success_or_failure | app.py UI 结构与交互状态 | 验证 `llm_connection_action_shows_loading_and_public_success_or_failure` 行为 | monkeypatch |
| T024 | tests/test_app_ui.py::test_saving_llm_config_does_not_trigger_connection_test | app.py UI 结构与交互状态 | 验证 `saving_llm_config_does_not_trigger_connection_test` 行为 | monkeypatch |
| T025 | tests/test_app_ui.py::test_theme_toggle_is_a_borderless_fixed_control_at_the_bottom_right | app.py UI 结构与交互状态 | 验证 `theme_toggle_is_a_borderless_fixed_control_at_the_bottom_right` 行为 | 进程内行为断言 |
| T026 | tests/test_app_ui.py::test_ui_controls_use_one_cjk_capable_font_stack_for_chinese_and_latin_text | app.py UI 结构与交互状态 | 验证 `ui_controls_use_one_cjk_capable_font_stack_for_chinese_and_latin_text` 行为 | 进程内行为断言 |
| T027 | tests/test_app_ui.py::test_header_reserves_scrollbar_space_to_keep_its_title_fixed_across_tabs | app.py UI 结构与交互状态 | 验证 `header_reserves_scrollbar_space_to_keep_its_title_fixed_across_tabs` 行为 | 进程内行为断言 |
| T028 | tests/test_app_ui.py::test_proposal_chat_locks_controls_until_streaming_response_finishes | app.py UI 结构与交互状态 | 验证 `proposal_chat_locks_controls_until_streaming_response_finishes` 行为 | monkeypatch |
| T029 | tests/test_app_ui.py::test_proposal_chat_is_text_only_and_does_not_mutate_history | app.py UI 结构与交互状态 | 验证 `proposal_chat_is_text_only_and_does_not_mutate_history` 行为 | 进程内行为断言 |
| T030 | tests/test_app_ui.py::test_task_page_uses_text_confirmation_without_a_click_bridge | app.py UI 结构与交互状态 | 验证 `task_page_uses_text_confirmation_without_a_click_bridge` 行为 | 进程内行为断言 |
| T031 | tests/test_app_ui.py::test_run_assistant_shows_message_before_waiting_for_reply | app.py UI 结构与交互状态 | 验证 `run_assistant_shows_message_before_waiting_for_reply` 行为 | monkeypatch |
| T032 | tests/test_app_ui.py::test_run_assistant_resets_browser_history_when_run_changes | app.py UI 结构与交互状态 | 验证 `run_assistant_resets_browser_history_when_run_changes` 行为 | monkeypatch |
| T033 | tests/test_app_ui.py::test_run_assistant_refresh_replaces_live_bubbles_without_growing_history | app.py UI 结构与交互状态 | 验证 `run_assistant_refresh_replaces_live_bubbles_without_growing_history` 行为 | monkeypatch |
| T034 | tests/test_app_ui.py::test_run_assistant_refresh_resets_old_dialogue_on_run_switch | app.py UI 结构与交互状态 | 验证 `run_assistant_refresh_resets_old_dialogue_on_run_switch` 行为 | monkeypatch |
| T035 | tests/test_app_ui.py::test_run_assistant_text_stop_and_pause_never_call_stop_pipeline_or_llm | app.py UI 结构与交互状态 | 验证 `run_assistant_text_stop_and_pause_never_call_stop_pipeline_or_llm` 行为 | monkeypatch |
| T036 | tests/test_app_ui.py::test_run_assistant_renders_status_and_adjustment_as_separate_safe_bubbles | app.py UI 结构与交互状态 | 验证 `run_assistant_renders_status_and_adjustment_as_separate_safe_bubbles` 行为 | 进程内行为断言 |
| T037 | tests/test_app_ui.py::test_pending_action_approval_uses_adapter_without_llm | app.py UI 结构与交互状态 | 验证 `pending_action_approval_uses_adapter_without_llm` 行为 | monkeypatch |
| T038 | tests/test_app_ui.py::test_pending_action_natural_approval_uses_adapter_without_llm | app.py UI 结构与交互状态 | 验证 `pending_action_natural_approval_uses_adapter_without_llm` 行为 | monkeypatch |
| T039 | tests/test_app_ui.py::test_pending_action_controls_require_awaiting_confirmation | app.py UI 结构与交互状态 | 验证 `pending_action_controls_require_awaiting_confirmation` 行为 | monkeypatch |
| T040 | tests/test_app_ui.py::test_pending_action_revision_uses_backend_and_remains_pending | app.py UI 结构与交互状态 | 验证 `pending_action_revision_uses_backend_and_remains_pending` 行为 | monkeypatch |
| T041 | tests/test_app_ui.py::test_parameter_shorthand_is_revision_request_but_question_is_not | app.py UI 结构与交互状态 | 验证 `parameter_shorthand_is_revision_request_but_question_is_not` 行为 | 进程内行为断言 |
| T156 | tests/test_frontend_api.py::test_stop_during_gromacs_requests_checkpoint_first_shutdown | willy.frontend_api | 验证 `stop_during_gromacs_requests_checkpoint_first_shutdown` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T157 | tests/test_frontend_api.py::test_clean_stop_unlinks_only_known_root_artifacts | willy.frontend_api | 验证 `clean_stop_unlinks_only_known_root_artifacts` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T158 | tests/test_frontend_api.py::test_reconcile_stale_latest_run_marks_only_transient_status_aborted | willy.frontend_api | 验证 `reconcile_stale_latest_run_marks_only_transient_status_aborted` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T159 | tests/test_frontend_api.py::test_stopping_status_uses_public_checkpoint_message | willy.frontend_api | 验证 `stopping_status_uses_public_checkpoint_message` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T160 | tests/test_frontend_api.py::test_escalated_protocol_change_tells_user_the_run_was_not_modified | willy.frontend_api | 验证 `escalated_protocol_change_tells_user_the_run_was_not_modified` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T161 | tests/test_frontend_api.py::test_awaiting_confirmation_summary_explains_the_llm_and_user_boundary | willy.frontend_api | 验证 `awaiting_confirmation_summary_explains_the_llm_and_user_boundary` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T162 | tests/test_frontend_api.py::test_get_pending_action_reads_only_the_waiting_public_summary | willy.frontend_api | 验证 `get_pending_action_reads_only_the_waiting_public_summary` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T163 | tests/test_frontend_api.py::test_pending_action_never_leaks_from_old_run_to_newest_run | willy.frontend_api | 验证 `pending_action_never_leaks_from_old_run_to_newest_run` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T164 | tests/test_frontend_api.py::test_run_panel_snapshot_resolves_the_run_once_for_status_and_action | willy.frontend_api | 验证 `run_panel_snapshot_resolves_the_run_once_for_status_and_action` 行为 | monkeypatch |
| T165 | tests/test_frontend_api.py::test_active_pipeline_run_wins_over_newer_historical_index_entry | willy.frontend_api | 验证 `active_pipeline_run_wins_over_newer_historical_index_entry` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T166 | tests/test_frontend_api.py::test_confirm_pending_action_reserves_the_same_run_and_spawns_controlled_retry | willy.frontend_api | 验证 `confirm_pending_action_reserves_the_same_run_and_spawns_controlled_retry` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T167 | tests/test_frontend_api.py::test_confirm_pending_action_restores_waiting_state_when_runner_cannot_start | willy.frontend_api | 验证 `confirm_pending_action_restores_waiting_state_when_runner_cannot_start` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T168 | tests/test_frontend_api.py::test_run_status_cas_rejects_stale_revision_and_terminal_regression | willy.frontend_api | 验证 `run_status_cas_rejects_stale_revision_and_terminal_regression` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T169 | tests/test_frontend_api.py::test_revising_pending_action_keeps_run_waiting_and_config_unchanged | willy.frontend_api | 验证 `revising_pending_action_keeps_run_waiting_and_config_unchanged` 行为 | tmp_path 隔离工作区 + monkeypatch + 异常断言 |
| T170 | tests/test_frontend_api.py::test_invalid_revised_proposal_preserves_original_waiting_action | willy.frontend_api | 验证 `invalid_revised_proposal_preserves_original_waiting_action` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T171 | tests/test_frontend_api.py::test_pipeline_launch_receipt_does_not_repeat_the_proposed_plan | willy.frontend_api | 验证 `pipeline_launch_receipt_does_not_repeat_the_proposed_plan` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T172 | tests/test_frontend_api.py::test_run_assistant_defaults_to_the_latest_run | willy.frontend_api | 验证 `run_assistant_defaults_to_the_latest_run` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T173 | tests/test_frontend_api.py::test_run_assistant_fast_status_query_does_not_require_llm | willy.frontend_api | 验证 `run_assistant_fast_status_query_does_not_require_llm` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T174 | tests/test_frontend_api.py::test_run_assistant_pending_message_distinguishes_fact_and_explanation_paths | willy.frontend_api | 验证 `run_assistant_pending_message_distinguishes_fact_and_explanation_paths` 行为 | 进程内行为断言 |
| T175 | tests/test_frontend_api.py::test_llm_config_is_persisted_locally_without_leaking_to_ui | willy.frontend_api | 验证 `llm_config_is_persisted_locally_without_leaking_to_ui` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T176 | tests/test_frontend_api.py::test_llm_config_rejects_blank_or_multiline_values | willy.frontend_api | 验证 `llm_config_rejects_blank_or_multiline_values` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T177 | tests/test_frontend_api.py::test_llm_config_notice_shows_the_loaded_model_or_a_safe_fallback | willy.frontend_api | 验证 `llm_config_notice_shows_the_loaded_model_or_a_safe_fallback` 行为 | monkeypatch |
| T178 | tests/test_frontend_api.py::test_llm_connection_uses_transient_form_values_and_forces_a_tool_call | willy.frontend_api | 验证 `llm_connection_uses_transient_form_values_and_forces_a_tool_call` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T179 | tests/test_frontend_api.py::test_llm_connection_does_not_append_v1_to_the_entered_url | willy.frontend_api | 验证 `llm_connection_does_not_append_v1_to_the_entered_url` 行为 | monkeypatch |
| T180 | tests/test_frontend_api.py::test_llm_connection_rejects_invalid_url_without_constructing_a_client | willy.frontend_api | 验证 `llm_connection_rejects_invalid_url_without_constructing_a_client` 行为 | monkeypatch |
| T181 | tests/test_frontend_api.py::test_llm_connection_explains_missing_transient_key_and_v1_paths | willy.frontend_api | 验证 `llm_connection_explains_missing_transient_key_and_v1_paths` 行为 | monkeypatch |
| T182 | tests/test_frontend_api.py::test_llm_connection_maps_network_and_http_failures_to_public_results[html_response] | willy.frontend_api | 验证 `llm_connection_maps_network_and_http_failures_to_public_results` 行为；参数集 `html_response` | pytest 参数化 + monkeypatch |
| T183 | tests/test_frontend_api.py::test_llm_connection_maps_network_and_http_failures_to_public_results[status_401] | willy.frontend_api | 验证 `llm_connection_maps_network_and_http_failures_to_public_results` 行为；参数集 `status_401` | pytest 参数化 + monkeypatch |
| T184 | tests/test_frontend_api.py::test_llm_connection_maps_network_and_http_failures_to_public_results[status_403] | willy.frontend_api | 验证 `llm_connection_maps_network_and_http_failures_to_public_results` 行为；参数集 `status_403` | pytest 参数化 + monkeypatch |
| T185 | tests/test_frontend_api.py::test_llm_connection_maps_network_and_http_failures_to_public_results[status_404] | willy.frontend_api | 验证 `llm_connection_maps_network_and_http_failures_to_public_results` 行为；参数集 `status_404` | pytest 参数化 + monkeypatch |
| T186 | tests/test_frontend_api.py::test_llm_connection_maps_network_and_http_failures_to_public_results[status_429] | willy.frontend_api | 验证 `llm_connection_maps_network_and_http_failures_to_public_results` 行为；参数集 `status_429` | pytest 参数化 + monkeypatch |
| T187 | tests/test_frontend_api.py::test_llm_connection_maps_network_and_http_failures_to_public_results[timeout] | willy.frontend_api | 验证 `llm_connection_maps_network_and_http_failures_to_public_results` 行为；参数集 `timeout` | pytest 参数化 + monkeypatch |
| T188 | tests/test_frontend_api.py::test_llm_connection_maps_network_and_http_failures_to_public_results[network_failure] | willy.frontend_api | 验证 `llm_connection_maps_network_and_http_failures_to_public_results` 行为；参数集 `network_failure` | pytest 参数化 + monkeypatch |
| T189 | tests/test_frontend_api.py::test_llm_connection_maps_missing_choices_to_protocol_error | willy.frontend_api | 验证 `llm_connection_maps_missing_choices_to_protocol_error` 行为 | monkeypatch |
| T190 | tests/test_frontend_api.py::test_llm_connection_maps_missing_tool_call_and_hides_exception_details | willy.frontend_api | 验证 `llm_connection_maps_missing_tool_call_and_hides_exception_details` 行为 | monkeypatch |
| T191 | tests/test_frontend_api.py::test_real_llm_connection_is_explicitly_opt_in | willy.frontend_api | 验证：Exercise the configured endpoint only under --run-llm-connection. | 进程内行为断言 |
| T192 | tests/test_frontend_api.py::test_visualization_scans_only_pdb_and_mol2_from_current_run_directory | willy.frontend_api | 验证 `visualization_scans_only_pdb_and_mol2_from_current_run_directory` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T193 | tests/test_frontend_api.py::test_visualization_prefers_active_lock_then_newest_numbered_run_directory | willy.frontend_api | 验证 `visualization_prefers_active_lock_then_newest_numbered_run_directory` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T194 | tests/test_frontend_api.py::test_visualization_style_uses_balanced_defaults_and_clamps_user_values | willy.frontend_api | 验证 `visualization_style_uses_balanced_defaults_and_clamps_user_values` 行为 | 进程内行为断言 |
| T195 | tests/test_frontend_api.py::test_run_assistant_status_renders_only_public_chinese_activity | willy.frontend_api | 验证 `run_assistant_status_renders_only_public_chinese_activity` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T196 | tests/test_frontend_api.py::test_run_summary_renders_local_mdrun_heartbeat_without_inventing_eta | willy.frontend_api | 验证 `run_summary_renders_local_mdrun_heartbeat_without_inventing_eta` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T197 | tests/test_frontend_api.py::test_run_summary_uses_compact_current_stage_eta_label | willy.frontend_api | 验证 `run_summary_uses_compact_current_stage_eta_label` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T198 | tests/test_frontend_api.py::test_run_assistant_status_animates_completion_marker | willy.frontend_api | 验证 `run_assistant_status_animates_completion_marker` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T199 | tests/test_frontend_api.py::test_run_assistant_status_shows_public_repair_attempt_and_adjustments | willy.frontend_api | 验证 `run_assistant_status_shows_public_repair_attempt_and_adjustments` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T200 | tests/test_frontend_api.py::test_run_assistant_status_uses_only_public_error_summary | willy.frontend_api | 验证 `run_assistant_status_uses_only_public_error_summary` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T201 | tests/test_frontend_api.py::test_run_assistant_status_ignores_an_orphan_root_status | willy.frontend_api | 验证 `run_assistant_status_ignores_an_orphan_root_status` 行为 | tmp_path 隔离工作区 + monkeypatch |

## B. 环境与基础错误模型

依赖发现、环境变量优先级、能力报告脱敏与结构化错误协议。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T042 | tests/test_env_checker.py::TestDepResult::test_construction | willy.env_checker / TestDepResult | 验证 `construction` 行为 | 进程内行为断言 |
| T043 | tests/test_env_checker.py::TestDepResult::test_needed_by_is_list | willy.env_checker / TestDepResult | 验证：needed_by 应为字符串列表 | 进程内行为断言 |
| T044 | tests/test_env_checker.py::TestDepResult::test_default_status_is_ok | willy.env_checker / TestDepResult | 验证 `default_status_is_ok` 行为 | 进程内行为断言 |
| T045 | tests/test_env_checker.py::TestDepResult::test_status_values | willy.env_checker / TestDepResult | 验证 `status_values` 行为 | 进程内行为断言 |
| T046 | tests/test_env_checker.py::TestEnvReport::test_empty_report | willy.env_checker / TestEnvReport | 验证 `empty_report` 行为 | 进程内行为断言 |
| T047 | tests/test_env_checker.py::TestEnvReport::test_all_ok | willy.env_checker / TestEnvReport | 验证 `all_ok` 行为 | 进程内行为断言 |
| T048 | tests/test_env_checker.py::TestEnvReport::test_some_missing | willy.env_checker / TestEnvReport | 验证 `some_missing` 行为 | 进程内行为断言 |
| T049 | tests/test_env_checker.py::TestEnvReport::test_failed_strs_format | willy.env_checker / TestEnvReport | 验证：failed_strs 应为人类可读的字符串 | 进程内行为断言 |
| T050 | tests/test_env_checker.py::TestEnvReport::test_is_ok_with_module_filter | willy.env_checker / TestEnvReport | 验证：is_ok 应按模块过滤 | 进程内行为断言 |
| T051 | tests/test_env_checker.py::TestEnvReport::test_format_method | willy.env_checker / TestEnvReport | 验证：format() 应返回人类可读的表格 | 进程内行为断言 |
| T052 | tests/test_env_checker.py::TestEnvReport::test_failed_strs_handles_no_exec | willy.env_checker / TestEnvReport | 验证：failed_strs 应处理 no_exec 状态 | 进程内行为断言 |
| T053 | tests/test_env_checker.py::TestDependenciesDefinition::test_dependencies_defined | willy.env_checker / TestDependenciesDefinition | 验证 `dependencies_defined` 行为 | 进程内行为断言 |
| T054 | tests/test_env_checker.py::TestDependenciesDefinition::test_dependency_names_unique | willy.env_checker / TestDependenciesDefinition | 验证 `dependency_names_unique` 行为 | 进程内行为断言 |
| T055 | tests/test_env_checker.py::TestDependenciesDefinition::test_dependencies_are_depresults | willy.env_checker / TestDependenciesDefinition | 验证：_DEPENDENCIES 中的每个条目都应是 DepResult 实例 | 进程内行为断言 |
| T056 | tests/test_env_checker.py::TestDependenciesDefinition::test_dependencies_have_kinds | willy.env_checker / TestDependenciesDefinition | 验证 `dependencies_have_kinds` 行为 | 进程内行为断言 |
| T057 | tests/test_env_checker.py::TestDependenciesDefinition::test_dependencies_grouped_by_module | willy.env_checker / TestDependenciesDefinition | 验证：依赖项应按实际执行步骤的规范名称分组 | 进程内行为断言 |
| T058 | tests/test_env_checker.py::TestDependenciesDefinition::test_dependency_ownership_is_derived_from_execution_registry | willy.env_checker / TestDependenciesDefinition | 验证 `dependency_ownership_is_derived_from_execution_registry` 行为 | 进程内行为断言 |
| T059 | tests/test_env_checker.py::TestDependenciesDefinition::test_envvar_dependencies_exist | willy.env_checker / TestDependenciesDefinition | 验证：BOSSdir 作为过渡期兼容变量保留在依赖报告中 | 进程内行为断言 |
| T060 | tests/test_env_checker.py::TestCheckAll::test_check_all_returns_env_report | willy.env_checker / TestCheckAll | 验证：check_all 应返回 EnvReport —— 不会崩溃 | 进程内行为断言 |
| T061 | tests/test_env_checker.py::TestCheckAll::test_check_all_has_results | willy.env_checker / TestCheckAll | 验证 `check_all_has_results` 行为 | 进程内行为断言 |
| T062 | tests/test_env_checker.py::TestCheckAll::test_check_all_result_statuses_valid | willy.env_checker / TestCheckAll | 验证：每个结果的状态应为有效值 | 进程内行为断言 |
| T063 | tests/test_env_checker.py::TestCheckModule::test_check_known_module | willy.env_checker / TestCheckModule | 验证：已知模块应返回 EnvReport | 进程内行为断言 |
| T064 | tests/test_env_checker.py::TestCheckModule::test_check_unknown_module | willy.env_checker / TestCheckModule | 验证：未知模块应返回空报告 | 进程内行为断言 |
| T065 | tests/test_env_checker.py::TestCheckModule::test_all_modules_work | willy.env_checker / TestCheckModule | 验证：所有 referenced 模块应可检查且不崩溃 | 进程内行为断言 |
| T066 | tests/test_env_checker.py::TestCheckModule::test_boss_default_does_not_mutate_process_environment | willy.env_checker / TestCheckModule | 验证 `boss_default_does_not_mutate_process_environment` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T067 | tests/test_env_checker.py::TestCheckModule::test_gaussian_helper_uses_registered_step_name | willy.env_checker / TestCheckModule | 验证：兼容 helper 也必须查询 env_checker 中真实注册的步骤名 | mock/patch |
| T068 | tests/test_env_checker.py::TestEnsure::test_ensure_passes_when_all_ok | willy.env_checker / TestEnsure | 验证：当所有依赖就绪时，ensure 不应抛出异常 | mock/patch |
| T069 | tests/test_env_checker.py::TestEnsure::test_ensure_raises_runtime_error_when_missing | willy.env_checker / TestEnsure | 验证：当依赖缺失时，ensure 应抛出 RuntimeError | mock/patch + 异常断言 |
| T070 | tests/test_env_checker.py::TestEnvCheckerEdgeCases::test_envvar_dependency_handling | willy.env_checker / TestEnvCheckerEdgeCases | 验证：旧 BOSSdir 显示名仍用于兼容既有预检界面 | 进程内行为断言 |
| T071 | tests/test_env_checker.py::TestEnvCheckerEdgeCases::test_file_exec_dependency_handling | willy.env_checker / TestEnvCheckerEdgeCases | 验证：file_exec 类型的依赖应存在 | 进程内行为断言 |
| T072 | tests/test_env_checker.py::TestEnvCheckerEdgeCases::test_vendor_paths_exist_in_dependencies | willy.env_checker / TestEnvCheckerEdgeCases | 验证：vendor/ 路径应存在于某些依赖中 | 进程内行为断言 |
| T073 | tests/test_env_checker.py::TestEnvCheckerEdgeCases::test_check_all_does_not_crash | willy.env_checker / TestEnvCheckerEdgeCases | 验证：check_all 即使依赖不可用也不应引发异常 | 进程内行为断言 |
| T074 | tests/test_env_registry.py::test_standard_binary_override_precedes_path | willy.env_registry | 验证 `standard_binary_override_precedes_path` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T075 | tests/test_env_registry.py::test_dotenv_standard_override_is_loaded_from_project_root | willy.env_registry | 验证 `dotenv_standard_override_is_loaded_from_project_root` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T076 | tests/test_env_registry.py::test_dotenv_value_uses_process_environment_before_dotenv | willy.env_registry | 验证 `dotenv_value_uses_process_environment_before_dotenv` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T077 | tests/test_env_registry.py::test_invalid_explicit_binary_is_configuration_error | willy.env_registry | 验证 `invalid_explicit_binary_is_configuration_error` 行为 | monkeypatch |
| T078 | tests/test_env_registry.py::test_project_root_never_falls_back_to_current_working_directory | willy.env_registry | 验证 `project_root_never_falls_back_to_current_working_directory` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T079 | tests/test_env_registry.py::test_orca_home_resolves_companions_and_child_library_path | willy.env_registry | 验证 `orca_home_resolves_companions_and_child_library_path` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T080 | tests/test_env_registry.py::test_ligpargen_child_gets_bossdir_without_global_mutation | willy.env_registry | 验证 `ligpargen_child_gets_bossdir_without_global_mutation` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T081 | tests/test_env_registry.py::test_capability_report_is_redacted_and_run_local | willy.env_registry | 验证 `capability_report_is_redacted_and_run_local` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T082 | tests/test_errors.py::TestErrorKind::test_all_kinds_have_unique_values | willy.errors / TestErrorKind | 验证：所有 ErrorKind 成员的值必须唯一 | 进程内行为断言 |
| T083 | tests/test_errors.py::TestErrorKind::test_retryable_property_exists_for_all | willy.errors / TestErrorKind | 验证：每个 ErrorKind 都应有 retryable 属性 | 进程内行为断言 |
| T084 | tests/test_errors.py::TestErrorKind::test_rollback_required_property_exists_for_all | willy.errors / TestErrorKind | 验证：每个 ErrorKind 都应有 rollback_required 属性 | 进程内行为断言 |
| T085 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.SCF_NOT_CONVERGED] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.SCF_NOT_CONVERGED` | pytest 参数化 |
| T086 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.GEOM_NOT_CONVERGED] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.GEOM_NOT_CONVERGED` | pytest 参数化 |
| T087 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.GAUSSIAN_CRASH] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.GAUSSIAN_CRASH` | pytest 参数化 |
| T088 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.ORCA_CRASH] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.ORCA_CRASH` | pytest 参数化 |
| T089 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.FORMCHK_FAILED] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.FORMCHK_FAILED` | pytest 参数化 |
| T090 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.GROMPP_FAILED] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.GROMPP_FAILED` | pytest 参数化 |
| T091 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.MDRUN_FAILED] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.MDRUN_FAILED` | pytest 参数化 |
| T092 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.EM_NOT_CONVERGED] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.EM_NOT_CONVERGED` | pytest 参数化 |
| T093 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.EQ_NOT_CONVERGED] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.EQ_NOT_CONVERGED` | pytest 参数化 |
| T094 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.TIMEOUT] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.TIMEOUT` | pytest 参数化 |
| T095 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.SOBTOP_FAILED] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.SOBTOP_FAILED` | pytest 参数化 |
| T096 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.LIGPARGEN_FAILED] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.LIGPARGEN_FAILED` | pytest 参数化 |
| T097 | tests/test_errors.py::TestErrorKind::test_retryable_errors[ErrorKind.UNKNOWN] | willy.errors / TestErrorKind | 验证：可重试的错误应返回 retryable=True；参数集 `ErrorKind.UNKNOWN` | pytest 参数化 |
| T098 | tests/test_errors.py::TestErrorKind::test_non_retryable_errors[ErrorKind.DEPENDENCY_MISSING] | willy.errors / TestErrorKind | 验证：不可自动修复的错误应返回 retryable=False；参数集 `ErrorKind.DEPENDENCY_MISSING` | pytest 参数化 |
| T099 | tests/test_errors.py::TestErrorKind::test_non_retryable_errors[ErrorKind.DEPENDENCY_NO_EXEC] | willy.errors / TestErrorKind | 验证：不可自动修复的错误应返回 retryable=False；参数集 `ErrorKind.DEPENDENCY_NO_EXEC` | pytest 参数化 |
| T100 | tests/test_errors.py::TestErrorKind::test_non_retryable_errors[ErrorKind.RESP_FAILED] | willy.errors / TestErrorKind | 验证：不可自动修复的错误应返回 retryable=False；参数集 `ErrorKind.RESP_FAILED` | pytest 参数化 |
| T101 | tests/test_errors.py::TestErrorKind::test_non_retryable_errors[ErrorKind.FILE_NOT_FOUND] | willy.errors / TestErrorKind | 验证：不可自动修复的错误应返回 retryable=False；参数集 `ErrorKind.FILE_NOT_FOUND` | pytest 参数化 |
| T102 | tests/test_errors.py::TestErrorKind::test_non_retryable_errors[ErrorKind.CONFIG_INVALID] | willy.errors / TestErrorKind | 验证：不可自动修复的错误应返回 retryable=False；参数集 `ErrorKind.CONFIG_INVALID` | pytest 参数化 |
| T103 | tests/test_errors.py::TestErrorKind::test_rollback_required_errors[ErrorKind.GROMPP_FAILED] | willy.errors / TestErrorKind | 验证：回滚类错误应返回 rollback_required=True；参数集 `ErrorKind.GROMPP_FAILED` | pytest 参数化 |
| T104 | tests/test_errors.py::TestErrorKind::test_rollback_required_errors[ErrorKind.MDRUN_FAILED] | willy.errors / TestErrorKind | 验证：回滚类错误应返回 rollback_required=True；参数集 `ErrorKind.MDRUN_FAILED` | pytest 参数化 |
| T105 | tests/test_errors.py::TestErrorKind::test_rollback_required_errors[ErrorKind.FORMCHK_FAILED] | willy.errors / TestErrorKind | 验证：回滚类错误应返回 rollback_required=True；参数集 `ErrorKind.FORMCHK_FAILED` | pytest 参数化 |
| T106 | tests/test_errors.py::TestErrorKind::test_rollback_required_errors[ErrorKind.SOBTOP_FAILED] | willy.errors / TestErrorKind | 验证：回滚类错误应返回 rollback_required=True；参数集 `ErrorKind.SOBTOP_FAILED` | pytest 参数化 |
| T107 | tests/test_errors.py::TestErrorKind::test_rollback_required_errors[ErrorKind.LIGPARGEN_FAILED] | willy.errors / TestErrorKind | 验证：回滚类错误应返回 rollback_required=True；参数集 `ErrorKind.LIGPARGEN_FAILED` | pytest 参数化 |
| T108 | tests/test_errors.py::TestErrorKind::test_rollback_required_errors[ErrorKind.ORCA_CRASH] | willy.errors / TestErrorKind | 验证：回滚类错误应返回 rollback_required=True；参数集 `ErrorKind.ORCA_CRASH` | pytest 参数化 |
| T109 | tests/test_errors.py::TestErrorKind::test_rollback_required_errors[ErrorKind.GAUSSIAN_CRASH] | willy.errors / TestErrorKind | 验证：回滚类错误应返回 rollback_required=True；参数集 `ErrorKind.GAUSSIAN_CRASH` | pytest 参数化 |
| T110 | tests/test_errors.py::TestErrorKind::test_no_rollback_errors[ErrorKind.SCF_NOT_CONVERGED] | willy.errors / TestErrorKind | 验证：非回滚类错误应返回 rollback_required=False；参数集 `ErrorKind.SCF_NOT_CONVERGED` | pytest 参数化 |
| T111 | tests/test_errors.py::TestErrorKind::test_no_rollback_errors[ErrorKind.GEOM_NOT_CONVERGED] | willy.errors / TestErrorKind | 验证：非回滚类错误应返回 rollback_required=False；参数集 `ErrorKind.GEOM_NOT_CONVERGED` | pytest 参数化 |
| T112 | tests/test_errors.py::TestErrorKind::test_no_rollback_errors[ErrorKind.RESP_FAILED] | willy.errors / TestErrorKind | 验证：非回滚类错误应返回 rollback_required=False；参数集 `ErrorKind.RESP_FAILED` | pytest 参数化 |
| T113 | tests/test_errors.py::TestErrorKind::test_no_rollback_errors[ErrorKind.EM_NOT_CONVERGED] | willy.errors / TestErrorKind | 验证：非回滚类错误应返回 rollback_required=False；参数集 `ErrorKind.EM_NOT_CONVERGED` | pytest 参数化 |
| T114 | tests/test_errors.py::TestErrorKind::test_no_rollback_errors[ErrorKind.EQ_NOT_CONVERGED] | willy.errors / TestErrorKind | 验证：非回滚类错误应返回 rollback_required=False；参数集 `ErrorKind.EQ_NOT_CONVERGED` | pytest 参数化 |
| T115 | tests/test_errors.py::TestErrorKind::test_no_rollback_errors[ErrorKind.TIMEOUT] | willy.errors / TestErrorKind | 验证：非回滚类错误应返回 rollback_required=False；参数集 `ErrorKind.TIMEOUT` | pytest 参数化 |
| T116 | tests/test_errors.py::TestErrorKind::test_retryable_set_matches_all_retryable | willy.errors / TestErrorKind | 验证：_RETRYABLE 集合中不应有未知成员 | 进程内行为断言 |
| T117 | tests/test_errors.py::TestErrorKind::test_rollback_set_matches_all_rollback | willy.errors / TestErrorKind | 验证：_ROLLBACK_REQUIRED 集合中不应有未知成员 | 进程内行为断言 |
| T118 | tests/test_errors.py::TestErrorKind::test_unused_error_kinds | willy.errors / TestErrorKind | 验证：BUG: SOBTOP_EXIT_24 和 LOCK_CONFLICT 在 ErrorKind 中定义， 但在整个代码库中从未引用或抛出 | 进程内行为断言 |
| T119 | tests/test_errors.py::TestStepError::test_minimal_construction | willy.errors / TestStepError | 验证：仅 kind 必填 | 进程内行为断言 |
| T120 | tests/test_errors.py::TestStepError::test_full_construction | willy.errors / TestStepError | 验证：所有字段均应正确存储 | 进程内行为断言 |
| T121 | tests/test_errors.py::TestStepError::test_raw_output_truncation | willy.errors / TestStepError | 验证：raw_output 可接受长字符串（截断由调用者负责） | 进程内行为断言 |
| T122 | tests/test_errors.py::TestStepResult::test_success_result | willy.errors / TestStepResult | 验证 `success_result` 行为 | 进程内行为断言 |
| T123 | tests/test_errors.py::TestStepResult::test_failure_result | willy.errors / TestStepResult | 验证 `failure_result` 行为 | 进程内行为断言 |
| T124 | tests/test_errors.py::TestStepResult::test_escalated_result | willy.errors / TestStepResult | 验证：升级结果应同时设置 success=False 和 escalated=True | 进程内行为断言 |
| T125 | tests/test_errors.py::TestStepResult::test_defaults | willy.errors / TestStepResult | 验证：默认值应与文档一致 | 进程内行为断言 |
| T126 | tests/test_errors.py::TestStepResult::test_to_dict_preserves_protocol_fields_and_is_json_safe | willy.errors / TestStepResult | 验证 `to_dict_preserves_protocol_fields_and_is_json_safe` 行为 | 进程内行为断言 |
| T127 | tests/test_errors.py::TestDiagnosisResult::test_to_dict_marker | willy.errors / TestDiagnosisResult | 验证：to_dict() 必须包含 _diagnosis=True 标记 | 进程内行为断言 |
| T128 | tests/test_errors.py::TestDiagnosisResult::test_default_severity | willy.errors / TestDiagnosisResult | 验证：默认严重级别应为 'info' | 进程内行为断言 |
| T129 | tests/test_errors.py::TestDiagnosisResult::test_all_severity_levels | willy.errors / TestDiagnosisResult | 验证：所有严重级别均应支持 | 进程内行为断言 |
| T130 | tests/test_errors.py::TestDiagnosisResult::test_extra_fields_preserved | willy.errors / TestDiagnosisResult | 验证：extra 字段应在 to_dict() 中完整保留 | 进程内行为断言 |
| T131 | tests/test_errors.py::TestDiagnosisResult::test_empty_issues_and_evidence | willy.errors / TestDiagnosisResult | 验证：issues 和 evidence 默认为空列表 | 进程内行为断言 |
| T132 | tests/test_errors.py::TestRetryContext::test_not_exhausted_initially | willy.errors / TestRetryContext | 验证 `not_exhausted_initially` 行为 | 进程内行为断言 |
| T133 | tests/test_errors.py::TestRetryContext::test_exhausted_when_attempts_equal_max | willy.errors / TestRetryContext | 验证 `exhausted_when_attempts_equal_max` 行为 | 进程内行为断言 |
| T134 | tests/test_errors.py::TestRetryContext::test_exhausted_when_attempts_exceed_max | willy.errors / TestRetryContext | 验证：即使 attempts > max_attempts 也应返回 True | 进程内行为断言 |
| T135 | tests/test_errors.py::TestRetryContext::test_actions_tried_accumulation | willy.errors / TestRetryContext | 验证 `actions_tried_accumulation` 行为 | 进程内行为断言 |
| T136 | tests/test_errors.py::TestRetryContext::test_max_attempts_default | willy.errors / TestRetryContext | 验证：默认 max_attempts 应为 3 | 进程内行为断言 |
| T137 | tests/test_errors.py::TestRetryContext::test_last_raw_output_default | willy.errors / TestRetryContext | 验证 `last_raw_output_default` 行为 | 进程内行为断言 |
| T138 | tests/test_errors.py::TestErrorPropagation::test_step_error_from_result_extraction | willy.errors / TestErrorPropagation | 验证：从 StepResult 中提取 StepError 应保持完整性 | 进程内行为断言 |
| T139 | tests/test_errors.py::TestErrorPropagation::test_chain_of_failures | willy.errors / TestErrorPropagation | 验证：多个步骤的失败链应保留各个错误 | 进程内行为断言 |
| T140 | tests/test_errors.py::TestErrorPropagation::test_error_kind_determines_agent_behavior | willy.errors / TestErrorPropagation | 验证：Agent 行为应基于 ErrorKind，不应解析裸字符串 | 进程内行为断言 |

## C. 配置与全局工具

config v2、迁移、分子知识库与 Layer 0 工具 schema/handler。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T449 | tests/test_toolist_global.py::TestToolDefinitions::test_all_tools_have_required_format | willy.toolist_global / TestToolDefinitions | 验证：每个工具定义必须遵循 OpenAI function calling 格式 | 进程内行为断言 |
| T450 | tests/test_toolist_global.py::TestToolDefinitions::test_tool_names_unique | willy.toolist_global / TestToolDefinitions | 验证：工具名称必须唯一 | 进程内行为断言 |
| T451 | tests/test_toolist_global.py::TestToolDefinitions::test_tool_names_follow_convention | willy.toolist_global / TestToolDefinitions | 验证：工具名称必须遵循 tools_{action}_{target} 命名规范 | 进程内行为断言 |
| T452 | tests/test_toolist_global.py::TestToolDefinitions::test_required_tools_present | willy.toolist_global / TestToolDefinitions | 验证：所有 9 个必需工具必须存在 | 进程内行为断言 |
| T453 | tests/test_toolist_global.py::TestToolsValidateConfig::test_handler_validates_config | willy.toolist_global / TestToolsValidateConfig | 验证 `handler_validates_config` 行为 | 进程内行为断言 |
| T454 | tests/test_toolist_global.py::TestToolsValidateConfig::test_json_parse_error_handling | willy.toolist_global / TestToolsValidateConfig | 验证 `json_parse_error_handling` 行为 | 进程内行为断言 |
| T455 | tests/test_toolist_global.py::TestMoleculeRegistry::test_registry_has_entries | willy.toolist_global / TestMoleculeRegistry | 验证：注册表必须提供知识库或内置兜底分子 | 进程内行为断言 |
| T456 | tests/test_toolist_global.py::TestMoleculeRegistry::test_lookup_exact_registered_name | willy.toolist_global / TestMoleculeRegistry | 验证：精确注册的离子名必须返回其结构化条目 | 进程内行为断言 |
| T457 | tests/test_toolist_global.py::TestMoleculeRegistry::test_lookup_case_insensitive | willy.toolist_global / TestMoleculeRegistry | 验证：ASCII 分子名应不区分大小写 | 进程内行为断言 |
| T458 | tests/test_toolist_global.py::TestMoleculeRegistry::test_lookup_unknown_returns_none | willy.toolist_global / TestMoleculeRegistry | 验证：完全未知的分子应返回 None | 进程内行为断言 |
| T459 | tests/test_toolist_global.py::TestMoleculeRegistry::test_alias_lookup | willy.toolist_global / TestMoleculeRegistry | 验证：知识库中的中文别名应返回对应离子 | 进程内行为断言 |
| T460 | tests/test_toolist_global.py::TestCompoundResolution::test_known_compounds_can_be_split | willy.toolist_global / TestCompoundResolution | 验证 `known_compounds_can_be_split` 行为 | 进程内行为断言 |
| T461 | tests/test_toolist_global.py::TestCompoundResolution::test_compound_split_preserves_stoichiometry | willy.toolist_global / TestCompoundResolution | 验证 `compound_split_preserves_stoichiometry` 行为 | 进程内行为断言 |
| T462 | tests/test_toolist_global.py::TestErrorDiagnosisPresets::test_errors_dict_has_entries | willy.toolist_global / TestErrorDiagnosisPresets | 验证 `errors_dict_has_entries` 行为 | 进程内行为断言 |
| T463 | tests/test_toolist_global.py::TestErrorDiagnosisPresets::test_error_diagnosis_returns_hints | willy.toolist_global / TestErrorDiagnosisPresets | 验证：每个错误条目应至少包含一条 hint | 进程内行为断言 |
| T464 | tests/test_toolist_global.py::TestHandleToolCall::test_unknown_tool_returns_error | willy.toolist_global / TestHandleToolCall | 验证：未知工具名应返回错误 JSON | 进程内行为断言 |
| T465 | tests/test_toolist_global.py::TestHandleToolCall::test_refresh_structs | willy.toolist_global / TestHandleToolCall | 验证：tools_refresh_structs 应重新加载注册表 | monkeypatch |
| T466 | tests/test_toolist_global.py::TestHandleToolCall::test_get_box_density_known_system | willy.toolist_global / TestHandleToolCall | 验证：tools_get_box_density returns the mass-density default contract. | 进程内行为断言 |
| T467 | tests/test_toolist_global.py::TestHandleToolCall::test_lookup_md_defaults_for_electrolyte | willy.toolist_global / TestHandleToolCall | 验证：tools_lookup_md_defaults 对 electrolyte 返回合理默认值 | 进程内行为断言 |
| T468 | tests/test_toolist_global.py::TestHandleToolCall::test_set_backend_quantum | willy.toolist_global / TestHandleToolCall | 验证：tools_set_backend_quantum 应更新 config.json | monkeypatch |
| T469 | tests/test_toolist_global.py::TestHandleToolCall::test_lookup_basis_set_returns_recommendation | willy.toolist_global / TestHandleToolCall | 验证：tools_lookup_basis_set 应返回基组推荐 | 进程内行为断言 |
| T570 | tests/test_workflow_config.py::test_valid_v2_config_passes | willy.workflow_config 与 MD 配置协议 | 验证 `valid_v2_config_passes` 行为 | 进程内行为断言 |
| T571 | tests/test_workflow_config.py::test_outer_schema_keeps_unknown_extensions_but_reports_them_structurally | willy.workflow_config 与 MD 配置协议 | 验证 `outer_schema_keeps_unknown_extensions_but_reports_them_structurally` 行为 | 进程内行为断言 |
| T572 | tests/test_workflow_config.py::test_outer_schema_rejects_malformed_nested_sections_before_defaults | willy.workflow_config 与 MD 配置协议 | 验证 `outer_schema_rejects_malformed_nested_sections_before_defaults` 行为 | 进程内行为断言 |
| T573 | tests/test_workflow_config.py::test_apply_config_rejects_invalid_outer_shape_without_writing | willy.workflow_config 与 MD 配置协议 | 验证 `apply_config_rejects_invalid_outer_shape_without_writing` 行为 | monkeypatch + 异常断言 |
| T574 | tests/test_workflow_config.py::test_apply_config_preserves_forward_compatible_extension | willy.workflow_config 与 MD 配置协议 | 验证 `apply_config_preserves_forward_compatible_extension` 行为 | monkeypatch |
| T575 | tests/test_workflow_config.py::test_malformed_config_returns_displayable_issues[None-config \u5fc5\u987b\u662f\u5bf9\u8c61] | willy.workflow_config 与 MD 配置协议 | 验证 `malformed_config_returns_displayable_issues` 行为；参数集 `None-config \u5fc5\u987b\u662f\u5bf9\u8c61` | pytest 参数化 |
| T576 | tests/test_workflow_config.py::test_malformed_config_returns_displayable_issues[config1-residues \u5fc5\u987b\u662f\u5bf9\u8c61] | willy.workflow_config 与 MD 配置协议 | 验证 `malformed_config_returns_displayable_issues` 行为；参数集 `config1-residues \u5fc5\u987b\u662f\u5bf9\u8c61` | pytest 参数化 |
| T577 | tests/test_workflow_config.py::test_malformed_config_returns_displayable_issues[config2-residues.A \u5fc5\u987b\u662f\u6570\u503c] | willy.workflow_config 与 MD 配置协议 | 验证 `malformed_config_returns_displayable_issues` 行为；参数集 `config2-residues.A \u5fc5\u987b\u662f\u6570\u503c` | pytest 参数化 |
| T578 | tests/test_workflow_config.py::test_malformed_config_returns_displayable_issues[config3-molecules \u5fc5\u987b\u662f\u5bf9\u8c61] | willy.workflow_config 与 MD 配置协议 | 验证 `malformed_config_returns_displayable_issues` 行为；参数集 `config3-molecules \u5fc5\u987b\u662f\u5bf9\u8c61` | pytest 参数化 |
| T579 | tests/test_workflow_config.py::test_legacy_durations_are_rejected_without_migration | willy.workflow_config 与 MD 配置协议 | 验证 `legacy_durations_are_rejected_without_migration` 行为 | 进程内行为断言 |
| T580 | tests/test_workflow_config.py::test_non_neutral_system_requires_confirmation | willy.workflow_config 与 MD 配置协议 | 验证 `non_neutral_system_requires_confirmation` 行为 | 进程内行为断言 |
| T581 | tests/test_workflow_config.py::test_invalid_eq_and_prod_boundaries_are_reported | willy.workflow_config 与 MD 配置协议 | 验证 `invalid_eq_and_prod_boundaries_are_reported` 行为 | 进程内行为断言 |
| T582 | tests/test_workflow_config.py::test_prod_temperature_must_match_eq_target | willy.workflow_config 与 MD 配置协议 | 验证 `prod_temperature_must_match_eq_target` 行为 | 进程内行为断言 |
| T583 | tests/test_workflow_config.py::test_special_system_rejects_isotropic_pressure_coupling | willy.workflow_config 与 MD 配置协议 | 验证 `special_system_rejects_isotropic_pressure_coupling` 行为 | 进程内行为断言 |
| T584 | tests/test_workflow_config.py::test_defaults_create_schema_v2_without_legacy_fields | willy.workflow_config 与 MD 配置协议 | 验证 `defaults_create_schema_v2_without_legacy_fields` 行为 | 进程内行为断言 |
| T585 | tests/test_workflow_config.py::test_defaults_preserve_existing_number_density_snapshot | willy.workflow_config 与 MD 配置协议 | 验证 `defaults_preserve_existing_number_density_snapshot` 行为 | 进程内行为断言 |
| T586 | tests/test_workflow_config.py::test_defaults_preserve_an_explicit_trr_output_request | willy.workflow_config 与 MD 配置协议 | 验证 `defaults_preserve_an_explicit_trr_output_request` 行为 | 进程内行为断言 |
| T587 | tests/test_workflow_config.py::test_defaults_preserve_an_explicit_oplsaa_force_field_request | willy.workflow_config 与 MD 配置协议 | 验证 `defaults_preserve_an_explicit_oplsaa_force_field_request` 行为 | 进程内行为断言 |
| T588 | tests/test_workflow_config.py::test_config_prompt_describes_explicit_trr_and_oplsaa_requests | willy.workflow_config 与 MD 配置协议 | 验证 `config_prompt_describes_explicit_trr_and_oplsaa_requests` 行为 | 进程内行为断言 |
| T589 | tests/test_workflow_config.py::test_defaults_preserve_legacy_fields_for_visible_rejection | willy.workflow_config 与 MD 配置协议 | 验证 `defaults_preserve_legacy_fields_for_visible_rejection` 行为 | 进程内行为断言 |
| T590 | tests/test_workflow_config.py::test_explicit_migration_writes_v2_and_mapping_without_overwriting_source | willy.workflow_config 与 MD 配置协议 | 验证 `explicit_migration_writes_v2_and_mapping_without_overwriting_source` 行为 | tmp_path 隔离工作区 |
| T591 | tests/test_workflow_config.py::test_confirmed_migration_adoption_replaces_active_config_with_valid_protocol | willy.workflow_config 与 MD 配置协议 | 验证 `confirmed_migration_adoption_replaces_active_config_with_valid_protocol` 行为 | tmp_path 隔离工作区 |
| T592 | tests/test_workflow_config.py::test_annealing_temperatures_must_descend_strictly | willy.workflow_config 与 MD 配置协议 | 验证 `annealing_temperatures_must_descend_strictly` 行为 | 进程内行为断言 |
| T593 | tests/test_workflow_config.py::test_apply_config_writes_v2_snapshot | willy.workflow_config 与 MD 配置协议 | 验证 `apply_config_writes_v2_snapshot` 行为 | monkeypatch |
| T594 | tests/test_workflow_config.py::test_apply_config_replaces_active_file_only_after_atomic_backup | willy.workflow_config 与 MD 配置协议 | 验证 `apply_config_replaces_active_file_only_after_atomic_backup` 行为 | monkeypatch |

## D. Agent 与 LLM 行为

OpenAI-compatible 配置、LayerAgent 的上下文、受控动作、预算、重试、升级，以及 18 个离线 mock LLM 场景。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T001 | tests/test_action_contract.py::test_default_catalog_covers_every_schema_and_declares_effects | willy.action_contract | 验证 `default_catalog_covers_every_schema_and_declares_effects` 行为 | 进程内行为断言 |
| T002 | tests/test_action_contract.py::test_proposal_is_json_safe_and_nested_arguments_are_immutable | willy.action_contract | 验证 `proposal_is_json_safe_and_nested_arguments_are_immutable` 行为 | 异常断言 |
| T003 | tests/test_action_contract.py::test_validation_requires_matching_enabled_declaration | willy.action_contract | 验证 `validation_requires_matching_enabled_declaration` 行为 | 异常断言 |
| T004 | tests/test_action_contract.py::test_executed_action_round_trip_does_not_accept_raw_output | willy.action_contract | 验证 `executed_action_round_trip_does_not_accept_raw_output` 行为 | 进程内行为断言 |
| T005 | tests/test_action_contract.py::test_read_only_declaration_cannot_claim_mutation | willy.action_contract | 验证 `read_only_declaration_cannot_claim_mutation` 行为 | 异常断言 |
| T006 | tests/test_action_contract.py::test_parameter_effect_raises_the_effect_of_a_specific_call | willy.action_contract | 验证 `parameter_effect_raises_the_effect_of_a_specific_call` 行为 | 进程内行为断言 |
| T007 | tests/test_agent_config.py::test_text_confirmation_starts_the_pending_plan_without_an_llm | willy.agent_config | 验证 `text_confirmation_starts_the_pending_plan_without_an_llm` 行为 | monkeypatch |
| T008 | tests/test_agent_config.py::test_text_confirmation_without_a_pending_plan_does_not_start | willy.agent_config | 验证 `text_confirmation_without_a_pending_plan_does_not_start` 行为 | monkeypatch |
| T009 | tests/test_agent_config.py::test_start_pipeline_rejects_validation_issues_before_reserving_a_run | willy.agent_config | 验证 `start_pipeline_rejects_validation_issues_before_reserving_a_run` 行为 | monkeypatch |
| T010 | tests/test_agent_config.py::test_new_failed_request_clears_an_older_pending_plan | willy.agent_config | 验证 `new_failed_request_clears_an_older_pending_plan` 行为 | monkeypatch |
| T011 | tests/test_agent_config.py::test_pending_plan_requires_its_own_latest_summary | willy.agent_config | 验证 `pending_plan_requires_its_own_latest_summary` 行为 | 进程内行为断言 |
| T012 | tests/test_agent_config.py::test_summary_uses_mass_density_default_prompt | willy.agent_config | 验证 `summary_uses_mass_density_default_prompt` 行为 | 进程内行为断言 |
| T202 | tests/test_layer_agent.py::TestEscalation::test_to_dict_truncates_raw_output | willy.layer_agent.LayerAgent / TestEscalation | 验证：to_dict() 应将 last_raw_output 截断到 500 字符 | 进程内行为断言 |
| T203 | tests/test_layer_agent.py::TestEscalation::test_empty_actions_and_output | willy.layer_agent.LayerAgent / TestEscalation | 验证：升级可能没有动作或输出 | 进程内行为断言 |
| T204 | tests/test_layer_agent.py::TestLayerAgentConstruction::test_minimal_construction | willy.layer_agent.LayerAgent / TestLayerAgentConstruction | 验证：最小有效构造应成功 | mock/patch |
| T205 | tests/test_layer_agent.py::TestLayerAgentConstruction::test_custom_max_retries | willy.layer_agent.LayerAgent / TestLayerAgentConstruction | 验证：自定义 max_retries 应被尊重 | mock/patch |
| T206 | tests/test_layer_agent.py::TestLayerAgentConstruction::test_on_action_callback | willy.layer_agent.LayerAgent / TestLayerAgentConstruction | 验证：on_action 回调应被存储 | mock/patch |
| T207 | tests/test_layer_agent.py::TestBuildContext::test_context_includes_step_info | willy.layer_agent.LayerAgent / TestBuildContext | 验证 `context_includes_step_info` 行为 | mock/patch |
| T208 | tests/test_layer_agent.py::TestBuildContext::test_context_truncates_long_config | willy.layer_agent.LayerAgent / TestBuildContext | 验证：config_text 应被截断到约 2000 字符 | mock/patch |
| T209 | tests/test_layer_agent.py::TestBuildContext::test_context_includes_artifacts | willy.layer_agent.LayerAgent / TestBuildContext | 验证 `context_includes_artifacts` 行为 | mock/patch |
| T210 | tests/test_layer_agent.py::TestBuildContext::test_context_includes_failed_step_outputs | willy.layer_agent.LayerAgent / TestBuildContext | 验证：失败步骤保留的中间产物也必须展示给 Agent | mock/patch |
| T211 | tests/test_layer_agent.py::TestEscalate::test_escalate_returns_failed_step_result | willy.layer_agent.LayerAgent / TestEscalate | 验证 `escalate_returns_failed_step_result` 行为 | mock/patch |
| T212 | tests/test_layer_agent.py::TestHandleFailure::test_handle_failure_returns_step_result | willy.layer_agent.LayerAgent / TestHandleFailure | 验证：handle_failure 应返回 StepResult | mock/patch |
| T213 | tests/test_layer_agent.py::TestHandleFailure::test_successful_tool_result_keeps_its_actual_step_identity | willy.layer_agent.LayerAgent / TestHandleFailure | 验证：Upstream repair success must not be relabelled as the failed EQ step. | mock/patch |
| T214 | tests/test_layer_agent.py::TestHandleFailure::test_handle_failure_exhausts_retries | willy.layer_agent.LayerAgent / TestHandleFailure | 验证：当超过 max_retries 时，应升级 | mock/patch |
| T215 | tests/test_layer_agent.py::TestHandleFailure::test_tool_failures_stop_at_hard_retry_limit | willy.layer_agent.LayerAgent / TestHandleFailure | 验证 `tool_failures_stop_at_hard_retry_limit` 行为 | mock/patch |
| T216 | tests/test_layer_agent.py::TestHandleFailure::test_protocol_change_request_escalates_without_a_second_llm_call | willy.layer_agent.LayerAgent / TestHandleFailure | 验证：A tool cannot turn a model-generated flag into user confirmation. | mock/patch |
| T217 | tests/test_layer_agent.py::TestHandleFailure::test_simulation_agent_blocks_protocol_change_before_tool_dispatch | willy.layer_agent.LayerAgent / TestHandleFailure | 验证 `simulation_agent_blocks_protocol_change_before_tool_dispatch` 行为 | mock/patch |
| T218 | tests/test_layer_agent.py::TestHandleFailure::test_handle_failure_detects_escalation_keyword | willy.layer_agent.LayerAgent / TestHandleFailure | 验证：当 LLM 在响应中说 'escalat' 时，应立即升级 | mock/patch |
| T219 | tests/test_layer_agent.py::TestHandleFailure::test_handle_failure_llm_exception_retry | willy.layer_agent.LayerAgent / TestHandleFailure | 验证：当 LLM API 抛出异常时，应重试一次 | mock/patch |
| T220 | tests/test_layer_agent.py::TestHandleFailure::test_llm_exceptions_consume_retry_budget_and_update_state | willy.layer_agent.LayerAgent / TestHandleFailure | 验证 `llm_exceptions_consume_retry_budget_and_update_state` 行为 | mock/patch |
| T221 | tests/test_layer_agent.py::TestLayerAgentEdgeCases::test_empty_tool_list | willy.layer_agent.LayerAgent / TestLayerAgentEdgeCases | 验证：零工具的 Agent 应能工作（纯文本响应） | mock/patch |
| T222 | tests/test_layer_agent.py::TestLayerAgentEdgeCases::test_tool_handler_returning_non_json | willy.layer_agent.LayerAgent / TestLayerAgentEdgeCases | 验证：工具处理程序可能返回非 JSON —— LLM 应处理它 | mock/patch |
| T223 | tests/test_layer_agent.py::TestLayerAgentEdgeCases::test_long_error_message | willy.layer_agent.LayerAgent / TestLayerAgentEdgeCases | 验证：极长的错误消息不应崩溃 Agent | mock/patch |
| T224 | tests/test_layer_agent.py::TestRecoveryAuthorization::test_model_confirmation_flag_cannot_dispatch_a_method_change | willy.layer_agent.LayerAgent / TestRecoveryAuthorization | 验证 `model_confirmation_flag_cannot_dispatch_a_method_change` 行为 | tmp_path 隔离工作区 + mock/patch |
| T225 | tests/test_layer_agent.py::TestRecoveryAuthorization::test_safe_dispatch_records_policy_and_execution_trace | willy.layer_agent.LayerAgent / TestRecoveryAuthorization | 验证 `safe_dispatch_records_policy_and_execution_trace` 行为 | tmp_path 隔离工作区 + mock/patch |
| T226 | tests/test_llm_budget.py::test_budget_limits_calls_and_reports_state | willy.llm_budget | 验证 `budget_limits_calls_and_reports_state` 行为 | 异常断言 |
| T227 | tests/test_llm_budget.py::test_circuit_breaker_opens_after_consecutive_failures | willy.llm_budget | 验证 `circuit_breaker_opens_after_consecutive_failures` 行为 | 异常断言 |
| T228 | tests/test_llm_config.py::test_no_key_means_llm_is_not_configured | willy.llm_config OpenAI-compatible 配置 | 验证 `no_key_means_llm_is_not_configured` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T229 | tests/test_llm_config.py::test_legacy_deepseek_key_uses_existing_defaults | willy.llm_config OpenAI-compatible 配置 | 验证 `legacy_deepseek_key_uses_existing_defaults` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T230 | tests/test_llm_config.py::test_generic_values_override_legacy_and_read_dotenv | willy.llm_config OpenAI-compatible 配置 | 验证 `generic_values_override_legacy_and_read_dotenv` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T231 | tests/test_llm_config.py::test_invalid_values_are_rejected_without_client_construction[not-a-url-model] | willy.llm_config OpenAI-compatible 配置 | 验证 `invalid_values_are_rejected_without_client_construction` 行为；参数集 `not-a-url-model` | pytest 参数化 + 异常断言 |
| T232 | tests/test_llm_config.py::test_invalid_values_are_rejected_without_client_construction[ftp://example.com-model] | willy.llm_config OpenAI-compatible 配置 | 验证 `invalid_values_are_rejected_without_client_construction` 行为；参数集 `ftp://example.com-model` | pytest 参数化 + 异常断言 |
| T233 | tests/test_llm_config.py::test_invalid_values_are_rejected_without_client_construction[https://example.com?token=x-model] | willy.llm_config OpenAI-compatible 配置 | 验证 `invalid_values_are_rejected_without_client_construction` 行为；参数集 `https://example.com?token=x-model` | pytest 参数化 + 异常断言 |
| T234 | tests/test_llm_config.py::test_invalid_values_are_rejected_without_client_construction[https://example.com-model\nother] | willy.llm_config OpenAI-compatible 配置 | 验证 `invalid_values_are_rejected_without_client_construction` 行为；参数集 `https://example.com-model\nother` | pytest 参数化 + 异常断言 |
| T235 | tests/test_llm_config.py::test_configured_client_uses_resolved_endpoint_and_model | willy.llm_config OpenAI-compatible 配置 | 验证 `configured_client_uses_resolved_endpoint_and_model` 行为 | tmp_path 隔离工作区 + monkeypatch |
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

量子/引擎日志解析、量子工具与跨层 tool 返回协议。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T236 | tests/test_log_parsers.py::TestParseGaussianLog::test_normal_termination | willy.log_parsers / TestParseGaussianLog | 验证 `normal_termination` 行为 | tmp_path 隔离工作区 |
| T237 | tests/test_log_parsers.py::TestParseGaussianLog::test_scf_not_converged | willy.log_parsers / TestParseGaussianLog | 验证 `scf_not_converged` 行为 | tmp_path 隔离工作区 |
| T238 | tests/test_log_parsers.py::TestParseGaussianLog::test_geom_not_converged | willy.log_parsers / TestParseGaussianLog | 验证 `geom_not_converged` 行为 | tmp_path 隔离工作区 |
| T239 | tests/test_log_parsers.py::TestParseGaussianLog::test_gaussian_crash | willy.log_parsers / TestParseGaussianLog | 验证 `gaussian_crash` 行为 | tmp_path 隔离工作区 |
| T240 | tests/test_log_parsers.py::TestParseGaussianLog::test_missing_file | willy.log_parsers / TestParseGaussianLog | 验证 `missing_file` 行为 | 进程内行为断言 |
| T241 | tests/test_log_parsers.py::TestParseGaussianLog::test_empty_file | willy.log_parsers / TestParseGaussianLog | 验证 `empty_file` 行为 | tmp_path 隔离工作区 |
| T242 | tests/test_log_parsers.py::TestParseGaussianLog::test_evidence_lines_truncated | willy.log_parsers / TestParseGaussianLog | 验证：证据行应被截断以控制上下文大小 | tmp_path 隔离工作区 |
| T243 | tests/test_log_parsers.py::TestParseGaussianLog::test_optimization_cycles_detected | willy.log_parsers / TestParseGaussianLog | 验证：优化周期数应被检测 | tmp_path 隔离工作区 |
| T244 | tests/test_log_parsers.py::TestParseOrcaOutput::test_normal_termination | willy.log_parsers / TestParseOrcaOutput | 验证 `normal_termination` 行为 | tmp_path 隔离工作区 |
| T245 | tests/test_log_parsers.py::TestParseOrcaOutput::test_scf_not_converged | willy.log_parsers / TestParseOrcaOutput | 验证 `scf_not_converged` 行为 | tmp_path 隔离工作区 |
| T246 | tests/test_log_parsers.py::TestParseOrcaOutput::test_missing_file | willy.log_parsers / TestParseOrcaOutput | 验证 `missing_file` 行为 | 进程内行为断言 |
| T247 | tests/test_log_parsers.py::TestParseOrcaOutput::test_orca_crash_detection | willy.log_parsers / TestParseOrcaOutput | 验证 `orca_crash_detection` 行为 | tmp_path 隔离工作区 |
| T248 | tests/test_log_parsers.py::TestParseGromacsLog::test_em_converged | willy.log_parsers / TestParseGromacsLog | 验证 `em_converged` 行为 | tmp_path 隔离工作区 |
| T249 | tests/test_log_parsers.py::TestParseGromacsLog::test_lincs_warnings_detected | willy.log_parsers / TestParseGromacsLog | 验证 `lincs_warnings_detected` 行为 | tmp_path 隔离工作区 |
| T250 | tests/test_log_parsers.py::TestParseGromacsLog::test_missing_file | willy.log_parsers / TestParseGromacsLog | 验证 `missing_file` 行为 | 进程内行为断言 |
| T251 | tests/test_log_parsers.py::TestParseGromacsLog::test_stage_specific_hints | willy.log_parsers / TestParseGromacsLog | 验证：不同阶段的提示应不同 | tmp_path 隔离工作区 |
| T252 | tests/test_log_parsers.py::TestDiagnoseLog::test_explicit_gaussian_engine | willy.log_parsers / TestDiagnoseLog | 验证 `explicit_gaussian_engine` 行为 | 进程内行为断言 |
| T253 | tests/test_log_parsers.py::TestDiagnoseLog::test_explicit_orca_engine | willy.log_parsers / TestDiagnoseLog | 验证 `explicit_orca_engine` 行为 | 进程内行为断言 |
| T254 | tests/test_log_parsers.py::TestDiagnoseLog::test_explicit_gromacs_engine | willy.log_parsers / TestDiagnoseLog | 验证 `explicit_gromacs_engine` 行为 | 进程内行为断言 |
| T255 | tests/test_log_parsers.py::TestDiagnoseLog::test_unknown_engine_fallback | willy.log_parsers / TestDiagnoseLog | 验证 `unknown_engine_fallback` 行为 | 进程内行为断言 |
| T256 | tests/test_log_parsers.py::TestEvidenceTruncation::test_very_long_lines_handled | willy.log_parsers / TestEvidenceTruncation | 验证：极长行不应导致解析器崩溃 | tmp_path 隔离工作区 |
| T257 | tests/test_log_parsers.py::TestEvidenceTruncation::test_binary_content_handled | willy.log_parsers / TestEvidenceTruncation | 验证：二进制内容不应导致解析器崩溃 | tmp_path 隔离工作区 |
| T470 | tests/test_toolist_quantum_topology.py::TestQuantumToolDefinitions::test_eight_tools_defined | willy.toolist_quantum / toolist_topology / TestQuantumToolDefinitions | 验证：应有 8 个量子工具 | 进程内行为断言 |
| T471 | tests/test_toolist_quantum_topology.py::TestQuantumToolDefinitions::test_all_tool_names | willy.toolist_quantum / toolist_topology / TestQuantumToolDefinitions | 验证 `all_tool_names` 行为 | 进程内行为断言 |
| T472 | tests/test_toolist_quantum_topology.py::TestQuantumToolDefinitions::test_tools_have_valid_json_schema | willy.toolist_quantum / toolist_topology / TestQuantumToolDefinitions | 验证 `tools_have_valid_json_schema` 行为 | 进程内行为断言 |
| T473 | tests/test_toolist_quantum_topology.py::TestQuantumToolDefinitions::test_retry_tools_use_current_fchk_contract | willy.toolist_quantum / toolist_topology / TestQuantumToolDefinitions | 验证：mol2 和 RESP 重试都必须消费 Step 2 的 *_opt.fchk | 进程内行为断言 |
| T474 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_unknown_tool_returns_error | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证 `unknown_tool_returns_error` 行为 | 进程内行为断言 |
| T475 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_diagnose_tool_returns_result | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：tools_diagnose_error_quantum 应返回诊断结果 | 进程内行为断言 |
| T476 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_modify_config_molecule | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：tools_modify_config_molecule 应更新 config.json | 进程内行为断言 |
| T477 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_modify_config_uses_the_active_run_snapshot | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：Agent 调整只影响当前运行，不得改写项目根配置 | tmp_path 隔离工作区 |
| T478 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_skip_molecule_quantum | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：tools_skip_molecule_quantum 应将分子加入跳过列表 | 进程内行为断言 |
| T479 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_mol2_conversion_error_on_missing_file | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：当文件不存在时，mol2 转换应优雅失败 | 进程内行为断言 |
| T480 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_mol2_conversion_returns_step_result_for_malformed_fchk | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：fchk 解析异常不得从公开工具路径泄露 | tmp_path 隔离工作区 |
| T481 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_mol2_conversion_rejects_missing_bond_connectivity[NBond] | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：缺失任一键连接字段时，不得生成可被 Sobtop 消费的 mol2；参数集 `NBond` | pytest 参数化 + tmp_path 隔离工作区 |
| T482 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_mol2_conversion_rejects_missing_bond_connectivity[IBond] | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：缺失任一键连接字段时，不得生成可被 Sobtop 消费的 mol2；参数集 `IBond` | pytest 参数化 + tmp_path 隔离工作区 |
| T483 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_mol2_conversion_rejects_missing_bond_connectivity[RBond] | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：缺失任一键连接字段时，不得生成可被 Sobtop 消费的 mol2；参数集 `RBond` | pytest 参数化 + tmp_path 隔离工作区 |
| T484 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_mol2_conversion_rejects_incomplete_coordinates | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：原子数与坐标数组长度不一致时必须失败 | tmp_path 隔离工作区 |
| T485 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_chg_retry_reports_missing_explicit_fchk[tools_retry_chg_g16] | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：两种后端重试都基于同一显式 fchk 输入；参数集 `tools_retry_chg_g16` | pytest 参数化 + tmp_path 隔离工作区 |
| T486 | tests/test_toolist_quantum_topology.py::TestQuantumToolHandler::test_chg_retry_reports_missing_explicit_fchk[tools_retry_chg_orca] | willy.toolist_quantum / toolist_topology / TestQuantumToolHandler | 验证：两种后端重试都基于同一显式 fchk 输入；参数集 `tools_retry_chg_orca` | pytest 参数化 + tmp_path 隔离工作区 |
| T487 | tests/test_toolist_quantum_topology.py::TestTopologyToolDefinitions::test_six_tools_defined | willy.toolist_quantum / toolist_topology / TestTopologyToolDefinitions | 验证：应有 6 个拓扑工具 | 进程内行为断言 |
| T488 | tests/test_toolist_quantum_topology.py::TestTopologyToolDefinitions::test_all_tool_names | willy.toolist_quantum / toolist_topology / TestTopologyToolDefinitions | 验证 `all_tool_names` 行为 | 进程内行为断言 |
| T489 | tests/test_toolist_quantum_topology.py::TestTopologyToolDefinitions::test_topology_config_tool_has_no_unused_default_charge | willy.toolist_quantum / toolist_topology / TestTopologyToolDefinitions | 验证 `topology_config_tool_has_no_unused_default_charge` 行为 | 进程内行为断言 |
| T490 | tests/test_toolist_quantum_topology.py::TestTopologyToolHandler::test_unknown_tool_returns_error | willy.toolist_quantum / toolist_topology / TestTopologyToolHandler | 验证 `unknown_tool_returns_error` 行为 | 进程内行为断言 |
| T491 | tests/test_toolist_quantum_topology.py::TestTopologyToolHandler::test_diagnose_tool_returns_result | willy.toolist_quantum / toolist_topology / TestTopologyToolHandler | 验证：tools_diagnose_error_topology 应返回诊断结果 | 进程内行为断言 |
| T492 | tests/test_toolist_quantum_topology.py::TestTopologyToolHandler::test_diagnose_detects_atomtype_error | willy.toolist_quantum / toolist_topology / TestTopologyToolHandler | 验证：应从 raw_output 中检测 atomtype 缺失 | 进程内行为断言 |
| T493 | tests/test_toolist_quantum_topology.py::TestTopologyToolHandler::test_diagnose_detects_sobtop_exit_24 | willy.toolist_quantum / toolist_topology / TestTopologyToolHandler | 验证：应从 raw_output 中检测 Sobtop 退出码 24 | 进程内行为断言 |
| T494 | tests/test_toolist_quantum_topology.py::TestTopologyToolHandler::test_diagnose_detects_ligpargen_error | willy.toolist_quantum / toolist_topology / TestTopologyToolHandler | 验证：应检测 LigParGen 相关错误 | 进程内行为断言 |
| T495 | tests/test_toolist_quantum_topology.py::TestTopologyToolHandler::test_modify_config_requires_active_run | willy.toolist_quantum / toolist_topology / TestTopologyToolHandler | 验证：Topology config tools may only mutate an explicit run snapshot. | 进程内行为断言 |
| T496 | tests/test_toolist_quantum_topology.py::TestTopologyToolHandler::test_skip_molecule_topology_is_disabled | willy.toolist_quantum / toolist_topology / TestTopologyToolHandler | 验证：Skip must not leave manifest and residues inconsistent. | 进程内行为断言 |
| T497 | tests/test_toolist_quantum_topology.py::TestCrossToolistConsistency::test_all_tool_names_use_tools_prefix | willy.toolist_quantum / toolist_topology / TestCrossToolistConsistency | 验证：所有工具名称必须以 tools_ 开头 | 进程内行为断言 |
| T498 | tests/test_toolist_quantum_topology.py::TestCrossToolistConsistency::test_all_handlers_return_json_strings | willy.toolist_quantum / toolist_topology / TestCrossToolistConsistency | 验证：诊断处理程序应返回 JSON 字符串 | 进程内行为断言 |
| T499 | tests/test_toolist_quantum_topology.py::TestCrossToolistConsistency::test_diagnosis_tools_all_use_diagnosis_marker | willy.toolist_quantum / toolist_topology / TestCrossToolistConsistency | 验证：所有 diagnose_* 工具应返回 _diagnosis=True | 进程内行为断言 |

## F. 拓扑后端与组装

Sobtop/OPLS 后端、manifest、重试账本、ITP 校验和主拓扑组装。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T531 | tests/test_top_assembly.py::test_build_revises_itps_in_requested_topology_directory | willy.topology.top_assembly | 验证 `build_revises_itps_in_requested_topology_directory` 行为 | tmp_path 隔离工作区 |
| T532 | tests/test_top_assembly.py::test_build_rejects_a_shared_topology_directory | willy.topology.top_assembly | 验证 `build_rejects_a_shared_topology_directory` 行为 | 进程内行为断言 |
| T533 | tests/test_topology_contract.py::TestTopologyConfig::test_amber_and_unregistered_values_are_rejected | willy.topology 后端与产物契约 / TestTopologyConfig | 验证 `amber_and_unregistered_values_are_rejected` 行为 | 进程内行为断言 |
| T534 | tests/test_topology_contract.py::TestTopologyConfig::test_legacy_contradictory_ligpargen_gaff_migrates_to_sobtop | willy.topology 后端与产物契约 / TestTopologyConfig | 验证 `legacy_contradictory_ligpargen_gaff_migrates_to_sobtop` 行为 | 进程内行为断言 |
| T535 | tests/test_topology_contract.py::TestTopologyConfig::test_legacy_ligpargen_opls_migrates_to_oplsaa | willy.topology 后端与产物契约 / TestTopologyConfig | 验证 `legacy_ligpargen_opls_migrates_to_oplsaa` 行为 | 进程内行为断言 |
| T536 | tests/test_topology_contract.py::TestTopologyDispatcher::test_dispatch_rejects_path_like_residue_name | willy.topology 后端与产物契约 / TestTopologyDispatcher | 验证 `dispatch_rejects_path_like_residue_name` 行为 | tmp_path 隔离工作区 |
| T537 | tests/test_topology_contract.py::TestTopologyDispatcher::test_sobtop_dispatch_uses_only_config_registered_components | willy.topology 后端与产物契约 / TestTopologyDispatcher | 验证 `sobtop_dispatch_uses_only_config_registered_components` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T538 | tests/test_topology_contract.py::TestTopologyDispatcher::test_dispatch_does_not_reset_existing_retry_ledger | willy.topology 后端与产物契约 / TestTopologyDispatcher | 验证 `dispatch_does_not_reset_existing_retry_ledger` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T539 | tests/test_topology_contract.py::TestTopologyDispatcher::test_opls_dispatch_passes_each_component_real_charge | willy.topology 后端与产物契约 / TestTopologyDispatcher | 验证 `opls_dispatch_passes_each_component_real_charge` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T540 | tests/test_topology_contract.py::TestTopologyDispatcher::test_opls_backend_passes_charge_and_opls_options_to_ligpargen | willy.topology 后端与产物契约 / TestTopologyDispatcher | 验证 `opls_backend_passes_charge_and_opls_options_to_ligpargen` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T541 | tests/test_topology_contract.py::TestSobtopExecutor::test_frozen_menu_sequence | willy.topology 后端与产物契约 / TestSobtopExecutor | 验证 `frozen_menu_sequence` 行为 | tmp_path 隔离工作区 |
| T542 | tests/test_topology_contract.py::TestSobtopExecutor::test_old_vendor_output_cannot_make_failed_run_succeed | willy.topology 后端与产物契约 / TestSobtopExecutor | 验证 `old_vendor_output_cannot_make_failed_run_succeed` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T543 | tests/test_topology_contract.py::TestSobtopExecutor::test_rc24_requires_complete_valid_current_outputs | willy.topology 后端与产物契约 / TestSobtopExecutor | 验证 `rc24_requires_complete_valid_current_outputs` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T544 | tests/test_topology_contract.py::TestSobtopExecutor::test_rc24_with_invalid_outputs_fails_and_cleans_vendor | willy.topology 后端与产物契约 / TestSobtopExecutor | 验证 `rc24_with_invalid_outputs_fails_and_cleans_vendor` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T545 | tests/test_topology_contract.py::TestSobtopExecutor::test_startup_oserror_is_a_dependency_step_result | willy.topology 后端与产物契约 / TestSobtopExecutor | 验证 `startup_oserror_is_a_dependency_step_result` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T546 | tests/test_topology_contract.py::TestSobtopExecutor::test_output_directory_must_be_explicit | willy.topology 后端与产物契约 / TestSobtopExecutor | 验证 `output_directory_must_be_explicit` 行为 | tmp_path 隔离工作区 |
| T547 | tests/test_topology_contract.py::TestSobtopExecutor::test_real_sobtop_minimal_integration_when_available | willy.topology 后端与产物契约 / TestSobtopExecutor | 验证：Exercise Sobtop only with a hash-verified external EC fixture bundle. | tmp_path 隔离工作区 |
| T548 | tests/test_topology_contract.py::TestOplsExecutor::test_same_residue_concurrent_runs_are_isolated | willy.topology 后端与产物契约 / TestOplsExecutor | 验证 `same_residue_concurrent_runs_are_isolated` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T549 | tests/test_topology_contract.py::TestOplsExecutor::test_startup_oserror_is_a_dependency_step_result | willy.topology 后端与产物契约 / TestOplsExecutor | 验证 `startup_oserror_is_a_dependency_step_result` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T550 | tests/test_topology_contract.py::TestOplsExecutor::test_output_directory_must_be_explicit | willy.topology 后端与产物契约 / TestOplsExecutor | 验证 `output_directory_must_be_explicit` 行为 | 进程内行为断言 |
| T551 | tests/test_topology_contract.py::TestOplsExecutor::test_path_like_output_name_is_rejected_before_any_write | willy.topology 后端与产物契约 / TestOplsExecutor | 验证 `path_like_output_name_is_rejected_before_any_write` 行为 | tmp_path 隔离工作区 |
| T552 | tests/test_topology_contract.py::TestOplsExecutor::test_existing_output_symlink_cannot_escape_run_directory | willy.topology 后端与产物契约 / TestOplsExecutor | 验证 `existing_output_symlink_cannot_escape_run_directory` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T553 | tests/test_topology_contract.py::TestOplsExecutor::test_moleculetype_restore_failure_is_structured_and_cleans_outputs | willy.topology 后端与产物契约 / TestOplsExecutor | 验证 `moleculetype_restore_failure_is_structured_and_cleans_outputs` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T554 | tests/test_topology_contract.py::TestTopologyRetryLedger::test_config_tool_updates_opls_options_used_by_retry | willy.topology 后端与产物契约 / TestTopologyRetryLedger | 验证 `config_tool_updates_opls_options_used_by_retry` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T555 | tests/test_topology_contract.py::TestTopologyRetryLedger::test_retry_budget_is_persisted_and_enforced_by_backend_tool | willy.topology 后端与产物契约 / TestTopologyRetryLedger | 验证 `retry_budget_is_persisted_and_enforced_by_backend_tool` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T556 | tests/test_topology_contract.py::TestTopologyRetryLedger::test_total_retry_budget_rejects_fifth_claim | willy.topology 后端与产物契约 / TestTopologyRetryLedger | 验证 `total_retry_budget_rejects_fifth_claim` 行为 | 进程内行为断言 |
| T557 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_successful_assembly_retry_keeps_atomtypes | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `successful_assembly_retry_keeps_atomtypes` 行为 | tmp_path 隔离工作区 |
| T558 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_assembly_namespace_does_not_overwrite_similarly_named_source | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `assembly_namespace_does_not_overwrite_similarly_named_source` 行为 | tmp_path 隔离工作区 |
| T559 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_missing_atomtypes_prevents_assembly | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `missing_atomtypes_prevents_assembly` 行为 | tmp_path 隔离工作区 |
| T560 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_undefined_atomtype_reference_prevents_assembly | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `undefined_atomtype_reference_prevents_assembly` 行为 | tmp_path 隔离工作区 |
| T561 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_moleculetype_must_match_manifest_residue | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `moleculetype_must_match_manifest_residue` 行为 | tmp_path 隔离工作区 |
| T562 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_missing_sections_and_atom_count_mismatch_fail | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `missing_sections_and_atom_count_mismatch_fail` 行为 | tmp_path 隔离工作区 |
| T563 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_validate_moleculetype_against_expected_residue | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `validate_moleculetype_against_expected_residue` 行为 | tmp_path 隔离工作区 |
| T564 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_manifest_missing_gro_prevents_assembly | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `manifest_missing_gro_prevents_assembly` 行为 | tmp_path 隔离工作区 |
| T565 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_manifest_artifact_tampering_prevents_assembly | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `manifest_artifact_tampering_prevents_assembly` 行为 | tmp_path 隔离工作区 |
| T566 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_gaff_uff_components_assemble_but_cross_family_is_rejected | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `gaff_uff_components_assemble_but_cross_family_is_rejected` 行为 | tmp_path 隔离工作区 |
| T567 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_atomtype_parameter_conflict_is_fatal | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `atomtype_parameter_conflict_is_fatal` 行为 | tmp_path 隔离工作区 |
| T568 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_itp_revision_is_section_aware_and_idempotent | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `itp_revision_is_section_aware_and_idempotent` 行为 | tmp_path 隔离工作区 |
| T569 | tests/test_topology_contract.py::TestArtifactAndAssemblyValidation::test_atomtype_conflict_escalates_without_llm_retry | willy.topology 后端与产物契约 / TestArtifactAndAssemblyValidation | 验证 `atomtype_conflict_escalates_without_llm_retry` 行为 | mock/patch |

## G. 模拟执行、ETA 与后处理

EM/EQ/PROD 协议、GROMACS 适配、ETA、阶段产物和分析流程。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T258 | tests/test_mdrun_eta.py::test_verbose_carriage_return_output_updates_eta_snapshot | willy.simulation.mdrun_eta | 验证 `verbose_carriage_return_output_updates_eta_snapshot` 行为 | tmp_path 隔离工作区 |
| T259 | tests/test_mdrun_eta.py::test_eta_snapshot_stays_waiting_without_a_gromacs_prediction | willy.simulation.mdrun_eta | 验证 `eta_snapshot_stays_waiting_without_a_gromacs_prediction` 行为 | tmp_path 隔离工作区 |
| T260 | tests/test_mdrun_eta.py::test_heartbeat_refreshes_liveness_and_reads_gromacs_2025_log_progress | willy.simulation.mdrun_eta | 验证 `heartbeat_refreshes_liveness_and_reads_gromacs_2025_log_progress` 行为 | tmp_path 隔离工作区 |
| T261 | tests/test_mdrun_eta.py::test_gromacs_duration_variant_updates_eta_without_deriving_from_step_rate | willy.simulation.mdrun_eta | 验证 `gromacs_duration_variant_updates_eta_without_deriving_from_step_rate` 行为 | tmp_path 隔离工作区 |
| T262 | tests/test_mdrun_eta.py::test_gromacs_2025_will_finish_eta_is_parsed_from_chunked_verbose_output | willy.simulation.mdrun_eta | 验证：GROMACS 2025 prints an absolute ctime ETA after PME tuning. | tmp_path 隔离工作区 + monkeypatch |
| T263 | tests/test_mdrun_eta.py::test_eta_snapshot_closes_when_mdrun_ends | willy.simulation.mdrun_eta | 验证 `eta_snapshot_closes_when_mdrun_ends` 行为 | tmp_path 隔离工作区 |
| T363 | tests/test_postprocess.py::test_postprocess_generates_traceable_analysis_outputs | willy.simulation.postprocess | 验证 `postprocess_generates_traceable_analysis_outputs` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T364 | tests/test_postprocess.py::test_postprocess_uses_system_for_pbc_and_requested_group_for_msd | willy.simulation.postprocess | 验证 `postprocess_uses_system_for_pbc_and_requested_group_for_msd` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T365 | tests/test_postprocess.py::test_postprocess_rejects_incomplete_trajectory | willy.simulation.postprocess | 验证 `postprocess_rejects_incomplete_trajectory` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T366 | tests/test_postprocess.py::test_postprocess_refuses_to_overwrite_existing_analysis | willy.simulation.postprocess | 验证 `postprocess_refuses_to_overwrite_existing_analysis` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T367 | tests/test_postprocess.py::test_postprocess_rejects_invalid_discard_fraction | willy.simulation.postprocess | 验证 `postprocess_rejects_invalid_discard_fraction` 行为 | tmp_path 隔离工作区 |
| T368 | tests/test_postprocess.py::test_postprocess_reports_missing_production_artifacts | willy.simulation.postprocess | 验证 `postprocess_reports_missing_production_artifacts` 行为 | tmp_path 隔离工作区 |
| T369 | tests/test_postprocess.py::test_postprocess_rejects_sources_without_completed_prod_manifest | willy.simulation.postprocess | 验证 `postprocess_rejects_sources_without_completed_prod_manifest` 行为 | tmp_path 隔离工作区 |
| T370 | tests/test_postprocess.py::test_postprocess_rejects_artifact_fingerprint_mismatch | willy.simulation.postprocess | 验证 `postprocess_rejects_artifact_fingerprint_mismatch` 行为 | tmp_path 隔离工作区 |
| T409 | tests/test_simulation_execution.py::test_grompp_and_mdrun_returns_required_stage_artifacts | willy.simulation 执行适配层 | 验证 `grompp_and_mdrun_returns_required_stage_artifacts` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T410 | tests/test_simulation_execution.py::test_gromacs_reports_preprocess_and_run_as_public_activities | willy.simulation 执行适配层 | 验证 `gromacs_reports_preprocess_and_run_as_public_activities` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T411 | tests/test_simulation_execution.py::test_gromacs_routes_liveness_heartbeats_without_redefining_activity | willy.simulation 执行适配层 | 验证 `gromacs_routes_liveness_heartbeats_without_redefining_activity` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T412 | tests/test_simulation_execution.py::test_mdp_reports_each_stage_as_public_progress | willy.simulation 执行适配层 | 验证 `mdp_reports_each_stage_as_public_progress` 行为 | tmp_path 隔离工作区 |
| T413 | tests/test_simulation_execution.py::test_grompp_and_mdrun_passes_custom_tpr_to_mdrun | willy.simulation 执行适配层 | 验证 `grompp_and_mdrun_passes_custom_tpr_to_mdrun` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T414 | tests/test_simulation_execution.py::test_grompp_and_mdrun_rejects_missing_required_input | willy.simulation 执行适配层 | 验证 `grompp_and_mdrun_rejects_missing_required_input` 行为 | tmp_path 隔离工作区 |
| T415 | tests/test_simulation_execution.py::test_grompp_and_mdrun_rejects_success_without_required_output | willy.simulation 执行适配层 | 验证 `grompp_and_mdrun_rejects_success_without_required_output` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T416 | tests/test_simulation_execution.py::test_mdrun_failure_has_private_process_evidence_and_mdrun_kind | willy.simulation 执行适配层 | 验证 `mdrun_failure_has_private_process_evidence_and_mdrun_kind` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T417 | tests/test_simulation_execution.py::test_detect_vacuum_region_from_final_eq_structure | willy.simulation 执行适配层 | 验证 `detect_vacuum_region_from_final_eq_structure` 行为 | tmp_path 隔离工作区 |
| T418 | tests/test_simulation_execution.py::test_eq_vacuum_waits_for_user_confirmation_before_packmol_changes | willy.simulation 执行适配层 | 验证 `eq_vacuum_waits_for_user_confirmation_before_packmol_changes` 行为 | tmp_path 隔离工作区 + monkeypatch + mock/patch |
| T419 | tests/test_simulation_protocol.py::test_default_eq_mdp_has_six_segment_cumulative_points_and_actual_time | willy.simulation.protocol / manifest | 验证 `default_eq_mdp_has_six_segment_cumulative_points_and_actual_time` 行为 | tmp_path 隔离工作区 |
| T420 | tests/test_simulation_protocol.py::test_partial_mdp_regeneration_preserves_other_stage_metadata | willy.simulation.protocol / manifest | 验证 `partial_mdp_regeneration_preserves_other_stage_metadata` 行为 | tmp_path 隔离工作区 |
| T421 | tests/test_simulation_protocol.py::test_eq_duration_boundaries_are_valid[7.0] | willy.simulation.protocol / manifest | 验证 `eq_duration_boundaries_are_valid` 行为；参数集 `7.0` | pytest 参数化 |
| T422 | tests/test_simulation_protocol.py::test_eq_duration_boundaries_are_valid[100.0] | willy.simulation.protocol / manifest | 验证 `eq_duration_boundaries_are_valid` 行为；参数集 `100.0` | pytest 参数化 |
| T423 | tests/test_simulation_protocol.py::test_eq_duration_outside_boundaries_is_rejected[6.999] | willy.simulation.protocol / manifest | 验证 `eq_duration_outside_boundaries_is_rejected` 行为；参数集 `6.999` | pytest 参数化 |
| T424 | tests/test_simulation_protocol.py::test_eq_duration_outside_boundaries_is_rejected[100.001] | willy.simulation.protocol / manifest | 验证 `eq_duration_outside_boundaries_is_rejected` 行为；参数集 `100.001` | pytest 参数化 |
| T425 | tests/test_simulation_protocol.py::test_prod_duration_boundaries_are_valid[2.0] | willy.simulation.protocol / manifest | 验证 `prod_duration_boundaries_are_valid` 行为；参数集 `2.0` | pytest 参数化 |
| T426 | tests/test_simulation_protocol.py::test_prod_duration_boundaries_are_valid[200.0] | willy.simulation.protocol / manifest | 验证 `prod_duration_boundaries_are_valid` 行为；参数集 `200.0` | pytest 参数化 |
| T427 | tests/test_simulation_protocol.py::test_prod_duration_outside_boundaries_is_rejected[1.999] | willy.simulation.protocol / manifest | 验证 `prod_duration_outside_boundaries_is_rejected` 行为；参数集 `1.999` | pytest 参数化 |
| T428 | tests/test_simulation_protocol.py::test_prod_duration_outside_boundaries_is_rejected[200.001] | willy.simulation.protocol / manifest | 验证 `prod_duration_outside_boundaries_is_rejected` 行为；参数集 `200.001` | pytest 参数化 |
| T429 | tests/test_simulation_protocol.py::test_short_final_hold_warns_and_disables_auto_acceptance | willy.simulation.protocol / manifest | 验证 `short_final_hold_warns_and_disables_auto_acceptance` 行为 | tmp_path 隔离工作区 |
| T430 | tests/test_simulation_protocol.py::test_eq_cannot_run_without_accepted_em | willy.simulation.protocol / manifest | 验证 `eq_cannot_run_without_accepted_em` 行为 | tmp_path 隔离工作区 |
| T431 | tests/test_simulation_protocol.py::test_prod_requires_the_accepted_eq_checkpoint | willy.simulation.protocol / manifest | 验证 `prod_requires_the_accepted_eq_checkpoint` 行为 | tmp_path 隔离工作区 |
| T432 | tests/test_simulation_protocol.py::test_stage_manifest_keeps_mdrun_evidence_private_to_the_run | willy.simulation.protocol / manifest | 验证 `stage_manifest_keeps_mdrun_evidence_private_to_the_run` 行为 | tmp_path 隔离工作区 |
| T433 | tests/test_simulation_protocol.py::test_prod_append_requires_matching_manifest_contract_and_checkpoint | willy.simulation.protocol / manifest | 验证 `prod_append_requires_matching_manifest_contract_and_checkpoint` 行为 | tmp_path 隔离工作区 |
| T434 | tests/test_simulation_protocol.py::test_upstream_protocol_change_invalidates_downstream_acceptance | willy.simulation.protocol / manifest | 验证 `upstream_protocol_change_invalidates_downstream_acceptance` 行为 | tmp_path 隔离工作区 |
| T435 | tests/test_simulation_protocol.py::test_config_edit_invalidates_changed_stage_and_all_downstream | willy.simulation.protocol / manifest | 验证 `config_edit_invalidates_changed_stage_and_all_downstream` 行为 | tmp_path 隔离工作区 |
| T436 | tests/test_simulation_protocol.py::test_manifest_archives_each_config_revision_used_by_a_stage | willy.simulation.protocol / manifest | 验证 `manifest_archives_each_config_revision_used_by_a_stage` 行为 | tmp_path 隔离工作区 |
| T437 | tests/test_simulation_protocol.py::test_packing_number_density_changes_packmol_box_size_and_input | willy.simulation.protocol / manifest | 验证 `packing_number_density_changes_packmol_box_size_and_input` 行为 | 进程内行为断言 |
| T438 | tests/test_simulation_protocol.py::test_mass_density_box_uses_topology_mass_and_explicit_pbc | willy.simulation.protocol / manifest | 验证 `mass_density_box_uses_topology_mass_and_explicit_pbc` 行为 | 进程内行为断言 |
| T439 | tests/test_simulation_protocol.py::test_auto_box_config_derives_mass_from_run_local_itp | willy.simulation.protocol / manifest | 验证 `auto_box_config_derives_mass_from_run_local_itp` 行为 | tmp_path 隔离工作区 |
| T440 | tests/test_simulation_protocol.py::test_packmol_uses_seekable_input_file | willy.simulation.protocol / manifest | 验证 `packmol_uses_seekable_input_file` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T441 | tests/test_simulation_protocol.py::test_grompp_default_does_not_force_warning_bypass | willy.simulation.protocol / manifest | 验证 `grompp_default_does_not_force_warning_bypass` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T442 | tests/test_simulation_protocol.py::test_em_zero_step_convergence_gets_a_one_frame_xtc | willy.simulation.protocol / manifest | 验证 `em_zero_step_convergence_gets_a_one_frame_xtc` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T443 | tests/test_simulation_protocol.py::test_prod_end_time_prefers_physical_progress_over_wall_clock | willy.simulation.protocol / manifest | 验证 `prod_end_time_prefers_physical_progress_over_wall_clock` 行为 | tmp_path 隔离工作区 |
| T444 | tests/test_simulation_protocol.py::test_prod_frame_count_uses_gmx_check_summary | willy.simulation.protocol / manifest | 验证 `prod_frame_count_uses_gmx_check_summary` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T500 | tests/test_toolist_simulation.py::TestSimulationToolDefinitions::test_thirteen_tools_defined | willy.toolist_simulation / TestSimulationToolDefinitions | 验证：应有 13 个工具定义，不暴露不完整的跳过分子功能 | 进程内行为断言 |
| T501 | tests/test_toolist_simulation.py::TestSimulationToolDefinitions::test_all_tool_names_present | willy.toolist_simulation / TestSimulationToolDefinitions | 验证：所有必需工具应存在 | 进程内行为断言 |
| T502 | tests/test_toolist_simulation.py::TestSimulationToolDefinitions::test_tool_meta_covers_all_tools | willy.toolist_simulation / TestSimulationToolDefinitions | 验证：TOOL_META 应覆盖所有已定义工具 | 进程内行为断言 |
| T503 | tests/test_toolist_simulation.py::TestSimulationToolDefinitions::test_skip_molecule_is_not_exposed | willy.toolist_simulation / TestSimulationToolDefinitions | 验证 `skip_molecule_is_not_exposed` 行为 | 进程内行为断言 |
| T504 | tests/test_toolist_simulation.py::TestSimulationToolDefinitions::test_eq_schema_exposes_all_three_annealing_temperatures | willy.toolist_simulation / TestSimulationToolDefinitions | 验证 `eq_schema_exposes_all_three_annealing_temperatures` 行为 | 进程内行为断言 |
| T505 | tests/test_toolist_simulation.py::TestSimulationToolDefinitions::test_gromacs_run_tools_expose_file_contract | willy.toolist_simulation / TestSimulationToolDefinitions | 验证 `gromacs_run_tools_expose_file_contract` 行为 | 进程内行为断言 |
| T506 | tests/test_toolist_simulation.py::TestSimulationToolDefinitions::test_protocol_tools_do_not_expose_model_confirmed_flag | willy.toolist_simulation / TestSimulationToolDefinitions | 验证：User approval is server-side, never a tool argument the model can forge. | 进程内行为断言 |
| T507 | tests/test_toolist_simulation.py::TestHandleSimulationToolCall::test_unknown_tool_returns_error | willy.toolist_simulation / TestHandleSimulationToolCall | 验证 `unknown_tool_returns_error` 行为 | 进程内行为断言 |
| T508 | tests/test_toolist_simulation.py::TestHandleSimulationToolCall::test_step_to_dict_format | willy.toolist_simulation / TestHandleSimulationToolCall | 验证：StepResult.to_dict 应包含 _step_result 标记 | 进程内行为断言 |
| T509 | tests/test_toolist_simulation.py::TestHandleSimulationToolCall::test_migration_adoption_requires_confirmation_and_switches_active_config | willy.toolist_simulation / TestHandleSimulationToolCall | 验证 `migration_adoption_requires_confirmation_and_switches_active_config` 行为 | tmp_path 隔离工作区 |
| T510 | tests/test_toolist_simulation.py::TestHandleSimulationToolCall::test_model_supplied_confirmed_flag_cannot_authorize_protocol_change | willy.toolist_simulation / TestHandleSimulationToolCall | 验证：Even an explicit LLM ``confirmed=true`` must leave config untouched. | 进程内行为断言 |
| T511 | tests/test_toolist_simulation.py::TestRetryProd::test_run_prod_accepts_extra_mdrun | willy.toolist_simulation / TestRetryProd | 验证 `run_prod_accepts_extra_mdrun` 行为 | 进程内行为断言 |
| T512 | tests/test_toolist_simulation.py::TestRetryProd::test_grompp_and_mdrun_does_accept_extra_mdrun | willy.toolist_simulation / TestRetryProd | 验证：用于对比：grompp_and_mdrun 确实接受 extra_mdrun | 进程内行为断言 |
| T513 | tests/test_toolist_simulation.py::TestRetryProd::test_handler_does_not_inject_unverified_restart_flags | willy.toolist_simulation / TestRetryProd | 验证 `handler_does_not_inject_unverified_restart_flags` 行为 | tmp_path 隔离工作区 + mock/patch |
| T514 | tests/test_toolist_simulation.py::TestRunGromacsTools::test_run_em_uses_bound_workspace | willy.toolist_simulation / TestRunGromacsTools | 验证 `run_em_uses_bound_workspace` 行为 | tmp_path 隔离工作区 + mock/patch |
| T515 | tests/test_toolist_simulation.py::TestRunGromacsTools::test_run_tool_rejects_path_outside_workspace | willy.toolist_simulation / TestRunGromacsTools | 验证 `run_tool_rejects_path_outside_workspace` 行为 | tmp_path 隔离工作区 |
| T516 | tests/test_toolist_simulation.py::TestRetryMdp::test_stage_is_forwarded_to_mdp_builder | willy.toolist_simulation / TestRetryMdp | 验证 `stage_is_forwarded_to_mdp_builder` 行为 | tmp_path 隔离工作区 + mock/patch |
| T517 | tests/test_toolist_simulation.py::TestRetryMdp::test_all_stage_requests_all_mdp_files | willy.toolist_simulation / TestRetryMdp | 验证 `all_stage_requests_all_mdp_files` 行为 | tmp_path 隔离工作区 + mock/patch |
| T518 | tests/test_toolist_simulation.py::TestRetryMdp::test_eq_change_rebuilds_eq_and_prod | willy.toolist_simulation / TestRetryMdp | 验证 `eq_change_rebuilds_eq_and_prod` 行为 | tmp_path 隔离工作区 + mock/patch |
| T519 | tests/test_toolist_simulation.py::TestRetryMdp::test_build_all_signature_accepts_overrides | willy.toolist_simulation / TestRetryMdp | 验证：build_all 按关键字接受 overrides 参数 | 进程内行为断言 |
| T520 | tests/test_toolist_simulation.py::TestRetryMdp::test_keyword_override_call_is_correct | willy.toolist_simulation / TestRetryMdp | 验证：build_all(overrides=overrides) 在 Python 中是合法的， 因为前两个参数有默认值 | 进程内行为断言 |
| T521 | tests/test_toolist_simulation.py::TestModifyConfigSimulation::test_writes_to_config_json | willy.toolist_simulation / TestModifyConfigSimulation | 验证：应更新 config.json 中的 md 字段 | 进程内行为断言 |
| T522 | tests/test_toolist_simulation.py::TestModifyConfigSimulation::test_reports_config_delta_after_the_run_snapshot_is_updated | willy.toolist_simulation / TestModifyConfigSimulation | 验证 `reports_config_delta_after_the_run_snapshot_is_updated` 行为 | 进程内行为断言 |
| T523 | tests/test_toolist_simulation.py::TestModifyConfigSimulation::test_missing_config_json | willy.toolist_simulation / TestModifyConfigSimulation | 验证：config.json 不存在时应有错误 | tmp_path 隔离工作区 |
| T524 | tests/test_toolist_simulation.py::TestModifyConfigSimulation::test_preserves_non_md_fields | willy.toolist_simulation / TestModifyConfigSimulation | 验证：修改 MD 字段不应影响其他 config 段 | 进程内行为断言 |
| T525 | tests/test_toolist_simulation.py::test_removed_skip_handler_does_not_mutate_configuration | willy.toolist_simulation | 验证 `removed_skip_handler_does_not_mutate_configuration` 行为 | 进程内行为断言 |
| T526 | tests/test_toolist_simulation.py::TestDiagnoseErrorSimulation::test_returns_diagnosis_result_format | willy.toolist_simulation / TestDiagnoseErrorSimulation | 验证：结果应包含 _diagnosis=True 标记 | 进程内行为断言 |
| T527 | tests/test_toolist_simulation.py::TestDiagnoseErrorSimulation::test_analyzes_grompp_stderr_for_atomtype | willy.toolist_simulation / TestDiagnoseErrorSimulation | 验证：应从 grompp stderr 中检测 atomtype 问题 | 进程内行为断言 |
| T528 | tests/test_toolist_simulation.py::TestDiagnoseErrorSimulation::test_analyzes_mdrun_stderr_for_nan | willy.toolist_simulation / TestDiagnoseErrorSimulation | 验证：应从 mdrun stderr 中检测 NaN | 进程内行为断言 |
| T529 | tests/test_toolist_simulation.py::TestStepResultSerialization::test_success_result_format | willy.toolist_simulation / TestStepResultSerialization | 验证 `success_result_format` 行为 | 进程内行为断言 |
| T530 | tests/test_toolist_simulation.py::TestStepResultSerialization::test_failure_result_format | willy.toolist_simulation / TestStepResultSerialization | 验证 `failure_result_format` 行为 | 进程内行为断言 |

## H. 流水线编排与状态机

步骤构建、唯一步骤注册、失败修复、run workspace、公开状态和恢复边界。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T264 | tests/test_pending_action.py::test_pending_action_does_not_change_config_until_applied | willy.simulation.pending_action | 验证 `pending_action_does_not_change_config_until_applied` 行为 | tmp_path 隔离工作区 |
| T265 | tests/test_pending_action.py::test_pending_action_rejects_changed_config_before_launch | willy.simulation.pending_action | 验证 `pending_action_rejects_changed_config_before_launch` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T266 | tests/test_pending_action.py::test_pending_action_applies_only_its_validated_fields | willy.simulation.pending_action | 验证 `pending_action_applies_only_its_validated_fields` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T267 | tests/test_pending_action.py::test_replacing_pending_action_preserves_config_until_new_confirmation | willy.simulation.pending_action | 验证 `replacing_pending_action_preserves_config_until_new_confirmation` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T268 | tests/test_pending_action.py::test_vacuum_recovery_requires_box_rebuild_from_step_seven | willy.simulation.pending_action | 验证 `vacuum_recovery_requires_box_rebuild_from_step_seven` 行为 | tmp_path 隔离工作区 |
| T269 | tests/test_pending_action.py::test_public_pending_action_exposes_only_bounded_editable_parameters | willy.simulation.pending_action | 验证 `public_pending_action_exposes_only_bounded_editable_parameters` 行为 | tmp_path 隔离工作区 |
| T278 | tests/test_pipeline_orchestrator.py::TestStepRegistryLayerMapping::test_step_layer_mapping_is_complete | willy.pipeline_orchestrator / TestStepRegistryLayerMapping | 验证：步骤 1-10 应全部映射 | 进程内行为断言 |
| T279 | tests/test_pipeline_orchestrator.py::TestStepRegistryLayerMapping::test_step_layers_correct | willy.pipeline_orchestrator / TestStepRegistryLayerMapping | 验证：各步骤的层映射应正确 | 进程内行为断言 |
| T280 | tests/test_pipeline_orchestrator.py::TestStepRegistryLayerMapping::test_no_steps_beyond_10 | willy.pipeline_orchestrator / TestStepRegistryLayerMapping | 验证：步骤 11+ 尚未定义 | 异常断言 |
| T281 | tests/test_pipeline_orchestrator.py::TestPipelineOrchestratorInit::test_no_api_key_disables_llm | willy.pipeline_orchestrator / TestPipelineOrchestratorInit | 验证：没有任何 LLM API Key 时，应禁用 LLM | monkeypatch |
| T282 | tests/test_pipeline_orchestrator.py::TestPipelineOrchestratorInit::test_use_llm_false_skips_init | willy.pipeline_orchestrator / TestPipelineOrchestratorInit | 验证：use_llm=False 时不应初始化 LLM 客户端 | 进程内行为断言 |
| T283 | tests/test_pipeline_orchestrator.py::TestPipelineOrchestratorInit::test_backend_stored | willy.pipeline_orchestrator / TestPipelineOrchestratorInit | 验证 `backend_stored` 行为 | 进程内行为断言 |
| T284 | tests/test_pipeline_orchestrator.py::TestPipelineOrchestratorInit::test_configured_model_is_injected_into_all_repair_agents | willy.pipeline_orchestrator / TestPipelineOrchestratorInit | 验证 `configured_model_is_injected_into_all_repair_agents` 行为 | monkeypatch + mock/patch |
| T285 | tests/test_pipeline_orchestrator.py::TestPipelineOrchestratorInit::test_new_run_never_inherits_previous_done_steps | willy.pipeline_orchestrator / TestPipelineOrchestratorInit | 验证：A fresh launch must not infer a resume from the global status file. | tmp_path 隔离工作区 + monkeypatch |
| T286 | tests/test_pipeline_orchestrator.py::TestPipelineOrchestratorInit::test_explicit_resume_requires_the_original_run_directory | willy.pipeline_orchestrator / TestPipelineOrchestratorInit | 验证 `explicit_resume_requires_the_original_run_directory` 行为 | tmp_path 隔离工作区 + monkeypatch + 异常断言 |
| T287 | tests/test_pipeline_orchestrator.py::TestPublicRepairUpdates::test_simulation_config_delta_is_labeled_and_unitized | willy.pipeline_orchestrator / TestPublicRepairUpdates | 验证 `simulation_config_delta_is_labeled_and_unitized` 行为 | 进程内行为断言 |
| T288 | tests/test_pipeline_orchestrator.py::TestBuildSteps::test_g16_backend_builds_10_steps | willy.pipeline_orchestrator / TestBuildSteps | 验证 `g16_backend_builds_10_steps` 行为 | 进程内行为断言 |
| T289 | tests/test_pipeline_orchestrator.py::TestBuildSteps::test_orca_backend_builds_10_steps | willy.pipeline_orchestrator / TestBuildSteps | 验证 `orca_backend_builds_10_steps` 行为 | 进程内行为断言 |
| T290 | tests/test_pipeline_orchestrator.py::TestBuildSteps::test_final_three_steps_are_gromacs_execution | willy.pipeline_orchestrator / TestBuildSteps | 验证 `final_three_steps_are_gromacs_execution` 行为 | 进程内行为断言 |
| T291 | tests/test_pipeline_orchestrator.py::TestBuildSteps::test_step_structure | willy.pipeline_orchestrator / TestBuildSteps | 验证：每个步骤应为 5 元组: (label, func, dep_module, is_batch, layer_index) | 进程内行为断言 |
| T292 | tests/test_pipeline_orchestrator.py::TestBuildSteps::test_step_1_is_batch | willy.pipeline_orchestrator / TestBuildSteps | 验证：第一步（结构优化）应是批量的 | 进程内行为断言 |
| T293 | tests/test_pipeline_orchestrator.py::TestBuildSteps::test_step_5_is_not_batch | willy.pipeline_orchestrator / TestBuildSteps | 验证：第五步（主拓扑）不应是批量的 | 进程内行为断言 |
| T294 | tests/test_pipeline_orchestrator.py::TestBuildSteps::test_all_pipeline_steps_receive_the_same_run_directory | willy.pipeline_orchestrator / TestBuildSteps | 验证：正常流水线不得回退到项目根目录的共享输入或拓扑目录 | tmp_path 隔离工作区 + monkeypatch + mock/patch |
| T295 | tests/test_pipeline_orchestrator.py::TestPublicQuantumProgress::test_g16_orca_resp_and_step2_callbacks_are_structured | willy.pipeline_orchestrator / TestPublicQuantumProgress | 验证 `g16_orca_resp_and_step2_callbacks_are_structured` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T296 | tests/test_pipeline_orchestrator.py::TestPublicQuantumProgress::test_resp_rejects_an_incomplete_configured_molecule_set | willy.pipeline_orchestrator / TestPublicQuantumProgress | 验证：Step 3 must not silently accept 4/5 configured molecules. | tmp_path 隔离工作区 |
| T297 | tests/test_pipeline_orchestrator.py::TestRunWorkspace::test_prepare_workspace_copies_precomputed_g16_input | willy.pipeline_orchestrator / TestRunWorkspace | 验证 `prepare_workspace_copies_precomputed_g16_input` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T298 | tests/test_pipeline_orchestrator.py::TestRunWorkspace::test_prepare_workspace_accepts_gjf_without_fchk | willy.pipeline_orchestrator / TestRunWorkspace | 验证：A new molecule reaches Step 1 silently when only its .gjf exists. | tmp_path 隔离工作区 + monkeypatch |
| T299 | tests/test_pipeline_orchestrator.py::TestRunWorkspace::test_prepare_workspace_rejects_molecule_without_quantum_input | willy.pipeline_orchestrator / TestRunWorkspace | 验证 `prepare_workspace_rejects_molecule_without_quantum_input` 行为 | tmp_path 隔离工作区 + monkeypatch + 异常断言 |
| T300 | tests/test_pipeline_orchestrator.py::TestSinglePointMol2Contract::test_g16_mol2_failure_marks_step_2_failed | willy.pipeline_orchestrator / TestSinglePointMol2Contract | 验证 `g16_mol2_failure_marks_step_2_failed` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T301 | tests/test_pipeline_orchestrator.py::TestHandleBatchResult::test_all_success | willy.pipeline_orchestrator / TestHandleBatchResult | 验证 `all_success` 行为 | 进程内行为断言 |
| T302 | tests/test_pipeline_orchestrator.py::TestHandleBatchResult::test_some_fail_no_llm | willy.pipeline_orchestrator / TestHandleBatchResult | 验证：在没有 LLM 的情况下，部分失败应返回 False | 进程内行为断言 |
| T303 | tests/test_pipeline_orchestrator.py::TestHandleBatchResult::test_molecule_failure_is_not_silently_filtered | willy.pipeline_orchestrator / TestHandleBatchResult | 验证：模拟层不能跳过分子而保留旧拓扑和建盒结果 | 进程内行为断言 |
| T304 | tests/test_pipeline_orchestrator.py::TestHandleBatchResult::test_artifacts_collected_for_success | willy.pipeline_orchestrator / TestHandleBatchResult | 验证 `artifacts_collected_for_success` 行为 | 进程内行为断言 |
| T305 | tests/test_pipeline_orchestrator.py::TestHandleBatchResult::test_empty_batch_is_a_failure | willy.pipeline_orchestrator / TestHandleBatchResult | 验证 `empty_batch_is_a_failure` 行为 | 进程内行为断言 |
| T306 | tests/test_pipeline_orchestrator.py::TestHandleBatchResult::test_each_failed_molecule_is_repaired_before_step_succeeds | willy.pipeline_orchestrator / TestHandleBatchResult | 验证 `each_failed_molecule_is_repaired_before_step_succeeds` 行为 | mock/patch |
| T307 | tests/test_pipeline_orchestrator.py::TestHandleSingleResult::test_success_collects_artifacts | willy.pipeline_orchestrator / TestHandleSingleResult | 验证 `success_collects_artifacts` 行为 | 进程内行为断言 |
| T308 | tests/test_pipeline_orchestrator.py::TestHandleSingleResult::test_failure_no_llm | willy.pipeline_orchestrator / TestHandleSingleResult | 验证 `failure_no_llm` 行为 | 进程内行为断言 |
| T309 | tests/test_pipeline_orchestrator.py::TestHandleSingleResult::test_user_stop_request_bypasses_error_and_agent_retry | willy.pipeline_orchestrator / TestHandleSingleResult | 验证 `user_stop_request_bypasses_error_and_agent_retry` 行为 | tmp_path 隔离工作区 |
| T310 | tests/test_pipeline_orchestrator.py::TestHandleSingleResult::test_failure_without_error_details | willy.pipeline_orchestrator / TestHandleSingleResult | 验证：错误为 None 时不应崩溃 | 进程内行为断言 |
| T311 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_no_agent_available | willy.pipeline_orchestrator / TestInvokeAgent | 验证：agent 为 None 时应返回 False | 进程内行为断言 |
| T312 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_agent_repair_success | willy.pipeline_orchestrator / TestInvokeAgent | 验证 `agent_repair_success` 行为 | mock/patch |
| T313 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_step_two_upstream_repair_requests_a_rerun | willy.pipeline_orchestrator / TestInvokeAgent | 验证：Creating only {name}.fchk cannot complete SP + mol2. | tmp_path 隔离工作区 + mock/patch |
| T314 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_eq_upstream_mdp_repair_rolls_back_to_em_without_marking_eq_done | willy.pipeline_orchestrator / TestInvokeAgent | 验证：A configuration repair cannot turn an EQ failure into EQ success. | tmp_path 隔离工作区 + mock/patch |
| T315 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_eq_failure_waits_for_user_confirmation_before_prod | willy.pipeline_orchestrator / TestInvokeAgent | 验证：An EQ failure creates a proposal; it must never auto-enter PROD. | tmp_path 隔离工作区 + monkeypatch + mock/patch |
| T316 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_confirmed_eq_action_reruns_eq_before_prod | willy.pipeline_orchestrator / TestInvokeAgent | 验证：An approved action rewrites MDPs then needs accepted EQ before PROD. | tmp_path 隔离工作区 + monkeypatch |
| T317 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_eq_success_without_manifest_acceptance_is_blocked_before_prod | willy.pipeline_orchestrator / TestInvokeAgent | 验证：An EQ tool result alone is insufficient to progress into PROD. | tmp_path 隔离工作区 + monkeypatch + mock/patch |
| T318 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_run_reexecutes_step_two_after_upstream_repair | willy.pipeline_orchestrator / TestInvokeAgent | 验证：The rerun runs Step 2 itself and only then permits downstream steps. | tmp_path 隔离工作区 + monkeypatch + mock/patch |
| T319 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_agent_receives_failed_step_intermediate_artifacts | willy.pipeline_orchestrator / TestInvokeAgent | 验证：Step 2 mol2 失败时，Agent 必须收到本次生成的 *_opt.fchk | mock/patch |
| T320 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_agent_escalation | willy.pipeline_orchestrator / TestInvokeAgent | 验证 `agent_escalation` 行为 | mock/patch |
| T321 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_agent_escalation_uses_current_failed_molecule | willy.pipeline_orchestrator / TestInvokeAgent | 验证：The final public error must not retain an earlier batch item's target. | mock/patch |
| T322 | tests/test_pipeline_orchestrator.py::TestInvokeAgent::test_agent_repair_failure_no_escalation | willy.pipeline_orchestrator / TestInvokeAgent | 验证：Agent 未升级的失败应中止流水线 | mock/patch |
| T323 | tests/test_pipeline_state.py::TestStateEnum::test_all_states_defined | willy.pipeline_state / TestStateEnum | 验证 `all_states_defined` 行为 | 进程内行为断言 |
| T324 | tests/test_pipeline_state.py::TestStateEnum::test_state_values_are_strings | willy.pipeline_state / TestStateEnum | 验证 `state_values_are_strings` 行为 | 进程内行为断言 |
| T325 | tests/test_pipeline_state.py::TestStateEnum::test_state_transitions_well_defined | willy.pipeline_state / TestStateEnum | 验证：典型状态流转: IDLE → RUNNING → DONE | 进程内行为断言 |
| T326 | tests/test_pipeline_state.py::TestPipelineStatus::test_default_values | willy.pipeline_state / TestPipelineStatus | 验证 `default_values` 行为 | 进程内行为断言 |
| T327 | tests/test_pipeline_state.py::TestPipelineStatus::test_all_fields_serializable | willy.pipeline_state / TestPipelineStatus | 验证：所有字段应对 JSON 可序列化 | 进程内行为断言 |
| T328 | tests/test_pipeline_state.py::TestPipelineStatus::test_escalation_field_defaults_to_dict | willy.pipeline_state / TestPipelineStatus | 验证 `escalation_field_defaults_to_dict` 行为 | 进程内行为断言 |
| T329 | tests/test_pipeline_state.py::TestPipelineStatus::test_escalation_field_with_dict | willy.pipeline_state / TestPipelineStatus | 验证 `escalation_field_with_dict` 行为 | 进程内行为断言 |
| T330 | tests/test_pipeline_state.py::TestPipelineStatus::test_actions_list_accumulation | willy.pipeline_state / TestPipelineStatus | 验证 `actions_list_accumulation` 行为 | 进程内行为断言 |
| T331 | tests/test_pipeline_state.py::TestPipelineStatus::test_error_field | willy.pipeline_state / TestPipelineStatus | 验证：error 字段存储最近的错误消息 | 进程内行为断言 |
| T332 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_initial_state_is_idle | willy.pipeline_state / TestPipelineStateMachine | 验证 `initial_state_is_idle` 行为 | 进程内行为断言 |
| T333 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_transition_updates_state | willy.pipeline_state / TestPipelineStateMachine | 验证 `transition_updates_state` 行为 | 进程内行为断言 |
| T334 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_awaiting_confirmation_cannot_skip_directly_to_running | willy.pipeline_state / TestPipelineStateMachine | 验证 `awaiting_confirmation_cannot_skip_directly_to_running` 行为 | 异常断言 |
| T335 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_terminal_state_cannot_resume | willy.pipeline_state / TestPipelineStateMachine | 验证 `terminal_state_cannot_resume` 行为 | 异常断言 |
| T336 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_state_revision_is_monotonic_and_persisted | willy.pipeline_state / TestPipelineStateMachine | 验证 `state_revision_is_monotonic_and_persisted` 行为 | tmp_path 隔离工作区 |
| T337 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_controlled_resume_preserves_revision_before_next_write | willy.pipeline_state / TestPipelineStateMachine | 验证 `controlled_resume_preserves_revision_before_next_write` 行为 | 进程内行为断言 |
| T338 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_external_stop_revision_cannot_be_overwritten_by_old_heartbeat | willy.pipeline_state / TestPipelineStateMachine | 验证 `external_stop_revision_cannot_be_overwritten_by_old_heartbeat` 行为 | tmp_path 隔离工作区 |
| T339 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_set_step | willy.pipeline_state / TestPipelineStateMachine | 验证 `set_step` 行为 | 进程内行为断言 |
| T340 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_mark_done | willy.pipeline_state / TestPipelineStateMachine | 验证 `mark_done` 行为 | 进程内行为断言 |
| T341 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_rollback_to_invalidates_downstream_steps | willy.pipeline_state / TestPipelineStateMachine | 验证 `rollback_to_invalidates_downstream_steps` 行为 | 进程内行为断言 |
| T342 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_controlled_restart_withdraws_only_the_restarted_suffix | willy.pipeline_state / TestPipelineStateMachine | 验证 `controlled_restart_withdraws_only_the_restarted_suffix` 行为 | 进程内行为断言 |
| T343 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_set_error | willy.pipeline_state / TestPipelineStateMachine | 验证 `set_error` 行为 | 进程内行为断言 |
| T344 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_start_retry | willy.pipeline_state / TestPipelineStateMachine | 验证 `start_retry` 行为 | 进程内行为断言 |
| T345 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_add_action | willy.pipeline_state / TestPipelineStateMachine | 验证 `add_action` 行为 | 进程内行为断言 |
| T346 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_public_repair_snapshot_includes_only_safe_adjustments | willy.pipeline_state / TestPipelineStateMachine | 验证 `public_repair_snapshot_includes_only_safe_adjustments` 行为 | tmp_path 隔离工作区 |
| T347 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_set_escalated | willy.pipeline_state / TestPipelineStateMachine | 验证 `set_escalated` 行为 | 进程内行为断言 |
| T348 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_set_aborted | willy.pipeline_state / TestPipelineStateMachine | 验证 `set_aborted` 行为 | 进程内行为断言 |
| T349 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_user_requested_abort_clears_stale_failure | willy.pipeline_state / TestPipelineStateMachine | 验证 `user_requested_abort_clears_stale_failure` 行为 | 进程内行为断言 |
| T350 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_full_pipeline_flow | willy.pipeline_state / TestPipelineStateMachine | 验证：完整的流水线状态流转: IDLE → RUNNING → (每个步骤) → DONE | 进程内行为断言 |
| T351 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_error_then_retry_then_done_flow | willy.pipeline_state / TestPipelineStateMachine | 验证：错误 → 重试 → 恢复 → 完成流程 | 进程内行为断言 |
| T352 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_final_escalation_flow | willy.pipeline_state / TestPipelineStateMachine | 验证：所有重试用尽 → 升级流程 | 进程内行为断言 |
| T353 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_writes_status_json | willy.pipeline_state / TestPipelineStateMachine | 验证：状态机应在初始化时写入 status.json | tmp_path 隔离工作区 + monkeypatch |
| T354 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_atomic_write | willy.pipeline_state / TestPipelineStateMachine | 验证：写入应为原子操作（先 .tmp，再 os.replace） | tmp_path 隔离工作区 + monkeypatch |
| T355 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_read_static_method | willy.pipeline_state / TestPipelineStateMachine | 验证：read() 应读取当前的 status.json | tmp_path 隔离工作区 + monkeypatch |
| T356 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_read_missing_file_returns_idle | willy.pipeline_state / TestPipelineStateMachine | 验证：status.json 不存在时应返回 IDLE | tmp_path 隔离工作区 + monkeypatch |
| T357 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_read_corrupted_file_returns_idle | willy.pipeline_state / TestPipelineStateMachine | 验证：status.json 损坏时应返回 IDLE | tmp_path 隔离工作区 + monkeypatch |
| T358 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_error_message_handling | willy.pipeline_state / TestPipelineStateMachine | 验证：错误消息处理 | tmp_path 隔离工作区 + monkeypatch |
| T359 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_rapid_state_transitions | willy.pipeline_state / TestPipelineStateMachine | 验证：快速状态转换不应崩溃 | tmp_path 隔离工作区 + monkeypatch |
| T360 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_custom_total_steps | willy.pipeline_state / TestPipelineStateMachine | 验证：自定义 total_steps 应被存储 | tmp_path 隔离工作区 + monkeypatch |
| T361 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_activity_is_strictly_structured_and_persisted_atomically | willy.pipeline_state / TestPipelineStateMachine | 验证 `activity_is_strictly_structured_and_persisted_atomically` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T362 | tests/test_pipeline_state.py::TestPipelineStateMachine::test_escalation_public_snapshot_drops_raw_output | willy.pipeline_state / TestPipelineStateMachine | 验证 `escalation_public_snapshot_drops_raw_output` 行为 | tmp_path 隔离工作区 |
| T377 | tests/test_recovery_contracts.py::test_pending_action_adapts_to_generic_contract | willy.action_contract / recovery contract | 验证 `pending_action_adapts_to_generic_contract` 行为 | 进程内行为断言 |
| T378 | tests/test_recovery_contracts.py::test_decision_trace_redacts_paths_and_secrets | willy.action_contract / recovery contract | 验证 `decision_trace_redacts_paths_and_secrets` 行为 | tmp_path 隔离工作区 |
| T379 | tests/test_recovery_policy.py::test_eq_policy_keeps_prod_out_and_requires_confirmation | willy.recovery_policy | 验证 `eq_policy_keeps_prod_out_and_requires_confirmation` 行为 | 进程内行为断言 |
| T380 | tests/test_recovery_policy.py::test_policy_allows_confirmed_safe_retry_with_bounded_attempts | willy.recovery_policy | 验证 `policy_allows_confirmed_safe_retry_with_bounded_attempts` 行为 | 进程内行为断言 |
| T381 | tests/test_recovery_policy.py::test_quantum_parameter_effects_require_confirmation_or_fork | willy.recovery_policy | 验证 `quantum_parameter_effects_require_confirmation_or_fork` 行为 | 进程内行为断言 |
| T445 | tests/test_step_registry.py::test_registry_is_contiguous_and_covers_the_current_pipeline | willy.step_registry | 验证 `registry_is_contiguous_and_covers_the_current_pipeline` 行为 | 进程内行为断言 |
| T446 | tests/test_step_registry.py::test_registry_rejects_gaps_and_duplicate_ids | willy.step_registry | 验证 `registry_rejects_gaps_and_duplicate_ids` 行为 | 异常断言 |
| T447 | tests/test_step_registry.py::test_execution_modules_own_normalized_tool_and_step_dependencies | willy.step_registry | 验证 `execution_modules_own_normalized_tool_and_step_dependencies` 行为 | 进程内行为断言 |
| T448 | tests/test_step_registry.py::test_execution_module_registry_rejects_unknown_steps_and_duplicate_tools | willy.step_registry | 验证 `execution_module_registry_rejects_unknown_steps_and_duplicate_tools` 行为 | 异常断言 |

## I. 运行管理、启动控制与运行助理

运行预留、互斥启动、进程生命周期、保留清理、审计、provenance、RunStore 与只读助理。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T270 | tests/test_pipeline_launch.py::test_reservation_is_atomic_and_allocates_a_run_id | willy.pipeline_launch | 验证 `reservation_is_atomic_and_allocates_a_run_id` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T271 | tests/test_pipeline_launch.py::test_reservation_derives_the_next_sequence_from_existing_run_directories | willy.pipeline_launch | 验证 `reservation_derives_the_next_sequence_from_existing_run_directories` 行为 | tmp_path 隔离工作区 |
| T272 | tests/test_pipeline_launch.py::test_legacy_live_lock_remains_authoritative_until_its_owner_exits | willy.pipeline_launch | 验证 `legacy_live_lock_remains_authoritative_until_its_owner_exits` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T273 | tests/test_pipeline_launch.py::test_frontend_recovers_the_current_run_from_a_live_legacy_lock | willy.pipeline_launch | 验证 `frontend_recovers_the_current_run_from_a_live_legacy_lock` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T274 | tests/test_pipeline_launch.py::test_unbound_startup_audit_has_only_public_messages | willy.pipeline_launch | 验证 `unbound_startup_audit_has_only_public_messages` 行为 | tmp_path 隔离工作区 |
| T275 | tests/test_pipeline_launch.py::test_frontend_launch_conflict_does_not_spawn_a_second_child | willy.pipeline_launch | 验证 `frontend_launch_conflict_does_not_spawn_a_second_child` 行为 | tmp_path 隔离工作区 + monkeypatch + mock/patch |
| T276 | tests/test_pipeline_launch.py::test_deferred_state_machine_does_not_write_root_before_run_binding | willy.pipeline_launch | 验证 `deferred_state_machine_does_not_write_root_before_run_binding` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T277 | tests/test_pipeline_launch.py::test_orchestrator_binds_state_to_run_before_any_status_write | willy.pipeline_launch | 验证 `orchestrator_binds_state_to_run_before_any_status_write` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T371 | tests/test_process_lifecycle.py::test_termination_escalates_the_process_group | willy.process_lifecycle | 验证 `termination_escalates_the_process_group` 行为 | monkeypatch |
| T372 | tests/test_process_lifecycle.py::test_lifecycle_record_excludes_command_paths | willy.process_lifecycle | 验证 `lifecycle_record_excludes_command_paths` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T373 | tests/test_process_lifecycle.py::test_managed_command_honors_stop_request_and_records_audit | willy.process_lifecycle | 验证 `managed_command_honors_stop_request_and_records_audit` 行为 | tmp_path 隔离工作区 |
| T374 | tests/test_process_lifecycle.py::test_managed_command_timeout_terminates_and_records_audit | willy.process_lifecycle | 验证 `managed_command_timeout_terminates_and_records_audit` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T375 | tests/test_prune_runs.py::test_prune_runs_requires_apply_and_updates_the_index | scripts.prune_runs | 验证 `prune_runs_requires_apply_and_updates_the_index` 行为 | tmp_path 隔离工作区 |
| T376 | tests/test_prune_runs.py::test_prune_runs_refuses_when_a_pipeline_lock_is_active | scripts.prune_runs | 验证 `prune_runs_refuses_when_a_pipeline_lock_is_active` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T382 | tests/test_run_assistant.py::TestRunRegistry::test_register_run_hashes_reused_quantum_intermediate | willy.run_registry / toolist_run / agent_run / TestRunRegistry | 验证 `register_run_hashes_reused_quantum_intermediate` 行为 | tmp_path 隔离工作区 |
| T383 | tests/test_run_assistant.py::TestRunRegistry::test_register_status_result_and_artifact_contract | willy.run_registry / toolist_run / agent_run / TestRunRegistry | 验证 `register_status_result_and_artifact_contract` 行为 | tmp_path 隔离工作区 |
| T384 | tests/test_run_assistant.py::TestRunRegistry::test_registry_rejects_path_traversal_and_log_secrets_are_redacted | willy.run_registry / toolist_run / agent_run / TestRunRegistry | 验证 `registry_rejects_path_traversal_and_log_secrets_are_redacted` 行为 | tmp_path 隔离工作区 + 异常断言 |
| T385 | tests/test_run_assistant.py::TestRunRegistry::test_box_parameters_expose_only_audited_geometry | willy.run_registry / toolist_run / agent_run / TestRunRegistry | 验证 `box_parameters_expose_only_audited_geometry` 行为 | tmp_path 隔离工作区 |
| T386 | tests/test_run_assistant.py::TestRunRegistry::test_status_observer_is_best_effort_and_event_typed | willy.run_registry / toolist_run / agent_run / TestRunRegistry | 验证 `status_observer_is_best_effort_and_event_typed` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T387 | tests/test_run_assistant.py::TestRunRegistry::test_public_status_events_and_reports_never_include_engine_output | willy.run_registry / toolist_run / agent_run / TestRunRegistry | 验证 `public_status_events_and_reports_never_include_engine_output` 行为 | tmp_path 隔离工作区 |
| T388 | tests/test_run_assistant.py::TestRunRegistry::test_active_eq_manifest_reconciles_a_status_that_wrongly_entered_prod | willy.run_registry / toolist_run / agent_run / TestRunRegistry | 验证：Only public state changes when a live EQ contradicts a PROD status. | tmp_path 隔离工作区 |
| T389 | tests/test_run_assistant.py::TestRunRegistry::test_stale_eq_manifest_does_not_reopen_a_stopped_run_as_live | willy.run_registry / toolist_run / agent_run / TestRunRegistry | 验证：A stale manifest alone cannot overwrite the public terminal path. | tmp_path 隔离工作区 |
| T390 | tests/test_run_assistant.py::TestRunTools::test_tools_are_all_read_only_and_server_binds_run_id | willy.run_registry / toolist_run / agent_run / TestRunTools | 验证 `tools_are_all_read_only_and_server_binds_run_id` 行为 | tmp_path 隔离工作区 |
| T391 | tests/test_run_assistant.py::TestRunTools::test_environment_tool_returns_only_redacted_capabilities | willy.run_registry / toolist_run / agent_run / TestRunTools | 验证 `environment_tool_returns_only_redacted_capabilities` 行为 | tmp_path 隔离工作区 |
| T392 | tests/test_run_assistant.py::TestRunTools::test_box_tool_returns_audited_geometry | willy.run_registry / toolist_run / agent_run / TestRunTools | 验证 `box_tool_returns_audited_geometry` 行为 | tmp_path 隔离工作区 |
| T393 | tests/test_run_assistant.py::TestRunTools::test_md_eta_tool_returns_only_validated_gromacs_prediction | willy.run_registry / toolist_run / agent_run / TestRunTools | 验证 `md_eta_tool_returns_only_validated_gromacs_prediction` 行为 | tmp_path 隔离工作区 |
| T394 | tests/test_run_assistant.py::TestRunTools::test_runtime_heartbeat_updates_status_without_appending_events | willy.run_registry / toolist_run / agent_run / TestRunTools | 验证 `runtime_heartbeat_updates_status_without_appending_events` 行为 | tmp_path 隔离工作区 |
| T395 | tests/test_run_assistant.py::TestRunTools::test_md_eta_tool_rejects_invalid_snapshot_and_handles_no_snapshot | willy.run_registry / toolist_run / agent_run / TestRunTools | 验证 `md_eta_tool_rejects_invalid_snapshot_and_handles_no_snapshot` 行为 | tmp_path 隔离工作区 |
| T396 | tests/test_run_assistant.py::TestRunTools::test_list_runs_does_not_create_an_index_for_legacy_runs | willy.run_registry / toolist_run / agent_run / TestRunTools | 验证 `list_runs_does_not_create_an_index_for_legacy_runs` 行为 | tmp_path 隔离工作区 |
| T397 | tests/test_run_assistant.py::TestRunAssistant::test_assistant_answers_status_fast_path_without_llm | willy.run_registry / toolist_run / agent_run / TestRunAssistant | 验证 `assistant_answers_status_fast_path_without_llm` 行为 | tmp_path 隔离工作区 + mock/patch |
| T398 | tests/test_run_assistant.py::TestRunAssistant::test_assistant_labels_waiting_confirmation_without_falling_back_to_unknown | willy.run_registry / toolist_run / agent_run / TestRunAssistant | 验证 `assistant_labels_waiting_confirmation_without_falling_back_to_unknown` 行为 | 进程内行为断言 |
| T399 | tests/test_run_assistant.py::TestRunAssistant::test_assistant_can_call_only_bound_read_tool_for_complex_question | willy.run_registry / toolist_run / agent_run / TestRunAssistant | 验证 `assistant_can_call_only_bound_read_tool_for_complex_question` 行为 | tmp_path 隔离工作区 + mock/patch |
| T400 | tests/test_run_assistant.py::TestRunAssistant::test_assistant_limits_history_to_six_compact_turns | willy.run_registry / toolist_run / agent_run / TestRunAssistant | 验证 `assistant_limits_history_to_six_compact_turns` 行为 | tmp_path 隔离工作区 + mock/patch |
| T401 | tests/test_run_assistant.py::TestRunAssistant::test_assistant_degrades_complex_question_to_local_facts_without_llm | willy.run_registry / toolist_run / agent_run / TestRunAssistant | 验证 `assistant_degrades_complex_question_to_local_facts_without_llm` 行为 | tmp_path 隔离工作区 |
| T402 | tests/test_run_assistant.py::TestRunAssistant::test_assistant_prefers_local_eta_and_waiting_heartbeat_times | willy.run_registry / toolist_run / agent_run / TestRunAssistant | 验证 `assistant_prefers_local_eta_and_waiting_heartbeat_times` 行为 | 进程内行为断言 |
| T403 | tests/test_run_assistant.py::TestOrchestratorRunRegistration::test_prepared_workspace_registers_run_and_binds_state | willy.run_registry / toolist_run / agent_run / TestOrchestratorRunRegistration | 验证 `prepared_workspace_registers_run_and_binds_state` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T404 | tests/test_run_provenance.py::test_provenance_is_run_local_redacted_and_tracks_config_revisions | willy.run_provenance | 验证 `provenance_is_run_local_redacted_and_tracks_config_revisions` 行为 | tmp_path 隔离工作区 |
| T405 | tests/test_run_provenance.py::test_provenance_rejects_config_outside_current_run | willy.run_provenance | 验证 `provenance_rejects_config_outside_current_run` 行为 | tmp_path 隔离工作区 |
| T406 | tests/test_run_store.py::test_manifest_read_modify_write_is_serialized | willy.run_store | 验证 `manifest_read_modify_write_is_serialized` 行为 | tmp_path 隔离工作区 |
| T407 | tests/test_run_store.py::test_events_and_decisions_share_one_monotonic_run_sequence | willy.run_store | 验证 `events_and_decisions_share_one_monotonic_run_sequence` 行为 | tmp_path 隔离工作区 |
| T408 | tests/test_run_store.py::test_pending_bundle_replays_missing_json_and_event_once | willy.run_store | 验证 `pending_bundle_replays_missing_json_and_event_once` 行为 | tmp_path 隔离工作区 |

## J. 外部 Smoke 与发布证据

外部工具预检、fixture 完整性、证据归档和 required 发布门禁。

| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |
|---|---|---|---|---|
| T141 | tests/test_external_smoke.py::test_smoke_registry_has_unique_case_ids_and_declared_timeout | willy.external_smoke / self-hosted CI gate | 验证 `smoke_registry_has_unique_case_ids_and_declared_timeout` 行为 | 进程内行为断言 |
| T142 | tests/test_external_smoke.py::test_smoke_preflight_is_read_only_and_reports_missing_fixture | willy.external_smoke / self-hosted CI gate | 验证 `smoke_preflight_is_read_only_and_reports_missing_fixture` 行为 | tmp_path 隔离工作区 |
| T143 | tests/test_external_smoke.py::test_smoke_preflight_accepts_only_hash_verified_declared_fixture | willy.external_smoke / self-hosted CI gate | 验证 `smoke_preflight_accepts_only_hash_verified_declared_fixture` 行为 | tmp_path 隔离工作区 |
| T144 | tests/test_external_smoke.py::test_smoke_preflight_rejects_tampered_fixture_bundle | willy.external_smoke / self-hosted CI gate | 验证 `smoke_preflight_rejects_tampered_fixture_bundle` 行为 | tmp_path 隔离工作区 |
| T145 | tests/test_external_smoke.py::test_smoke_evidence_is_opt_in_and_redacted | willy.external_smoke / self-hosted CI gate | 验证 `smoke_evidence_is_opt_in_and_redacted` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T146 | tests/test_external_smoke.py::test_evidence_verifier_rejects_preflight_record_as_execution | willy.external_smoke / self-hosted CI gate | 验证 `evidence_verifier_rejects_preflight_record_as_execution` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T147 | tests/test_external_smoke.py::test_evidence_verifier_requires_declared_outputs_for_successful_execution | willy.external_smoke / self-hosted CI gate | 验证 `evidence_verifier_requires_declared_outputs_for_successful_execution` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T148 | tests/test_external_smoke.py::test_evidence_verifier_rejects_path_traversal_in_tampered_artifact | willy.external_smoke / self-hosted CI gate | 验证 `evidence_verifier_rejects_path_traversal_in_tampered_artifact` 行为 | tmp_path 隔离工作区 + monkeypatch |
| T149 | tests/test_external_smoke.py::test_selected_smoke_cases_rejects_duplicate_ids | willy.external_smoke / self-hosted CI gate | 验证 `selected_smoke_cases_rejects_duplicate_ids` 行为 | 异常断言 |
| T150 | tests/test_external_smoke.py::test_external_smoke_preflight_gate[gromacs_minimal] | willy.external_smoke / self-hosted CI gate | 验证：An acceptance machine can make missing tools/fixtures a hard failure.；参数集 `gromacs_minimal` | pytest 参数化 |
| T151 | tests/test_external_smoke.py::test_external_smoke_preflight_gate[sobtop_ec] | willy.external_smoke / self-hosted CI gate | 验证：An acceptance machine can make missing tools/fixtures a hard failure.；参数集 `sobtop_ec` | pytest 参数化 |
| T152 | tests/test_external_smoke.py::test_external_smoke_preflight_gate[g16_minimal] | willy.external_smoke / self-hosted CI gate | 验证：An acceptance machine can make missing tools/fixtures a hard failure.；参数集 `g16_minimal` | pytest 参数化 |
| T153 | tests/test_external_smoke.py::test_external_smoke_preflight_gate[orca_minimal] | willy.external_smoke / self-hosted CI gate | 验证：An acceptance machine can make missing tools/fixtures a hard failure.；参数集 `orca_minimal` | pytest 参数化 |
| T154 | tests/test_external_smoke.py::test_external_smoke_preflight_gate[multiwfn_minimal] | willy.external_smoke / self-hosted CI gate | 验证：An acceptance machine can make missing tools/fixtures a hard failure.；参数集 `multiwfn_minimal` | pytest 参数化 |
| T155 | tests/test_external_smoke.py::test_external_smoke_preflight_gate[ligpargen_minimal] | willy.external_smoke / self-hosted CI gate | 验证：An acceptance machine can make missing tools/fixtures a hard failure.；参数集 `ligpargen_minimal` | pytest 参数化 |
