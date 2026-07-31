"""
agent_config.py — Layer 0 Config Agent: NL→config 解析、对话管理、方案总结。

app.py 只保留 UI 布局，实际逻辑统一在此。
"""

import json, os, math, shutil, signal, subprocess, threading
from pathlib import Path
from openai import OpenAI

from willy._paths import get_project_root
from willy.llm_config import _available_residues, validate_config, apply_config
from willy.toolist_global import TOOLS, handle_tool_call
# ── Config Agent System Prompt ──

CONFIG_AGENT_PROMPT = """你是 MD 模拟助手。根据用户意图选择模式：

**只能使用提供的 function calling 工具。不存在 bash/shell/命令行工具，禁止编造或调用不存在的工具。**

## 模式 A: 直接操作
用户意图是单次操作（非配置新体系）时，**只调工具，不生成 config**。
- "跳过 XXX"/"移除 XXX" → tools_skip_molecule_global
- "用 ORCA"/"换高斯" → tools_set_backend_quantum
- "列出分子"/"有什么分子" → tools_refresh_structs 或 tools_lookup_molecule
- "XXX 是什么"/"查一下 XXX" → tools_lookup_molecule
- "诊断 XXX" → tools_diagnose_error_config
- "验证配置" → tools_validate_config
操作完成后简短回复结果即可，**不要**输出 config JSON。

## 模式 B: 配置生成
用户描述了模拟体系（含分子名+数量）时，按以下流程：

### 输出格式（严格）
```json
{{
  "backend": "g16",
  "molecules": {{ "XXX": {{"charge": 0, "spin": 1, "basis": "b3lyp/6-311+g(d,p)", "solvent": "acetone", "mem": "", "nproc": null}} }},
  "residues": {{ "XXX": 100 }},
  "md": {{ "ref_t": 298, "prod_ns": 10, "eq_ns": 5, "dt": 0.001, "ref_p": 1.01325 }},
  "defaults": {{ "mem": "5GB", "nproc": 8 }}
}}
```
- backend: 量子化学后端，默认 g16。用户说"用 ORCA""orca" 时设为 orca。调用 tools_set_backend_quantum 工具。
- molecules/residues 的 key 必须用 struct/ 下的精确文件名: {molecules}。用户写 Li+/Li⁺/锂离子 都映射到 Li，NO3-/NO₃⁻/硝酸根 都映射到 NO3
- md 中温度用整数 K（常温/室温=298），时间 ns
- 未指定的参数用 tools_lookup_md_defaults 获取默认值
- 化合物用 tools_resolve_compound 拆分后再查电荷
- **"各"分配**: "A和B各N个"→A=N,B=N; "A、B各N"→都N; 多个"各"依次分配
- **"不加盐""纯溶剂"**: 盐=阴阳离子对, 不加盐=residues只含溶剂分子

### 查询顺序
1. 提取所有分子名 → 逐个 tools_lookup_molecule
2. 未找到 → tools_refresh_structs → 重试 (最多 2 轮)
3. 仍失败 → 返回 Error `{{"error":{{"type":"invalid_molecule",...}}}}`
4. 全部存在 → tools_resolve_compound (如有) → tools_lookup_md_defaults (1次) → 计算 total, net charge → 输出 JSON

## 错误/警告
**Error** (阻塞): `invalid_molecule`, `invalid_value`, `ambiguous`
**Warning** (非阻塞): `charge_imbalance` (净电荷≠0), `compute_heavy` (>10000 分子)

### 完整输出
```json
{{"error": null, "warnings": [], "backend": "g16", "molecules": {{...}}, "residues": {{...}}, "md": {{...}}, "defaults": {{...}}}}
```

## Few-shot
**Normal**: Li 100, TFSI 100, FEC 300 → {{"error":null,"warnings":[],"backend":"g16","residues":{{"Li":100,"TFSI":100,"FEC":300}},...}}
**ORCA**: 用 ORCA 算 Li 50, TFSI 50, 350K → 先调 tools_set_backend_quantum("orca") → {{"error":null,"warnings":[],"backend":"orca","residues":{{"Li":50,"TFSI":50}},...}}
**Ambiguous**: 锂盐100 溶剂200 → {{"error":{{"type":"ambiguous","detail":"锂盐和溶剂未指定具体分子"}},"available":["LiTFSI","LiPF6","EC",...]}}
**Warning**: Li 20000, TFSI 20000 → {{"error":null,"warnings":[{{"type":"compute_heavy","detail":"Total 40000 molecules"}}],...}}
**操作**: 跳过 TFSI → 调 tools_skip_molecule_global("TFSI") → "已跳过 TFSI"
**操作**: 用 ORCA → 调 tools_set_backend_quantum("orca") → "后端已切换为 orca"

可用分子: {molecules}"""

ROOT = get_project_root()

# ── DeepSeek ──
def _load_ds_key():
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text().split("\n"):
                if line.startswith("DEEPSEEK_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
    return key

_DS = None
_k = _load_ds_key()
if _k:
    _DS = OpenAI(api_key=_k, base_url="https://api.deepseek.com")

CONFIRM = ["好的","确认","跑吧","执行","开始","ok","yes","run","行","可以","没问题","就这样","go"]
_last_config = None
_HIDE_BTN = {"visible": False}
_SHOW_BTN = {"visible": True}


def _tool_summary(tool_name: str, result_str: str) -> tuple[str, str]:
    """将工具调用结果转成用户友好的 (图标, 摘要) 。"""
    try:
        r = json.loads(result_str)
    except Exception:
        return ("🔧", tool_name)
    if tool_name == "tools_lookup_molecule":
        if "error" in r:
            return ("❌", f"{r['error']}")
        name = r.get("name", "?")
        mol_type = r.get("type", "?")
        chg = r.get("charge", 0)
        chg_str = f"电荷{chg:+d}" if chg else "中性"
        return ("✅", f"{name}: {mol_type}, {chg_str}")
    elif tool_name == "tools_resolve_compound":
        if "error" in r:
            return ("❌", f"{r['error']}")
        return ("🧩", r.get("note", "已拆分"))
    elif tool_name == "tools_lookup_md_defaults":
        return ("⚙️", f"默认: T={r.get('ref_t','?')}K, dt={r.get('dt','?')}ps")
    elif tool_name == "tools_get_box_density":
        return ("📐", f"推荐密度: {r.get('density','?')} 分子/nm³")
    elif tool_name == "tools_lookup_basis_set":
        return ("🔬", f"推荐基组: {r.get('recommended','?')}")
    elif tool_name == "tools_refresh_structs":
        return ("🔄", f"已刷新, {r.get('count','?')} 个分子可用")
    elif tool_name == "tools_diagnose_error_config":
        return ("🔧", f"诊断: {r.get('symptom','?')}")
    elif tool_name == "tools_validate_config":
        if r.get("valid"):
            return ("✅", "配置验证通过")
        return ("⚠️", f"问题: {', '.join(r.get('issues', ['未知']))}")
    elif tool_name == "tools_set_backend_quantum":
        return ("⚙️", f"后端: {r.get('backend', '?')}")
    elif tool_name == "tools_skip_molecule_global":
        return ("⏭", f"已跳过: {r.get('molecule', '?')}")
    return ("🔧", tool_name)


def launch_pipeline() -> str:
    """启动流水线（验证 + 写入配置 + 后台启动）。返回用户消息。"""
    global _last_config
    if not _last_config:
        raise ValueError("没有可用的配置方案")
    validate_config(_last_config)
    apply_config(_last_config)
    backend = _last_config.get("backend", "g16")

    # 使用 subprocess.Popen 替代 os.system：启动新会话以追踪进程组
    pid_file = ROOT / ".pipeline.pid"
    proc = subprocess.Popen(
        f"cd {ROOT} && python3 run_pipeline.py {backend} 2>&1",
        shell=True, start_new_session=True,
    )
    pid_file.write_text(str(proc.pid))

    def _wait_then_cleanup():
        proc.wait()
        pid_file.unlink(missing_ok=True)

    threading.Thread(target=_wait_then_cleanup, daemon=True).start()
    return f"✅ 收到, 流水线已启动!\n\n后端: {backend}\n\n{summarize(_last_config)}"


# ============================================================
# System Prompt
# ============================================================

def get_system_prompt() -> str:
    molecules = ", ".join(_available_residues().keys())
    return CONFIG_AGENT_PROMPT.format(molecules=molecules)


# ============================================================
# LLM Helpers
# ============================================================

def _llm(system, user):
    r = _DS.chat.completions.create(
        model="deepseek-v4-pro",
        messages=[{"role":"system","content":system},{"role":"user","content":user}],
        temperature=0.1)
    return r.choices[0].message.content

def _j(raw):
    for tag in ("```json","```yaml","```"):
        if tag in raw:
            txt = raw.split(tag)[1].split("```")[0].strip()
            try: return json.loads(txt)
            except json.JSONDecodeError:
                try:
                    import yaml
                    return yaml.safe_load(txt)
                except Exception: pass
    try: return json.loads(raw)
    except json.JSONDecodeError:
        import yaml; return yaml.safe_load(raw)


# ============================================================
# 方案总结
# ============================================================

def summarize(cfg):
    r, md, mols = cfg.get("residues",{}), cfg.get("md",{}), cfg.get("molecules",{})
    parts = []
    for n, c in r.items():
        chg = mols.get(n,{}).get("charge",0)
        parts.append(f"{n} {c}" + (f"(电荷{chg:+d})" if chg else ""))
    total = sum(r.values())
    box = math.ceil((total/6.0)**(1/3)*10)
    t = md.get("ref_t","?"); eq_ns = md.get("eq_ns","?"); prod_ns = md.get("prod_ns","?")
    warnings = cfg.get("warnings", [])
    warn_lines = ""
    for w in warnings:
        t2 = w.get("type","")
        if t2 == "charge_imbalance": warn_lines += "\n⚠️ **电荷警告**: 体系不呈电中性"
        elif t2 == "compute_heavy": warn_lines += f"\n⚠️ **算力警告**: 共 {total} 个分子, 远超常规 (推荐 <10000)"
    box_nm = box / 10
    return f"""**模拟方案确认**

组成: {', '.join(parts)}
共 {total} 个分子/残基/基团, 盒子默认为正方体, 边长约 {box_nm:.1f} nm
平衡温度 {t}K | 退火时长 {eq_ns} ns | 产出时长 {prod_ns} ns{warn_lines}

下一步将开始结构优化。

确认无误后回复即可开始！"""


# ============================================================
# 上传
# ============================================================

def handle_upload(file, chat_history):
    if file is None:
        return None, chat_history, chat_history
    try:
        src = Path(file.name) if hasattr(file, 'name') else Path(str(file))
        if not src.exists():
            msg = f"上传失败：文件 {src.name} 不存在"
        else:
            dst = ROOT / "struct" / src.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            msg = f"已上传 {src.name} 到 struct/"
            from willy.toolist_global import _registry
            _registry._load()
    except Exception as e:
        msg = f"上传失败：{e}"
    h = list(chat_history) + [{"role": "user", "content": msg}]
    return None, h, h


# ============================================================
# 对话核心
# ============================================================

def chat(message, history):
    global _last_config

    def _emit(h):
        return "", h, h, "", _HIDE_BTN

    if not message.strip():
        return _emit(history)

    user_h = list(history) + [{"role":"user","content":message}]
    yield "", user_h, user_h, "", _HIDE_BTN

    av = list(_available_residues().keys())
    if not av:
        h = list(user_h) + [{"role":"assistant","content":"struct/ 下暂无分子文件。"}]
        yield _emit(h); return
    if not _DS:
        h = list(user_h) + [{"role":"assistant","content":"DeepSeek API 未配置。"}]
        yield _emit(h); return

    # confirm?
    is_short = len(message.strip()) <= 10
    if is_short and any(kw in message.lower().strip() for kw in CONFIRM):
        if _last_config:
            try:
                msg_text = launch_pipeline()
                h = list(history) + [{"role":"user","content":message},
                    {"role":"assistant","content":msg_text}]
                yield "", h, h, "", _HIDE_BTN; return
            except Exception as e:
                h = list(history) + [{"role":"user","content":message},
                    {"role":"assistant","content":f"执行失败: {e}"}]
                yield "", h, h, "", _HIDE_BTN; return
        else:
            h = list(history) + [{"role":"user","content":message},
                {"role":"assistant","content":"请先描述模拟需求, 生成方案后再确认。"}]
            yield "", h, h, "", _HIDE_BTN; return

    # thinking
    progress_lines = ["🔍 正在分析您的体系..."]
    thinking_h = list(user_h) + [{"role":"assistant","content": "\n".join(progress_lines)}]
    yield "", thinking_h, thinking_h, "", _HIDE_BTN

    # LLM call
    for attempt in range(3):
        try:
            msgs = [{"role":"system","content":get_system_prompt()},
                    {"role":"user","content":message}]
            for _ in range(5):
                r = _DS.chat.completions.create(
                    model="deepseek-v4-pro", messages=msgs,
                    tools=TOOLS, temperature=0.1)
                msg = r.choices[0].message
                if msg.tool_calls:
                    msgs.append({"role":"assistant","content":"",
                                 "tool_calls":[{"id":t.id,"type":"function",
                                 "function":{"name":t.function.name,"arguments":t.function.arguments}}
                                 for t in msg.tool_calls]})
                    for t in msg.tool_calls:
                        result_str = handle_tool_call(t.function.name,
                                         json.loads(t.function.arguments))
                        msgs.append({"role":"tool","tool_call_id":t.id,
                                     "content": result_str})
                        icon, line = _tool_summary(t.function.name, result_str)
                        progress_lines.append(f"{icon} {line}")
                        progress_h = list(user_h) + [{"role":"assistant", "content": "\n".join(progress_lines)}]
                        yield "", progress_h, progress_h, "", _HIDE_BTN
                else:
                    config_dict = _j(msg.content)
                    err = config_dict.get("error") if config_dict else None
                    if isinstance(err, str):
                        err = {"type": err.lower().replace(" ","_"), "detail": err}
                    if isinstance(err, dict) and err.get("type"):
                        etype = err["type"]
                        detail = err.get("detail","")
                        if etype == "invalid_molecule" and attempt < 2:
                            from willy.toolist_global import _registry
                            _registry._load()
                            print(f"[agent] 🔄 {detail}, retry...")
                            break
                        elif etype in ("invalid_value","ambiguous"):
                            hint = err.get("suggestion","请修正后重新输入")
                            h = list(user_h)+[{"role":"assistant","content":f"⚠ {detail}\n\n{hint}"}]
                            yield _emit(h); return
                        else:
                            avail = ", ".join(err.get("available",[]))
                            h = list(user_h)+[{"role":"assistant","content":f"❌ {detail}\n\n可用: {avail}"}]
                            yield _emit(h); return
                    if config_dict and config_dict.get("residues"):
                        _last_config = config_dict; break
                    if attempt < 2:
                        print(f"[agent] ⚠ empty residues, retry {attempt+2}/3...")
                        break
            if _last_config: break
        except Exception as e:
            if attempt == 2:
                h = list(user_h)+[{"role":"assistant","content":f"❌ LLM 失败 (已重试3次): {e}"}]
                yield _emit(h); return
    if not _last_config:
        h = list(user_h)+[{"role":"assistant","content":"❌ LLM 多次返回异常，请重新输入。"}]
        yield _emit(h); return

    progress_lines.append("📋 配置方案已生成")
    progress_h = list(user_h) + [{"role":"assistant", "content": "\n".join(progress_lines)}]
    yield "", progress_h, progress_h, "", _HIDE_BTN

    final_msg = summarize(_last_config) + "\n\n---\n👉 点击下方 **✅ 确认启动流水线** 按钮开始模拟"
    h = list(user_h)+[{"role":"assistant","content": final_msg}]
    yield "", h, h, "", _SHOW_BTN
