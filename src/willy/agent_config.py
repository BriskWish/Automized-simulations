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
from willy.remote_registry import RemoteRegistryError, parse_execution_md
from willy.toolist_global import TOOLS, handle_tool_call
from willy.quantum.input_audit import (
    QuantumInputAuditError,
    apply_audited_quantum_properties,
    audit_config_quantum_inputs,
    quantum_input_contract_issues,
)
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
- "用 ORCA"/"用 G16"/"用 G09"/"换高斯" → tools_set_backend_quantum
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
  "molecules": {{ "XXX": {{"charge": "<本次输入审计返回的整数>", "spin": "<本次输入审计返回的正整数>", "basis": "b3lyp/6-311+g(d,p)", "solvent": "acetone", "mem": "", "nproc": null}} }},
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
  "box": {{"box_size": null, "target_mass_density_g_cm3": 0.7, "tolerance": 2.0}},
  "defaults": {{ "mem": "5GB", "nproc": 8 }}
}}
```
- backend: 量子化学后端，默认 g16。用户说"用 G09""gaussian09" 时设为 g09；说"用 ORCA""orca" 时设为 orca。调用 tools_set_backend_quantum 工具。
- molecules/residues 的 key 必须用 struct/ 下的精确文件名: {molecules}。用户写 Li+/Li⁺/锂离子 都映射到 Li，NO3-/NO₃⁻/硝酸根 都映射到 NO3
- **电荷和自旋不是默认值，也不能从知识库猜测。** 在输出任何 config 前，必须调用一次 `tools_inspect_quantum_inputs`，传入最终 backend 和最终所有 `name/count`。g16/g09 只接受同名 `.gjf`，orca 只接受同名 `.inp`；工具返回的每个 `charge` 和 `spin` 是唯一可写入 JSON 的数值。示例中的尖括号仅说明来源，实际 JSON 必须填工具返回的整数。
- 审计返回 `ok=false` 时返回阻塞 Error，不得输出可确认 config。`charge_balance=imbalanced` 时，除非用户已明确要求并在 JSON 中写入 `ion_compensation` 或 `non_neutral_confirmed=true`，否则返回 `charge_imbalance` Error，说明净电荷和缺失的配平信息；绝不能把任意分子改写为中性来绕过。
- md 必须使用 schema_version=2；EQ 采用六段退火，PROD 只接受独立 duration_ns，PROD 温度必须等于 EQ target_temperature
- 用户明确要求“额外输出全精度 TRR 轨迹”、"输出 TRR"或同义表述时，设 md.outputs.trr=true；未明确要求时保持 false
- topology 默认使用 {{"backend":"sobtop","force_field":"gaff_uff"}}；用户明确要求 OPLS-AA/OPLSAA 时，设为 {{"backend":"oplsaa","force_field":"oplsaa"}}
- 用户未指定盒边长或初始密度时，设置 `box.target_mass_density_g_cm3=0.7`；方案说明必须写“初始体积将由使用默认0.7g/cm3的密度猜测”。实际边长由建盒步骤从当前 `.itp` 的原子质量计算。用户明确指定边长时用 `box.box_size`，明确指定初始密度时用 `box.target_mass_density_g_cm3`。
- 用户明确指定“使用 N 核/线程”时，将 N 写入 `defaults.nproc`；未指定时保持默认 8。只有明确要求某个分子单独使用不同核数时，才在该分子的 `nproc` 写覆盖值；该默认值同时控制本地 GROMACS 的 `mdrun -nt`，分子级覆盖控制该分子的量子结构优化和单点。
- 未指定的参数用 tools_lookup_md_defaults 获取默认值
- 化合物用 tools_resolve_compound 拆分后再查电荷
- **"各"分配**: "A和B各N个"→A=N,B=N; "A、B各N"→都N; 多个"各"依次分配
- **"不加盐""纯溶剂"**: 盐=阴阳离子对, 不加盐=residues只含溶剂分子

### 查询顺序
1. 提取所有分子名 → 逐个 tools_lookup_molecule（仅用于名称映射，返回的电荷不具权威性）
2. 未找到 → tools_refresh_structs → 重试 (最多 2 轮)
3. 仍失败 → 返回 Error `{{"error":{{"type":"invalid_molecule",...}}}}`
4. 全部存在 → tools_resolve_compound (如有) → tools_lookup_md_defaults (1次) → 选定 backend 后调用 tools_inspect_quantum_inputs → 以其 charge/spin 和 net_charge 输出 JSON

## 错误/警告
**Error** (阻塞): `invalid_molecule`, `invalid_value`, `ambiguous`, `invalid_quantum_input`, `charge_imbalance`
**Warning** (非阻塞): `compute_heavy` (>10000 分子)

### 完整输出
```json
{{"error": null, "warnings": [], "backend": "g16", "molecules": {{...}}, "residues": {{...}}, "md": {{...}}, "defaults": {{...}}}}
```

## Few-shot
**Normal**: Li 100, TFSI 100, FEC 300 → 先调用 tools_inspect_quantum_inputs(g16, 全部组分) → 将返回的电荷/自旋写入 {{"error":null,...}}。
**G09**: 用 Gaussian 09 算 Li 50, TFSI 50 → 先调 tools_set_backend_quantum("g09")，再调用 tools_inspect_quantum_inputs(g09, 全部组分)；缺少 `.gjf` 时返回错误，不得回退读取 `.inp`。
**ORCA**: 用 ORCA 算 Li 50, TFSI 50, 350K → 先调 tools_set_backend_quantum("orca")，再调用 tools_inspect_quantum_inputs(orca, 全部组分)；缺少 `.inp` 时返回错误，不得回退读取 `.gjf`。
**Ambiguous**: 锂盐100 溶剂200 → {{"error":{{"type":"ambiguous","detail":"锂盐和溶剂未指定具体分子"}},"available":["LiTFSI","LiPF6","EC",...]}}
**Warning**: Li 20000, TFSI 20000 → {{"error":null,"warnings":[{{"type":"compute_heavy","detail":"Total 40000 molecules"}}],...}}
**操作**: 跳过 TFSI → 调 tools_skip_molecule_global("TFSI") → "已跳过 TFSI"
**操作**: 用 ORCA → 调 tools_set_backend_quantum("orca") → "后端已切换为 orca"
**操作**: 用 G09 → 调 tools_set_backend_quantum("g09") → "后端已切换为 g09"

可用分子: {molecules}"""

ROOT = get_project_root()

# ── OpenAI-compatible LLM ──
_DS = None
_LLM_SETTINGS = None
_LLM_CONFIG_ERROR = ""


def refresh_llm_client() -> bool:
    """Re-resolve the selected provider after a local configuration action."""
    global _DS, _LLM_SETTINGS, _LLM_CONFIG_ERROR
    _DS = None
    _LLM_SETTINGS = None
    _LLM_CONFIG_ERROR = ""
    try:
        _DS, _LLM_SETTINGS = configured_llm_client(ROOT)
    except LLMConfigError as exc:
        # The UI surfaces a concise configuration error. Never include credentials.
        _LLM_CONFIG_ERROR = str(exc)
    return _DS is not None


refresh_llm_client()

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
        chg = r.get("charge")
        chg_str = (
            f"电荷{chg:+d}" if isinstance(chg, int) and chg
            else ("中性" if chg == 0 else "电荷待输入审计")
        )
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
    elif tool_name == "tools_inspect_quantum_inputs":
        if not r.get("ok"):
            return ("⚠️", f"量子输入审计未通过: {'；'.join(r.get('issues', ['未知问题'])[:2])}")
        component_count = len(r.get("components", []))
        balance = r.get("charge_balance")
        if balance == "balanced":
            return ("✅", f"量子输入审计通过: {component_count} 个组分，电荷平衡")
        net_charge = r.get("net_charge")
        net_label = f"{net_charge:+d}" if isinstance(net_charge, int) else "未知"
        return ("⚠️", f"量子输入审计: {component_count} 个组分，净电荷 {net_label}")
    elif tool_name == "tools_skip_molecule_global":
        return ("⏭", f"已跳过: {r.get('molecule', '?')}")
    return ("🔧", tool_name)


def _audit_covers_candidate(audit: object, config: Mapping[str, object]) -> bool:
    """Check that the model actually inspected the exact proposal components."""
    if not isinstance(audit, Mapping) or not audit.get("ok"):
        return False
    backend = str(config.get("backend", "g16")).strip().lower()
    if audit.get("backend") != backend:
        return False
    residues = config.get("residues")
    entries = audit.get("components")
    if not isinstance(residues, Mapping) or not isinstance(entries, list):
        return False
    inspected: dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, Mapping) or entry.get("status") != "valid":
            return False
        name, count = entry.get("name"), entry.get("count")
        if not isinstance(name, str) or isinstance(count, bool) or not isinstance(count, int):
            return False
        inspected[name] = count
    expected: dict[str, int] = {}
    try:
        for name, count in residues.items():
            if not isinstance(name, str) or isinstance(count, bool) or int(count) != count:
                return False
            expected[name] = int(count)
    except (TypeError, ValueError):
        return False
    return inspected == expected


def _audit_candidate_config(config: Mapping[str, object]) -> tuple[dict[str, object] | None, list[str]]:
    """Re-read raw inputs server-side and return a source-authoritative plan."""
    try:
        audit = audit_config_quantum_inputs(config, struct_dir=ROOT / "struct")
        if not audit.get("ok"):
            return None, [str(issue) for issue in audit.get("issues", [])[:16]]
        audited_config = apply_audited_quantum_properties(config, audit)
        return audited_config, quantum_input_contract_issues(audited_config, audit)
    except QuantumInputAuditError as exc:
        return None, [str(exc)]


def start_pipeline(config: Mapping[str, object] | None) -> PipelineLaunchReceipt:
    """Start one pipeline from the session-bound, confirmed configuration."""
    if not isinstance(config, Mapping):
        write_startup_audit(ROOT, "failed")
        return PipelineLaunchReceipt("启动失败，请先生成有效的模拟方案。", None, "failed")
    launch_config = copy.deepcopy(dict(config))
    try:
        audit = audit_config_quantum_inputs(launch_config, struct_dir=ROOT / "struct")
        input_issues = quantum_input_contract_issues(launch_config, audit)
        if input_issues:
            raise ValueError("; ".join(input_issues))
        launch_config = apply_audited_quantum_properties(launch_config, audit)
        issues = validate_config(launch_config)
    except Exception:
        write_startup_audit(ROOT, "failed")
        return PipelineLaunchReceipt("启动失败，请检查模拟方案。", None, "failed")
    if issues:
        write_startup_audit(ROOT, "failed")
        return PipelineLaunchReceipt("启动失败，请检查模拟方案。", None, "failed")

    execution_md = _execution_md_from_config(launch_config)
    if execution_md["backend"] != "local":
        # Phase 1 can safely select and freeze a remote profile, but no
        # adapter is allowed to fall through to local ``run_gmx()``.  Keep the
        # proposal confirmable once the explicitly gated SSH/Slurm executor is
        # installed and externally verified.
        write_startup_audit(ROOT, "remote_executor_unavailable")
        return PipelineLaunchReceipt(
            "远程 GROMACS 执行器尚未接入主流水线；未启动本机或远程进程。"
            "请改选本机执行，或等待 SSH/Slurm external smoke 通过后再确认。",
            None,
            "remote_executor_unavailable",
        )

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

def get_system_prompt(execution_facts: str = "") -> str:
    molecules = ", ".join(_available_residues().keys())
    prompt = CONFIG_AGENT_PROMPT.format(molecules=molecules)
    if execution_facts:
        prompt += (
            "\n\n## 已确认的 MD 执行边界\n"
            f"{execution_facts}\n"
            "该执行选择由服务端在通过输入审计后写入 config.execution.md。"
            "不得在 JSON 中新增、修改或猜测 execution、SSH、Slurm、主机、路径、"
            "认证或命令字段。"
        )
    return prompt


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
            if density_value == 0.7:
                box_note = "初始体积将由使用默认0.7g/cm3的密度猜测；实际边长将在建盒步骤由拓扑质量计算"
            else:
                box_note = f"初始体积将按目标质量密度 {density_value:g} g/cm3 猜测；实际边长将在建盒步骤由拓扑质量计算"
        except (TypeError, ValueError):
            box_note = "初始体积将在建盒步骤由拓扑质量计算"
    elif legacy_density is not None:
        box_note = f"初始体积将按历史分子数密度 {legacy_density} 分子/nm3 估算"
    else:
        box_note = "初始体积将由使用默认0.7g/cm3的密度猜测；实际边长将在建盒步骤由拓扑质量计算"
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
    execution = cfg.get("execution", {})
    execution = execution if isinstance(execution, Mapping) else {}
    execution_md = execution.get("md", {})
    execution_md = execution_md if isinstance(execution_md, Mapping) else {}
    execution_backend = execution_md.get("backend", "local")
    execution_profile = execution_md.get("profile")
    if execution_backend == "ssh" and isinstance(execution_profile, str):
        execution_note = f"SSH 远程 GROMACS（profile: {execution_profile}；前序步骤仍在本机）"
    elif execution_backend == "slurm" and isinstance(execution_profile, str):
        execution_note = f"Slurm 远程 GROMACS（profile: {execution_profile}；前序步骤仍在本机）"
    else:
        execution_note = "本机执行"
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
MD 执行：{execution_note}

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


def _latest_pending_summary(history: list[dict] | None) -> str | None:
    """Find the newest proposal card, allowing read-only replies after it."""
    if not history:
        return None
    for item in reversed(history):
        if not isinstance(item, Mapping):
            continue
        if item.get("role") != "assistant":
            continue
        content = item.get("content")
        if isinstance(content, str) and "**模拟方案确认**" in content:
            return content
    return None


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
    summary = _latest_pending_summary(history)
    if not isinstance(config, Mapping) or summary is None:
        return None
    if (
        not isinstance(summary_fingerprint, str)
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


# A pending proposal is an interactive editing session.  These markers are
# deliberately conservative: a plain question must not silently mutate a
# frozen plan, while an explicit field/value change is sent back through the
# normal audited config-generation path.
_CONFIG_REPLACEMENT_MARKERS = (
    "新的项目", "新项目", "新体系", "新的体系", "另一个体系", "另一个项目",
    "新的模拟", "新模拟", "新的需求", "新需求", "重新提交", "重新生成",
    "从头开始", "不要沿用", "不沿用上一轮",
)
_CONFIG_REVISION_MARKERS = (
    "改为", "改成", "调整为", "设置为", "设为", "换成", "换为", "改用",
    "修改", "调整", "增加", "减少", "加上", "再加", "删除", "去掉", "移除",
    "加入", "添加", "启用", "关闭", "采用",
    "密度", "温度", "时长", "时间步", "步长", "tau_p", "taup", "压浴",
    "热浴", "力场", "后端", "基组", "盒子", "边长", "trr", "g16", "g09", "orca",
    "opls", "gaff", "uff", "远程", "ssh", "slurm", "本机执行",
)
def _is_config_replacement_request(message: str) -> bool:
    text = message.casefold().strip()
    return any(marker.casefold() in text for marker in _CONFIG_REPLACEMENT_MARKERS)


def _is_config_revision_request(message: str) -> bool:
    text = message.casefold().strip()
    if not text or _is_launch_confirmation(text) or _is_config_replacement_request(text):
        return False
    question_leads = ("为什么", "为何", "怎么", "如何", "是否", "能否", "能不能", "可以吗", "可不可以")
    explicit_assignments = ("改为", "改成", "调整为", "设置为", "设为", "换成", "换为", "改用")
    if (
        not any(char.isdigit() for char in text)
        and any(text.startswith(prefix) for prefix in question_leads)
        and not any(marker in text for marker in explicit_assignments)
    ):
        return False
    if any(marker.casefold() in text for marker in _CONFIG_REVISION_MARKERS):
        return True
    # Short incremental inputs such as "Li 20" or "EC 300" are common in
    # the second turn; recognize them without requiring a Chinese verb.
    if any(char.isdigit() for char in text):
        available = (name.casefold() for name in _available_residues())
        if any(name and name in text for name in available):
            return True
    return False


def _classify_pending_config_intent(message: str) -> str:
    """Classify a second-turn message while a plan awaits confirmation."""
    if _is_launch_confirmation(message):
        return "confirm"
    if _is_config_replacement_request(message):
        return "replace"
    if _is_config_revision_request(message):
        return "revise"
    # Unmarked prose is treated as a question so it cannot mutate a plan by
    # accident; the LLM can explain what concrete change is required.
    return "question"


def _pending_config_context(
    pending_plan: Mapping[str, object] | None,
    history: list[dict] | None,
) -> dict[str, object] | None:
    """Return the validated proposal and bounded conversation context."""
    config = _pending_launch_config(pending_plan, history)
    if config is None or not history:
        return None
    recent: list[dict[str, str]] = []
    for item in history[-6:]:
        if not isinstance(item, Mapping):
            continue
        role, content = item.get("role"), item.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            recent.append({"role": role, "content": content[-4000:]})
    return {
        "config": config,
        "summary": _latest_pending_summary(history) or "",
        "recent_history": recent,
    }


def _pending_revision_message(message: str, context: Mapping[str, object]) -> str:
    """Bind an incremental edit to the exact proposal shown in the UI."""
    config = context.get("config", {})
    summary = context.get("summary", "")
    recent = context.get("recent_history", [])
    return (
        "这是对上一轮待确认模拟方案的增量修改请求。请把上一轮方案作为基线，"
        "只修改用户明确提出的字段，保留其余字段不变。输出前必须重新调用 "
        "tools_inspect_quantum_inputs，并按审计结果生成完整配置 JSON；不要启动流水线，"
        "不要调用 tools_set_backend_quantum 直接改写全局配置。\n\n"
        f"上一轮方案摘要：\n{summary}\n\n"
        "上一轮冻结配置（唯一基线，JSON）：\n"
        f"{json.dumps(config, ensure_ascii=False, sort_keys=True)}\n\n"
        "最近对话上下文：\n"
        f"{json.dumps(recent, ensure_ascii=False)}\n\n"
        f"本轮用户修改要求：\n{message}"
    )


def _answer_pending_config_question(message: str, context: Mapping[str, object]) -> str:
    """Answer a question without changing or invalidating the pending plan."""
    if not _DS:
        return "当前方案仍在等待确认；请回复“运行”确认，或直接说明需要修改的参数。"
    prompt = (
        "你是模拟配置方案助理。用户正在查看一份尚未确认的方案。"
        "只回答用户的问题，不生成新的 config JSON，不启动流水线，也不改变方案。"
        "回答应明确说明若要修改需要用户提出具体字段；不要编造未提供的文件或计算结果。\n\n"
        f"待确认方案摘要：\n{context.get('summary', '')}\n\n"
        "冻结配置：\n"
        f"{json.dumps(context.get('config', {}), ensure_ascii=False, sort_keys=True)}\n\n"
        f"用户问题：\n{message}"
    )
    try:
        answer = _llm(
            "你是严谨、简洁的 MD 配置解释助手。只回答问题，不执行操作。",
            prompt,
        )
        if isinstance(answer, str) and answer.strip():
            return answer.strip()
    except Exception:
        pass
    return "当前方案仍在等待确认；请回复“运行”确认，或直接说明需要修改的参数。"


def _execution_md_from_config(config: Mapping[str, object]) -> dict[str, object]:
    """Return the frozen execution choice, retaining the legacy local default."""
    execution = config.get("execution")
    if not isinstance(execution, Mapping):
        return {"backend": "local", "profile": None, "retain_remote_run": True}
    md = execution.get("md")
    if not isinstance(md, Mapping):
        return {"backend": "local", "profile": None, "retain_remote_run": True}
    return {
        "backend": md.get("backend", "local"),
        "profile": md.get("profile"),
        "retain_remote_run": md.get("retain_remote_run", True),
    }


def _trusted_execution_selection(
    execution_context: Mapping[str, object] | None,
) -> tuple[dict[str, object] | None, str, str | None]:
    """Resolve a browser selection through the local profile registry.

    The browser context is only a selection intent.  It cannot grant remote
    access: the backend/profile pair is parsed again against the private,
    permission-checked registry before it is allowed into a frozen plan.
    """
    if execution_context is None:
        return (
            {"backend": "local", "profile": None, "retain_remote_run": True},
            "MD 层使用本机 GROMACS；量子、拓扑、Packmol 与后处理保持本机。",
            None,
        )
    if not isinstance(execution_context, Mapping):
        return None, "", "远程执行选择格式无效，请在远程任务页重新选择。"
    backend_value = execution_context.get("execution_mode")
    profile_value = execution_context.get("execution_profile_id")
    backend = backend_value.strip().lower() if isinstance(backend_value, str) else ""
    if backend == "local":
        return (
            {"backend": "local", "profile": None, "retain_remote_run": True},
            "MD 层使用本机 GROMACS；量子、拓扑、Packmol 与后处理保持本机。",
            None,
        )
    if backend not in {"ssh", "slurm"} or not isinstance(profile_value, str):
        return None, "", "远程执行选择无效，请选择已登记的 SSH 或 Slurm 配置。"
    try:
        selection = parse_execution_md({
            "backend": backend,
            "profile": profile_value,
            "retain_remote_run": True,
        }).as_dict()
    except RemoteRegistryError as exc:
        return None, "", f"所选远程执行配置不可用：{exc}。请切换到本机或登记并修正 profile。"
    launcher = "SSH 直连" if backend == "ssh" else "Slurm 调度"
    facts = (
        f"MD 层的 GROMACS 将使用 {launcher}，profile ID 为 {selection['profile']}。"
        "量子、拓扑、Packmol 始终在本机；只能将受控 GROMACS 阶段交给该 profile。"
    )
    return selection, facts, None


def _attach_trusted_execution(
    config: Mapping[str, object],
    execution_md: Mapping[str, object],
) -> dict[str, object]:
    """Replace any model-emitted execution object with the verified selection."""
    snapshot = copy.deepcopy(dict(config))
    snapshot["execution"] = {"md": dict(execution_md)}
    return snapshot


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

def chat(
    message,
    history,
    pending_plan: Mapping[str, object] | None = None,
    execution_context: Mapping[str, object] | None = None,
):
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
        selected_execution, _execution_facts, selection_error = _trusted_execution_selection(
            execution_context,
        )
        if selection_error:
            h = user_h + [{"role": "assistant", "content": selection_error}]
            yield _emit(h, pending_plan)
            return
        if execution_context is not None and selected_execution is not None:
            if _execution_md_from_config(launch_config) != selected_execution:
                h = user_h + [{
                    "role": "assistant",
                    "content": (
                        "远程任务页的执行选择已在方案生成后变化。为避免将冻结方案提交到错误的"
                        "执行目标，请先按当前选择重新生成或修改方案，再确认运行。"
                    ),
                }]
                yield _emit(h, pending_plan)
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

    # Keep an awaiting proposal alive while the user asks about it or edits it.
    # The exact frozen config and the recent turns are bound into the next LLM
    # request, so a second turn is an incremental edit rather than a stateless
    # replacement. Explicitly starting a new project still invalidates it.
    pending_context = _pending_config_context(pending_plan, history)
    pending_intent = _classify_pending_config_intent(message)
    revision_mode = False
    llm_message = message
    if pending_context is not None and pending_intent != "replace":
        if pending_intent == "revise":
            revision_mode = True
            llm_message = _pending_revision_message(message, pending_context)
        else:
            answer = _answer_pending_config_question(message, pending_context)
            h = user_h + [{"role": "assistant", "content": answer}]
            yield _emit(h, pending_plan)
            return
    else:
        pending_plan = None

    selected_execution, execution_facts, selection_error = _trusted_execution_selection(
        execution_context,
    )
    if selection_error:
        h = user_h + [{"role": "assistant", "content": selection_error}]
        yield _emit(h, pending_plan)
        return

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
    progress_lines = [
        "🔁 正在基于上一轮方案修改..." if revision_mode else "🔍 正在分析您的体系..."
    ]
    thinking_h = list(user_h) + [{"role":"assistant","content": "\n".join(progress_lines)}]
    yield "", thinking_h, thinking_h, pending_plan, "", _HIDE_BTN

    # LLM call
    candidate_config = None
    for attempt in range(3):
        try:
            msgs = [{"role":"system","content":get_system_prompt(execution_facts)},
                    {"role":"user","content":llm_message}]
            input_audits: list[dict[str, object]] = []
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
                        if t.function.name == "tools_inspect_quantum_inputs":
                            try:
                                audit_result = json.loads(result_str)
                            except json.JSONDecodeError:
                                audit_result = None
                            if isinstance(audit_result, dict):
                                input_audits.append(audit_result)
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
                        elif etype in (
                            "invalid_value", "ambiguous", "invalid_quantum_input", "charge_imbalance",
                        ):
                            hint = err.get("suggestion","请修正后重新输入")
                            h = list(user_h)+[{"role":"assistant","content":f"⚠ {detail}\n\n{hint}"}]
                            yield _emit(h, pending_plan); return
                        else:
                            avail = ", ".join(err.get("available",[]))
                            h = list(user_h)+[{"role":"assistant","content":f"❌ {detail}\n\n可用: {avail}"}]
                            yield _emit(h, pending_plan); return
                    if isinstance(config_dict, dict) and config_dict.get("residues"):
                        if not any(_audit_covers_candidate(audit, config_dict) for audit in input_audits):
                            progress_lines.append("⚠️ 方案缺少对应原始输入审计，正在要求重新检查")
                            progress_h = list(user_h) + [{"role":"assistant", "content": "\n".join(progress_lines)}]
                            yield "", progress_h, progress_h, pending_plan, "", _HIDE_BTN
                            msgs.append({"role": "assistant", "content": msg.content or ""})
                            msgs.append({
                                "role": "user",
                                "content": (
                                    "不得直接输出方案。请先调用 tools_inspect_quantum_inputs，"
                                    "参数必须与最终 backend、分子名称和数目完全一致；"
                                    "再按审计结果重新输出 JSON。"
                                ),
                            })
                            continue
                        candidate_with_execution = _attach_trusted_execution(
                            config_dict,
                            selected_execution or {
                                "backend": "local",
                                "profile": None,
                                "retain_remote_run": True,
                            },
                        )
                        audited_config, input_issues = _audit_candidate_config(candidate_with_execution)
                        if audited_config is None or input_issues:
                            details = "；".join(input_issues or ["量子输入审计未通过"])
                            h = list(user_h) + [{
                                "role": "assistant",
                                "content": f"⚠️ 原始量子输入检查未通过：{details}\n\n请补齐或修正与所选后端对应的输入文件后重新提交。",
                            }]
                            yield _emit(h, pending_plan)
                            return
                        candidate_config = audited_config
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
