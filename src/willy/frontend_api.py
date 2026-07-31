"""
frontend_api.py
===============
前端专用 API —— 封装所有后端操作（路径、shell、文件系统），
app.py 只通过此模块访问后端，不再直接触碰路径/shell/文件系统。

用法:
  from willy.frontend_api import (
      stop_pipeline, is_pipeline_running,
      get_molecule_catalog, flatten_catalog, resolve_molecule,
      get_molecule_viewer_data, get_progress_markdown,
  )
"""

from __future__ import annotations
import json, os, shutil, subprocess, signal, time
from datetime import datetime, timezone
from pathlib import Path

from willy._paths import get_project_root
from willy.pipeline_state import PipelineStateMachine, State

ROOT = get_project_root()

# ============================================================
# 流水线控制
# ============================================================


def _reset_status_idle():
    """将 status.json 重置为 IDLE，进度面板归零。直接删除文件确保干净状态。"""
    sp = ROOT / "status.json"
    sp.unlink(missing_ok=True)


def _kill_process_group():
    """通过进程组 ID 终止流水线及其所有子进程（最可靠方式）。"""
    pid_file = ROOT / ".pipeline.pid"
    if not pid_file.exists():
        return
    try:
        pgid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        pid_file.unlink(missing_ok=True)
        return

    # 两阶段终止：SIGTERM → 等待 → SIGKILL
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except OSError:
            break  # 进程组已不存在
        if sig == signal.SIGTERM:
            time.sleep(0.5)
    pid_file.unlink(missing_ok=True)


def _kill_orphans():
    """兜底：按名称清扫可能漏网的外部子进程。

    覆盖流水线可能调用的所有外部程序：
    g16/orca（量子）、formchk（chk转换）、obabel（格式转换）、
    multiwfn（RESP电荷）、sobtop/ligpargen（拓扑）、packmol（盒子）、
    gmx/grompp/mdrun（MD模拟）、l502（Gaussian内部）。
    """
    TARGETS = [
        "run_pipeline\\.py", "g16", "orca", "formchk", "obabel",
        "multiwfn", "sobtop", "ligpargen", "packmol", "gmx",
        "grompp", "mdrun", "l502\\.exe",
    ]
    for name in TARGETS:
        try:
            subprocess.run(
                ["pkill", "-9", "-f", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            # pkill 不可用时，进程组终止仍是首选的停止路径。
            continue


def stop_pipeline(clean: bool = False) -> str:
    """中止流水线。clean=True 时额外清理产物文件。返回用户消息。"""
    _kill_process_group()   # 首选：进程组精确杀
    _kill_orphans()         # 兜底：按名称清扫
    _reset_status_idle()    # 进度面板归零
    (ROOT / ".pipeline.lock").unlink(missing_ok=True)
    if clean:
        for artifact in ("model.inp", "model.pdb"):
            (ROOT / artifact).unlink(missing_ok=True)
        return "已中止，产物已清理"
    return "已中止，产物已保留"


def is_pipeline_running() -> bool:
    """检查是否有活着的流水线进程。"""
    s = PipelineStateMachine.read()
    if s.state not in (State.RUNNING.value, State.RETRYING.value):
        return False

    # 首选：PID 文件检查
    pid_file = ROOT / ".pipeline.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)  # 信号 0 只检查进程是否存活
            return True
        except (OSError, ValueError):
            pid_file.unlink(missing_ok=True)

    # 回退：pgrep 精确匹配
    try:
        result = subprocess.run(
            ['pgrep', '-f', r'run_pipeline\.py'],
            capture_output=True, timeout=2)
        return result.returncode == 0
    except Exception:
        return False


def is_pipeline_alive() -> bool:
    """检查流水线进程是否存活（供进度面板使用）。"""
    pid_file = ROOT / ".pipeline.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            pid_file.unlink(missing_ok=True)
    try:
        result = subprocess.run(
            ['pgrep', '-f', r'run_pipeline\.py'],
            capture_output=True, timeout=2)
        return result.returncode == 0
    except Exception:
        return False


# ============================================================
# 分子目录
# ============================================================

# 支持的结构文件及其格式
_STRUCT_GLOBS = [
    ("topo", "*.pdb", "pdb"),
    ("struct", "*.mol2", "mol2"),
]


def _get_charge_map() -> dict[str, int]:
    """从 knowledge_tools registry + config.json 获取分子电荷。"""
    charges: dict[str, int] = {}
    try:
        from willy.toolist_global import _registry
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


def get_molecule_catalog() -> dict[str, dict[str, str]]:
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
        dir_path = ROOT / rel_dir
        if dir_path.exists():
            for p in dir_path.glob(pattern):
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


def flatten_catalog(catalog: dict[str, dict[str, str]]) -> list[str]:
    """展平为 Gradio Dropdown 可用的 choice 列表。

    Example: ['🟢 阳离子', '  Li', '  ...']
    """
    choices: list[str] = []
    for cat, mols in catalog.items():
        if not mols:
            continue
        choices.append(cat)
        for name in sorted(mols.keys()):
            choices.append(f"  {name}")
    return choices


def resolve_molecule(choice: str, catalog: dict[str, dict[str, str]]) -> tuple[str | None, str | None]:
    """解析下拉选项，返回 (category, molecule_name)。非分子行返回 (None, None)。"""
    stripped = choice.strip() if choice else ""
    for cat, mols in catalog.items():
        if stripped == cat.strip():
            return (None, None)
        for name in mols:
            if stripped == name or stripped == f"  {name}".strip():
                return (cat, name)
    return (None, None)


# ============================================================
# 3D 查看器
# ============================================================

# 空状态占位（SCAN 风格浅灰绿底）
_VIEWER_EMPTY = (
    '<div style="width:100%;height:400px;border-radius:18px;background:#eef2ef;'
    'display:flex;align-items:center;justify-content:center;'
    'color:#8a9a90;font-size:14px;border:2px dashed #d7ddda;flex-direction:column;gap:8px">'
    '<span style="font-size:28px">🔬</span>'
    '<span>从下方下拉菜单选择分子查看</span>'
    '</div>'
)


def get_molecule_viewer_data(mol_name: str) -> dict | None:
    """获取分子的 3D 查看器渲染数据。

    Returns:
        {"content": str, "format": "mol2"|"pdb", "atom_count": int, "file_path": str} | None
    """
    catalog = get_molecule_catalog()
    choices = flatten_catalog(catalog)

    if not mol_name or mol_name not in choices:
        return None

    _cat, resolved_name = resolve_molecule(mol_name, catalog)
    if resolved_name is None:
        return None

    # 找到文件路径
    file_path = None
    for cat_mols in catalog.values():
        if resolved_name in cat_mols:
            file_path = cat_mols[resolved_name]
            break

    if file_path is None or not Path(file_path).exists():
        return None

    mol_data = Path(file_path).read_text()
    ext = Path(file_path).suffix.lower()
    fmt = "mol2" if ext == ".mol2" else "pdb"
    atom_count = mol_data.count("ATOM") if mol_data else 0

    return {
        "content": mol_data,
        "format": fmt,
        "atom_count": atom_count,
        "file_path": file_path,
    }


def render_viewer_html(mol_choice: str = None) -> str:
    """根据下拉选项渲染 3D 分子查看器 HTML。"""
    viewer_data = get_molecule_viewer_data(mol_choice) if mol_choice else None

    if viewer_data is None:
        return _VIEWER_EMPTY

    mol_data = viewer_data["content"]
    fmt = viewer_data["format"]
    atom_count = viewer_data["atom_count"]
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
    # Resolve the molecule name from the choice
    catalog = get_molecule_catalog()
    _cat, mol_name = resolve_molecule(mol_choice, catalog) if mol_choice else (None, None)
    display_name = mol_name or ""

    return (
        f'<div style="background:#eef2ef;border-radius:18px;overflow:hidden">'
        f'<iframe srcdoc="{_h.escape(html)}" style="width:100%;height:340px;border:none" '
        f'sandbox="allow-scripts allow-same-origin"></iframe>'
        f'<div style="text-align:center;padding:6px 0 14px;font-size:12px;color:#56605b;'
        f'font-family:system-ui,sans-serif">{display_name}</div>'
        f'</div>'
    )


# ============================================================
# 进度面板
# ============================================================


def get_progress_markdown() -> str:
    """返回进度面板的 Markdown 字符串（含进程存活检查）。"""
    s = PipelineStateMachine.read()
    if s.state == State.IDLE.value:
        return ""

    pipeline_alive = is_pipeline_alive() if s.state in (State.RUNNING.value, State.RETRYING.value) else True

    labels = {1: "量子计算", 2: "结构转换", 3: "RESP 电荷", 4: "拓扑生成",
              5: "主拓扑", 6: "MD 参数", 7: "初始盒子", 8: "就绪"}
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
            if s.progress_detail:
                lines.append(f"  🧬 {s.progress_detail}")
            if s.state == State.RETRYING.value:
                lines.append(f"  🤖 {s.agent} Agent 修复中 ({s.retry_n}/{s.retry_max})")
                for act in s.actions[-3:]:
                    lines.append(f"    · {act}")
            if s.error:
                lines.append(f"  ⚠ {s.error[:120]}")
            continue
        lines.append(f"- ⬚ {labels.get(i, f'步骤{i}')}")

    if s.state == State.ESCALATED.value:
        lines.append("")
        lines.append("🆘 **自动修复失败，已升级**")
        esc = s.escalation
        if esc:
            lines.append(f"- 层: {esc.get('layer', '?')}")
            lines.append(f"- 已尝试 {esc.get('attempts_made', '?')} 次")
            if esc.get("recommendation"):
                lines.append(f"- 建议: {esc['recommendation'][:200]}")
    elif s.state == State.DONE.value:
        lines.append("")
        lines.append("✅ **全流程完成**")
    elif s.state == State.ABORTED.value:
        lines.append("")
        lines.append("⏹ **已中止**")

    return "\n".join(lines)


def refresh_molecule_choices() -> list[str]:
    """刷新分子下拉列表（上传新结构后调用）。"""
    return flatten_catalog(get_molecule_catalog())
