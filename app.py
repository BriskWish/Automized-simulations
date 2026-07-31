"""app.py — Willy Agent Gradio UI (纯前端, 后端操作全部委托给 frontend_api)"""

import gradio as gr

from willy.agent_config import chat, handle_upload, launch_pipeline
from willy.frontend_api import (
    stop_pipeline,
    is_pipeline_running,
    get_molecule_catalog,
    flatten_catalog,
    render_viewer_html,
    get_progress_markdown,
    refresh_molecule_choices as _refresh_choices,
)


def _chat_wrapper(message, history):
    """包装 chat() generator，丢弃 html_val（前端已移除 system prompt 展示区）。"""
    for msg_val, chatbot_val, state_val, _html_val, btn_dict in chat(message, history):
        yield msg_val, chatbot_val, state_val, gr.update(**btn_dict)


# ============================================================
# 流水线控制
# ============================================================

def _pipeline_running() -> bool:
    return is_pipeline_running()


def _stop_pipeline_clean():
    return (
        gr.update(value=stop_pipeline(clean=True)),
        gr.update(visible=False),
        gr.update(visible=False),
    )


def _stop_pipeline_keep():
    return (
        gr.update(value=stop_pipeline(clean=False)),
        gr.update(visible=False),
        gr.update(visible=False),
    )


def _on_confirm(chat_history):
    """确认按钮回调：启动流水线，更新聊天记录，隐藏按钮。"""
    try:
        msg_text = launch_pipeline()
    except Exception as e:
        msg_text = f"❌ 执行失败: {e}"
    h = list(chat_history) + [{"role": "assistant", "content": msg_text}]
    return h, h, gr.update(visible=False)


# ============================================================
# 分子目录下拉
# ============================================================

def _build_catalog_and_choices():
    catalog = get_molecule_catalog()
    choices = flatten_catalog(catalog)
    cat_labels = ["全部"] + [c for c in catalog.keys()]
    return catalog, choices, cat_labels


def _filter_by_cat(cat, catalog):
    if cat == "全部" or cat is None:
        return gr.update(choices=flatten_catalog(catalog))
    return gr.update(choices=flatten_catalog({cat: catalog.get(cat, {})}))


# ============================================================
# 上传
# ============================================================

def _on_upload(upload_file, chat_state):
    """上传分子结构 → 更新聊天 + 刷新下拉列表。"""
    _file, chatbot, new_state = handle_upload(upload_file, chat_state)
    return (
        _file,
        chatbot,
        new_state,
        gr.update(choices=["全部"] + [c for c in get_molecule_catalog().keys()]),
        gr.update(choices=_refresh_choices()),
    )


# ============================================================
# UI
# ============================================================

with gr.Blocks(title="Willy Agent") as app:
    gr.HTML("<h1 style='font-family:sans-serif;margin:0'>Willy AI Driven MD Agent</h1>")

    # CSS row-reverse: 代码中先定义 → 渲染到右侧；后定义 → 渲染到左侧
    # 因此先定义查看器（视觉右），后定义聊天（视觉左）
    with gr.Row(elem_classes=["swap-cols"]):
        # ── 视觉右侧：3D 查看器 + 进度面板（代码中先定义，变量供聊天区引用）──
        with gr.Column(scale=1):
            catalog, all_choices, cat_labels = _build_catalog_and_choices()

            with gr.Row():
                cat_dropdown = gr.Dropdown(
                    choices=cat_labels, value="全部", label="分类", scale=1)
                mol_dropdown = gr.Dropdown(
                    choices=all_choices, value=None, label="分子", scale=2)

            cat_dropdown.change(
                fn=lambda cat: _filter_by_cat(cat, get_molecule_catalog()),
                inputs=[cat_dropdown], outputs=[mol_dropdown])

            viewer_html = gr.HTML(value=render_viewer_html(None))
            mol_dropdown.change(fn=render_viewer_html, inputs=[mol_dropdown], outputs=[viewer_html])

            progress_md = gr.Markdown(get_progress_markdown())
            gr.Timer(3).tick(fn=get_progress_markdown, outputs=[progress_md])

        # ── 视觉左侧：聊天 ──
        with gr.Column(scale=1):
            welcome_msg = [{"role": "assistant",
                "content": "你好！我是 **Willy**，你的 MD 模拟助手。\n\n"
                           "只需用自然语言描述你的体系，我会给出方案，"
                           "自动完成当前可用的体系准备流程，并展示运行进度。\n\n"
                           "已内置支持的分子：Li, TFSI, NO3, PF6, FEC, DME, DMM, EC, EMC, TTE, DMAA。"
                           "您也可以上传自己的结构后再启动。\n\n"
                           "启动示例：*Li 90, NO3 10, TFSI 80, EC 200, FEC 200, TTE 2000*"
                           "（其他参数可选填：常温常压，平衡采样 30ns）"}]
            chat_state = gr.State(welcome_msg)
            chatbot = gr.Chatbot(height=720, value=welcome_msg)
            with gr.Row():
                msg = gr.Textbox(placeholder="描述你的模拟体系, 如: Li 80 TFSI 80 FEC 300, 350K, 20ns",
                                 lines=2, label="", scale=4)
                with gr.Column(scale=1, min_width=80):
                    send_btn = gr.Button("发送", variant="primary")
                    upload = gr.UploadButton("上传结构", file_types=[".gjf", ".mol2", ".pdb", ".xyz"])
            upload.upload(
                fn=_on_upload,
                inputs=[upload, chat_state],
                outputs=[upload, chatbot, chat_state, cat_dropdown, mol_dropdown])

            with gr.Row(elem_classes=["center-row"]):
                confirm_btn = gr.Button("✅ 确认启动流水线", variant="primary", visible=False, size="sm")

            msg.submit(fn=_chat_wrapper, inputs=[msg, chat_state],
                       outputs=[msg, chatbot, chat_state, confirm_btn])
            send_btn.click(fn=_chat_wrapper, inputs=[msg, chat_state],
                           outputs=[msg, chatbot, chat_state, confirm_btn])
            confirm_btn.click(fn=_on_confirm, inputs=[chat_state],
                              outputs=[chatbot, chat_state, confirm_btn])

            # ── 中止按钮 ──
            stop_btn = gr.Button("中止流水线", variant="stop", size="sm", visible=False)
            clean_btn = gr.Button("清理产物并中止", variant="secondary", size="sm", visible=False)
            keep_btn = gr.Button("仅中止(保留产物)", variant="secondary", size="sm", visible=False)

            def _check_then_ask():
                running = is_pipeline_running()
                if not running:
                    return (gr.update(value="当前没有运行中的流水线", visible=False),
                            gr.update(visible=False), gr.update(visible=False))
                return (gr.update(value="流水线运行中，选择操作："),
                        gr.update(visible=True), gr.update(visible=True))

            def _refresh_stop():
                return gr.update(visible=is_pipeline_running())

            gr.Timer(3).tick(fn=_refresh_stop, outputs=[stop_btn])
            stop_btn.click(fn=_check_then_ask, outputs=[stop_btn, clean_btn, keep_btn])
            clean_btn.click(fn=_stop_pipeline_clean, outputs=[stop_btn, clean_btn, keep_btn])
            keep_btn.click(fn=_stop_pipeline_keep, outputs=[stop_btn, clean_btn, keep_btn])

app.queue()
app.launch(server_name="127.0.0.1", server_port=7860, share=False,
           css="html{font-size:14px} footer{display:none!important} .center-row{justify-content:center} .swap-cols{flex-direction:row-reverse}")
