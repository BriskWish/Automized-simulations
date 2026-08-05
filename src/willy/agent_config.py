"""
agent_config.py — Layer 0 Config Agent: NL→config 解析、对话管理、方案总结。

app.py 只保留 UI 布局，实际逻辑统一在此。
"""

import copy
import hashlib
import json
import secrets
import shutil
import signal
import subprocess
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from willy._paths import get_project_root
from willy.llm_config import DEFAULT_LLM_MODEL, LLMConfigError, configured_llm_client
from willy.workflow_config import _available_residues, validate_config, apply_config
from willy.toolist_global import TOOLS, handle_tool_call
from willy.pipeline_launch import (
    PipelineLockConflict,
    cleanup_finished_launch,
    reserve_pipeline_launch,
    write_startup_audit,
)
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
  "md": {{
    "schema_version": 2,
    "dt": 0.001,
    "ref_p": 1.01325,
    "eq": {{"high_temperature": 500, "transition_temperature": 400, "target_temperature": 298, "segments_ns": {{"heat": 2, "hold_high": 1, "cool_transition": 2, "hold_transition": 1, "cool_target": 2, "hold_target": 2}}}},
    "prod": {{"duration_ns": 10, "temperature": 298}},
    "outputs": {{"trr": false}}
  }},
  "topology": {{"backend": "sobtop", "force_field": "gaff_uff"}},
  "box": {{"box_size": null, "target_mass_density_g_cm3": 1.5, "tolerance": 2.0}},
  "defaults": {{ "mem": "5GB", "nproc": 8 }}
}}
```
- backend: 量子化学后端，默认 g16。用户说"用 ORCA""orca" 时设为 orca。调用 tools_set_backend_quantum 工具。
- molecules/residues 的 key 必须用 struct/ 下的精确文件名: {molecules}。用户写 Li+/Li⁺/锂离子 都映射到 Li，NO3-/NO₃⁻/硝酸根 都映射到 NO3
- md 必须使用 schema_version=2；EQ 采用六段退火，PROD 只接受独立 duration_ns，PROD 温度必须等于 EQ target_temperature
- 用户明确要求“额外输出全精度 TRR 轨迹”、"输出 TRR"或同义表述时，设 md.outputs.trr=true；未明确要求时保持 false
- topology 默认使用 {{"backend":"sobtop","force_field":"gaff_uff"}}；用户明确要求 OPLS-AA/OPLSAA 时，设为 {{"backend":"oplsaa","force_field":"oplsaa"}}
- 用户未指定盒边长或初始密度时，设置 `box.target_mass_density_g_cm3=1.5`；方案说明必须写“初始体积将由使用默认1.5g/cm3的密度猜测”。实际边长由建盒步骤从当前 `.itp` 的原子质量计算。用户明确指定边长时用 `box.box_size`，明确指定初始密度时用 `box.target_mass_density_g_cm3`。
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

# ── OpenAI-compatible LLM ──
_DS = None
_LLM_SETTINGS = None
_LLM_CONFIG_ERROR = ""
try:
    _DS, _LLM_SETTINGS = configured_llm_client(ROOT)
except LLMConfigError as exc:
    # The UI surfaces a concise configuration error. Never include credentials.
    _LLM_CONFIG_ERROR = str(exc)

LAUNCH_CONFIRMATIONS = frozenset({
    "好的", "确认", "确认运行", "确认启动", "跑吧", "执行", "开始", "开始运行",
    "开始启动", "运行", "启动", "ok", "yes", "run", "start", "行", "可以",
    "没问题", "就这样", "go",
})
_HIDE_BTN = {"visible": False}
_PENDING_LAUNCH_PLAN_VERSION = 1


@dataclass(frozen=True)
class PipelineLaunchReceipt:
    """The public result of one UI launch request."""

    message: str
    run_id: str | None
    state: str


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
        eq = r.get("eq", {}) if isinstance(r.get("eq", {}), dict) else {}
        return ("⚙️", f"默认: T={eq.get('target_temperature','?')}K, dt={r.get('dt','?')}ps")
    elif tool_name == "tools_get_box_density":
        return ("📐", f"默认初始质量密度: {r.get('target_mass_density_g_cm3','?')} g/cm3")
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


def start_pipeline(config: Mapping[str, object] | None) -> PipelineLaunchReceipt:
    """Start one pipeline from the session-bound, confirmed configuration."""
    if not isinstance(config, Mapping):
        write_startup_audit(ROOT, "failed")
        return PipelineLaunchReceipt("启动失败，请先生成有效的模拟方案。", None, "failed")
    launch_config = copy.deepcopy(dict(config))
    try:
        issues = validate_config(launch_config)
    except Exception:
        write_startup_audit(ROOT, "failed")
        return PipelineLaunchReceipt("启动失败，请检查模拟方案。", None, "failed")
    if issues:
        write_startup_audit(ROOT, "failed")
        return PipelineLaunchReceipt("启动失败，请检查模拟方案。", None, "failed")

    try:
        reservation = reserve_pipeline_launch(ROOT)
    except PipelineLockConflict as exc:
        write_startup_audit(ROOT, "lock_conflict")
        return PipelineLaunchReceipt("已有任务运行，未启动第二个子进程。", exc.run_id, "lock_conflict")

    try:
        apply_config(launch_config)
        backend = launch_config.get("backend", "g16")
        proc = subprocess.Popen(
            [
                "python3", "run_pipeline.py", backend,
                "--run-dir", str(reservation.run_dir),
                "--lock-fd", str(reservation.fd),
                "--launch-token", reservation.token,
            ],
            cwd=str(ROOT),
            start_new_session=True,
            pass_fds=(reservation.fd,),
        )
        reservation.mark_runner_started(proc.pid)
        (ROOT / ".pipeline.pid").write_text(str(proc.pid))
        reservation.detach_parent()
    except Exception:
        write_startup_audit(ROOT, "failed")
        reservation.release()
        return PipelineLaunchReceipt("启动失败，请检查配置后重试。", None, "failed")

    def _wait_then_cleanup():
        proc.wait()
        pid_file = ROOT / ".pipeline.pid"
        try:
            if pid_file.read_text().strip() == str(proc.pid):
                pid_file.unlink(missing_ok=True)
        except OSError:
            pass
        cleanup_finished_launch(ROOT, reservation.run_id, reservation.token)

    threading.Thread(target=_wait_then_cleanup, daemon=True).start()
    return PipelineLaunchReceipt(
        f"流水线已启动。\n\n运行：{reservation.run_id}\n\n后端：{backend}",
        reservation.run_id,
        "started",
    )


def launch_pipeline(config: Mapping[str, object] | None = None) -> str:
    """Text-only launch API requiring the caller's explicit plan snapshot."""
    return start_pipeline(config).message


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
        model=_LLM_SETTINGS.model if _LLM_SETTINGS else DEFAULT_LLM_MODEL,
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
    box_config = cfg.get("box", {})
    box_config = box_config if isinstance(box_config, dict) else {}
    explicit_box = box_config.get("box_size")
    target_density = box_config.get("target_mass_density_g_cm3")
    legacy_density = box_config.get("packing_number_density_nm3")
    if explicit_box is not None:
        try:
            box_note = f"盒子为正方体，指定边长 {float(explicit_box) / 10.0:.3f} nm"
        except (TypeError, ValueError):
            box_note = "盒子边长将在建盒步骤校验"
    elif target_density is not None:
        try:
            density_value = float(target_density)
            if density_value == 1.5:
                box_note = "初始体积将由使用默认1.5g/cm3的密度猜测；实际边长将在建盒步骤由拓扑质量计算"
            else:
                box_note = f"初始体积将按目标质量密度 {density_value:g} g/cm3 猜测；实际边长将在建盒步骤由拓扑质量计算"
        except (TypeError, ValueError):
            box_note = "初始体积将在建盒步骤由拓扑质量计算"
    elif legacy_density is not None:
        box_note = f"初始体积将按历史分子数密度 {legacy_density} 分子/nm3 估算"
    else:
        box_note = "初始体积将由使用默认1.5g/cm3的密度猜测；实际边长将在建盒步骤由拓扑质量计算"
    eq = md.get("eq", {}) if isinstance(md.get("eq", {}), dict) else {}
    prod = md.get("prod", {}) if isinstance(md.get("prod", {}), dict) else {}
    segments = eq.get("segments_ns", {}) if isinstance(eq.get("segments_ns", {}), dict) else {}
    t = eq.get("target_temperature", "?")
    eq_ns = sum(float(value) for value in segments.values()) if segments else "?"
    prod_ns = prod.get("duration_ns", "?")
    trr_enabled = bool(md.get("outputs", {}).get("trr", False))
    output_note = " | 额外输出全精度 TRR 轨迹" if trr_enabled else ""
    topology = cfg.get("topology", {})
    topology = topology if isinstance(topology, dict) else {}
    force_field = "OPLS-AA" if topology.get("force_field") == "oplsaa" else "GAFF/UFF"
    warnings = cfg.get("warnings", [])
    warn_lines = ""
    for w in warnings:
        t2 = w.get("type","")
        if t2 == "charge_imbalance": warn_lines += "\n⚠️ **电荷警告**: 体系不呈电中性"
        elif t2 == "compute_heavy": warn_lines += f"\n⚠️ **算力警告**: 共 {total} 个分子, 远超常规 (推荐 <10000)"
    return f"""**模拟方案确认**

组成: {', '.join(parts)}
共 {total} 个分子/残基/基团, {box_note}
平衡温度 {t}K | 退火时长 {eq_ns} ns | 产出时长 {prod_ns} ns | 力场 {force_field}{output_note}{warn_lines}

下一步将开始结构优化。

确认无误后回复“运行”“开始运行”或“确认运行”即可开始。"""


def _normalized_confirmation(message: str) -> str:
    """Normalize a short launch reply without accepting arbitrary prose."""
    return "".join(message.casefold().split()).strip("，。！？!?、,.")


def _fingerprint_payload(value: object) -> str:
    """Return a stable fingerprint for session-bound plan evidence."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def create_pending_launch_plan(config: Mapping[str, object], summary: str) -> dict[str, object]:
    """Freeze one generated configuration for exactly one browser session."""
    snapshot = copy.deepcopy(dict(config))
    return {
        "version": _PENDING_LAUNCH_PLAN_VERSION,
        "plan_id": secrets.token_urlsafe(18),
        "config": snapshot,
        "config_fingerprint": _fingerprint_payload(snapshot),
        "summary_fingerprint": _fingerprint_payload(summary),
    }


def _pending_launch_config(
    pending_plan: Mapping[str, object] | None,
    history: list[dict] | None,
) -> dict[str, object] | None:
    """Return a plan only when it is bound to the latest session summary."""
    if not isinstance(pending_plan, Mapping) or not history:
        return None
    if pending_plan.get("version") != _PENDING_LAUNCH_PLAN_VERSION:
        return None
    config = pending_plan.get("config")
    summary_fingerprint = pending_plan.get("summary_fingerprint")
    config_fingerprint = pending_plan.get("config_fingerprint")
    latest = history[-1]
    if not isinstance(config, Mapping) or not isinstance(latest, Mapping):
        return None
    summary = latest.get("content")
    if (
        latest.get("role") != "assistant"
        or not isinstance(summary, str)
        or "**模拟方案确认**" not in summary
        or not isinstance(summary_fingerprint, str)
        or not isinstance(config_fingerprint, str)
    ):
        return None
    try:
        if not secrets.compare_digest(summary_fingerprint, _fingerprint_payload(summary)):
            return None
        if not secrets.compare_digest(config_fingerprint, _fingerprint_payload(config)):
            return None
    except (TypeError, ValueError):
        return None
    return copy.deepcopy(dict(config))


def _is_launch_confirmation(message: str) -> bool:
    return _normalized_confirmation(message) in LAUNCH_CONFIRMATIONS


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

def chat(message, history, pending_plan: Mapping[str, object] | None = None):
    """Generate or confirm a plan without sharing it across browser sessions."""

    def _emit(h, plan):
        return "", h, h, plan, "", _HIDE_BTN

    if not message.strip():
        yield _emit(history, pending_plan)
        return

    user_h = list(history) + [{"role":"user","content":message}]

    if _is_launch_confirmation(message):
        launch_config = _pending_launch_config(pending_plan, history)
        if launch_config is None:
            h = user_h + [{
                "role": "assistant",
                "content": "当前没有待确认的模拟方案，请先描述模拟需求。",
            }]
            yield _emit(h, None)
            return
        try:
            receipt = start_pipeline(launch_config)
            launch_message = receipt.message
        except Exception:
            receipt = PipelineLaunchReceipt("流水线未能启动，请检查模拟方案后重试。", None, "failed")
            launch_message = receipt.message
        h = user_h + [{"role": "assistant", "content": launch_message}]
        yield _emit(h, None if receipt.state == "started" else pending_plan)
        return

    # Any new request invalidates the previous session proposal before the LLM
    # is called. A failed generation must never make an older plan confirmable.
    pending_plan = None
    yield "", user_h, user_h, pending_plan, "", _HIDE_BTN

    av = list(_available_residues().keys())
    if not av:
        h = list(user_h) + [{"role":"assistant","content":"struct/ 下暂无分子文件。"}]
        yield _emit(h, pending_plan); return
    if not _DS:
        reason = "LLM 服务未配置。"
        if _LLM_CONFIG_ERROR:
            reason = "LLM 服务配置无效，请检查配置页面。"
        h = list(user_h) + [{"role":"assistant","content":reason}]
        yield _emit(h, pending_plan); return

    # thinking
    progress_lines = ["🔍 正在分析您的体系..."]
    thinking_h = list(user_h) + [{"role":"assistant","content": "\n".join(progress_lines)}]
    yield "", thinking_h, thinking_h, pending_plan, "", _HIDE_BTN

    # LLM call
    candidate_config = None
    for attempt in range(3):
        try:
            msgs = [{"role":"system","content":get_system_prompt()},
                    {"role":"user","content":message}]
            for _ in range(5):
                r = _DS.chat.completions.create(
                    model=_LLM_SETTINGS.model if _LLM_SETTINGS else DEFAULT_LLM_MODEL, messages=msgs,
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
                        yield "", progress_h, progress_h, pending_plan, "", _HIDE_BTN
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
                            yield _emit(h, pending_plan); return
                        else:
                            avail = ", ".join(err.get("available",[]))
                            h = list(user_h)+[{"role":"assistant","content":f"❌ {detail}\n\n可用: {avail}"}]
                            yield _emit(h, pending_plan); return
                    if isinstance(config_dict, dict) and config_dict.get("residues"):
                        candidate_config = config_dict
                        break
                    if attempt < 2:
                        print(f"[agent] ⚠ empty residues, retry {attempt+2}/3...")
                        break
            if candidate_config is not None:
                break
        except Exception as e:
            if attempt == 2:
                h = list(user_h)+[{"role":"assistant","content":f"❌ LLM 失败 (已重试3次): {e}"}]
                yield _emit(h, pending_plan); return
    if candidate_config is None:
        h = list(user_h)+[{"role":"assistant","content":"❌ LLM 多次返回异常，请重新输入。"}]
        yield _emit(h, pending_plan); return

    progress_lines.append("📋 配置方案已生成")
    progress_h = list(user_h) + [{"role":"assistant", "content": "\n".join(progress_lines)}]
    yield "", progress_h, progress_h, pending_plan, "", _HIDE_BTN

    final_msg = summarize(candidate_config)
    pending_plan = create_pending_launch_plan(candidate_config, final_msg)
    h = list(user_h)+[{"role":"assistant","content": final_msg}]
    yield _emit(h, pending_plan)
