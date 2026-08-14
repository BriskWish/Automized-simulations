"""app.py — Willy Agent Gradio UI (纯前端, 后端操作全部委托给 frontend_api)"""

import html
import os
import re
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
    run_local_dependency_preflight,
    save_llm_config,
    test_llm_connection,
    get_latest_run_control_state,
    run_assistant_pending_message,
    run_assistant_control_command,
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
    --workbench-panel-height: 63.4rem;
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

.gradio-container [role="tab"] {
    font-size: 1.1rem;
    font-weight: 700;
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

.gradio-container.willy-dark #proposal-assistant code,
.gradio-container.willy-dark #run-assistant code {
    background: #36413c !important;
    border: 1px solid #53645b;
    border-radius: 4px;
    color: #f0f5f1 !important;
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    padding: 0.08rem 0.3rem;
}

#visualization-panel,
#proposal-assistant,
#run-assistant,
#chart-panel {
    border: 1px solid;
    border-radius: 6px;
    box-sizing: border-box;
    height: var(--workbench-panel-height);
    min-height: var(--workbench-panel-height);
    padding: 1rem;
}

#proposal-assistant { overflow: hidden; }

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

#structure-selector-row {
    align-items: flex-end;
    flex: 0 0 auto;
    flex-wrap: nowrap !important;
    gap: 0.75rem;
    margin-bottom: 0.5rem;
}

#structure-selector-row > .gr-column {
    min-width: 0;
}

#structure-selector-row > * {
    min-width: 0 !important;
}

#structure-viewer {
    flex: 0 0 auto;
    height: min(43.2rem, calc(var(--workbench-panel-height) - 12rem));
    min-height: 0;
    overflow: hidden;
}

#structure-viewer .structure-viewer-frame {
    display: grid;
    grid-template-rows: minmax(0, 1fr) auto;
    height: 100%;
    min-height: 0;
}

#structure-viewer .structure-viewer-frame iframe {
    display: block;
    height: 100% !important;
    min-height: 0;
    width: 100% !important;
}

#structure-viewer .structure-viewer-name {
    color: #765f4f;
    font-family: system-ui, sans-serif;
    font-size: 12px;
    line-height: 1.35;
    overflow-wrap: anywhere;
    padding: 0.45rem 0 0.2rem;
    text-align: center;
}

#structure-legend {
    flex: 0 0 auto;
    margin: 0 0 0.45rem;
    text-align: center;
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

#proposal-header,
#run-header {
    align-items: center;
    flex: 0 0 2.25rem;
    gap: 0.5rem;
    margin-bottom: 0.65rem;
    min-height: 2.25rem;
}

#proposal-header .panel-title,
#run-header .panel-title {
    margin: 0;
}

#proposal-chat,
#run-chat {
    flex: 0 0 auto;
    height: 650px !important;
    margin-top: 0;
}

#proposal-input-row,
#run-input-row {
    margin-bottom: 0;
    margin-top: auto;
}

#run-slash-menu {
    background: var(--input-bg);
    border: 1px solid var(--input-border);
    border-radius: 6px;
    box-shadow: 0 0.5rem 1.25rem rgba(27, 40, 33, 0.22);
    box-sizing: border-box;
    max-width: calc(100vw - 1.5rem);
    padding: 0.25rem;
    position: fixed;
    z-index: 10000;
}

#run-slash-menu button {
    align-items: center;
    background: transparent;
    border: 0;
    border-radius: 4px;
    color: var(--text);
    cursor: pointer;
    display: flex;
    gap: 0.65rem;
    justify-content: flex-start;
    min-height: 2.5rem;
    padding: 0.45rem 0.6rem;
    text-align: left;
    width: 100%;
}

#run-slash-menu button:hover,
#run-slash-menu button[aria-selected="true"] {
    background: var(--color-accent-soft);
}

.run-slash-menu-command {
    color: var(--run-title);
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-weight: 700;
    min-width: 5.2rem;
}

.run-slash-menu-description {
    color: var(--muted-text);
    font-size: 0.9rem;
    line-height: 1.25;
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

#beginner-guide-page {
    margin: 0 auto;
    max-width: 70rem;
    padding: 0.5rem 0 1.5rem;
}

#beginner-guide-page .guide-section {
    border-top: 1px solid var(--input-border);
    padding: 1.15rem 0;
}

#beginner-guide-page .guide-section:first-child {
    border-top: 0;
    padding-top: 0;
}

#beginner-guide-page h2 {
    color: var(--text);
    font-size: 1.25rem;
    margin: 0 0 0.65rem;
}

#beginner-guide-page h3 {
    color: var(--text);
    font-size: 1rem;
    margin: 1rem 0 0.35rem;
}

#beginner-guide-page p {
    color: var(--muted-text);
    line-height: 1.75;
    margin: 0.55rem 0;
}

#beginner-guide-page code {
    color: var(--text);
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

#third-party-notices {
    border-top: 1px dashed var(--input-border);
    margin-top: 2rem;
    padding-top: 1.25rem;
}

#third-party-notices h2 {
    color: var(--text);
    font-size: 1.1rem;
    margin: 0 0 0.65rem;
}

#third-party-notices p,
#third-party-notices li {
    color: var(--muted-text);
    line-height: 1.7;
}

#third-party-notices p {
    margin: 0.8rem 0;
}

#third-party-notices ol {
    margin: 0.55rem 0 1rem;
    padding-left: 1.35rem;
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
    :root { --workbench-panel-height: 57.6rem; }

    #assistant-row,
    #workspace-row {
        flex-wrap: wrap;
    }

    #structure-selector-row {
        flex-wrap: wrap !important;
    }

    #structure-selector-row > * {
        flex-basis: 100% !important;
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

RUN_ASSISTANT_SLASH_MENU_JS = r"""
() => {
    const options = [
        {command: "/resume", description: "按原参数从安全步骤续跑"},
        {command: "/fork", description: "修改参数并创建独立子运行"},
    ];
    let activeIndex = 0;
    let activeOptions = [];
    let activeInput = null;

    if (!document.body) return;
    if (window.__willyRunAssistantSlashMenuInstalled) return [];
    window.__willyRunAssistantSlashMenuInstalled = true;
    let menu = document.getElementById("run-slash-menu");
    if (!menu) {
        menu = document.createElement("div");
        menu.id = "run-slash-menu";
        menu.setAttribute("role", "listbox");
        menu.hidden = true;
        document.body.appendChild(menu);
    }

    const inputFromEvent = (event) => {
        const path = event.composedPath();
        const input = path.find((node) =>
            node instanceof HTMLTextAreaElement || node instanceof HTMLInputElement
        );
        // Gradio renders the textarea inside a shadow root. ``closest`` on the
        // native input cannot see the outer ``#run-message`` host, whereas the
        // composed event path contains both nodes.
        return input && path.some((node) => node?.id === "run-message") ? input : null;
    };

    const hideMenu = () => {
        menu.hidden = true;
        activeOptions = [];
    };

    const setInputValue = (input, value) => {
        const prototype = input instanceof HTMLTextAreaElement
            ? HTMLTextAreaElement.prototype
            : HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
        if (setter) {
            setter.call(input, value);
        } else {
            input.value = value;
        }
        input.dispatchEvent(new Event("input", {bubbles: true, composed: true}));
        input.focus();
    };

    const positionMenu = () => {
        if (!activeInput || menu.hidden) return;
        const rect = activeInput.getBoundingClientRect();
        const width = Math.min(Math.max(rect.width, 260), window.innerWidth - 24);
        menu.style.width = `${width}px`;
        menu.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))}px`;
        menu.style.top = `${Math.max(8, rect.top - menu.offsetHeight - 8)}px`;
    };

    const chooseActive = () => {
        const choice = activeOptions[activeIndex];
        if (!choice || !activeInput) return;
        setInputValue(activeInput, choice.command === "/fork" ? "/fork " : choice.command);
        hideMenu();
    };

    const renderMenu = (input) => {
        const query = input.value.trimStart().toLowerCase();
        if (!query.startsWith("/")) {
            hideMenu();
            return;
        }
        activeOptions = options.filter((option) => option.command.startsWith(query));
        if (!activeOptions.length) {
            hideMenu();
            return;
        }
        activeInput = input;
        activeIndex = Math.min(activeIndex, activeOptions.length - 1);
        menu.innerHTML = activeOptions.map((option, index) => `
            <button type="button" role="option" data-command="${option.command}"
                aria-selected="${index === activeIndex}">
                <span class="run-slash-menu-command">${option.command}</span>
                <span class="run-slash-menu-description">${option.description}</span>
            </button>
        `).join("");
        menu.hidden = false;
        positionMenu();
    };

    document.addEventListener("input", (event) => {
        const input = inputFromEvent(event);
        if (input) {
            activeIndex = 0;
            renderMenu(input);
        }
    }, true);

    document.addEventListener("focusout", (event) => {
        if (inputFromEvent(event) !== activeInput) return;
        window.setTimeout(() => {
            if (!menu.matches(":hover")) hideMenu();
        }, 120);
    }, true);

    document.addEventListener("keydown", (event) => {
        if (menu.hidden || inputFromEvent(event) !== activeInput) return;
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            event.stopImmediatePropagation();
            const delta = event.key === "ArrowDown" ? 1 : -1;
            activeIndex = (activeIndex + delta + activeOptions.length) % activeOptions.length;
            renderMenu(activeInput);
        } else if (event.key === "Enter") {
            event.preventDefault();
            event.stopImmediatePropagation();
            chooseActive();
        } else if (event.key === "Escape") {
            event.preventDefault();
            event.stopImmediatePropagation();
            hideMenu();
        }
    }, true);

    menu.addEventListener("pointerdown", (event) => {
        const button = event.target.closest("button[data-command]");
        if (!button || !activeInput) return;
        event.preventDefault();
        const command = button.dataset.command;
        const optionIndex = activeOptions.findIndex((option) => option.command === command);
        if (optionIndex >= 0) {
            activeIndex = optionIndex;
            chooseActive();
        }
    });

    window.addEventListener("resize", positionMenu);
    window.addEventListener("scroll", positionMenu, true);
}
"""

PROPOSAL_EXAMPLE = (
    "示例：请帮我跑一个 MD：100 Li+、100 PF6-、400 EC、600 EMC，"
    "生产 20 ns；优化 Gaussian16/Gaussian09，力场 OPLSAA"
)

# Keep both assistants aligned while leaving room for their three-line input rows.
ASSISTANT_CHAT_HEIGHT = 650

OFFICIAL_ACCOUNT_QR = "assets/qrcode_for_gh_7df1329939c6_258.jpg"

BEGINNER_GUIDE_HTML = """
<section class="guide-section">
    <h2>零、Agent 的概况</h2>
    <p>Willy 基于Linux/WSL2系统,由方案助理和运行助理协作完成分子动力学工作流。方案助理负责把自然语言需求整理为可确认的体系与协议；运行助理负责展示当前工程状态、阶段产物和需要用户确认的调整方案。</p>
    <p>二者功能、聊天记录不互通。</p>
    <p>位置：在“任务”页上方左右两侧使用两名助理。只有确认最新方案后，流水线才会启动。</p>
    <p>Agent严格执行 结构优化-电荷设置-拓扑生成-模拟参数生成-进行模拟 的操作链路，仅在参数初始配置或报错时发生LLM介入。</p>
    <p>MD模拟过程中遵循以下步骤： 1.能量最小化（Energy Minimization, em）；2.梯度退火平衡(Gradient Annealing Equilibrium， EQ)；3.生产阶段（Production, prod)</p>
</section>
<section class="guide-section">
    <h2>一、运行 Agent 最少需要的外置依赖</h2>
    <p>实现完整工作流，至少需要本机具备：1.一个用于量化计算的软件（Gaussian16、Gaussian09 或 ORCA）；2. <strong>GROMACS 2022.0 或更高版本</strong>。如果您没有这些软件，需要自行安装，Willy只检验软件可用性。实际使用哪个后端/力场，由用户确认方案决定。</p>
    <p><strong>推荐 ORCA：</strong>建议使用 ORCA 6.x 的 Linux x86_64 官方发行包。在 <a href="https://orcaforum.kofo.mpg.de/" target="_blank" rel="noopener noreferrer">ORCA Forum</a> 注册并接受许可后下载，解压至本机目录；将该安装目录填入 <code>WILLY_ORCA_HOME</code>，或将 <code>orca</code> 放入 PATH。请勿使用来源不明的重打包二进制。</p>
    <p><strong>LigParGen（仅 OPLS-AA 路径）：</strong>除 LigParGen 外，还需要带格式插件和数据文件的完整 Open Babel 3、C shell（<code>csh</code>）以及 BOSS 运行环境。内置的精简 Open Babel 运行时不能替代完整安装。对应位置为本机 <code>.env</code>：<code>WILLY_LIGPARGEN_BIN</code>、<code>WILLY_OBABEL_BIN</code>、<code>WILLY_CSH_BIN</code>、<code>WILLY_BOSS_HOME</code>；未选择 OPLS-AA 时无需配置这一组依赖。</p>
    <p>位置：LLM 服务在“配置”页填写并测试；本机软件的可用情况会在工程启动后记录，并由运行助理报告。</p>
</section>
<section class="guide-section">
    <h2>二、体系的初始确定</h2>
    <p>在“任务”页的方案助理输入体系组分、数量、目标温度、模拟时长和偏好的量子或力场后端。需要使用自有结构时，通过同页的“上传结构”加入结构，再让方案助理生成方案。</p>
    <p>位置：方案摘要会出现在方案助理对话中；请先核对分子、电荷、自旋、组分数量和模拟目标，再确认运行。</p>
</section>
<section class="guide-section">
    <h2>三、可供修改的参数</h2>
    <h3>3.1 量子层</h3>
    <p>可调整量化计算后端的分子电荷与自旋、结构优化与单点计算要求。位置：在“任务”页向方案助理提出修改，并在新的方案摘要中核对。</p>
    <h3>3.2 拓扑层</h3>
    <p>可调整力场选择和组分对应关系。位置：在方案确认前通过方案助理修改；运行中出现拓扑错误时，运行助理会报告处理状态。</p>
    <h3>3.3 模拟层</h3>
    <p>可调整初始密度、盒子尺寸、EM/EQ/PROD 协议、温度、压力、耦合方式、时间步长、生产时长和输出精度。位置：在方案确认前提出；EQ 验收失败后的协议改动必须在运行助理中再次确认。</p>
</section>
<section class="guide-section">
    <h2>四、LLM 的报错处理</h2>
    <p>若方案助理无法响应或无法调用工具，先在“配置”页核对 API Key、Base URL 和 Model，再使用“测试连接”。测试不会保存配置，也不会自动补全 Base URL 的 <code>/v1</code>。</p>
    <p>若问题发生在科学计算阶段，请查看运行助理的新状态气泡和待确认方案；不要把聊天中的“中止”当作控制命令，停止操作只通过“中止流水线”按钮完成。</p>
</section>
<section class="guide-section">
    <h2>五、获取结构</h2>
    <p>在“任务”页左下的“可视化”区域，先在左栏选择运行目录，再在右栏选择该运行目录下的 PDB 或 MOL2 文件。已验收的 EM、EQ、PROD 阶段可生成用于查看的结构产物。</p>
    <p>位置：可视化右侧的图表绘制区域仍在开发中；结构文件名称保留完整名称或运行内相对路径，便于区分历史工程。</p>
</section>
<section class="guide-section">
    <h2>六、其他</h2>
    <p>RDF、RMSD 等可视化图表仍为后续功能开发，欢迎各位用户提出宝贵意见和建议！</p>
</section>
"""


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


def _refresh_visualization_run_choices(
    selected_run_id: str | None,
    selected_file: str | None,
):
    """Refresh both selectors after the run directory list changes."""
    run_choices = frontend_api.get_run_visualization_run_choices()
    run_id = selected_run_id if selected_run_id in run_choices else (
        run_choices[0] if run_choices else None
    )
    file_choices = frontend_api.get_run_visualization_file_choices(run_id)
    file_choice = selected_file if selected_file in file_choices else (
        file_choices[0] if file_choices else None
    )
    return (
        gr.update(choices=run_choices, value=run_id, interactive=True),
        gr.update(choices=file_choices, value=file_choice, interactive=True),
        frontend_api.render_run_visualization_html(file_choice, run_id=run_id),
        frontend_api.render_run_visualization_legend_html(file_choice, run_id=run_id),
    )


def _refresh_visualization_file_choices(
    selected_run_id: str | None,
    selected_file: str | None,
):
    """Refresh files for the selected run without changing the run choice."""
    file_choices = frontend_api.get_run_visualization_file_choices(selected_run_id)
    file_choice = selected_file if selected_file in file_choices else (
        file_choices[0] if file_choices else None
    )
    return (
        gr.update(choices=file_choices, value=file_choice, interactive=True),
        frontend_api.render_run_visualization_html(file_choice, run_id=selected_run_id),
        frontend_api.render_run_visualization_legend_html(
            file_choice, run_id=selected_run_id,
        ),
    )


def _render_selected_visualization(
    selected_run_id: str | None,
    selected_file: str | None,
):
    """Render only the file authorized by the selected run directory."""
    return (
        frontend_api.render_run_visualization_html(
            selected_file,
            run_id=selected_run_id,
        ),
        frontend_api.render_run_visualization_legend_html(
            selected_file,
            run_id=selected_run_id,
        ),
    )


def _render_proposal_chat(history):
    """Return the text-only proposal conversation for Gradio rendering."""
    return [dict(message) for message in list(history or [])]


def _chat_wrapper(message, history, pending_plan=None, execution_context=None):
    """Adapt the proposal generator and bind the current execution intent."""
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
        message,
        current_history,
        pending_plan,
        execution_context=execution_context,
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


def _pending_option_id(message: str | None, action: Mapping[str, object] | None) -> str | None:
    """Parse an explicit Chinese/Arabic option number for the current action."""
    if not action or not isinstance(action.get("options"), list) or len(action["options"]) < 2:
        return None
    normalized = _normalize_run_control_text(message).lower()
    match = re.search(r"(?:方案|选|选择)([一二三123])", normalized)
    if not match:
        return None
    ordinal = {"一": "1", "二": "2", "三": "3"}.get(match.group(1), match.group(1))
    option_id = f"option_{ordinal}"
    return option_id if any(
        isinstance(option, Mapping) and option.get("option_id") == option_id
        for option in action["options"]
    ) else None


def _is_option_confirmation(message: str | None) -> bool:
    normalized = _normalize_run_control_text(message).lower()
    return any(marker in normalized for marker in ("确认", "同意", "批准"))


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
            "错误摘要、自动修复和已登记产物。\n\n"
            "全部工程文件保存在本项目根目录的 `md_run/最新编号/` 中。"
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
    live_summary = snapshot.get("live_summary") if isinstance(snapshot, Mapping) else None
    action = snapshot.get("pending_action") if isinstance(snapshot, Mapping) else None
    error_event = snapshot.get("error_event") if isinstance(snapshot, Mapping) else None
    status_event_id = snapshot.get("status_event_id") if isinstance(snapshot, Mapping) else None
    return {
        "run_id": run_id if isinstance(run_id, str) else None,
        "summary": summary if isinstance(summary, str) else "### 工程状态\n\n暂时无法读取当前运行。",
        "live_summary": live_summary if isinstance(live_summary, str) else summary,
        "pending_action": _public_pending_action(action),
        "error_event": dict(error_event) if isinstance(error_event, Mapping) else None,
        # Older adapters can omit this field.  Keep one live card rather than
        # treating each polling result as a fresh historical transition.
        "status_event_id": (
            status_event_id
            if isinstance(status_event_id, str) and status_event_id.strip()
            else f"{run_id or 'none'}:status:legacy"
        ),
        "timeline_events": bool(snapshot.get("timeline_events")) if isinstance(snapshot, Mapping) else False,
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

    def source_lines(value: Mapping[str, object]) -> list[str]:
        status = value.get("knowledge_status")
        source = value.get("advice_source")
        if status == "retrieved" and source == "knowledge_base":
            names = []
            for entry in value.get("knowledge_entries", []) if isinstance(value.get("knowledge_entries"), list) else []:
                if isinstance(entry, Mapping):
                    name = _public_action_text(entry.get("name"), limit=140)
                    number = entry.get("number")
                    if name and isinstance(number, int):
                        names.append(f"{number} {name}")
            lines = [f"知识库依据：{ '；'.join(names) }" if names else "知识库依据：已读取并校验条目"]
        elif status in {"not_matched", "unavailable"} or source == "llm_unverified":
            lines = ["知识库状态：未命中或不可用；建议来源：LLM 未经知识库验证的推断。"]
        else:
            return []
        notice = _public_action_text(value.get("compatibility_notice"), limit=220)
        if notice:
            lines.append(f"兼容性提醒：{notice}")
        return lines

    options = action.get("options")
    if isinstance(options, list) and len(options) > 1:
        lines = [
            "#### 待确认的模拟调整",
            f"问题摘要：{summary}",
            "检测到多个可能原因，请选择一个相互独立的方案：",
        ]
        for ordinal, option in enumerate(options[:3], 1):
            if not isinstance(option, Mapping):
                continue
            title = _public_action_text(option.get("title"), limit=120) or f"方案{ordinal}"
            cause = _public_action_text(option.get("cause"), limit=200)
            evidence = _public_action_text(option.get("evidence"), limit=240)
            option_summary = _public_action_text(option.get("summary"), limit=200)
            lines.append(f"**方案{ordinal}：{title}**")
            if cause:
                lines.append(f"可能原因：{cause}")
            if evidence:
                lines.append(f"依据：{evidence}")
            if option_summary:
                lines.append(f"处理摘要：{option_summary}")
            lines.extend(source_lines(option))
            adjustments = option.get("adjustments")
            for adjustment in adjustments[:8] if isinstance(adjustments, list) else []:
                if not isinstance(adjustment, Mapping):
                    continue
                name = _public_action_text(adjustment.get("name"), limit=48)
                before = _public_action_text(adjustment.get("before"), limit=48)
                after = _public_action_text(adjustment.get("after"), limit=48)
                if name and before and after:
                    lines.append(f"- {name}：{before} -> {after}")
        lines.append("回复“方案1/方案一”选择；回复“确认方案1/确认方案一”直接执行。")
        return "\n\n".join(lines)
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
    lines.extend(source_lines(action))
    lines.append("回复“确认”“同意”或“按方案执行”以按此方案重跑。")
    return "\n\n".join(lines)


_RUN_ASSISTANT_EVENT_KIND = "_run_assistant_event_kind"
_RUN_ASSISTANT_EVENT_ID = "_run_assistant_event_id"


def _run_assistant_event(
    kind: str,
    event_id: str,
    content: str,
) -> dict[str, str]:
    """Create an app-owned visual bubble that never becomes LLM context."""
    return {
        "role": "assistant",
        "content": content,
        _RUN_ASSISTANT_EVENT_KIND: kind,
        _RUN_ASSISTANT_EVENT_ID: event_id[:240],
    }


def _run_assistant_event_id(message: object) -> str | None:
    if not isinstance(message, Mapping):
        return None
    event_id = message.get(_RUN_ASSISTANT_EVENT_ID)
    return event_id if isinstance(event_id, str) and event_id else None


def _run_assistant_event_kind(message: object) -> str | None:
    if not isinstance(message, Mapping):
        return None
    kind = message.get(_RUN_ASSISTANT_EVENT_KIND)
    return kind if isinstance(kind, str) and kind else None


def _status_bubble(snapshot: Mapping[str, object]) -> dict[str, str]:
    content = snapshot.get("live_summary", snapshot.get("summary"))
    run_id = snapshot.get("run_id")
    event_id = snapshot.get("status_event_id")
    safe_content = content if isinstance(content, str) else "### 工程状态\n\n暂时无法读取当前运行。"
    safe_run_id = run_id if isinstance(run_id, str) else "none"
    safe_event_id = event_id if isinstance(event_id, str) else f"{safe_run_id}:status:legacy"
    return _run_assistant_event("status", safe_event_id, safe_content)


def _snapshot_notice_bubbles(snapshot: Mapping[str, object]) -> list[dict[str, str]]:
    """Render immutable public error and proposal events once per identity."""
    notices: list[dict[str, str]] = []
    error = snapshot.get("error_event")
    if isinstance(error, Mapping):
        event_id = error.get("event_id")
        content = error.get("content")
        if isinstance(event_id, str) and event_id and isinstance(content, str) and content:
            notices.append(_run_assistant_event("error", event_id, content[:2_000]))
    action = snapshot.get("pending_action")
    action_text = _pending_action_text(action if isinstance(action, Mapping) else None)
    run_id = snapshot.get("run_id")
    action_id = action.get("action_id") if isinstance(action, Mapping) else None
    if action_text and isinstance(run_id, str) and isinstance(action_id, str) and action_id:
        notices.append(_run_assistant_event(
            "proposal", f"{run_id}:pending_action:{action_id}", action_text
        ))
    return notices


def _sync_run_assistant_history(history, snapshot: Mapping[str, object]) -> list[dict[str, str]]:
    """Refresh one live status bubble, sealing it when its public phase changes."""
    current = [dict(message) for message in list(history or []) if isinstance(message, Mapping)]
    welcome = _run_assistant_welcome_history()[0]
    if not current or current[0] != welcome:
        current = [welcome, *[message for message in current if message != welcome]]
    status = _status_bubble(snapshot)
    status_id = _run_assistant_event_id(status)
    latest_status_index = next(
        (
            index
            for index in range(len(current) - 1, -1, -1)
            if _run_assistant_event_kind(current[index]) == "status"
        ),
        None,
    )
    if latest_status_index is None or _run_assistant_event_id(current[latest_status_index]) != status_id:
        # A step/state/action/error transition seals the previous card as a
        # historical snapshot and creates a new card for the current run.
        current.append(status)
    else:
        # Heartbeats and ETA updates belong to the current card, not history.
        current[latest_status_index] = status

    existing = {
        event_id
        for event_id in (_run_assistant_event_id(message) for message in current)
        if event_id is not None
    }
    for notice in _snapshot_notice_bubbles(snapshot):
        event_id = _run_assistant_event_id(notice)
        if event_id is not None and event_id not in existing:
            current.append(notice)
            existing.add(event_id)
    return current


def _run_assistant_llm_history(history) -> list[dict[str, str]]:
    """Exclude app-rendered status/events from browser dialogue sent to the LLM."""
    return [
        {"role": message["role"], "content": message["content"]}
        for message in list(history or [])
        if isinstance(message, Mapping)
        and isinstance(message.get("role"), str)
        and isinstance(message.get("content"), str)
        and _run_assistant_event_kind(message) is None
    ]


def _render_run_assistant_chat(history) -> list[dict[str, str]]:
    """Render every status, error and proposal as an independent chat message."""
    return [
        {"role": message["role"], "content": message["content"]}
        for message in list(history or [])
        if isinstance(message, Mapping)
        and isinstance(message.get("role"), str)
        and isinstance(message.get("content"), str)
    ]


def _refresh_run_assistant_view(history, bound_run_id, stop_requested, stop_confirmation_pending):
    """Refresh the current status card and retain prior status/event bubbles."""
    snapshot = _run_assistant_snapshot()
    current_history, current_run_id = _run_assistant_history_for_current_run(
        history, bound_run_id, snapshot["run_id"]
    )
    current_history = _sync_run_assistant_history(current_history, snapshot)
    return (
        _render_run_assistant_chat(current_history),
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


def _select_pending_action(action: Mapping[str, object], option_id: str) -> str:
    """Select one public option through the backend CAS boundary."""
    action_id = action.get("action_id")
    run_id = action.get("run_id")
    selector = getattr(frontend_api, "select_pending_action_option", None)
    if not isinstance(action_id, str) or not isinstance(run_id, str) or not callable(selector):
        return "当前待确认方案暂不可选择，请稍后刷新状态。"
    kwargs = {}
    if isinstance(action.get("state_revision"), int) and isinstance(action.get("config_fingerprint"), str):
        kwargs = {"state_revision": action["state_revision"], "config_fingerprint": action["config_fingerprint"]}
    try:
        result = selector(action_id, option_id, run_id, **kwargs)
    except Exception:
        return "方案选择未送达，请刷新状态后重试。"
    return _public_action_text(result, limit=240) or "方案选择未送达，请刷新状态后重试。"


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
    current_history = _sync_run_assistant_history(current_history, snapshot)
    if not message or not message.strip():
        yield (
            "",
            _render_run_assistant_chat(current_history),
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
        _render_run_assistant_chat(pending_history),
        pending_history,
        gr.update(interactive=False),
        current_run_id,
    )

    try:
        reply = chat_run_assistant(
            current_run_id, message, _run_assistant_llm_history(current_history)
        )
    except Exception:
        reply = "运行助理暂时不可用，请稍后重试。"
    final_snapshot = _run_assistant_snapshot()
    final_history, final_run_id = _run_assistant_history_for_current_run(
        current_history, current_run_id, final_snapshot["run_id"]
    )
    if final_run_id == current_run_id:
        final_history = current_history + [user_entry, {"role": "assistant", "content": reply}]
    final_history = _sync_run_assistant_history(final_history, final_snapshot)
    yield (
        gr.update(value="", interactive=True),
        _render_run_assistant_chat(final_history),
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
    current_history = _sync_run_assistant_history(current_history, snapshot)
    control_reply = run_assistant_control_command(current_run_id, message)
    if control_reply is not None:
        updated = _run_assistant_reply(current_history, message, control_reply)
        final_snapshot = _run_assistant_snapshot()
        final_history, final_run_id = _run_assistant_history_for_current_run(
            updated, current_run_id, final_snapshot["run_id"]
        )
        final_history = _sync_run_assistant_history(final_history, final_snapshot)
        yield (
            gr.update(value="", interactive=True),
            _render_run_assistant_chat(final_history),
            final_history,
            gr.update(interactive=True),
            _refresh_stop_button(stop_requested, stop_confirmation_pending),
            stop_confirmation_pending,
            stop_requested,
            final_run_id,
        )
        return
    normalized = _normalize_run_control_text(message)
    action = _awaiting_confirmation_action(snapshot["pending_action"])
    option_id = _pending_option_id(message, action)
    if action and option_id:
        selection_reply = _select_pending_action(action, option_id)
        # “确认方案一” is an explicit selection plus approval in one request.
        if _is_option_confirmation(message):
            selected_snapshot = _run_assistant_snapshot()
            selected_action = _awaiting_confirmation_action(selected_snapshot.get("pending_action"))
            if selected_action and selected_action.get("selected_option_id") == option_id:
                selection_reply = _confirm_pending_action(selected_action)
                selection_reply = f"已选择并确认方案{option_id.rsplit('_', 1)[-1]}。{selection_reply}"
        updated = _run_assistant_reply(current_history, message, selection_reply)
        final_snapshot = _run_assistant_snapshot()
        final_history, final_run_id = _run_assistant_history_for_current_run(
            updated, current_run_id, final_snapshot["run_id"]
        )
        final_history = _sync_run_assistant_history(final_history, final_snapshot)
        yield (
            gr.update(value="", interactive=True),
            _render_run_assistant_chat(final_history),
            final_history,
            gr.update(interactive=True),
            _refresh_stop_button(stop_requested, stop_confirmation_pending),
            stop_confirmation_pending,
            stop_requested,
            final_run_id,
        )
        return
    if action and _is_pending_action_approval(message):
        if action.get("selection_required") and not action.get("selected_option_id"):
            public_reply = "当前有多个候选方案，请先回复“方案1/方案一”“方案2/方案二”或“方案3/方案三”，再明确确认。"
            updated = _run_assistant_reply(current_history, message, public_reply)
            yield (
                gr.update(value="", interactive=True),
                _render_run_assistant_chat(updated),
                updated,
                gr.update(interactive=True),
                _refresh_stop_button(stop_requested, stop_confirmation_pending),
                stop_confirmation_pending,
                stop_requested,
                current_run_id,
            )
            return
        public_reply = _confirm_pending_action(action)
        updated = _run_assistant_reply(current_history, message, public_reply)
        final_snapshot = _run_assistant_snapshot()
        final_history, final_run_id = _run_assistant_history_for_current_run(
            updated, current_run_id, final_snapshot["run_id"]
        )
        final_history = _sync_run_assistant_history(final_history, final_snapshot)
        yield (
            gr.update(value="", interactive=True),
            _render_run_assistant_chat(final_history),
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
            _render_run_assistant_chat(pending_history),
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
        final_history = _sync_run_assistant_history(final_history, final_snapshot)
        yield (
            gr.update(value="", interactive=True),
            _render_run_assistant_chat(final_history),
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
            _render_run_assistant_chat(updated),
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


def _run_dependency_preflight():
    """Render the advisory local dependency report without gating task launch."""
    yield (
        gr.update(value="正在检查本机软件与内置模组..."),
        gr.update(interactive=False),
    )
    try:
        result = run_local_dependency_preflight()
        markdown = _format_dependency_preflight_result(result)
    except Exception:
        markdown = "**依赖预检执行失败。**\n\n请检查本机环境后重试；该检查不会阻止本地任务启动。"
    yield (
        gr.update(value=markdown),
        gr.update(interactive=True),
    )


_SOBTOP_REQUIREMENTS = (
    "sobtop", "atomtype", "sobtop_ini", "sobtop_lj", "sobtop_bonded",
    "bundled_obabel_wrapper", "bundled_obabel", "bundled_openbabel", "bundled_coordgen",
)

_DEPENDENCY_DISPLAY_GROUPS = (
    ("量化结构", (
        ("G16", ("g16",)),
        ("G16 formchk", ("formchk",)),
        ("G09", ("g09",)),
        ("G09 formchk", ("g09_formchk",)),
        ("ORCA", ("orca",)),
        ("ORCA 转换工具", ("orca_2mkl",)),
    )),
    ("电荷配置", (("Multiwfn", ("multiwfn",)),)),
    ("拓扑参数", (
        ("Sobtop", _SOBTOP_REQUIREMENTS),
        ("LigParGen", ("ligpargen",)),
        ("BOSS", ("boss",)),
        ("Open Babel", ("obabel",)),
        ("C Shell", ("csh",)),
    )),
    ("初始建盒", (("Packmol", ("packmol",)),)),
    ("模拟运行", (("GROMACS", ("gmx",)),)),
)


def _dependency_source_label(items: list[Mapping[str, object]]) -> str:
    """Translate internal resolution origins into concise user-facing labels."""
    sources = {str(item.get("source", "")) for item in items}
    if "bundled" in sources:
        return "内置"
    if sources & {"willy_env", "dotenv"}:
        return "已配置外置"
    if sources & {"path", "legacy_env"}:
        return "已检测到外置"
    return "未检测到外置"


def _format_dependency_preflight_result(result: object) -> str:
    """Render each checked dependency once without exposing route diagnostics."""
    if not isinstance(result, Mapping):
        return "**依赖预检未返回有效结果。**\n\n请检查本机环境后重试；该检查不会阻止本地任务启动。"
    raw_groups = result.get("groups")
    if not isinstance(raw_groups, list):
        return "**依赖预检未返回有效结果。**\n\n请检查本机环境后重试；该检查不会阻止本地任务启动。"

    items_by_id: dict[str, Mapping[str, object]] = {}
    for group in raw_groups:
        if not isinstance(group, Mapping):
            continue
        alternatives = group.get("alternatives")
        if not isinstance(alternatives, list):
            continue
        for alternative in alternatives:
            if not isinstance(alternative, Mapping):
                continue
            items = alternative.get("items")
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                requirement_id = item.get("requirement_id")
                if isinstance(requirement_id, str) and requirement_id not in items_by_id:
                    items_by_id[requirement_id] = item

    if not items_by_id:
        return "**依赖预检未返回有效结果。**\n\n请检查本机环境后重试；该检查不会阻止本地任务启动。"

    lines = ["### 本机依赖预检"]
    for category, dependencies in _DEPENDENCY_DISPLAY_GROUPS:
        lines.append(f"#### {category}")
        for index, (label, requirement_ids) in enumerate(dependencies, start=1):
            items = [items_by_id[item_id] for item_id in requirement_ids if item_id in items_by_id]
            satisfied = bool(items) and all(item.get("status") == "available" for item in items)
            status = "满足" if satisfied else "不满足"
            lines.append(f"{index}. {label}：{status}；来源：{_dependency_source_label(items)}")
    return "\n".join(lines)


_LOCAL_EXECUTION_CONTEXT = {
    "execution_mode": "local",
    "execution_profile_id": "local-default",
    "available": True,
    "summary": "本版本仅支持本机 GROMACS 执行。",
}


def _proposal_execution_summary(context: Mapping[str, object]) -> str:
    """Render the selected intent and its server-side freeze boundary."""
    summary = context.get("summary")
    if not isinstance(summary, str) or not summary:
        return "执行偏好暂不可读取；生成方案前不会启动计算。"
    return f"**当前方案执行方式**：{summary}"


def _proposal_welcome_history(context: Mapping[str, object]) -> list[dict[str, str]]:
    """Build the proposal assistant's greeting with the active execution boundary."""
    return [{
        "role": "assistant",
        "content": (
            "你好！我是 **Willy-方案助理**，你的 MD 模拟助手。☀️\n\n"
            "只需用自然语言描述你的体系，我会给出方案，"
            "自动完成体系准备、EM、NPT退火和生产模拟，并展示运行进度。"
            "需要全精度 TRR 轨迹时，请在对话中明确提出。您也可以上传自己的结构后再启动。\n\n"
            "也可以询问本项目的架构、状态机或工具边界；这类回答只依据已登记文档，"
            "不会修改方案、配置或运行状态。\n\n"
            f"{_proposal_execution_summary(context)}\n\n"
            "提示：G09 未经可靠全链路验证，使用时可能出现运行问题。\n\n"
            "（仅分子个数、分子名为必要，其他可选填）"
        ),
    }]


# ============================================================
# UI
# ============================================================

with gr.Blocks(title="Willy : AI驱动的小分子Gromacs模拟工具") as app:
    with gr.Row(elem_id="app-header"):
        gr.HTML("<h1>Willy : AI驱动的小分子Gromacs模拟工具</h1>", elem_id="app-title")
        dark_mode = gr.Checkbox(label="夜间模式", value=False, elem_id="theme-toggle", container=False)
        dark_mode.change(js=THEME_TOGGLE_JS, inputs=[dark_mode], outputs=[])

    initial_execution_context = dict(_LOCAL_EXECUTION_CONTEXT)
    execution_profile_state = gr.State(initial_execution_context)

    with gr.Tabs():
        with gr.Tab("本地任务"):
            with gr.Row(elem_id="assistant-row", equal_height=True):
                with gr.Column(scale=1):
                    with gr.Column(elem_id="proposal-assistant"):
                        with gr.Row(elem_id="proposal-header"):
                            gr.HTML("<div class='panel-title'>方案助理</div>")
                        welcome_msg = _proposal_welcome_history(initial_execution_context)
                        chat_state = gr.State(welcome_msg)
                        proposal_plan_state = gr.State(None)
                        chatbot = gr.Chatbot(
                            height=ASSISTANT_CHAT_HEIGHT,
                            value=welcome_msg,
                            label="",
                            elem_id="proposal-chat",
                        )
                        with gr.Row(elem_id="proposal-input-row"):
                            msg = gr.Textbox(placeholder=PROPOSAL_EXAMPLE, lines=3, label="", scale=4)
                            with gr.Column(scale=1, min_width=80):
                                send_btn = gr.Button("发送", variant="primary")
                                upload = gr.UploadButton(
                                    "上传结构",
                                    file_types=[".gjf", ".inp", ".mol2", ".pdb", ".xyz"],
                                )
                        upload.upload(
                            fn=_on_upload,
                            inputs=[upload, chat_state, proposal_plan_state],
                            outputs=[upload, chatbot, chat_state, proposal_plan_state])

                        msg.submit(fn=_chat_wrapper, inputs=[
                                       msg, chat_state, proposal_plan_state, execution_profile_state,
                                   ],
                                   outputs=[msg, chatbot, chat_state, proposal_plan_state, send_btn, upload],
                                   trigger_mode="once", concurrency_limit=1,
                                   concurrency_id="proposal-assistant-chat")
                        send_btn.click(fn=_chat_wrapper, inputs=[
                                       msg, chat_state, proposal_plan_state, execution_profile_state,
                                   ],
                                       outputs=[msg, chatbot, chat_state, proposal_plan_state, send_btn, upload],
                                       trigger_mode="once", concurrency_limit=1,
                                       concurrency_id="proposal-assistant-chat")

                with gr.Column(scale=1):
                    with gr.Column(elem_id="run-assistant"):
                        with gr.Row(elem_id="run-header"):
                            gr.HTML("<div class='panel-title'>运行助理</div>")
                        run_welcome_msg = _run_assistant_welcome_history()
                        run_assistant_run_id = gr.State(None)
                        initial_run_snapshot = _run_assistant_snapshot()
                        initial_run_history = _sync_run_assistant_history(
                            run_welcome_msg, initial_run_snapshot
                        )
                        run_chat_state = gr.State(initial_run_history)
                        run_chatbot = gr.Chatbot(
                            height=ASSISTANT_CHAT_HEIGHT,
                            value=_render_run_assistant_chat(initial_run_history),
                            label="",
                            elem_id="run-chat",
                            group_consecutive_messages=False,
                        )
                        with gr.Row(elem_id="run-input-row"):
                            run_message = gr.Textbox(
                                placeholder="询问当前运行的状态、工序进度或错误", label="", lines=3, scale=4,
                                elem_id="run-message")
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
                        initial_run_choices = frontend_api.get_run_visualization_run_choices()
                        initial_run_choice = (
                            initial_run_choices[0] if initial_run_choices else None
                        )
                        initial_structure_choices = frontend_api.get_run_visualization_file_choices(
                            initial_run_choice,
                        )
                        initial_structure_choice = (
                            initial_structure_choices[0] if initial_structure_choices else None
                        )
                        with gr.Row(elem_id="structure-selector-row"):
                            run_selector = gr.Dropdown(
                                choices=initial_run_choices,
                                value=initial_run_choice,
                                label="运行目录",
                                allow_custom_value=False,
                                interactive=True,
                                scale=1,
                                min_width=0,
                                elem_id="structure-run-selector",
                            )
                            structure_selector = gr.Dropdown(
                                choices=initial_structure_choices,
                                value=initial_structure_choice,
                                label="结构文件",
                                allow_custom_value=False,
                                interactive=True,
                                scale=1,
                                min_width=0,
                                elem_id="structure-selector",
                            )
                        structure_legend = gr.HTML(
                            frontend_api.render_run_visualization_legend_html(
                                initial_structure_choice,
                                run_id=initial_run_choice,
                            ),
                            elem_id="structure-legend",
                        )
                        structure_viewer = gr.HTML(
                            frontend_api.render_run_visualization_html(
                                initial_structure_choice,
                                run_id=initial_run_choice,
                            ),
                            elem_id="structure-viewer",
                        )
                        run_selector.focus(
                            fn=_refresh_visualization_run_choices,
                            inputs=[run_selector, structure_selector],
                            outputs=[run_selector, structure_selector, structure_viewer, structure_legend],
                            trigger_mode="always_last",
                            concurrency_limit=1,
                            concurrency_id="structure-directory-refresh",
                            show_progress="hidden",
                        )
                        run_selector.change(
                            fn=_refresh_visualization_run_choices,
                            inputs=[run_selector, structure_selector],
                            outputs=[run_selector, structure_selector, structure_viewer, structure_legend],
                            trigger_mode="always_last",
                            concurrency_limit=1,
                            concurrency_id="structure-directory-refresh",
                            show_progress="hidden",
                        )
                        structure_selector.focus(
                            fn=_refresh_visualization_file_choices,
                            inputs=[run_selector, structure_selector],
                            outputs=[structure_selector, structure_viewer, structure_legend],
                            trigger_mode="always_last",
                            concurrency_limit=1,
                            concurrency_id="structure-directory-refresh",
                            show_progress="hidden",
                        )
                        structure_selector.change(
                            fn=_render_selected_visualization,
                            inputs=[run_selector, structure_selector],
                            outputs=[structure_viewer, structure_legend],
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
            gr.Markdown("### 1. LLM 配置", elem_id="llm-configuration-heading")
            llm_configuration_notice = gr.Markdown(
                get_llm_config_notice(),
                elem_id="llm-configuration-notice",
            )
            api_key_status = gr.Markdown("", elem_id="llm-configuration-status")
            gr.Markdown(
                "本版本仅支持用户自配 OpenAI-compatible API Key；托管网关配置入口已冻结。",
                elem_id="llm-provider-notice",
            )
            with gr.Column() as byok_llm_config:
                api_key_input = gr.Textbox(
                    label="OpenAI-compatible API Key",
                    placeholder="sk-***你的API key***",
                    type="password",
                    info=(
                        "支持 OpenAI、DeepSeek、阿里云百炼、智谱 AI、月之暗面（Kimi）等厂商。"
                        "Agent制作者不会以任何方式获取您的API-key。"
                    ),
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

            gr.Markdown("### 2. 运行依赖预检", elem_id="dependency-preflight-heading")
            gr.Markdown(
                "检查本机软件和内置模组。检测到的软件会自动保存为本项目默认配置；"
                "不会覆盖已有配置，也不会阻止本地任务启动。",
                elem_id="dependency-preflight-notice",
            )
            dependency_preflight_button = gr.Button(
                "预检运行依赖",
                variant="secondary",
                elem_id="dependency-preflight-button",
            )
            dependency_preflight_status = gr.Markdown(
                "尚未执行依赖预检。",
                elem_id="dependency-preflight-status",
            )
            dependency_preflight_button.click(
                fn=_run_dependency_preflight,
                outputs=[dependency_preflight_status, dependency_preflight_button],
                trigger_mode="once",
                concurrency_limit=1,
                concurrency_id="dependency-preflight",
                show_progress="hidden",
            )

        with gr.Tab("新手指南"):
            with gr.Column(elem_id="beginner-guide-page"):
                gr.HTML(BEGINNER_GUIDE_HTML)

        with gr.Tab("关于"):
            with gr.Column(elem_id="about-page"):
                gr.HTML(
                    """
                    <section class="about-intro">
                        <h2>Willy : AI驱动的小分子Gromacs模拟工具</h2>
                        <p>Willy 是基于Gromacs软件的MD模拟自动化Agent组。目前它有两名员工：Willy-方案助理 和 Willy-运行助理。</p>
                    </section>
                    <div class="about-grid">
                        <section class="about-section">
                            <h2>参与者</h2>
                            <ul>
                                <li>项目整体统筹：小w </li>
                                <li>架构、交付、质量：ChatGPT 5.6-terra </li>
                                <li>前端、文档、测试、Tools等领域工程：ChatGPT 5.6-terra， DeepSeek V4 Pro </li>
                            </ul>
                        </section>
                        <section class="about-section">
                            <h2>Willy 能做什么？</h2>
                            <ul>
                                <li>Willy-方案助理会根据您的自然语言描述生成配置，执行从分子结构优化到 GROMACS 模拟的全链路。</li>
                                <li>Willy-运行助理负责监控和记录整个模拟流程，并在出现问题时为您提供建议、解决方案和重跑续跑计划。</li>
                                <li>您还可以通过可视化界面看到运行中产出的的分子结构与MD盒子结果。</li>
                            </ul>
                        </section>
                        <section class="about-section">
                            <h2>后续规划</h2>
                            <ul>
                                <li>扩展更多的工具链集成。</li>
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
                with gr.Column(elem_id="third-party-notices"):
                    gr.HTML(
                        """
                        <section>
                            <h2>第三方组件引用与著作权</h2>
                            <p>Willy 集成并编排第三方科学软件，但 Willy 作者不拥有其原始项目的著作权。</p>
                            <p>研究工作如使用了 Willy 集成的 Sobtop 拓扑文件生成、Multiwfn 电荷生成，请至少引用以下文献或网站：</p>
                            <ol>
                                <li>Tian Lu, Sobtop, Version [当前版本], <a href="http://sobereva.com/soft/Sobtop" target="_blank" rel="noopener noreferrer">http://sobereva.com/soft/<strong>Sobtop</strong></a> (accessed on 日 月 年)</li>
                                <li>Tian Lu, Feiwu Chen, Multiwfn: A Multifunctional Wavefunction Analyzer, Journal of Computational Chemistry 33, 580-592 (2012). DOI: 10.1002/jcc.22885</li>
                                <li>Tian Lu, A comprehensive electron wavefunction analysis toolbox for chemists, Multiwfn, Journal of Chemical Physics 161, 082503 (2024). DOI: 10.1063/5.0216272</li>
                            </ol>
                            <p>研究工作如使用了 Willy 集成的 Packmol 建盒组件，请至少引用以下文献：</p>
                            <ol>
                                <li>L. Martinez, R. Andrade, E. G. Birgin, J. M. Martinez, Packmol: A package for building initial configurations for molecular dynamics simulations, Journal of Computational Chemistry 30, 2157-2164 (2009). DOI: 10.1002/jcc.21224</li>
                                <li>J. M. Martinez, L. Martinez, Packing optimization for the automated generation of complex system's initial configurations for molecular dynamics and docking, Journal of Computational Chemistry 24, 819-825 (2003). DOI: 10.1002/jcc.10216</li>
                            </ol>
                            <p>使用 Sobtop、Packmol 或其他第三方组件时，使用者还应遵守其各自的许可、分发和引用要求。Willy 对这些组件仅提供集成与工作流编排，不主张其原始软件、文档或学术成果的著作权。</p>
                        </section>
                        """
                    )

    app.load(
        fn=None,
        inputs=None,
        outputs=None,
        js=RUN_ASSISTANT_SLASH_MENU_JS,
        queue=False,
    )
app.queue()

if __name__ == "__main__":
    app.launch(server_name="127.0.0.1", server_port=int(dotenv_value("WILLY_SERVER_PORT") or "7860"), share=False,
               inbrowser=True, css=APP_CSS)
