"""app.py — Willy Agent Gradio UI (纯前端, 逻辑在 agent.py)"""

import gradio as gr
import shutil
import json
from datetime import datetime, timezone
from pathlib import Path

from willy._paths import get_project_root
from willy.agent import chat, summarize, handle_upload, launch_pipeline
from willy.pipeline_state import PipelineStateMachine, State


def _chat_wrapper(message, history):
    """包装 chat() generator，将纯 dict 转成 gr.update() 以兼容 Gradio 6。"""
    for msg_val, chatbot_val, state_val, html_val, btn_dict in chat(message, history):
        yield msg_val, chatbot_val, state_val, html_val, gr.update(**btn_dict)

ROOT = get_project_root()


def _tool_system_prompt() -> str:
    from willy.agent import get_system_prompt
    return get_system_prompt()


# ============================================================
# 流水线控制
# ============================================================

def _pipeline_running() -> bool:
    s = PipelineStateMachine.read()
    return s.state in (State.RUNNING.value, State.RETRYING.value)


def _set_pipeline_aborted():
    """将 status.json 状态置为 ABORTED，供中止函数调用。"""
    sp = ROOT / "status.json"
    if sp.exists():
        try:
            data = json.loads(sp.read_text())
            data["state"] = State.ABORTED.value
            data["updated_at"] = datetime.now(timezone.utc).isoformat()
            sp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        except (json.JSONDecodeError, OSError):
            sp.unlink(missing_ok=True)


def _stop_pipeline_clean():
    import os
    os.system("pkill -f 'run_pipeline.py' 2>/dev/null; pkill -f 'g16' 2>/dev/null; pkill -9 -f 'l502.exe' 2>/dev/null")
    _set_pipeline_aborted()
    (ROOT / ".pipeline.lock").unlink(missing_ok=True)
    os.system(f"rm -f {ROOT}/model.inp {ROOT}/model.pdb")
    return (gr.update(value="已中止，产物已清理"), gr.update(visible=False), gr.update(visible=False))


def _stop_pipeline_keep():
    import os
    os.system("pkill -f 'run_pipeline.py' 2>/dev/null; pkill -f 'g16' 2>/dev/null; pkill -9 -f 'l502.exe' 2>/dev/null")
    _set_pipeline_aborted()
    (ROOT / ".pipeline.lock").unlink(missing_ok=True)
    return (gr.update(value="已中止，产物已保留"), gr.update(visible=False), gr.update(visible=False))


def _on_confirm(chat_history):
    """确认按钮回调：启动流水线，更新聊天记录，隐藏按钮。"""
    try:
        msg_text = launch_pipeline()
    except Exception as e:
        msg_text = f"❌ 执行失败: {e}"
    h = list(chat_history) + [{"role": "assistant", "content": msg_text}]
    return h, h, gr.update(visible=False)


# ============================================================
# 进度面板
# ============================================================

def _progress_ui() -> str:
    import subprocess

    s = PipelineStateMachine.read()
    if s.state == State.IDLE.value:
        return ""

    # ── 存活检查：流水线进程是否还在 ──
    pipeline_alive = True
    if s.state in (State.RUNNING.value, State.RETRYING.value):
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'run_pipeline.py'],
                capture_output=True, timeout=2)
            pipeline_alive = result.returncode == 0
        except Exception:
            pipeline_alive = True  # 无法判断时假定存活，避免误报

    labels = {1:"量子计算",2:"结构转换",3:"RESP 电荷",4:"拓扑生成",5:"主拓扑",6:"MD 参数",7:"初始盒子",8:"就绪"}
    lines = ["### 流水线进度", ""]

    for i in range(1, s.total_steps + 1):
        if i in s.done_steps:
            lines.append(f"- ✅ {labels.get(i, f'步骤{i}')}")
            continue
        if i == s.step and not pipeline_alive:
            lines.append(f"- 💀 {labels.get(i, f'步骤{i}')} 进程已退出")
            if s.error:
                lines.append(f"  最后错误: {s.error[:120]}")
            continue
        if i == s.step and s.state in (State.RUNNING.value, State.RETRYING.value):
            icon = "🔄" if s.state == State.RETRYING.value else "⏳"
            lines.append(f"- {icon} {labels.get(i, f'步骤{i}')} 进行中...")
            if s.state == State.RETRYING.value:
                lines.append(f"  🤖 {s.agent} Agent 修复中 ({s.retry_n}/{s.retry_max})")
                for act in s.actions[-3:]:
                    lines.append(f"    · {act}")
            if s.error:
                lines.append(f"  ⚠ {s.error[:120]}")
            continue
        if i > s.step or (i == s.step and s.state == State.IDLE.value):
            lines.append(f"- ⬚ {labels.get(i, f'步骤{i}')}")
            continue
        lines.append(f"- ⬚ {labels.get(i, f'步骤{i}')}")

    if s.state == State.ESCALATED.value:
        lines.append("")
        lines.append("🆘 **自动修复失败，已升级**")
        esc = s.escalation
        if esc:
            lines.append(f"- 层: {esc.get('layer','?')}")
            lines.append(f"- 已尝试 {esc.get('attempts_made','?')} 次")
            if esc.get("recommendation"):
                lines.append(f"- 建议: {esc['recommendation'][:200]}")
    elif s.state == State.DONE.value:
        lines.append("")
        lines.append("✅ **全流程完成**")
    elif s.state == State.ABORTED.value:
        lines.append("")
        lines.append("⏹ **已中止**")

    return "\n".join(lines)


# ============================================================
# 3D Viewer —— SCAN 风格：浅色背景 + 球棍模型 + 分类下拉
# ============================================================

# 支持的结构文件及其格式
_STRUCT_GLOBS = [
    ("topo", "*.pdb", "pdb"),
    ("struct", "*.mol2", "mol2"),
]

# 空状态占位（SCAN 风格浅灰绿底）
_VIEWER_EMPTY = (
    '<div style="width:100%;height:400px;border-radius:18px;background:#eef2ef;'
    'display:flex;align-items:center;justify-content:center;'
    'color:#8a9a90;font-size:14px;border:2px dashed #d7ddda;flex-direction:column;gap:8px">'
    '<span style="font-size:28px">🔬</span>'
    '<span>从下方下拉菜单选择分子查看</span>'
    '</div>'
)

# ── 分子目录构建（带电荷分类）──

def _get_charge_map() -> dict[str, int]:
    """从 knowledge_tools registry + config.json 获取分子电荷。"""
    charges = {}
    try:
        from willy.knowledge_tools import _registry
        for name in _registry.get_all_names():
            info = _registry.lookup(name)
            if info:
                charges[name] = info.get("charge", 0)
    except Exception:
        pass
    cfg = ROOT / "config.json"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text())
            for name, info in data.get("molecules", {}).items():
                if name not in charges:
                    charges[name] = info.get("charge", 0)
        except (json.JSONDecodeError, OSError):
            pass
    return charges


def _build_mol_catalog() -> dict[str, dict[str, str]]:
    """扫描结构文件，按电荷分类。

    Returns: {category_label: {display_name: file_path}}
    """
    charges = _get_charge_map()
    cats: dict[str, dict[str, str]] = {
        "🟢 阳离子": {},
        "🔴 阴离子": {},
        "🔵 溶剂 / 中性分子": {},
    }

    for rel_dir, pattern, fmt in _STRUCT_GLOBS:
        for p in (ROOT / rel_dir).glob(pattern) if (ROOT / rel_dir).exists() else []:
            name = p.stem
            if name.endswith("_run"):
                continue
            chg = charges.get(name, 0)
            if chg > 0:
                cats["🟢 阳离子"][name] = str(p)
            elif chg < 0:
                cats["🔴 阴离子"][name] = str(p)
            else:
                cats["🔵 溶剂 / 中性分子"][name] = str(p)

    # model.pdb（体系盒子）特殊分类
    model = ROOT / "model.pdb"
    if model.exists():
        cats.setdefault("📦 体系模型", {})["📦 model"] = str(model)

    # 清理空分类
    return {k: v for k, v in cats.items() if v}


def _flatten_catalog(catalog: dict[str, dict[str, str]]) -> list[str]:
    """展平为 Gradio Dropdown 可用的 choice 列表：['🟢 阳离子', '  Li', '  ...']"""
    choices = []
    for cat, mols in catalog.items():
        if not mols:
            continue
        choices.append(cat)               # 分类标题行
        for name in sorted(mols.keys()):
            choices.append(f"  {name}")   # 缩进表示子项
    return choices


def _resolve_mol(choice: str, catalog: dict[str, dict[str, str]]) -> tuple[str | None, str | None]:
    """解析下拉选项，返回 (category, molecule_name)。非分子行返回 (None, None)。"""
    stripped = choice.strip() if choice else ""
    for cat, mols in catalog.items():
        if stripped == cat.strip():
            return (None, None)  # 点击分类标题，不加载
        for name in mols:
            if stripped == name or stripped == f"  {name}".strip():
                return (cat, name)
    return (None, None)


# ── Viewer 渲染 ──

def _load_viewer(mol_choice: str = None) -> str:
    """根据下拉选项渲染 3D 分子查看器 HTML。"""
    catalog = _build_mol_catalog()
    choices = _flatten_catalog(catalog)

    if not mol_choice or mol_choice not in choices:
        return _VIEWER_EMPTY

    _cat, mol_name = _resolve_mol(mol_choice, catalog)
    if mol_name is None:
        return _VIEWER_EMPTY

    # 找到文件路径
    file_path = None
    for cat_mols in catalog.values():
        if mol_name in cat_mols:
            file_path = cat_mols[mol_name]
            break

    if file_path is None or not Path(file_path).exists():
        return _VIEWER_EMPTY

    mol_data = Path(file_path).read_text()
    ext = Path(file_path).suffix.lower()
    fmt = "mol2" if ext == ".mol2" else "pdb"
    atom_count = mol_data.count("ATOM") if mol_data else 0
    sphere_scale = 0.8 if atom_count <= 10 else (0.4 if atom_count <= 100 else 0.28)

    mol_json = json.dumps(mol_data)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
html,body{{margin:0;padding:0;width:100%;height:100%;overflow:hidden;background:#eef2ef}}
#v{{width:100%;height:100%;position:absolute;top:0;left:0}}
</style></head><body>
<div id="v"></div>
<script src="https://3Dmol.org/build/3Dmol-min.js"></script>
<script>
(function(){{
  function init(){{
    if(typeof $3Dmol==="undefined"){{setTimeout(init,150);return;}}
    var v=$3Dmol.createViewer("v",{{backgroundColor:"#eef2ef"}});
    v.addModel({mol_json},"{fmt}");
    v.setStyle({{}},{{stick:{{radius:0.16,colorscheme:"Jmol"}},sphere:{{scale:{sphere_scale},colorscheme:"Jmol"}}}});
    v.zoomTo();v.render();v.zoom(1.2);
  }}
  init();
}})();
</script></body></html>"""

    import html as _h
    return (
        f'<div style="background:#eef2ef;border-radius:18px;overflow:hidden">'
        f'<iframe srcdoc="{_h.escape(html)}" style="width:100%;height:340px;border:none" '
        f'sandbox="allow-scripts allow-same-origin"></iframe>'
        f'<div style="text-align:center;padding:6px 0 14px;font-size:12px;color:#56605b;'
        f'font-family:system-ui,sans-serif">{mol_name}</div>'
        f'</div>'
    )


def _refresh_mol_choices() -> list[str]:
    """刷新分子下拉列表（上传新结构后调用）。"""
    return _flatten_catalog(_build_mol_catalog())


# ============================================================
# UI
# ============================================================

with gr.Blocks(title="Willy Agent") as app:
    gr.HTML("<h1 style='font-family:sans-serif;margin:0'>Willy AI Driven MD Agent</h1>")
    with gr.Row():
        with gr.Column(scale=1):
            _catalog = _build_mol_catalog()
            _all_choices = _flatten_catalog(_catalog)

            with gr.Row():
                cat_dropdown = gr.Dropdown(
                    choices=["全部"] + [c for c in _catalog.keys()],
                    value="全部", label="分类", scale=1)
                mol_dropdown = gr.Dropdown(
                    choices=_all_choices,
                    value=None, label="分子", scale=2)

            def _filter_by_cat(cat):
                if cat == "全部" or cat is None:
                    return gr.update(choices=_all_choices)
                return gr.update(choices=_flatten_catalog({cat: _catalog.get(cat, {})}))

            cat_dropdown.change(fn=_filter_by_cat, inputs=[cat_dropdown], outputs=[mol_dropdown])

            viewer_html = gr.HTML(value=_load_viewer(None))
            mol_dropdown.change(fn=_load_viewer, inputs=[mol_dropdown], outputs=[viewer_html])

            progress_md = gr.Markdown(_progress_ui())
            gr.Timer(3).tick(fn=_progress_ui, outputs=[progress_md])

        with gr.Column(scale=1):
            welcome_msg = [{"role":"assistant",
                "content":"你好！我是 **Willy**，你的 MD 模拟助手。\n\n"
                          "只需用自然语言描述你的体系，我会给出方案，"
                          "启动并全程监控 GROMACS 的模拟过程！\n\n"
                          "已内置支持的分子：Li, TFSI, NO3, PF6, FEC, DME, DMM, EC, EMC, TTE, DMAA。"
                          "您也可以上传自己的结构后再启动。\n\n"
                          "启动示例：*Li 90, NO3 10, TFSI 80, EC 200, FEC 200, TTE 2000*"
                          "（其他参数可选填：常温常压，平衡采样 30ns）"}]
            chat_state = gr.State(welcome_msg)
            chatbot = gr.Chatbot(height=360, value=welcome_msg)
            with gr.Row():
                msg = gr.Textbox(placeholder="描述你的模拟体系, 如: Li 80 TFSI 80 FEC 300, 350K, 20ns",
                                 lines=2, label="", scale=4)
                with gr.Column(scale=1, min_width=80):
                    send_btn = gr.Button("发送", variant="primary")
                    upload = gr.UploadButton("上传结构", file_types=[".gjf",".mol2",".pdb",".xyz"])
            upload.upload(fn=handle_upload, inputs=[upload, chat_state], outputs=[upload, chatbot, chat_state]).then(
                fn=lambda: (gr.update(choices=["全部"] + [c for c in _build_mol_catalog().keys()]),
                            gr.update(choices=_refresh_mol_choices())),
                outputs=[cat_dropdown, mol_dropdown])

            with gr.Row(elem_classes=["center-row"]):
                confirm_btn = gr.Button("✅ 确认启动流水线", variant="primary", visible=False, size="sm")

            msg.submit(fn=_chat_wrapper, inputs=[msg, chat_state], outputs=[msg, chatbot, chat_state, gr.HTML(), confirm_btn])
            send_btn.click(fn=_chat_wrapper, inputs=[msg, chat_state], outputs=[msg, chatbot, chat_state, gr.HTML(), confirm_btn])
            confirm_btn.click(fn=_on_confirm, inputs=[chat_state], outputs=[chatbot, chat_state, confirm_btn])

            stop_btn = gr.Button("中止流水线", variant="stop", size="sm", visible=False)
            clean_btn = gr.Button("清理产物并中止", variant="secondary", size="sm", visible=False)
            keep_btn = gr.Button("仅中止(保留产物)", variant="secondary", size="sm", visible=False)

            def _check_then_ask():
                running = _pipeline_running()
                if not running:
                    return (gr.update(value="当前没有运行中的流水线", visible=False),
                            gr.update(visible=False), gr.update(visible=False))
                return (gr.update(value="流水线运行中，选择操作："),
                        gr.update(visible=True), gr.update(visible=True))

            def _refresh_stop(): return gr.update(visible=_pipeline_running())
            gr.Timer(3).tick(fn=_refresh_stop, outputs=[stop_btn])
            stop_btn.click(fn=_check_then_ask, outputs=[stop_btn, clean_btn, keep_btn])
            clean_btn.click(fn=_stop_pipeline_clean, outputs=[stop_btn, clean_btn, keep_btn])
            keep_btn.click(fn=_stop_pipeline_keep, outputs=[stop_btn, clean_btn, keep_btn])

app.queue()
app.launch(server_name="0.0.0.0", server_port=7860, share=False, css="html{font-size:14px} footer{display:none!important} .center-row{justify-content:center}")
