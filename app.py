"""app.py — Willy Agent Gradio UI (纯前端, 后端操作全部委托给 frontend_api)"""

import html
import os
from collections.abc import Mapping

import gradio as gr

from willy import frontend_api
from willy.agent_config import chat, handle_upload
from willy.env_registry import dotenv_value
from willy.llm_config import llm_form_defaults
from willy.frontend_api import (
    stop_pipeline,
    is_pipeline_running,
    get_llm_config_notice,
    save_llm_config,
    test_llm_connection,
    get_latest_run_control_state,
    run_assistant_pending_message,
    chat_run_assistant,
)


def _ensure_loopback_proxy_bypass() -> None:
    """Keep Gradio's local startup check out of user-configured HTTP proxies."""
    loopback_hosts = ("127.0.0.1", "localhost", "::1")
    for variable in ("NO_PROXY", "no_proxy"):
        entries = [entry.strip() for entry in os.environ.get(variable, "").split(",") if entry.strip()]
        existing = {entry.lower() for entry in entries}
        entries.extend(host for host in loopback_hosts if host.lower() not in existing)
        os.environ[variable] = ",".join(entries)


_ensure_loopback_proxy_bypass()


APP_CSS = """
:root {
    --willy-ui-font: "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB", sans-serif;
    --font: var(--willy-ui-font);
    --page-bg: #f7f3eb;
    --text: #25302d;
    --muted-text: #58625e;
    --input-bg: #fffdfa;
    --input-border: #cfc8be;
    --visual-bg: #eee9e2;
    --visual-border: #d9d0c6;
    --visual-title: #745743;
    --proposal-bg: #e6edef;
    --proposal-border: #cbd9dd;
    --proposal-title: #28586b;
    --run-bg: #e7eee8;
    --run-border: #cbdacc;
    --run-title: #2e6349;
    --chart-bg: #eee8ef;
    --chart-border: #d8c9dc;
    --chart-title: #725779;
    --status-muted: #b7c6be;
    --status-running: #17825d;
    --status-error: #b6333c;
    --status-stopped: #67736d;
    --workbench-panel-height: 52.8rem;
}

html.willy-dark,
.gradio-container.willy-dark {
    --page-bg: #171a1a;
    --text: #e5e9e5;
    --muted-text: #b8c1bb;
    --input-bg: #262b2b;
    --input-border: #4c5754;
    --visual-bg: #25221f;
    --visual-border: #554c43;
    --visual-title: #e1b988;
    --proposal-bg: #1d272a;
    --proposal-border: #3d585f;
    --proposal-title: #9bd5df;
    --run-bg: #1e2922;
    --run-border: #405d4b;
    --run-title: #9bd3af;
    --chart-bg: #29232b;
    --chart-border: #5d4d61;
    --chart-title: #d8b8dd;
    --status-muted: #6d8076;
    --status-running: #55c991;
    --status-error: #f07b82;
    --status-stopped: #b2beb7;

    /* Gradio component theme: tabs, chat bubbles, inputs, and button surfaces. */
    --body-background-fill: var(--page-bg);
    --background-fill-primary: #202524;
    --background-fill-secondary: #292f2d;
    --block-background-fill: #222827;
    --block-border-color: var(--input-border);
    --block-label-text-color: var(--muted-text);
    --body-text-color: var(--text);
    --body-text-color-subdued: var(--muted-text);
    --input-background-fill: var(--input-bg);
    --input-border-color: var(--input-border);
    --border-color-primary: var(--input-border);
    --border-color-accent: var(--status-running);
    --color-accent: var(--status-running);
    --color-accent-soft: #214d3d;
    --button-primary-background-fill: #247d60;
    --button-primary-background-fill-hover: #2e9471;
    --button-primary-border-color: #247d60;
    --button-primary-text-color: #ffffff;
    --button-secondary-background-fill: #2a3330;
    --button-secondary-background-fill-hover: #37443f;
    --button-secondary-border-color: var(--input-border);
    --button-secondary-text-color: var(--text);
}

html {
    font-size: 14px;
    overflow-y: scroll;
    scrollbar-gutter: stable;
}
html, body, .gradio-container {
    background: var(--page-bg) !important;
    color: var(--text);
    font-family: var(--willy-ui-font) !important;
}

.gradio-container :is(button, input, textarea, select, [role="tab"], label, .block-label) {
    font-family: var(--willy-ui-font) !important;
}

footer { display: none !important; }
.center-row { justify-content: center; }

#assistant-row,
#workspace-row {
    align-items: stretch;
    gap: 0.75rem;
}

#assistant-row > div,
#workspace-row > div {
    min-width: 0;
}

#app-header {
    display: grid !important;
    grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
    align-items: center;
    margin-bottom: 0.75rem;
}

#app-title {
    grid-column: 2;
    text-align: center;
}

#app-title h1 {
    color: var(--text);
    font-family: var(--willy-ui-font);
    font-size: 2.25rem;
    line-height: 1.2;
    margin: 0;
}

#theme-toggle {
    background: transparent !important;
    border: 0 !important;
    bottom: 1rem;
    box-shadow: none !important;
    margin: 0 !important;
    min-width: 0;
    padding: 0 !important;
    position: fixed !important;
    right: 1rem;
    width: auto !important;
    z-index: 1000;
}

#theme-toggle label {
    align-items: center;
    background: transparent !important;
    border: 0 !important;
    box-shadow: none !important;
    color: var(--text);
    cursor: pointer;
    display: inline-flex;
    font-size: 0.9rem;
    font-weight: 600;
    gap: 0.5rem;
    padding: 0 !important;
    white-space: nowrap;
}

#theme-toggle input[type="checkbox"] {
    appearance: none;
    background: var(--input-bg);
    border: 1px solid var(--input-border);
    border-radius: 999px;
    cursor: pointer;
    height: 1.4rem;
    margin: 0;
    position: relative;
    transition: background 0.18s ease, border-color 0.18s ease;
    width: 2.55rem;
}

#theme-toggle input[type="checkbox"]::after {
    background: var(--muted-text);
    border-radius: 50%;
    content: "";
    height: 0.95rem;
    left: 0.18rem;
    position: absolute;
    top: 0.16rem;
    transition: transform 0.18s ease, background 0.18s ease;
    width: 0.95rem;
}

#theme-toggle input[type="checkbox"]:checked {
    background: var(--status-running);
    border-color: var(--status-running);
}

#theme-toggle input[type="checkbox"]:checked::after {
    background: #ffffff;
    transform: translateX(1.1rem);
}

.gradio-container textarea,
.gradio-container input:not([type="checkbox"]),
.gradio-container select {
    background: var(--input-bg) !important;
    border-color: var(--input-border) !important;
    color: var(--text) !important;
}

.gradio-container .prose,
.gradio-container .prose * {
    color: var(--text);
}

.gradio-container .block-label,
.gradio-container label span {
    color: var(--muted-text);
}

.gradio-container.willy-dark [role="tab"] {
    background: transparent;
    color: var(--muted-text);
}

.gradio-container.willy-dark [role="tab"][aria-selected="true"] {
    background: var(--background-fill-primary);
    color: var(--text);
}

.gradio-container.willy-dark button:not(.primary):not(.stop) {
    background: var(--button-secondary-background-fill);
    border-color: var(--button-secondary-border-color);
    color: var(--button-secondary-text-color);
}

#stop-pipeline-button:disabled {
    background: #858d89 !important;
    border-color: #858d89 !important;
    color: #ffffff !important;
    cursor: not-allowed;
    opacity: 1;
}

.gradio-container.willy-dark #proposal-assistant .bot,
.gradio-container.willy-dark #run-assistant .bot {
    background: var(--background-fill-secondary);
    border-color: var(--border-color-primary);
}

.gradio-container.willy-dark #proposal-assistant .user,
.gradio-container.willy-dark #run-assistant .user {
    background: var(--color-accent-soft);
    border-color: var(--border-color-accent);
}

#visualization-panel,
#proposal-assistant,
#run-assistant,
#chart-panel {
    border: 1px solid;
    border-radius: 6px;
    box-sizing: border-box;
    height: var(--workbench-panel-height);
    padding: 1rem;
}

#proposal-assistant { overflow-y: auto; }

#visualization-panel,
#run-assistant,
#chart-panel {
    overflow: hidden;
}

#visualization-panel {
    background: var(--visual-bg);
    border-color: var(--visual-border);
    display: flex;
    flex-direction: column;
}

#structure-selector {
    flex: 0 0 auto;
    margin-bottom: 0.5rem;
}

#structure-viewer {
    flex: 1 1 auto;
    min-height: 0;
    overflow: hidden;
}

#structure-viewer iframe {
    display: block;
    height: min(36rem, calc(var(--workbench-panel-height) - 11rem)) !important;
    width: 100% !important;
}

#proposal-assistant {
    background: var(--proposal-bg);
    border-color: var(--proposal-border);
}

#proposal-assistant,
#run-assistant {
    display: flex;
    flex-direction: column;
}

#proposal-header {
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.65rem;
}

#proposal-header .panel-title { margin: 0; }

#proposal-chat,
#run-chat {
    margin-top: auto;
}

#proposal-input-row,
#run-input-row {
    margin-bottom: 0;
}

#run-assistant {
    background: var(--run-bg);
    border-color: var(--run-border);
}

#chart-panel {
    background: var(--chart-bg);
    border-color: var(--chart-border);
}

.panel-title {
    font-size: 1.3rem;
    font-weight: 700;
    letter-spacing: 0;
    margin: 0 0 0.65rem;
}

#visualization-panel .panel-title { color: var(--visual-title); }
#proposal-assistant .panel-title { color: var(--proposal-title); }
#run-assistant .panel-title { color: var(--run-title); }
#chart-panel .panel-title { color: var(--chart-title); }

.development-placeholder {
    align-items: center;
    color: var(--muted-text);
    display: flex;
    font-size: 1rem;
    height: calc(var(--workbench-panel-height) - 5rem);
    justify-content: center;
}

#about-page {
    margin: 0 auto;
    max-width: 70rem;
    padding: 0.5rem 0 1.5rem;
}

#about-page .about-intro {
    max-width: 62rem;
}

#about-page .about-intro h2 {
    color: var(--text);
    font-size: 1.55rem;
    margin: 0 0 0.55rem;
}

#about-page p,
#about-page li {
    color: var(--muted-text);
    line-height: 1.7;
}

#about-page .about-grid {
    display: grid;
    gap: 1.5rem;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    margin-top: 1.5rem;
}

#about-page .about-section {
    border-top: 1px solid var(--input-border);
    padding-top: 0.8rem;
}

#about-page .about-section h2 {
    color: var(--text);
    font-size: 1.1rem;
    margin: 0 0 0.45rem;
}

#about-page .about-section p {
    margin: 0;
}

#about-page .about-section ul {
    margin: 0;
    padding-left: 1.2rem;
}

#about-qr-section {
    align-items: center;
    border-top: 1px solid #aeb4b0;
    display: flex;
    flex-direction: column;
    margin-top: 2.5rem;
    padding-top: 1.25rem;
}

#about-qr-section p {
    margin: 0 0 0.75rem;
}

#official-account-code {
    margin: 0 auto;
    max-width: 12rem;
}

#official-account-code img {
    display: block;
    height: auto !important;
    margin: 0 auto;
    max-width: 100%;
}

#run-chat .pipeline-status-indicator {
    display: inline-block;
    position: relative;
    width: 1.15em;
    height: 1.15em;
    margin-right: 0.35rem;
    vertical-align: -0.18em;
}

#run-chat .pipeline-status-spinner {
    box-sizing: border-box;
    display: block;
    position: absolute;
    inset: 0.08em;
    border: 0.14em solid var(--status-muted);
    border-top-color: var(--status-running);
    border-radius: 50%;
}

#run-chat .pipeline-status-indicator--running .pipeline-status-spinner {
    animation: pipeline-status-spin 0.9s linear infinite;
}

#run-chat .pipeline-status-indicator--done .pipeline-status-spinner {
    animation: pipeline-status-spinner-exit 0.2s ease-out forwards;
}

#run-chat .pipeline-status-check {
    display: block;
    position: absolute;
    inset: 0;
    color: var(--status-running);
    font-family: system-ui, sans-serif;
    font-size: 1.15em;
    font-weight: 700;
    line-height: 1.05;
    opacity: 0;
    text-align: center;
    transform: scale(0.72);
    animation: pipeline-status-check-enter 0.25s ease-out 0.16s forwards;
}

#run-chat .pipeline-status-indicator--error,
#run-chat .pipeline-status-indicator--stopped {
    color: var(--status-error);
    font-family: system-ui, sans-serif;
    font-size: 1.1em;
    font-weight: 700;
    line-height: 1.1;
    text-align: center;
}

#run-chat .pipeline-status-indicator--stopped {
    color: var(--status-stopped);
    font-size: 0.7em;
}

@keyframes pipeline-status-spin {
    to { transform: rotate(360deg); }
}

@keyframes pipeline-status-spinner-exit {
    to { opacity: 0; transform: rotate(90deg) scale(0.72); }
}

@keyframes pipeline-status-check-enter {
    to { opacity: 1; transform: scale(1); }
}

@media (prefers-reduced-motion: reduce) {
    #run-chat .pipeline-status-spinner,
    #run-chat .pipeline-status-check {
        animation: none;
    }

    #run-chat .pipeline-status-indicator--done .pipeline-status-spinner {
        opacity: 0;
    }

    #run-chat .pipeline-status-check {
        opacity: 1;
        transform: none;
    }
}

@media (max-width: 768px) {
    :root { --workbench-panel-height: 48rem; }

    #assistant-row,
    #workspace-row {
        flex-wrap: wrap;
    }

    #assistant-row > div,
    #workspace-row > div {
        flex-basis: 100% !important;
        width: 100% !important;
    }

    #about-page .about-grid {
        grid-template-columns: 1fr;
    }
}

"""

THEME_TOGGLE_JS = """
(is_dark) => {
    const enabled = Boolean(is_dark);
    document.documentElement.classList.toggle("willy-dark", enabled);

    const app = document.querySelector("gradio-app");
    const container = app?.shadowRoot?.querySelector(".gradio-container")
        ?? document.querySelector(".gradio-container");
    container?.classList.toggle("willy-dark", enabled);
    return [];
}
"""

PROPOSAL_EXAMPLE = (
    "示例：请帮我跑一个 MD：100 Li+、100 PF6-、400 EC、600 EMC，"
    "生产 20 ns；优化 Gaussian16，力场 OPLSAA"
)

OFFICIAL_ACCOUNT_QR = "assets/qrcode_for_gh_7df1329939c6_258.jpg"


def _refresh_current_run_structure_choices(selected_choice: str | None):
    """Refresh the active run directory when the structure chooser is opened."""
    choices = frontend_api.get_run_visualization_choices()
    choice = selected_choice if selected_choice in choices else (choices[0] if choices else None)
    return (
        gr.update(choices=choices, value=choice, interactive=True),
        frontend_api.render_run_visualization_html(choice),
    )


def _render_current_run_structure(choice: str | None):
    """Render only a structure still authorized by the current run directory."""
    return frontend_api.render_run_visualization_html(choice)


def _render_proposal_chat(history):
    """Return the text-only proposal conversation for Gradio rendering."""
    return [dict(message) for message in list(history or [])]


def _chat_wrapper(message, history, pending_plan=None):
    """Adapt the proposal generator and keep the conversation input consistent."""
    current_history = list(history or [])
    latest_chatbot = _render_proposal_chat(current_history)
    latest_state = current_history
    latest_plan = pending_plan
    if not message or not message.strip():
        yield (
            gr.update(value="", interactive=True),
            _render_proposal_chat(current_history),
            current_history,
            pending_plan,
            gr.update(interactive=True),
            gr.update(interactive=True),
        )
        return

    for msg_val, chatbot_val, state_val, plan_val, _html_val, _confirmation_state in chat(
        message, current_history, pending_plan,
    ):
        latest_chatbot = _render_proposal_chat(chatbot_val)
        latest_state = state_val
        latest_plan = plan_val
        yield (
            gr.update(value=msg_val, interactive=False),
            latest_chatbot,
            state_val,
            plan_val,
            gr.update(interactive=False),
            gr.update(interactive=False),
        )

    yield (
        gr.update(value="", interactive=True),
        latest_chatbot,
        latest_state,
        latest_plan,
        gr.update(interactive=True),
        gr.update(interactive=True),
    )


# ============================================================
# 流水线控制
# ============================================================

def _pipeline_running() -> bool:
    return is_pipeline_running()


def _request_pipeline_stop():
    """Request a stop and lock the control only after backend acknowledgement."""
    try:
        message = stop_pipeline(clean=False) or ""
    except Exception:
        message = ""

    if "安全停止" in message:
        label = "正在安全停止"
    elif "已请求中止" in message:
        label = "正在停止"
    else:
        return gr.update(
            value="中止请求未送达，请重试",
            interactive=True,
            variant="stop",
            visible=True,
        ), False, "中止请求未送达，请重试。"

    return gr.update(
        value=label,
        interactive=False,
        variant="secondary",
        visible=True,
    ), True, "已请求安全停止，正在等待当前工序写入 checkpoint。"


def _handle_stop_button_click(confirmation_pending: bool, stop_requested: bool):
    """Use a server-visible two-step confirmation for the stop button."""
    if stop_requested:
        return gr.update(
            value="正在安全停止",
            interactive=False,
            variant="secondary",
            visible=True,
        ), False, True
    if not confirmation_pending:
        return gr.update(
            value="确认中止",
            interactive=True,
            variant="stop",
            visible=True,
        ), True, False

    button_update, acknowledged, _message = _request_pipeline_stop()
    return button_update, False, acknowledged


def _refresh_stop_button(stop_requested: bool, confirmation_pending: bool):
    """Render the stop button from persisted state after a frontend reload."""
    state = get_latest_run_control_state()
    if state == "stopping":
        return gr.update(
            value="正在安全停止",
            interactive=False,
            variant="secondary",
            visible=True,
        )
    if state == "aborted":
        return gr.update(
            value="当前模拟已中止",
            interactive=False,
            variant="secondary",
            visible=True,
        )
    if stop_requested:
        return gr.update(
            value="正在安全停止",
            interactive=False,
            variant="secondary",
            visible=True,
        )
    if is_pipeline_running():
        if confirmation_pending:
            return gr.update(value="确认中止", interactive=True, variant="stop", visible=True)
        return gr.update(value="中止流水线", interactive=True, variant="stop", visible=True)
    return gr.update(visible=False)


# ============================================================
# 上传
# ============================================================

def _on_upload(upload_file, chat_state, _pending_plan=None):
    """上传分子结构并更新方案助理对话。"""
    _file, chatbot, new_state = handle_upload(upload_file, chat_state)
    # A changed structure invalidates any plan generated from the prior catalog.
    return _file, chatbot, new_state, None


# ============================================================
# 运行助理（只读；协议调整确认由 frontend_api 受控处理）
# ============================================================

_PENDING_ACTION_APPROVAL_TEXTS = frozenset({
    "确认",
    "同意",
    "同意方案",
    "同意该方案",
    "确认执行",
    "确认重跑",
    "按方案执行",
    "开始重跑",
})
_PASSIVE_RUN_CONTROL_TEXTS = frozenset({
    "中止",
    "中止流水线",
    "停止",
    "停止流水线",
    "暂停",
    "稍后",
    "确认中止",
    "不同意",
    "拒绝",
    "取消",
})
_PENDING_ACTION_REVISION_MARKERS = (
    "改为", "改成", "更换", "替换", "调整为", "延长", "缩短",
    "增加", "减少", "换成", "采用", "使用", "调到", "调至", "设为",
    "设置", "修改", "改变", "降低", "提高", "增大", "减小",
)
_PENDING_ACTION_PARAMETER_MARKERS = (
    "dt", "tau_t", "eq_tau_p", "lincs_iter", "lincs_order", "时间步",
    "恒温", "压强", "压力", "保温段", "退火段", "建盒密度", "初始密度",
)
_PENDING_ACTION_VISIBLE_STATES = frozenset({"pending", "awaiting_confirmation"})


def _normalize_run_control_text(message: str | None) -> str:
    """Normalize only harmless formatting before matching reserved UI commands."""
    return "".join(
        character
        for character in (message or "").strip()
        if not character.isspace() and character not in "，。！？!?、,."
    )


def _is_pending_action_approval(message: str | None) -> bool:
    """Accept only unambiguous approval wording for the visible proposal."""
    normalized = _normalize_run_control_text(message)
    if normalized in _PENDING_ACTION_APPROVAL_TEXTS:
        return True
    if not normalized or any(marker in normalized for marker in ("不同意", "拒绝", "取消", "暂停", "稍后")):
        return False
    has_approval = any(marker in normalized for marker in ("同意", "确认", "批准"))
    has_execution = any(marker in normalized for marker in ("重跑", "执行", "开始", "继续"))
    has_proposal = any(marker in normalized for marker in ("方案", "调整", "建议"))
    return has_approval and (has_execution or has_proposal)


def _run_assistant_reply(history, message: str, reply: str):
    """Append a local deterministic response without involving the LLM."""
    current_history = list(history or [])
    return current_history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": reply},
    ]


def _run_assistant_welcome_history() -> list[dict[str, str]]:
    """Return a fresh conversation when the selected run changes."""
    return [{
        "role": "assistant",
        "content": (
            "你好！我是 **Willy-运行助理**。☀️\n\n"
            "我会自动跟踪当前运行，并说明当前工序、处理对象、完成进度、"
            "错误摘要、自动修复和已登记产物。"
        ),
    }]


def _run_assistant_history_for_current_run(history, bound_run_id=None, current_run_id=None):
    """Discard browser-only dialogue when the rendered run changes."""
    if current_run_id is None:
        current_run_id = frontend_api.latest_run_id()
    current_history = list(history or [])
    if bound_run_id is not None and bound_run_id != current_run_id:
        current_history = _run_assistant_welcome_history()
    return current_history, current_run_id


def _public_pending_action(value: object) -> dict[str, object] | None:
    """Validate the public proposal before it enters a visual info bubble."""
    if not isinstance(value, Mapping):
        return None
    action_state = value.get("status", value.get("state"))
    if action_state not in _PENDING_ACTION_VISIBLE_STATES:
        return None
    if not isinstance(value.get("action_id"), str) or not value["action_id"].strip():
        return None
    return dict(value)


def _awaiting_confirmation_action(action: Mapping[str, object] | None) -> dict[str, object] | None:
    """Return an action only while its owning run waits for confirmation."""
    if action and action.get("status") == "awaiting_confirmation":
        return dict(action)
    return None


def _run_assistant_snapshot() -> dict[str, object]:
    """Read the status and proposal from one backend-selected run identity."""
    try:
        snapshot = frontend_api.get_run_panel_snapshot()
    except Exception:
        snapshot = {}
    run_id = snapshot.get("run_id") if isinstance(snapshot, Mapping) else None
    summary = snapshot.get("summary") if isinstance(snapshot, Mapping) else None
    action = snapshot.get("pending_action") if isinstance(snapshot, Mapping) else None
    return {
        "run_id": run_id if isinstance(run_id, str) else None,
        "summary": summary if isinstance(summary, str) else "### 工程状态\n\n暂时无法读取当前运行。",
        "pending_action": _public_pending_action(action),
    }


def _public_action_text(value: object, *, limit: int = 160) -> str | None:
    """Constrain an already-public payload to one safe UI line."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    return html.escape(text[:limit])


def _pending_action_text(action: Mapping[str, object] | None) -> str | None:
    """Render a fixed proposal bubble without exposing raw logs or config."""
    if not action:
        return None

    summary = _public_action_text(action.get("summary")) or "当前阶段需要确认协议调整。"
    restart_step = action.get("restart_step")
    restart_label = f"第 {restart_step} 步" if isinstance(restart_step, int) else "指定失败步骤"
    step_label = _public_action_text(action.get("step_label"), limit=64)
    if step_label:
        restart_label = f"{restart_label}（{step_label}）"
    lines = [
        "#### 待确认的模拟调整",
        f"问题摘要：{summary}",
        f"重跑位置：{restart_label}",
    ]
    adjustments = action.get("adjustments")
    safe_adjustments: list[str] = []
    if isinstance(adjustments, list):
        for adjustment in adjustments[:8]:
            if not isinstance(adjustment, Mapping):
                continue
            name = _public_action_text(adjustment.get("name"), limit=48)
            before = _public_action_text(adjustment.get("before"), limit=48)
            after = _public_action_text(adjustment.get("after"), limit=48)
            purpose = _public_action_text(
                adjustment.get("purpose", adjustment.get("reason")),
                limit=96,
            )
            if not all((name, before, after)):
                continue
            detail = f"- {name}：{before} -> {after}"
            if purpose:
                detail += f"（{purpose}）"
            safe_adjustments.append(detail)
    if safe_adjustments:
        lines.extend(["核心修改：", *safe_adjustments])
    editable = action.get("editable_parameters")
    safe_editable: list[str] = []
    if isinstance(editable, list):
        for parameter in editable[:8]:
            if not isinstance(parameter, Mapping):
                continue
            name = _public_action_text(parameter.get("name"), limit=48)
            current = _public_action_text(parameter.get("current"), limit=48)
            value_range = _public_action_text(parameter.get("range"), limit=72)
            if not all((name, current, value_range)):
                continue
            safe_editable.append(f"- {name}（当前 {current}，范围 {value_range}）")
    if safe_editable:
        lines.extend(["可复审参数：", *safe_editable])
    lines.append("回复“确认”“同意”或“按方案执行”以按此方案重跑。")
    return "\n\n".join(lines)


def _render_run_assistant_chat(history, snapshot: Mapping[str, object]) -> list[dict[str, str]]:
    """Render welcome, then live cards, without changing LLM conversation state."""
    summary = snapshot.get("summary")
    cards = [{
        "role": "assistant",
        "content": summary if isinstance(summary, str) else "### 工程状态\n\n暂时无法读取当前运行。",
    }]
    action_text = _pending_action_text(snapshot.get("pending_action"))
    if action_text:
        cards.append({"role": "assistant", "content": action_text})
    messages = [dict(message) for message in list(history or [])]
    welcome = _run_assistant_welcome_history()[0]
    if messages and messages[0] == welcome:
        return [messages[0], *cards, *messages[1:]]
    return [*cards, *messages]


def _refresh_run_assistant_view(history, bound_run_id, stop_requested, stop_confirmation_pending):
    """Replace live info bubbles without appending them to dialogue history."""
    snapshot = _run_assistant_snapshot()
    current_history, current_run_id = _run_assistant_history_for_current_run(
        history, bound_run_id, snapshot["run_id"]
    )
    return (
        _render_run_assistant_chat(current_history, snapshot),
        current_history,
        current_run_id,
        _refresh_stop_button(stop_requested, stop_confirmation_pending),
    )


def _confirm_pending_action(action: Mapping[str, object]) -> str:
    """Delegate one approved public action to the backend validation boundary."""
    action_id = action.get("action_id")
    run_id = action.get("run_id")
    confirmer = getattr(frontend_api, "confirm_pending_action", None)
    if (
        not isinstance(action_id, str)
        or not action_id
        or not isinstance(run_id, str)
        or not callable(confirmer)
    ):
        return "当前待确认方案暂不可执行，请稍后刷新状态。"
    confirmation_kwargs = {}
    revision = action.get("state_revision")
    fingerprint = action.get("config_fingerprint")
    if (
        isinstance(revision, int)
        and not isinstance(revision, bool)
        and revision >= 0
        and isinstance(fingerprint, str)
        and fingerprint
    ):
        confirmation_kwargs = {
            "state_revision": revision,
            "config_fingerprint": fingerprint,
        }
    try:
        result = confirmer(action_id, run_id, **confirmation_kwargs)
    except Exception:
        return "确认请求未送达，请稍后重试。"
    return _public_action_text(result, limit=240) or "确认请求未送达，请稍后重试。"


def _requests_pending_action_revision(message: str | None) -> bool:
    """Recognize explicit alternative-plan wording without routing questions."""
    normalized = _normalize_run_control_text(message).lower()
    if not normalized:
        return False
    if any(marker in normalized for marker in ("为什么", "为何", "是否", "能否", "可以吗")):
        direct_change = (
            "改为", "改成", "调到", "调至", "设为", "设置为", "换成", "替换为", "=",
        )
        if not any(marker in normalized for marker in direct_change):
            return False
    if any(marker in normalized for marker in _PENDING_ACTION_REVISION_MARKERS):
        return True
    return (
        any(marker in normalized for marker in _PENDING_ACTION_PARAMETER_MARKERS)
        and any(marker in normalized for marker in ("改", "调", "设", "取", "用", "换", "变", "="))
    )


def _revise_pending_action(action: Mapping[str, object], user_request: str) -> str:
    """Delegate a bounded replacement proposal to the backend validation gate."""
    action_id = action.get("action_id")
    run_id = action.get("run_id")
    reviser = getattr(frontend_api, "revise_pending_action", None)
    if (
        not isinstance(action_id, str)
        or not action_id
        or not isinstance(run_id, str)
        or not callable(reviser)
    ):
        return "当前待确认方案暂不可更新，请稍后刷新状态。"
    revision_kwargs = {}
    revision = action.get("state_revision")
    fingerprint = action.get("config_fingerprint")
    if (
        isinstance(revision, int)
        and not isinstance(revision, bool)
        and revision >= 0
        and isinstance(fingerprint, str)
        and fingerprint
    ):
        revision_kwargs = {
            "state_revision": revision,
            "config_fingerprint": fingerprint,
        }
    try:
        result = reviser(action_id, user_request, run_id, **revision_kwargs)
    except Exception:
        return "新的调整方案未生成，原方案仍保持等待确认。"
    return _public_action_text(result, limit=240) or "新的调整方案未生成，原方案仍保持等待确认。"


def _ask_run_assistant(message, history, bound_run_id=None):
    snapshot = _run_assistant_snapshot()
    current_history, current_run_id = _run_assistant_history_for_current_run(
        history, bound_run_id, snapshot["run_id"]
    )
    if not message or not message.strip():
        yield (
            "",
            _render_run_assistant_chat(current_history, snapshot),
            current_history,
            gr.update(interactive=True),
            current_run_id,
        )
        return

    user_entry = {"role": "user", "content": message}
    pending_history = current_history + [
        user_entry,
        {"role": "assistant", "content": run_assistant_pending_message(message)},
    ]
    yield (
        gr.update(value="", interactive=False),
        _render_run_assistant_chat(pending_history, snapshot),
        pending_history,
        gr.update(interactive=False),
        current_run_id,
    )

    try:
        reply = chat_run_assistant(current_run_id, message, current_history)
    except Exception:
        reply = "运行助理暂时不可用，请稍后重试。"
    final_snapshot = _run_assistant_snapshot()
    final_history, final_run_id = _run_assistant_history_for_current_run(
        current_history, current_run_id, final_snapshot["run_id"]
    )
    if final_run_id == current_run_id:
        final_history = current_history + [user_entry, {"role": "assistant", "content": reply}]
    yield (
        gr.update(value="", interactive=True),
        _render_run_assistant_chat(final_history, final_snapshot),
        final_history,
        gr.update(interactive=True),
        final_run_id,
    )


def _handle_run_assistant_message(
    message,
    history,
    stop_confirmation_pending: bool,
    stop_requested: bool,
    bound_run_id=None,
):
    """Handle public action approvals before the read-only Run Assistant dialogue."""
    snapshot = _run_assistant_snapshot()
    current_history, current_run_id = _run_assistant_history_for_current_run(
        history, bound_run_id, snapshot["run_id"]
    )
    normalized = _normalize_run_control_text(message)
    action = _awaiting_confirmation_action(snapshot["pending_action"])
    if action and _is_pending_action_approval(message):
        public_reply = _confirm_pending_action(action)
        updated = _run_assistant_reply(current_history, message, public_reply)
        final_snapshot = _run_assistant_snapshot()
        final_history, final_run_id = _run_assistant_history_for_current_run(
            updated, current_run_id, final_snapshot["run_id"]
        )
        yield (
            gr.update(value="", interactive=True),
            _render_run_assistant_chat(final_history, final_snapshot),
            final_history,
            gr.update(interactive=True),
            _refresh_stop_button(stop_requested, stop_confirmation_pending),
            stop_confirmation_pending,
            stop_requested,
            final_run_id,
        )
        return

    if action and _requests_pending_action_revision(message):
        user_entry = {"role": "user", "content": message}
        pending_history = current_history + [
            user_entry,
            {"role": "assistant", "content": "正在根据你的要求更新待确认方案..."},
        ]
        yield (
            gr.update(value="", interactive=False),
            _render_run_assistant_chat(pending_history, snapshot),
            pending_history,
            gr.update(interactive=False),
            _refresh_stop_button(stop_requested, stop_confirmation_pending),
            stop_confirmation_pending,
            stop_requested,
            current_run_id,
        )
        public_reply = _revise_pending_action(action, message)
        final_snapshot = _run_assistant_snapshot()
        final_history, final_run_id = _run_assistant_history_for_current_run(
            current_history, current_run_id, final_snapshot["run_id"]
        )
        if final_run_id == current_run_id:
            final_history = current_history + [user_entry, {"role": "assistant", "content": public_reply}]
        yield (
            gr.update(value="", interactive=True),
            _render_run_assistant_chat(final_history, final_snapshot),
            final_history,
            gr.update(interactive=True),
            _refresh_stop_button(stop_requested, stop_confirmation_pending),
            stop_confirmation_pending,
            stop_requested,
            final_run_id,
        )
        return

    if normalized in _PASSIVE_RUN_CONTROL_TEXTS:
        if action:
            public_reply = "当前工程保持等待确认，未执行重跑或中止。"
        elif normalized in {"中止", "中止流水线", "停止", "停止流水线", "确认中止"}:
            public_reply = "文本消息不会中止工程；请使用“中止流水线”按钮并再次点击确认。"
        else:
            public_reply = "当前没有待确认的协议调整，工程状态未改变。"
        updated = _run_assistant_reply(current_history, message, public_reply)
        yield (
            gr.update(value="", interactive=True),
            _render_run_assistant_chat(updated, snapshot),
            updated,
            gr.update(interactive=True),
            _refresh_stop_button(stop_requested, stop_confirmation_pending),
            stop_confirmation_pending,
            stop_requested,
            current_run_id,
        )
        return

    for textbox, chatbot, state, send_button, current_run_id in _ask_run_assistant(
        message, history, bound_run_id
    ):
        yield (
            textbox,
            chatbot,
            state,
            send_button,
            _refresh_stop_button(stop_requested, stop_confirmation_pending),
            stop_confirmation_pending,
            stop_requested,
            current_run_id,
        )


def _save_llm_config(api_key, base_url, model):
    """Save local LLM settings and clear the credential from the browser form."""
    return "", save_llm_config(api_key, base_url, model)


def _format_llm_connection_result(result):
    if result["ok"]:
        return f"**{result['message']}。**\n\n{result['suggestion']}"
    return f"**连接测试失败：{result['message']}。**\n\n建议：{result['suggestion']}"


def _test_llm_connection(api_key, base_url, model):
    """Render a visible loading state while the one-off test request runs."""
    yield (
        gr.update(value="正在测试 LLM 连接..."),
        gr.update(interactive=False),
        gr.update(interactive=False),
    )
    result = test_llm_connection(api_key, base_url, model)
    yield (
        gr.update(value=_format_llm_connection_result(result)),
        gr.update(interactive=True),
        gr.update(interactive=True),
    )


# ============================================================
# UI
# ============================================================

with gr.Blocks(title="Willy Agent") as app:
    with gr.Row(elem_id="app-header"):
        gr.HTML("<h1>Willy AI Driven MD Agent</h1>", elem_id="app-title")
        dark_mode = gr.Checkbox(label="夜间模式", value=False, elem_id="theme-toggle", container=False)
        dark_mode.change(js=THEME_TOGGLE_JS, inputs=[dark_mode], outputs=[])

    with gr.Tabs():
        with gr.Tab("任务"):
            with gr.Row(elem_id="assistant-row", equal_height=True):
                with gr.Column(scale=1):
                    with gr.Column(elem_id="proposal-assistant"):
                        with gr.Row(elem_id="proposal-header"):
                            gr.HTML("<div class='panel-title'>方案助理</div>")
                        welcome_msg = [{"role": "assistant",
                            "content": "你好！我是 **Willy-方案助理**，你的 MD 模拟助手。☀️\n\n"
                                       "只需用自然语言描述你的体系，我会给出方案，"
                                       "自动完成体系准备、EM、NPT退火和生产模拟，并展示运行进度。"
                                       "需要全精度 TRR 轨迹时，请在对话中明确提出。您也可以上传自己的结构后再启动。\n\n"
                                       "（仅分子个数、分子名为必要，其他可选填）"}]
                        chat_state = gr.State(welcome_msg)
                        proposal_plan_state = gr.State(None)
                        chatbot = gr.Chatbot(height=480, value=welcome_msg, label="", elem_id="proposal-chat")
                        with gr.Row(elem_id="proposal-input-row"):
                            msg = gr.Textbox(placeholder=PROPOSAL_EXAMPLE, lines=3, label="", scale=4)
                            with gr.Column(scale=1, min_width=80):
                                send_btn = gr.Button("发送", variant="primary")
                                upload = gr.UploadButton("上传结构", file_types=[".gjf", ".mol2", ".pdb", ".xyz"])
                        upload.upload(
                            fn=_on_upload,
                            inputs=[upload, chat_state, proposal_plan_state],
                            outputs=[upload, chatbot, chat_state, proposal_plan_state])

                        msg.submit(fn=_chat_wrapper, inputs=[msg, chat_state, proposal_plan_state],
                                   outputs=[msg, chatbot, chat_state, proposal_plan_state, send_btn, upload],
                                   trigger_mode="once", concurrency_limit=1,
                                   concurrency_id="proposal-assistant-chat")
                        send_btn.click(fn=_chat_wrapper, inputs=[msg, chat_state, proposal_plan_state],
                                       outputs=[msg, chatbot, chat_state, proposal_plan_state, send_btn, upload],
                                       trigger_mode="once", concurrency_limit=1,
                                       concurrency_id="proposal-assistant-chat")

                with gr.Column(scale=1):
                    with gr.Column(elem_id="run-assistant"):
                        gr.HTML("<div class='panel-title'>运行助理</div>")
                        run_welcome_msg = _run_assistant_welcome_history()
                        run_chat_state = gr.State(run_welcome_msg)
                        run_assistant_run_id = gr.State(None)
                        initial_run_snapshot = _run_assistant_snapshot()
                        run_chatbot = gr.Chatbot(
                            height=480,
                            value=_render_run_assistant_chat(run_welcome_msg, initial_run_snapshot),
                            label="",
                            elem_id="run-chat",
                        )
                        with gr.Row(elem_id="run-input-row"):
                            run_message = gr.Textbox(
                                placeholder="询问当前运行的状态、工序进度或错误", label="", lines=3, scale=4)
                            with gr.Column(scale=1, min_width=80):
                                run_send_btn = gr.Button("发送", variant="primary")
                                stop_btn = gr.Button(
                                    "中止流水线",
                                    variant="stop",
                                    visible=False,
                                    elem_id="stop-pipeline-button",
                                )
                        stop_requested = gr.State(False)
                        stop_confirmation_pending = gr.State(False)

                        run_message.submit(
                            fn=_handle_run_assistant_message,
                            inputs=[
                                run_message,
                                run_chat_state,
                                stop_confirmation_pending,
                                stop_requested,
                                run_assistant_run_id,
                            ],
                            outputs=[
                                run_message,
                                run_chatbot,
                                run_chat_state,
                                run_send_btn,
                                stop_btn,
                                stop_confirmation_pending,
                                stop_requested,
                                run_assistant_run_id,
                            ],
                            trigger_mode="once", concurrency_limit=1,
                            concurrency_id="run-assistant-chat",
                        )
                        run_send_btn.click(
                            fn=_handle_run_assistant_message,
                            inputs=[
                                run_message,
                                run_chat_state,
                                stop_confirmation_pending,
                                stop_requested,
                                run_assistant_run_id,
                            ],
                            outputs=[
                                run_message,
                                run_chatbot,
                                run_chat_state,
                                run_send_btn,
                                stop_btn,
                                stop_confirmation_pending,
                                stop_requested,
                                run_assistant_run_id,
                            ],
                            trigger_mode="once", concurrency_limit=1,
                            concurrency_id="run-assistant-chat",
                        )
                        stop_btn.click(
                            fn=_handle_stop_button_click,
                            inputs=[stop_confirmation_pending, stop_requested],
                            outputs=[stop_btn, stop_confirmation_pending, stop_requested],
                            trigger_mode="once",
                            concurrency_limit=1,
                            concurrency_id="pipeline-stop",
                        )
                        gr.Timer(3).tick(
                            fn=_refresh_run_assistant_view,
                            inputs=[
                                run_chat_state,
                                run_assistant_run_id,
                                stop_requested,
                                stop_confirmation_pending,
                            ],
                            outputs=[
                                run_chatbot,
                                run_chat_state,
                                run_assistant_run_id,
                                stop_btn,
                            ],
                        )
            with gr.Row(elem_id="workspace-row", equal_height=True):
                with gr.Column(scale=1):
                    with gr.Column(elem_id="visualization-panel"):
                        gr.HTML("<div class='panel-title'>可视化</div>")
                        initial_structure_choices = frontend_api.get_run_visualization_choices()
                        initial_structure_choice = (
                            initial_structure_choices[0] if initial_structure_choices else None
                        )
                        structure_selector = gr.Dropdown(
                            choices=initial_structure_choices,
                            value=initial_structure_choice,
                            label="结构",
                            info="打开下拉菜单时刷新当前运行目录",
                            allow_custom_value=False,
                            interactive=True,
                            elem_id="structure-selector",
                        )
                        structure_viewer = gr.HTML(
                            frontend_api.render_run_visualization_html(initial_structure_choice),
                            elem_id="structure-viewer",
                        )
                        structure_selector.focus(
                            fn=_refresh_current_run_structure_choices,
                            inputs=[structure_selector],
                            outputs=[structure_selector, structure_viewer],
                            trigger_mode="always_last",
                            concurrency_limit=1,
                            concurrency_id="structure-directory-refresh",
                            show_progress="hidden",
                        )
                        structure_selector.change(
                            fn=_render_current_run_structure,
                            inputs=[structure_selector],
                            outputs=[structure_viewer],
                            trigger_mode="always_last",
                            concurrency_limit=1,
                            concurrency_id="structure-directory-refresh",
                            show_progress="hidden",
                        )

                with gr.Column(scale=1):
                    with gr.Column(elem_id="chart-panel"):
                        gr.HTML("<div class='panel-title'>图表绘制</div>")
                        gr.HTML("<div class='development-placeholder'>开发中...</div>")

        with gr.Tab("配置"):
            llm_base_url, llm_model = llm_form_defaults()
            gr.Markdown(get_llm_config_notice(), elem_id="llm-configuration-notice")
            api_key_status = gr.Markdown("", elem_id="llm-configuration-status")
            api_key_input = gr.Textbox(
                label="OpenAI-compatible API Key",
                placeholder="sk-***你的API key***",
                type="password",
                info="支持 OpenAI、DeepSeek、阿里云百炼、智谱 AI、月之暗面（Kimi）等厂商。",
            )
            llm_base_url_input = gr.Textbox(
                label="Base URL",
                value=llm_base_url,
                placeholder="https://api.deepseek.com 或 http://localhost:8000/v1",
                info="按服务提供商的URL文档填写。部分兼容/中转站服务不能使用网址直填，需在URL结尾添加 /v1。",
            )
            llm_model_input = gr.Textbox(
                label="Model",
                value=llm_model,
                info="只能填写服务提供商支持的模型，请注意横线、下划线或大小写格式。",
            )
            with gr.Row():
                api_key_test = gr.Button(
                    "测试连接",
                    variant="secondary",
                    elem_id="test-llm-connection-button",
                )
                api_key_save = gr.Button("保存", variant="primary")
            api_key_test.click(
                fn=_test_llm_connection,
                inputs=[api_key_input, llm_base_url_input, llm_model_input],
                outputs=[api_key_status, api_key_test, api_key_save],
                trigger_mode="once",
                concurrency_limit=1,
                concurrency_id="llm-connection-test",
                show_progress="hidden",
            )
            api_key_save.click(
                fn=_save_llm_config,
                inputs=[api_key_input, llm_base_url_input, llm_model_input],
                outputs=[api_key_input, api_key_status],
            )

        with gr.Tab("关于"):
            with gr.Column(elem_id="about-page"):
                gr.HTML(
                    """
                    <section class="about-intro">
                        <h2>Willy AI Driven MD Agent</h2>
                        <p>Willy 是基于Gromacs软件的MD模拟自动化Agent组。目前它有两名员工：Willy-方案助理 和 Willy-运行助理。</p>
                    </section>
                    <div class="about-grid">
                        <section class="about-section">
                            <h2>参与者</h2>
                            <ul>
                                <li>项目整体统筹（唯一真人）：小w </li>
                                <li>架构、交付与质量：ChatGPT 5.6-terra </li>
                                <li>前端、文档、测试、Tools等领域工程：ChatGPT 5.6-terra， DeepSeek V4 Pro </li>
                            </ul>
                        </section>
                        <section class="about-section">
                            <h2>Willy 能做什么？</h2>
                            <ul>
                                <li>Willy-方案助理会根据您的自然语言描述生成配置，执行从分子optimization到 GROMACS 模拟的全链路并谨慎地处理错误和重试。</li>
                                <li>Willy-运行助理负责监控和记录整个模拟流程。</li>
                                <li>您还可以通过可视化界面看到运行中产出的的分子结构与MD盒子结果。</li>
                            </ul>
                        </section>
                        <section class="about-section">
                            <h2>后续规划</h2>
                            <ul>
                                <li>扩展更多的工具链集成。</li>
                                <li>扩展确认式恢复、重试和分支运行控制。</li>
                                <li>提供历史运行对比，以及受控的后处理和图表能力。</li>
                            </ul>
                        </section>
                    </div>
                    """
                )
                with gr.Column(elem_id="about-qr-section"):
                    gr.HTML("<p>公众号：小w的学习笔记</p>")
                    gr.Image(
                        value=OFFICIAL_ACCOUNT_QR,
                        show_label=False,
                        container=False,
                        interactive=False,
                        elem_id="official-account-code",
                    )

app.queue()

if __name__ == "__main__":
    app.launch(server_name="127.0.0.1", server_port=int(dotenv_value("WILLY_SERVER_PORT") or "7860"), share=False,
               inbrowser=True, css=APP_CSS)
