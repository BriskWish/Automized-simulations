"""
agent_config.py — Layer 0 Config Agent: NL→config 解析、对话管理、方案总结。

app.py 只保留 UI 布局，实际逻辑统一在此。
"""

import copy
import hashlib
import json
import re
import secrets
import signal
import subprocess
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from willy._paths import get_project_root
from willy.charge_scaling import DEFAULT_ION_CHARGE_SCALE, validate_ion_charge_scale
from willy.controlled_launch import LaunchCleanupError, handoff_controlled_process, spawn_gated_process
from willy.execution_resources import normalize_config_nproc
from willy.llm_config import DEFAULT_LLM_MODEL, LLMConfigError, configured_llm_client
from willy.workflow_config import _available_residues, validate_config, apply_config, molecule_solvent_settings
from willy.remote_registry import RemoteRegistryError, parse_execution_md
from willy.toolist_global import TOOLS, handle_tool_call
from willy.structure_uploads import StructureUploadError, normalize_uploaded_structure
from willy.simulation.protocol import (
    EQ_SEGMENT_NAMES,
    eq_annealing_points,
    merge_v2_defaults,
    require_valid_md_config,
)
from willy.quantum.input_audit import (
    QuantumInputAuditError,
    apply_audited_quantum_properties,
    audit_config_quantum_inputs,
    quantum_input_contract_issues,
)
from willy.quantum.smd_solvents import (
    GAS_SOLVENT, list_solvents, lookup_solvent, register_manual_solvent, resolve_exact,
)
from willy.prompt_contract import (
    PROMPT_CONTRACT_VERSION,
    build_contract_system_prompt,
    build_structured_context,
)
from willy.pipeline_launch import (
    PipelineLockConflict,
    cleanup_finished_launch,
    pipeline_command,
    reserve_pipeline_launch,
    write_startup_audit,
)
from willy.proposal_workspace import (
    formalize_plan_directory,
    rollback_formalized_plan,
)
# ── Config Agent System Prompt ──

CONFIG_AGENT_PROMPT = """你是 MD 模拟助手。生成可确认的模拟方案：

**只能使用提供的 function calling 工具。不存在 bash/shell/命令行工具，禁止编造或调用不存在的工具。**

## 配置生成
用户描述了模拟体系（含分子名+数量）时，按以下流程：

### 输出格式（严格）
```json
{{
  "backend": "g16",
  "ion_charge_scale": 1.0,
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
- backend: 量子化学后端，默认 g16。用户说"用 G09""gaussian09" 时设为 g09；说"用 ORCA""orca" 时设为 orca。后端只能写入候选 JSON，不能调用写入型工具。
- molecules/residues 的 key 必须用 struct/ 下的核心文件名: {molecules}。带电写法由注册表统一归一化，例如 Li+/Li⁺/锂离子 映射到 Li，上传的 Ca2+/Ca²⁺ 映射到 Ca，NO3-/NO₃⁻/硝酸根 映射到 NO3；最终 key 不含电荷符号。
- solvent 按 molecules.<name>.solvent 保存；用户只指定一个溶剂时，默认统一应用到本次选中的全部分子。先调用 tools_lookup_solvent 精确确认库条目，未确认不得编造名称。gas 表示不启用 SMD。ORCA 当前不支持 SMD，只能使用 gas。
- 溶剂选择在语义草案用 solvent_selection 表示：default 是所有选中分子的溶剂名，molecules 是按分子指定的覆盖映射。例如 {{"default":"Water","molecules":{{"Li":"gas"}}}}。未涉及溶剂时可省略；修改方案时保留未修改分子的溶剂。服务端绑定名称与 solvent_ref，优化和单点共同使用这两个字段，不得另造阶段级溶剂参数。
- 不得调用 tools_register_solvent 或自行写溶剂库；用户可明确发送“登记溶剂 MyMix epsilon=20 epsinf=1.8”，或仅发送“epsilon=20 epsinf=1.8”由受控代码登记 default_N。登记本身不修改待确认方案，之后使用登记名称生成/修改方案。人工 Generic 仅有 Eps/EpsInf，属于介电近似，不是完整 SMD 参数化，必须明确提示。
- **电荷和自旋不是默认值，也不能从知识库猜测。** 在输出任何完整候选 config 前，必须完成一次 `tools_inspect_quantum_inputs` 审计；g16/g09 只接受同名 `.gjf`，orca 只接受同名 `.inp`。工具返回的每个 `charge` 和 `spin` 是唯一可写入最终 JSON 的数值。语义草案阶段只能给出 backend 和 residues，服务端会在本轮审计上下文中固定其 backend/name/count 后进入强制审计阶段；这不是用户确认前的最终运行配置冻结。
- 审计返回 `ok=false` 时返回阻塞 Error，不得输出可确认 config。`charge_balance=imbalanced` 时，除非用户已明确要求并在 JSON 中写入 `ion_compensation` 或 `non_neutral_confirmed=true`，否则返回 `charge_imbalance` Error，说明净电荷和缺失的配平信息；绝不能把任意分子改写为中性来绕过。
- md 必须使用 schema_version=2；EQ 采用六段退火，PROD 只接受独立 duration_ns，PROD 温度必须等于 EQ target_temperature
- 用户明确要求“额外输出全精度 TRR 轨迹”、"输出 TRR"或同义表述时，设 md.outputs.trr=true；未明确要求时保持 false
- topology 默认使用 {{"backend":"sobtop","force_field":"gaff_uff"}}；用户明确要求 OPLS-AA/OPLSAA 时，设为 {{"backend":"oplsaa","force_field":"oplsaa"}}
- 顶层 ion_charge_scale 默认 1.00，仅接受 0.60..1.00（含边界）、百分之一精度的有限数值；0.8、0.80、0.800 等价。拒绝 bool、字符串、非有限数值、超范围或超过两位有效小数，绝不能截断、舍入或擅自替换用户的非法请求。未要求修改时保留上一方案的缩放因子。
- 该因子统一作用于所有净电荷非零组分的原子部分电荷，包括带电分子、基团和离子，不另分类型；中性组分保持不变。量子整数 charge 和 spin 必须原样保留，不参与缩放或电荷配平修改。非 sobtop 拓扑不消费 .chg，ion_charge_scale != 1.00 必须报 invalid_value，不得忽略或重复缩放。
- 确认摘要必须显示两位小数的缩放因子，并逐段说明六段 EQ 的顺序、NPT 系综、时长及温度起止；恒温段也必须显示起止温度（如 500K→500K）。
- 用户未指定盒边长或初始密度时，设置 `box.target_mass_density_g_cm3=0.7`；方案说明必须写“初始体积将由使用默认0.7g/cm3的密度猜测”。实际边长由建盒步骤从当前 `.itp` 的原子质量计算。用户明确指定边长时用 `box.box_size`，明确指定初始密度时用 `box.target_mass_density_g_cm3`。
- 用户明确指定“使用 N 核/线程”时，将 N 写入 `defaults.nproc`；未指定时保持默认上限 8。只有明确要求某个分子单独使用不同核数时，才在该分子的 `nproc` 写覆盖值；该默认值同时控制本地 GROMACS 的 `mdrun -nt`，分子级覆盖控制该分子的量子结构优化和单点。服务端会在用户确认启动时扫描本机 CPU 核数：未显式指定时写入 `min(8, CPU核数)`，显式值超出本机容量时写回本机核数、提示用户并继续运行。
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
**G09**: 用 Gaussian 09 算 Li 50, TFSI 50 → 在候选 JSON 中设 backend=g09，再调用 tools_inspect_quantum_inputs(g09, 全部组分)；缺少 `.gjf` 时返回错误，不得回退读取 `.inp`。
**ORCA**: 用 ORCA 算 Li 50, TFSI 50, 350K → 在候选 JSON 中设 backend=orca，再调用 tools_inspect_quantum_inputs(orca, 全部组分)；缺少 `.inp` 时返回错误，不得回退读取 `.gjf`。
**Ambiguous**: 锂盐100 溶剂200 → {{"error":{{"type":"ambiguous","detail":"锂盐和溶剂未指定具体分子"}},"available":["LiTFSI","LiPF6","EC",...]}}
**Warning**: Li 20000, TFSI 20000 → {{"error":null,"warnings":[{{"type":"compute_heavy","detail":"Total 40000 molecules"}}],...}}
**修改**: 移除 TFSI → 生成不含 TFSI 的新候选 JSON，等待用户确认。
**修改**: 用 ORCA → 生成 backend=orca 的新候选 JSON，等待用户确认。
**修改**: 用 G09 → 生成 backend=g09 的新候选 JSON，等待用户确认。

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
CONFIG_PROMPT_VERSION = "config-assistant-v2"
CONFIG_MAX_ATTEMPTS = 3
CONFIG_MAX_TOOL_ROUNDS = 5
CONFIG_LLM_TIMEOUT_S = 30.0
_PLAN_READ_ONLY_TOOL_NAMES = frozenset({
    "tools_lookup_molecule",
    "tools_resolve_compound",
    "tools_lookup_md_defaults",
    "tools_get_box_density",
    "tools_lookup_basis_set",
    "tools_lookup_solvent",
    "tools_refresh_structs",
    "tools_diagnose_error_config",
    "tools_validate_config",
    "tools_inspect_quantum_inputs",
})
QUANTUM_AUDIT_TOOL_NAME = "tools_inspect_quantum_inputs"
# The server accepts only this named call with the exact normalized arguments.
# Automatic selection keeps that contract while supporting providers that
# reject a fixed-function ``tool_choice`` request.
QUANTUM_AUDIT_TOOL_CHOICE = "auto"
_SEMANTIC_TOOL_NAMES = _PLAN_READ_ONLY_TOOL_NAMES - {QUANTUM_AUDIT_TOOL_NAME}
SEMANTIC_TOOLS = tuple(
    tool for tool in TOOLS
    if tool.get("function", {}).get("name") in _SEMANTIC_TOOL_NAMES
)
QUANTUM_AUDIT_TOOLS = tuple(
    tool for tool in TOOLS
    if tool.get("function", {}).get("name") == QUANTUM_AUDIT_TOOL_NAME
)
# Kept as a public compatibility alias for callers that previously imported the
# planning-tool collection.  The generation loop uses the stage-specific sets.
PLAN_TOOLS = SEMANTIC_TOOLS

# Framework/design questions share the proposal assistant's conversation, but
# are a separate read-only mode.  The classifier is only consulted for these
# explicit project-level markers so ordinary molecule/protocol requests keep
# the existing generation call budget and protocol unchanged.
_FRAMEWORK_DESIGN_MARKERS = (
    "本项目", "项目架构", "框架", "架构", "状态机", "manifest", "工具边界",
    "权限边界", "模块关系", "已实现能力", "规划事项", "设计文档", "产品能力",
    "数据契约", "源码结构",
)
_FRAMEWORK_SOURCE_WHITELIST = (
    ("README.md", "README"),
    ("docs/Willy.md", "Willy"),
    ("docs/status_api.md", "status_api"),
    ("docs/run_assistant.md", "run_assistant"),
    ("docs/document_registry.md", "document_registry"),
)
_FRAMEWORK_SOURCE_MAX_SECTION_CHARS = 1_200
_FRAMEWORK_SOURCE_MAX_SECTIONS = 6
_MOLECULE_CATALOG_LIST_MARKERS = (
    "当前有哪些分子", "有哪些分子", "有些什么分子", "可用分子", "分子列表",
    "列出分子库", "查看分子库", "显示分子库", "有哪些结构", "可用结构",
)
_MOLECULE_CATALOG_QUERY_PATTERNS = (
    re.compile(r"^\s*(?:查询|查找|搜索|找一下)\s*(?:分子库(?:中)?(?:的)?)?\s*(?P<name>[^，。！？?]+?)\s*$"),
    re.compile(r"^\s*(?:分子库(?:中|里)?(?:有|是否有)|有没有|是否有)\s*(?P<name>[^，。！？?]+?)(?:吗|么|嘛)?\s*$"),
    re.compile(r"^\s*(?P<name>[^，。！？?]+?)\s*(?:在|是否在)\s*分子库(?:中|里)?(?:有|吗)?\s*$"),
)


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
        issues = quantum_input_contract_issues(audited_config, audit)
        issues.extend(validate_config(audited_config))
        return audited_config, list(dict.fromkeys(issues))
    except QuantumInputAuditError as exc:
        return None, [str(exc)]


def start_pipeline(
    config: Mapping[str, object] | None,
    *,
    proposal_workspace_id: str | None = None,
) -> PipelineLaunchReceipt:
    """Freeze and start a session-bound configuration after explicit confirmation.

    This is the only proposal-assistant boundary that writes the active
    ``config.json``.  Validation is repeated here so a pending candidate can
    be edited freely without becoming a run snapshot before confirmation.
    """
    if not isinstance(config, Mapping):
        write_startup_audit(ROOT, "failed")
        return PipelineLaunchReceipt("启动失败，请先生成有效的模拟方案。", None, "failed")
    scale_issues = [issue for issue in validate_config(config) if "ion_charge_scale" in issue]
    if scale_issues:
        write_startup_audit(ROOT, "failed")
        return PipelineLaunchReceipt("启动失败：" + "；".join(scale_issues), None, "failed")
    resource_plan = normalize_config_nproc(config)
    launch_config = resource_plan.config
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
        # Phase 1 can safely select and bind a remote profile, but no
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

    formalized_plan = False
    try:
        if proposal_workspace_id is not None:
            formalize_plan_directory(
                ROOT,
                plan_id=proposal_workspace_id,
                run_dir=reservation.run_dir,
                expected_config=config,
            )
            formalized_plan = True
        # The pending candidate becomes the active run configuration only
        # after confirmation, repeat validation, and launch-lock reservation.
        apply_config(launch_config)
        backend = launch_config.get("backend", "g16")
        proc = spawn_gated_process(
            pipeline_command(
                ROOT, backend,
                "--run-dir", str(reservation.run_dir),
                "--lock-fd", str(reservation.fd),
                "--launch-token", reservation.token,
            ),
            cwd=ROOT, lock_fd=reservation.fd,
        )

        def _wait_then_cleanup():
            proc.wait()
            pid_file = ROOT / ".pipeline.pid"
            try:
                if pid_file.read_text().strip() == str(proc.pid):
                    pid_file.unlink(missing_ok=True)
            except OSError:
                pass
            cleanup_finished_launch(ROOT, reservation.run_id, reservation.token)

        handoff_controlled_process(proc, reservation, ROOT, lambda: threading.Thread(target=_wait_then_cleanup, daemon=True).start())
    except LaunchCleanupError:
        return PipelineLaunchReceipt("启动交接失败，子进程尚未退出；已保留启动锁，禁止重复启动。", reservation.run_id, "failed")
    except Exception:
        try:
            if formalized_plan and proposal_workspace_id is not None:
                rollback_formalized_plan(
                    ROOT,
                    plan_id=proposal_workspace_id,
                    run_dir=reservation.run_dir,
                )
        finally:
            reservation.release()
        try:
            write_startup_audit(ROOT, "failed")
        except OSError:
            pass
        if formalized_plan:
            return PipelineLaunchReceipt("启动失败，待确认方案已保留，可修正后重新确认。", None, "failed")
        return PipelineLaunchReceipt("启动失败，请检查配置后重试。", None, "failed")

    resource_notice = ""
    if resource_plan.warnings:
        resource_notice = "\n\n资源提示：" + "；".join(resource_plan.warnings)
    return PipelineLaunchReceipt(
        f"流水线已启动。\n\n运行：{reservation.run_id}\n\n后端：{backend}{resource_notice}",
        reservation.run_id,
        "started",
    )


def launch_pipeline(config: Mapping[str, object] | None = None) -> str:
    """Text-only launch API requiring the caller's explicit plan snapshot."""
    return start_pipeline(config).message


# ============================================================
# System Prompt
# ============================================================

def get_system_prompt(
    execution_facts: str = "",
    *,
    stage: str = "semantic_normalize",
) -> str:
    """Return the stage-specific system contract for configuration generation."""
    molecules = ", ".join(_available_residues().keys())
    domain_rules = CONFIG_AGENT_PROMPT.format(molecules=molecules)
    if execution_facts:
        domain_rules += (
            "\n\n已确认的 MD 执行边界由受控结构化上下文提供。"
            "不得在 JSON 中新增、修改或猜测 execution、SSH、Slurm、主机、路径、"
            "认证或命令字段。"
        )
    stage_rules = {
        "semantic_normalize": (
            "当前仅处于语义提取与服务端归一化前阶段。可以调用本轮提供的名称、"
            "默认值等只读工具，随后只能输出一个含 backend 和 residues 的语义草案。"
            "不得把草案称为已审计、可确认或可运行方案；服务端会独立发起强制量子输入审计。"
        ),
        "quantum_input_audit": (
            "当前仅处于原始量子输入审计阶段。必须发出唯一一个 tools_inspect_quantum_inputs "
            "调用，且参数必须逐字匹配服务端锁定的 backend 与 components；不得输出文本或 JSON。"
        ),
        "strict_json": (
            "当前处于审计后的严格 JSON 输出阶段。不得调用工具；必须使用受控上下文中的"
            "量子输入审计结果，且不得改变已审计 backend、residues、电荷或自旋，"
            "也不得改变服务端确定的 ion_charge_scale。"
        ),
    }.get(stage, "")
    return build_contract_system_prompt(
        assistant_name="Willy 方案助理",
        scope="将用户的模拟需求整理为可确认、可审计的工作流配置",
        domain_rules=(
            domain_rules
            + "\n\n方案生成与修改只能调用本轮提供的只读工具。"
            "后端是候选 JSON 的字段，不得调用写入型工具修改项目全局配置；"
            "跳过分子、启动流水线和修改已有运行不属于本助理的本轮权限。"
            + "\n\n" + stage_rules
        ),
    )


def _requested_ion_charge_scale(message: str) -> float | None:
    """Validate explicit scale literals before a model can reinterpret them."""
    label = (
        r"ion_charge_scale|(?:ion[\s_-]*)?charge[\s_-]*scal(?:e|ing)(?:\s+factor)?"
        r"|缩放(?:因子|系数|比例|倍率)?"
    )
    literal = (
        r'''(?:"[^"\n]*"|'[^'\n]*'|[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'''
        r"|[+-]?(?:nan|inf(?:inity)?)|true|false|null|none|\[[^\]]*\]|\{[^}]*\})"
    )
    connector = (
        r'''\s*(?:["'](?=\s*[:=：]))?\s*'''
        r"(?:(?:[:=：]|调整|设置|选择|指定|等于|改|设|选|为|到|至|成|取|用|按|乘以|是|by|to)\s*)*"
    )
    patterns = (
        rf"(?:{label}){connector}(?P<value>{literal})(?P<percent>\s*[%％])?",
        rf"(?:电荷|charges?)\s*(?:按|乘以|乘|×|by)\s*(?P<value>{literal})(?P<percent>\s*[%％])?",
        rf"(?P<value>{literal})(?P<percent>\s*[%％])?\s*倍\s*(?:的)?(?:部分|离子)?电荷",
    )
    requested: list[float] = []
    for pattern in patterns:
        for match in re.finditer(pattern, message, re.IGNORECASE):
            token = match.group("value")
            if re.match(r"[A-Za-z0-9_.%％/+*×-]", message[match.end():]):
                raise ValueError("ion_charge_scale 必须是单个数值，不能是表达式或非法数值")
            try:
                numeric = Decimal(token)
            except InvalidOperation:
                raise ValueError("ion_charge_scale 必须是有限数值，不接受 bool、字符串或其他类型") from None
            lower, upper = (Decimal("60"), Decimal("100")) if match.group("percent") else (Decimal("0.60"), Decimal("1.00"))
            if not numeric.is_finite() or not lower <= numeric <= upper:
                raise ValueError("ion_charge_scale 必须在 0.60..1.00（含边界），不允许截断越界")
            numerator, denominator = numeric.as_integer_ratio()
            if match.group("percent"):
                denominator *= 100
            if numerator * 100 % denominator:
                raise ValueError("ion_charge_scale 仅允许百分之一精度，不能超过两位有效小数")
            requested.append(validate_ion_charge_scale(numerator / denominator))
    if not requested and re.search(
        rf"(?:{label})\s*[\"']?\s*(?:[:=：]|调整|设置|选择|指定|等于|改|设|选|为|到|至|成|取|用)",
        message,
        re.IGNORECASE,
    ):
        raise ValueError("无法确定 ion_charge_scale，请明确提供 0.60..1.00 的数值")
    if re.search(r"(?:取消|关闭|禁用|不做)(?:离子|部分|原子)?电荷缩放|电荷不缩放", message):
        requested.append(DEFAULT_ION_CHARGE_SCALE)
    if len(set(requested)) > 1:
        raise ValueError("ion_charge_scale 存在多个不同请求值，请明确唯一的统一缩放因子")
    return requested[0] if requested else None


def _bind_ion_charge_scale(config: Mapping[str, object], expected: float) -> dict[str, object]:
    """Bind model output to the validated request, previous plan, or default."""
    scale = validate_ion_charge_scale(config.get("ion_charge_scale", expected))
    if scale != expected:
        raise ValueError(f"ion_charge_scale 必须保留本轮确定的 {expected:.2f}，模型不得擅自替换")
    candidate = copy.deepcopy(dict(config))
    candidate["ion_charge_scale"] = scale
    return candidate


_MANUAL_SMD_NOTICE = "手工 Generic 仅提供 Eps/EpsInf，属于介电近似；未补齐非电静力描述符，不是完整 SMD 参数化。"
_SMD_PARAMETER_LABELS = {
    "epsilon": "epsilon", "eps": "epsilon", "ε": "epsilon",
    "介电常数": "epsilon", "静态介电常数": "epsilon",
    "epsinf": "epsinf", "εinf": "epsinf", "ε_inf": "epsinf", "ε∞": "epsinf",
    "极限介电常数": "epsinf", "光学介电常数": "epsinf", "高频介电常数": "epsinf",
}
_SMD_PARAMETER_FIELD = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(label) for label in sorted(_SMD_PARAMETER_LABELS, key=len, reverse=True))
    + r")\s*(?:=|:|：|为|是)?\s*([^\s,，;；]+)", re.I,
)


def _solvent_catalog_reply(message: str) -> str | None:
    """Handle explicit catalog commands without granting a model write access."""
    text = message.strip().strip("。.!！")
    if re.fullmatch(r"(?:请)?(?:当前|现在)?\s*(?:查询|查看|列出|有哪些|有什么)?\s*(?:所有|全部|可用|已登记|支持的)?(?:SMD\s*)?(?:溶剂(?:库|列表)?|solvents?)\s*(?:有哪些|有什么)?[?？]?", text, re.I):
        records = list_solvents(ROOT)
        return "已登记溶剂：" + "、".join(record.name for record in records) + "；gas 表示不启用 SMD。"
    query = re.fullmatch(
        r"(?:请)?(?:查询|查找|查一下|查|搜索)\s*(?:SMD\s*)?(?:溶剂(?:库)?|solvents?\b)\s*[：:]?\s*(.+?)\s*[?？]?",
        text, re.I,
    ) or re.fullmatch(r"溶剂库(?:里|中)?(?:有没有|有)\s*(.+?)\s*(?:吗|么)?[?？]?", text, re.I)
    if query:
        result = lookup_solvent(query[1].strip(), project_root=ROOT)
        if result["match"]:
            record = result["match"]
            reply = f"精确匹配：{record['name']}；来源 {record['source']}；epsilon={record['epsilon']}；epsinf={record['epsinf']}。"
            return reply + ("\n" + _MANUAL_SMD_NOTICE if record["source"] == "manual" else "")
        return "未找到精确溶剂；候选：" + "、".join(record["name"] for record in result["candidates"]) + "。候选不会自动采用。"
    if re.search(r"例如|比如|举例|示例|假设|如果|假如|不要|请勿|勿|无需|不必|暂不|先不|先别|别登记|别注册|不登记|不注册|[?？]", text):
        return None
    registration = re.fullmatch(r"(?:请)?(?:登记|注册|新增|添加)\s*(?:(?:人工|手工|SMD)\s*)?(?:溶剂)?\s*(.*)", text, re.I)
    arguments = registration[1] if registration else text
    fields = list(_SMD_PARAMETER_FIELD.finditer(arguments))
    if not fields:
        return None
    remainder = _SMD_PARAMETER_FIELD.sub("", arguments).strip(" ,，;；:：")
    name_match = re.fullmatch(r"(?:name\s*[=:：]|名称\s*[=:：为]?|命名为|名为)\s*[\"“]?([^\"”]+)[\"”]?", remainder, re.I)
    if name_match:
        solvent_name = name_match[1].strip()
    elif (
        registration and re.fullmatch(r"[\w.()+-]+", remainder)
        and arguments[:fields[0].start()].strip(" ,，;；:：") == remainder
    ):
        solvent_name = remainder
    elif not remainder:
        solvent_name = None
    else:
        return None
    values = {}
    for field in fields:
        key = _SMD_PARAMETER_LABELS[field[1].casefold()]
        if key in values:
            raise ValueError(f"{key} 重复，请每个参数只指定一次")
        values[key] = field[2]
    if set(values) != {"epsilon", "epsinf"}:
        raise ValueError("登记溶剂必须同时明确给出 epsilon 和 epsinf")
    record = register_manual_solvent(solvent_name, values["epsilon"], values["epsinf"], project_root=ROOT)
    return (
        f"已登记人工溶剂 {record.name}：epsilon={record.epsilon}，epsinf={record.epsinf}。\n"
        f"{_MANUAL_SMD_NOTICE}\n登记未修改或确认模拟方案。可继续说“溶剂使用 {record.name}”，"
        "默认应用全部选中分子；也可指定某个分子。"
    )


def _requested_global_solvent(message: str) -> str | None:
    """Recognize an explicit global assignment, not a question or molecular override."""
    matches = re.findall(
        r"(?:^|[,，;；])\s*(?:请)?(?:(?:全部|所有)(?:选中)?分子(?:的)?\s*)?"
        r"(?:SMD\s*)?(?:溶剂\s*(?:统一)?\s*(?:使用|用|改为|设为|设置为|为|=)|solvent\s*=)"
        r"\s*([\w.()+-]+)\s*(?=$|[,，;；。])",
        message.strip(), re.I,
    )
    if len({name.casefold() for name in matches}) > 1:
        raise ValueError("全局溶剂指定重复且冲突，请改为按分子指定")
    return matches[0] if matches else None


def _prepare_plan_solvents(
    config: Mapping[str, object], previous_config: Mapping[str, object] | None = None,
    *, requested_default: str | None = None,
) -> dict[str, object]:
    """Resolve a read-only semantic selection; only explicit user commands register."""
    result = copy.deepcopy(dict(config))
    selection = result.pop("solvent_selection", {})
    if not isinstance(selection, Mapping) or set(selection) - {"default", "molecules"}:
        raise ValueError("solvent_selection 仅允许 default 和 molecules")
    selection = dict(selection)
    if requested_default is not None:
        if "default" in selection and str(selection["default"]).strip().casefold() != requested_default.casefold():
            raise ValueError("语义草案的全局溶剂与用户明确请求不一致")
        selection["default"] = requested_default
    overrides = selection.get("molecules", {})
    if not isinstance(overrides, Mapping) or set(overrides) - set(result.get("residues", {})):
        raise ValueError("溶剂覆盖必须对应本方案选中的分子")
    molecules = result.get("molecules", {})
    previous = (previous_config or {}).get("molecules", {})
    if not isinstance(molecules, Mapping) or not isinstance(previous, Mapping):
        raise ValueError("molecules 必须是对象")
    active = bool(selection) or any(
        isinstance(molecule, Mapping) and ("solvent" in molecule or "solvent_ref" in molecule)
        for molecule in (*molecules.values(), *previous.values())
    )
    if not active:
        return result
    molecules = result.setdefault("molecules", {})
    backend = str(result.get("backend", "g16")).strip().casefold()
    for name in dict.fromkeys((*result.get("residues", {}), *molecules)):
        molecule = molecules.setdefault(name, {})
        if not isinstance(molecule, dict):
            raise ValueError(f"molecules.{name} 必须是对象")
        inherited = previous.get(name, {})
        inherited = inherited if isinstance(inherited, Mapping) else {}
        selected = overrides.get(name, selection.get("default"))
        if name in overrides or ("default" in selection and name in result.get("residues", {})):
            if "solvent" in molecule and str(molecule["solvent"]).strip().casefold() != str(selected).strip().casefold():
                if str(molecule["solvent"]).strip().casefold() != str(inherited.get("solvent", "")).strip().casefold():
                    raise ValueError(f"{name}: solvent 与语义溶剂选择冲突")
                if "solvent_ref" in molecule and molecule["solvent_ref"] != inherited.get("solvent_ref"):
                    raise ValueError(f"{name}: solvent_ref 不是原方案的快照")
                molecule.pop("solvent_ref", None)
            molecule["solvent"] = selected
        elif "solvent" not in molecule and "solvent" in inherited:
            molecule["solvent"] = inherited["solvent"]
        same_as_previous = (
            "solvent" in inherited
            and str(molecule.get("solvent", "")).strip().casefold() == str(inherited["solvent"]).strip().casefold()
        )
        if same_as_previous and "solvent_ref" not in molecule and "solvent_ref" in inherited:
            molecule["solvent_ref"] = copy.deepcopy(inherited["solvent_ref"])
        if same_as_previous and "solvent_ref" in inherited and molecule.get("solvent_ref") != inherited["solvent_ref"]:
            raise ValueError(f"{name}: 不得改写同名溶剂的已有快照；请登记新名称")
        trusted_snapshot = same_as_previous and molecule.get("solvent_ref") == inherited.get("solvent_ref") and "solvent_ref" in inherited
        solvent, snapshot = molecule_solvent_settings(
            molecule, backend, project_root=ROOT, require_registered=not trusted_snapshot,
        )
        molecule.update(solvent=solvent, solvent_ref=snapshot)
    return result


def _bind_strict_solvents(config: Mapping[str, object], semantic: Mapping[str, object]) -> dict[str, object]:
    """Prevent the no-tool stage from changing the server-bound solvent selection."""
    if "solvent_selection" in config:
        raise ValueError("最终配置不得再改写 solvent_selection")
    result = copy.deepcopy(dict(config))
    molecules = result.setdefault("molecules", {})
    if not isinstance(molecules, dict):
        raise ValueError("molecules 必须是对象")
    for name, expected in semantic.get("molecules", {}).items():
        if not isinstance(expected, Mapping):
            raise ValueError(f"molecules.{name} 必须是对象")
        if "solvent_ref" not in expected:
            continue
        molecule = molecules.setdefault(name, {})
        if not isinstance(molecule, dict):
            raise ValueError(f"molecules.{name} 必须是对象")
        candidate = {**expected, **molecule}
        solvent, snapshot = molecule_solvent_settings(candidate, str(result.get("backend", "g16")), project_root=ROOT)
        if solvent.casefold() != expected["solvent"].casefold() or snapshot != expected["solvent_ref"]:
            raise ValueError(f"{name}: 严格配置改变了已绑定的溶剂或快照")
        molecule.update(solvent=solvent, solvent_ref=snapshot)
    return _prepare_plan_solvents(result, semantic)


def _config_outline(config: Mapping[str, object] | None) -> dict[str, object]:
    """Return only proposal fields that are meaningful to a revision request."""
    if not isinstance(config, Mapping):
        return {}
    outline: dict[str, object] = {}
    for key in (
        "backend", "residues", "molecules", "md", "topology", "box", "defaults",
        "ion_compensation", "non_neutral_confirmed", "ion_charge_scale",
    ):
        if key in config:
            outline[key] = copy.deepcopy(config[key])
    return outline


def _config_task_context(
    *,
    mode: str,
    user_request: str,
    execution_facts: str,
    pending_config: Mapping[str, object] | None = None,
    recent_history: list[dict[str, str]] | None = None,
    attempt: int = 0,
    tool_round: int = 0,
    stage: str = "semantic_normalize",
) -> str:
    """Create a bounded, authority-labelled context for one config request."""
    available = list(_available_residues())[:128]
    immutable: dict[str, object] = {
        "prompt_contract_version": PROMPT_CONTRACT_VERSION,
        "prompt_version": CONFIG_PROMPT_VERSION,
        "execution_boundary": execution_facts or "MD 仅使用服务端确认的本机执行边界",
        "supported_quantum_backends": ["g16", "g09", "orca"],
        "available_structure_names": available,
        "raw_input_rule": "g16/g09 仅接受 .gjf；orca 仅接受 .inp；电荷和自旋必须来自审计工具。",
    }
    if pending_config is not None:
        immutable["pending_plan_outline"] = _config_outline(pending_config)
    evidence: dict[str, object] = {
        "server_validated": [
            "候选方案会在展示前重新审计原始量子输入、回填电荷和自旋，并校验 workflow/MD schema",
            "execution 字段由服务端覆盖，模型没有远程连接或启动权限",
        ],
    }
    if recent_history:
        evidence["recent_conversation"] = [
            {"role": item["role"], "content": item["content"][-600:]}
            for item in recent_history[-2:]
            if item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str)
        ]
    expected: object
    if mode in {"plan_generate", "plan_revise"} and stage == "semantic_normalize":
        expected = {
            "content": "严格 JSON 对象：语义草案，必须含 backend 和 residues，或固定 error 对象",
            "stage_boundary": "此阶段不得声称量子输入已审计；服务端会从草案归一化 backend 和 components 后进入强制审计阶段。",
        }
    else:
        expected = {
            "content": "简洁中文解释；区分已记录事实与建议；不得输出 config JSON",
        }
    return build_structured_context(
        task_type={"mode": mode, "user_request": user_request.strip()[:3_000]},
        current_layer_and_step={"layer": "config", "step": "方案生成与确认前校验", "run_id": None},
        immutable_facts=immutable,
        verified_evidence=evidence,
        unverified_assumptions=[
            "分子名称映射、体系类型和参数建议需要通过工具或审计结果确认",
            "用户未明确给出的科学参数只能使用已登记默认值，不能假定为已确认偏好",
        ],
        allowed_actions=[
            "调用本轮提供的配置只读工具",
            "生成语义草案或解释当前待确认方案",
        ],
        prohibited_actions=[
            "不得启动流水线、写入 config.json、修改已有待确认方案或执行外部程序",
            "不得把未审计电荷/自旋写入候选配置",
            "不得绕过服务端量子输入审计阶段",
            "不得自行设置 execution、SSH、Slurm、主机、路径、认证或命令字段",
        ],
        remaining_budget={
            "remaining_generation_attempts": max(0, CONFIG_MAX_ATTEMPTS - attempt),
            "remaining_tool_rounds": max(0, CONFIG_MAX_TOOL_ROUNDS - tool_round),
            "per_call_timeout_s": CONFIG_LLM_TIMEOUT_S,
        },
        expected_output_format=expected,
    )


def _quantum_audit_context(
    *,
    user_request: str,
    backend: str,
    components: list[dict[str, object]],
    execution_facts: str,
) -> str:
    """Build the isolated one-tool context for raw quantum-input auditing."""
    return build_structured_context(
        task_type={"mode": "plan_quantum_input_audit", "user_request": user_request.strip()[:3_000]},
        current_layer_and_step={"layer": "config", "step": "原始量子输入强制审计", "run_id": None},
        immutable_facts={
            "prompt_contract_version": PROMPT_CONTRACT_VERSION,
            "normalized_backend": backend,
            "normalized_components": components,
            "execution_boundary": execution_facts or "MD 仅使用服务端确认的本机执行边界",
        },
        verified_evidence={
            "server_validated": [
                "backend 和 components 已由语义草案经服务端归一化",
                "此阶段不接受模型更改 backend、分子名或数量",
            ],
        },
        unverified_assumptions=[
            "对应后端的原始量子输入是否存在、格式是否有效及其电荷/自旋尚未验证。",
        ],
        allowed_actions=[
            "只能且必须调用一次 tools_inspect_quantum_inputs，参数必须与 normalized_backend 和 normalized_components 完全相同。",
        ],
        prohibited_actions=[
            "不得输出普通文本、JSON 方案或其他工具调用",
            "不得改变 backend、components、execution 或启动流水线",
        ],
        remaining_budget={"required_tool_calls": 1, "remaining_tool_rounds": 1, "per_call_timeout_s": CONFIG_LLM_TIMEOUT_S},
        expected_output_format={
            "tool_call": "tools_inspect_quantum_inputs",
            "arguments": {"backend": backend, "components": components},
        },
    )


def _strict_config_context(
    *,
    user_request: str,
    semantic_draft: Mapping[str, object],
    audit: Mapping[str, object],
    execution_facts: str,
    mode: str,
) -> str:
    """Build the no-tool rendering stage after source input audit succeeds."""
    return build_structured_context(
        task_type={"mode": "plan_strict_json", "source_mode": mode, "user_request": user_request.strip()[:3_000]},
        current_layer_and_step={"layer": "config", "step": "审计后严格配置生成", "run_id": None},
        immutable_facts={
            "prompt_contract_version": PROMPT_CONTRACT_VERSION,
            "semantic_selection": {
                "backend": semantic_draft.get("backend"),
                "residues": semantic_draft.get("residues"),
                "ion_charge_scale": semantic_draft.get("ion_charge_scale", DEFAULT_ION_CHARGE_SCALE),
                "solvents": {
                    name: {key: molecule[key] for key in ("solvent", "solvent_ref") if key in molecule}
                    for name, molecule in semantic_draft.get("molecules", {}).items()
                    if isinstance(molecule, Mapping)
                },
            },
            "execution_boundary": execution_facts or "MD 仅使用服务端确认的本机执行边界",
        },
        verified_evidence={
            "quantum_input_audit": dict(audit),
            "server_validated": [
                "量子输入审计已经通过；组件电荷和自旋必须以此结果为准",
                "最终配置仍会由服务端执行输入契约、schema 与执行边界校验",
            ],
        },
        unverified_assumptions=[
            "未明确的 MD、拓扑和建盒参数只能使用已登记默认值。",
        ],
        allowed_actions=["输出一个严格 JSON 候选 config，或固定 error 对象。"],
        prohibited_actions=[
            "不得调用工具、输出 Markdown、启动流水线或修改执行边界",
            "不得修改已审计的 backend、分子名、数量、电荷或自旋，以及服务端绑定的 solvent/solvent_ref",
        ],
        remaining_budget={"remaining_model_calls": 1, "per_call_timeout_s": CONFIG_LLM_TIMEOUT_S},
        expected_output_format={
            "content": "严格 JSON 对象：完整候选 config，或固定 error 对象",
            "required_before_config": "必须使用 quantum_input_audit 中的 source-authoritative charge/spin。",
        },
    )


def _normalize_quantum_audit_request(
    draft: Mapping[str, object],
) -> tuple[dict[str, object] | None, str | None]:
    """Extract one exact, server-validated audit request from a semantic draft."""
    backend = draft.get("backend", "g16")
    if not isinstance(backend, str) or backend.strip().lower() not in {"g16", "g09", "orca"}:
        return None, "候选方案未提供受支持的量子后端。"
    residues = draft.get("residues")
    if not isinstance(residues, Mapping) or not residues:
        return None, "候选方案未提供可审计的分子及数量。"
    available = set(_available_residues())
    components: list[dict[str, object]] = []
    for name, count in residues.items():
        if not isinstance(name, str) or name not in available:
            return None, "候选方案包含未登记的分子名称，无法进行原始输入审计。"
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            return None, f"分子 {name} 的数量必须是正整数，无法进行原始输入审计。"
        components.append({"name": name, "count": count})
    components.sort(key=lambda item: str(item["name"]))
    return {"backend": backend.strip().lower(), "components": components}, None


def _audit_result_from_required_tool_call(
    message: object,
    expected_request: Mapping[str, object],
) -> tuple[dict[str, object] | None, str | None]:
    """Accept exactly one matching audit call and dispatch it server-side."""
    tool_calls = getattr(message, "tool_calls", None)
    if not isinstance(tool_calls, list) or len(tool_calls) != 1:
        return None, "model_no_tool_call"
    tool_call = tool_calls[0]
    function = getattr(tool_call, "function", None)
    if getattr(function, "name", None) != QUANTUM_AUDIT_TOOL_NAME:
        return None, "model_invalid_tool_call"
    try:
        arguments = json.loads(getattr(function, "arguments", ""))
    except (TypeError, json.JSONDecodeError):
        return None, "model_invalid_tool_arguments"
    if arguments != expected_request:
        return None, "model_audit_scope_mismatch"
    result_str = handle_tool_call(QUANTUM_AUDIT_TOOL_NAME, dict(arguments))
    try:
        result = json.loads(result_str)
    except json.JSONDecodeError:
        return None, "quantum_input_audit_invalid_response"
    if not isinstance(result, dict):
        return None, "quantum_input_audit_invalid_response"
    return result, None


# ============================================================
# LLM Helpers
# ============================================================

def _llm(system, user):
    r = _DS.chat.completions.create(
        model=_LLM_SETTINGS.model if _LLM_SETTINGS else DEFAULT_LLM_MODEL,
        messages=[{"role":"system","content":system},{"role":"user","content":user}],
        temperature=0.1,
        timeout=CONFIG_LLM_TIMEOUT_S,
    )
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


def _llm_failure_notice(error: Exception) -> tuple[str, bool]:
    """Return a redacted UI message and whether a transient retry is useful."""
    response = getattr(error, "response", None)
    status = getattr(error, "status_code", None) or getattr(response, "status_code", None)
    if status in {400, 404, 405, 422}:
        return (
            "⚠️ LLM 模型或工具调用协议不兼容。"
            "请在配置页重新测试连接，或更换支持自动 function calling 的模型。",
            False,
        )
    if status in {401, 403}:
        return "⚠️ LLM API Key 无效或无权访问当前模型，请检查配置。", False
    if status == 429:
        return "⚠️ LLM 服务限流或余额不足，请稍后重试并检查账户配额。", False
    return "❌ LLM 服务暂时不可用，请检查网络和配置后重试。", True


# ============================================================
# 方案总结
# ============================================================

def summarize(cfg):
    r, md, mols = cfg.get("residues",{}), cfg.get("md",{}), cfg.get("molecules",{})
    md, _ = require_valid_md_config(merge_v2_defaults(md))
    scale = validate_ion_charge_scale(cfg.get("ion_charge_scale", DEFAULT_ION_CHARGE_SCALE))
    backend = str(cfg.get("backend", "g16")).strip().casefold()
    structure = []
    solvent_notes = []
    from willy.quantum.smd_solvents import input_has_scrf
    for n, c in r.items():
        molecule = mols.get(n, {})
        molecule = molecule if isinstance(molecule, Mapping) else {}
        level = molecule.get("basis")
        solvent_name, solvent_record = molecule_solvent_settings(molecule, backend, project_root=ROOT)
        input_path = ROOT / "struct" / f"{n}.gjf"
        original_scrf = input_has_scrf(input_path) if backend in {"g16", "g09"} else False
        scrf_action = (
            "覆盖原始 SCRF（含多行设置）" if original_scrf else "原始无 SCRF，将新增"
        ) if solvent_name.casefold() != GAS_SOLVENT else (
            "移除原始 SCRF（含多行设置），不启用 SMD" if original_scrf else "不启用 SMD"
        )
        solvent_notes.append(f"- {n}：{solvent_name}（{solvent_record['source']}）；{scrf_action}。优化/单点共享此名称与 solvent_ref。")
        if solvent_record["source"] == "manual":
            solvent_notes.append(f"  Eps={solvent_record['epsilon']}，EpsInf={solvent_record['epsinf']}。{_MANUAL_SMD_NOTICE}")
        structure.append({
            "name": str(n),
            "count": int(c),
            "optimization_level": level.strip() if isinstance(level, str) and level.strip() else "未指定",
            "solvent": solvent_name,
            "solvent_source": solvent_record["source"],
            "scrf_override": scrf_action,
        })
        if solvent_record["source"] == "manual":
            structure[-1]["solvent_note"] = (
                f"Eps={solvent_record['epsilon']}，EpsInf={solvent_record['epsinf']}；{_MANUAL_SMD_NOTICE}"
            )
    total = sum(r.values())
    box_config = cfg.get("box", {})
    box_config = box_config if isinstance(box_config, dict) else {}
    explicit_box = box_config.get("box_size")
    target_density = box_config.get("target_mass_density_g_cm3")
    legacy_density = box_config.get("packing_number_density_nm3")
    if explicit_box is not None:
        try:
            density_display = f"指定盒边长 {float(explicit_box) / 10.0:.3f} nm"
        except (TypeError, ValueError):
            density_display = "指定盒边长，待建盒校验"
    elif target_density is not None:
        try:
            density_value = float(target_density)
            density_display = f"{density_value:.2f} g/cm3"
        except (TypeError, ValueError):
            density_display = "待建盒步骤校验"
    elif legacy_density is not None:
        density_display = f"历史分子数密度 {legacy_density} 分子/nm3"
    else:
        density_display = "0.70 g/cm3"
    eq = md["eq"]
    t = eq["target_temperature"]
    _, temperatures, actual_segments = eq_annealing_points(md)
    segment_labels = ("升温", "高温恒温", "降至过渡温度", "过渡恒温", "降至目标温度", "目标恒温")
    md_steps = [{"label": "EM", "meta": "能量最小化"}]
    for index, (name, label) in enumerate(zip(EQ_SEGMENT_NAMES, segment_labels)):
        duration = actual_segments[name]
        requested_duration = float(eq["segments_ns"][name])
        duration_note = f"{duration:.9g} ns"
        if duration != requested_duration:
            duration_note += f"（请求 {requested_duration:.9g} ns，按时间步对齐）"
        md_steps.append({
            "label": f"EQ · {label}（{name}）",
            "meta": f"NPT · {duration_note} · {temperatures[index]:.9g}K→{temperatures[index + 1]:.9g}K",
        })
    prod_ns = md["prod"]["duration_ns"]
    md_steps.append({"label": "PROD", "meta": f"NPT · {prod_ns:.9g} ns · {t:.9g}K→{t:.9g}K"})
    card_data = {
        "version": 1,
        "structure": structure,
        "environment": {
            "charge_scale": f"{scale:.2f}",
            "initial_density": density_display,
            "total_molecules": int(total),
        },
        "md_steps": md_steps,
    }
    encoded_card_data = json.dumps(card_data, ensure_ascii=False, separators=(",", ":"))
    solvent_summary = "\n".join(solvent_notes)
    return f"""[[WILLY_PLAN_DATA:{encoded_card_data}]]
**模拟方案确认**

{solvent_summary}

下一步将开始结构优化。

确认无误后回复“运行”即可开始。
若参数有误请提出，我会更新方案。"""


def _normalized_confirmation(message: str) -> str:
    """Normalize a short launch reply without accepting arbitrary prose."""
    return "".join(message.casefold().split()).strip("，。！？!?、,.")


def _fingerprint_payload(value: object) -> str:
    """Return a stable fingerprint for session-bound plan evidence."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def create_pending_launch_plan(config: Mapping[str, object], summary: str) -> dict[str, object]:
    """Store one validated, unconfirmed candidate for exactly one browser session.

    This is deliberately not a run snapshot.  The final configuration freeze
    happens only in ``start_pipeline`` after the user gives an explicit launch
    confirmation and the server repeats validation.
    """
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


# A pending proposal is an interactive editing session. These markers are
# deliberately conservative: a plain question must not silently mutate the
# pending candidate, while an explicit field/value change is sent back through
# the normal audited config-generation path.
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
    "ion_charge_scale", "电荷", "缩放", "charge scale", "charge_scale",
    "溶剂使用", "溶剂用", "溶剂为", "solvent=", "solvent =",
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
            recent.append({"role": role, "content": content[-600:]})
    return {
        "config": config,
        "summary": _latest_pending_summary(history) or "",
        "recent_history": recent,
    }


def _pending_revision_message(
    message: str,
    context: Mapping[str, object],
    execution_facts: str,
    *,
    attempt: int = 0,
    tool_round: int = 0,
) -> str:
    """Build the structured prompt envelope for an incremental revision."""
    return _config_task_context(
        mode="plan_revise",
        user_request=message,
        execution_facts=execution_facts,
        pending_config=context.get("config") if isinstance(context.get("config"), Mapping) else None,
        recent_history=context.get("recent_history") if isinstance(context.get("recent_history"), list) else None,
        attempt=attempt,
        tool_round=tool_round,
    )


def _answer_pending_config_question(message: str, context: Mapping[str, object]) -> str:
    """Answer a question without changing or invalidating the pending plan."""
    if not _DS:
        return "当前方案仍在等待确认；请回复“运行”确认，或直接说明需要修改的参数。"
    prompt = _config_task_context(
        mode="plan_explain",
        user_request=message,
        execution_facts="当前方案尚未确认，任何执行目标均未授权。",
        pending_config=context.get("config") if isinstance(context.get("config"), Mapping) else None,
        recent_history=context.get("recent_history") if isinstance(context.get("recent_history"), list) else None,
    )
    try:
        answer = _llm(
            build_contract_system_prompt(
                assistant_name="Willy 方案助理",
                scope="解释当前待确认方案，不产生或修改配置",
                domain_rules=(
                    "只回答问题，不生成 config JSON，不启动流水线，也不改变方案。"
                    "只能解释受控上下文中的当前待确认方案概要；需要修改时要求用户提出具体字段。"
                ),
            ),
            prompt,
        )
        if isinstance(answer, str) and answer.strip():
            return answer.strip()
    except Exception:
        pass
    return "当前方案仍在等待确认；请回复“运行”确认，或直接说明需要修改的参数。"


def _looks_like_framework_design_question(message: str) -> bool:
    """Limit the extra intent call to explicit project-level questions."""
    text = (message or "").casefold().strip()
    return bool(text) and any(marker.casefold() in text for marker in _FRAMEWORK_DESIGN_MARKERS)


def _framework_intent_context(message: str) -> str:
    """Build the read-only envelope used to classify a possible design query."""
    return build_structured_context(
        task_type={"mode": "framework_design_intent", "user_request": message.strip()[:3_000]},
        current_layer_and_step={"layer": "config", "step": "方案助理只读意图判断", "run_id": None},
        immutable_facts={
            "prompt_contract_version": PROMPT_CONTRACT_VERSION,
            "assistant_boundary": "仍是现有 Willy 方案助理；本轮没有新增助理、工具或执行入口",
            "evidence_boundary": "仅当分类为 framework_design_question 时读取服务端白名单项目文档",
        },
        verified_evidence={
            "classification_rule": (
                "framework_design_question 仅表示询问本项目的架构、状态机、manifest、"
                "工具/权限边界、已实现能力或规划；分子、MD 参数和当前方案问题不是该类别"
            ),
        },
        unverified_assumptions=["用户意图尚未由模型分类"],
        allowed_actions=["只输出 intent 分类 JSON"],
        prohibited_actions=[
            "不得生成方案 JSON、调用工具、启动流水线或修改配置/历史/待确认方案",
            "不得读取源码、运行目录、环境变量、密钥或任意路径",
        ],
        remaining_budget={"intent_calls": 1, "per_call_timeout_s": CONFIG_LLM_TIMEOUT_S},
        expected_output_format={
            "content": '{"intent":"framework_design_question"} 或 {"intent":"plan_request"}',
            "allowed_intents": ["framework_design_question", "plan_request"],
        },
    )


def _classify_framework_design_intent(message: str) -> bool:
    """Ask the configured model whether a project-level design answer is needed."""
    if not _DS or not _looks_like_framework_design_question(message):
        return False
    system = build_contract_system_prompt(
        assistant_name="Willy 方案助理的只读意图分类器",
        scope="判断用户是否在询问本项目的框架/设计事实",
        domain_rules=(
            "只能返回一个 JSON 对象，不得输出解释或 Markdown。"
            "framework_design_question 仅用于本项目架构、模块、状态机、manifest、"
            "工具/权限边界、已实现能力或规划事项；普通模拟配置、分子、协议参数、"
            "当前待确认方案问题一律返回 plan_request。"
        ),
    )
    try:
        parsed = _j(_llm(system, _framework_intent_context(message)))
    except Exception:
        return False
    return isinstance(parsed, Mapping) and parsed.get("intent") == "framework_design_question"


def _framework_document_sections(text: str) -> list[tuple[str, str]]:
    """Split approved Markdown into bounded heading sections for retrieval."""
    headings = list(re.finditer(r"(?m)^#{1,4}\s+(.+?)\s*$", text))
    if not headings:
        return [("文档正文", text)]
    sections: list[tuple[str, str]] = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        body = text[heading.end():end].strip()
        if body:
            sections.append((heading.group(1).strip(), body))
    return sections


def _framework_design_evidence(message: str) -> list[dict[str, str]]:
    """Read only approved project documents and return relevant excerpts."""
    root = Path(ROOT).resolve()
    query = (message or "").casefold()
    candidates: list[tuple[int, str, str, str]] = []
    for relative, document_id in _FRAMEWORK_SOURCE_WHITELIST:
        source = (root / relative).resolve()
        try:
            source.relative_to(root)
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError):
            continue
        for heading, body in _framework_document_sections(text):
            haystack = f"{heading} {body}".casefold()
            score = sum(1 for marker in _FRAMEWORK_DESIGN_MARKERS if marker.casefold() in query and marker.casefold() in haystack)
            if score:
                candidates.append((score, document_id, heading, body))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [
        {
            "document": document_id,
            "section": heading,
            "excerpt": body[:_FRAMEWORK_SOURCE_MAX_SECTION_CHARS],
        }
        for _score, document_id, heading, body in candidates[:_FRAMEWORK_SOURCE_MAX_SECTIONS]
    ]


def _answer_framework_design_question(message: str) -> str:
    """Answer a project design question without changing any assistant state."""
    evidence = _framework_design_evidence(message)
    if not _DS:
        return "项目设计问答需要先配置可用的 LLM；当前未生成或修改任何方案。"
    if not evidence:
        return "已登记项目文档中没有足够证据确认这个设计问题；当前未生成或修改任何方案。"
    prompt = build_structured_context(
        task_type={"mode": "framework_design_answer", "user_request": message.strip()[:3_000]},
        current_layer_and_step={"layer": "config", "step": "方案助理只读设计问答", "run_id": None},
        immutable_facts={
            "prompt_contract_version": PROMPT_CONTRACT_VERSION,
            "assistant_boundary": "现有 Willy 方案助理的只读子模式，不新增助理",
            "evidence_boundary": "以下是服务端从固定白名单文档读取的片段",
        },
        verified_evidence={"approved_project_documents": evidence},
        unverified_assumptions=["文档片段之外的源码级细节无法由本轮确认"],
        allowed_actions=["根据文档片段用中文解释，并标注文档与章节"],
        prohibited_actions=[
            "不得生成 config JSON、修改 pending plan/history、写文件、启动/停止/重跑/分叉进程",
            "不得调用工具、读取任意路径、源码、运行目录、环境变量、密钥或原始日志",
            "不得把规划事项说成已实现，也不得编造文档未提供的事实",
        ],
        remaining_budget={"answer_calls": 1, "per_call_timeout_s": CONFIG_LLM_TIMEOUT_S},
        expected_output_format={
            "content": "简洁中文回答；区分已实现事实与规划事项；引用【文档｜章节】",
        },
    )
    system = build_contract_system_prompt(
        assistant_name="Willy 方案助理的只读设计问答",
        scope="依据白名单项目文档解释框架、架构和设计事实",
        domain_rules=(
            "只能基于受控上下文中的文档片段回答。明确区分‘已实现’与‘规划/后续’，"
            "引用对应的【文档｜章节】。不确定时直说无法从当前片段确认。"
            "本轮绝不生成方案、修改状态、写文件或启动任何进程。"
        ),
    )
    try:
        answer = _llm(system, prompt)
    except Exception:
        return "项目设计问答暂时不可用；当前未生成或修改任何方案。"
    if not isinstance(answer, str) or not answer.strip():
        return "项目设计问答未返回有效内容；当前未生成或修改任何方案。"
    return answer.strip()


def _molecule_catalog_intent(message: str) -> tuple[str, str | None] | None:
    """Classify explicit molecule-library questions without an LLM call."""
    text = (message or "").strip()
    if not text:
        return None
    compact = re.sub(r"\s+", "", text)
    if any(marker in compact for marker in _MOLECULE_CATALOG_LIST_MARKERS):
        return "list", None
    for pattern in _MOLECULE_CATALOG_QUERY_PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            name = match.group("name").strip()
            if name:
                return "lookup", name
    return None


def _answer_molecule_catalog_intent(intent: tuple[str, str | None]) -> str:
    """Return core filenames only and keep the proposal state read-only."""
    from willy.toolist_global import (
        list_registered_molecules,
        lookup_registered_molecule,
        suggest_registered_molecules,
    )

    mode, query = intent
    if mode == "list":
        names = list_registered_molecules()
        if not names:
            return "当前分子库为空。"
        rows = ["、".join(names[index:index + 10]) for index in range(0, len(names), 10)]
        return f"当前可用分子（{len(names)}）：\n" + "\n".join(rows)

    assert query is not None
    matched = lookup_registered_molecule(query)
    suggestions = suggest_registered_molecules(query)
    if matched is not None:
        name = str(matched["name"])
        nearby = [candidate for candidate in suggestions if candidate != name]
        detail = f"精确匹配：{name}"
        if nearby:
            detail += f"\n相近核心文件名：{'、'.join(nearby)}"
        return detail
    if suggestions:
        return f"未找到“{query}”的精确结构。最接近的核心文件名：{'、'.join(suggestions)}"
    return f"未找到“{query}”的匹配或相近结构。"


def _execution_md_from_config(config: Mapping[str, object]) -> dict[str, object]:
    """Return the validated execution choice, retaining the legacy local default."""
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
    permission-checked registry before it is allowed into a pending plan.
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
        normalize_uploaded_structure(src, project_root=ROOT)
        msg = f"已上传{src.name}，仅保留坐标、电荷、自旋。"
        from willy.toolist_global import _registry
        _registry._load()
    except StructureUploadError as exc:
        msg = f"上传失败：{exc}"
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
    proposal_workspace_id: str | None = None,
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
                        "远程任务页的执行选择已在方案生成后变化。为避免将待确认方案提交到错误的"
                        "执行目标，请先按当前选择重新生成或修改方案，再确认运行。"
                    ),
                }]
                yield _emit(h, pending_plan)
                return
        try:
            # Preserve the established direct-call contract for non-browser
            # callers.  The durable workspace token is supplied only by the
            # proposal API that owns a persisted ``plan__`` directory.
            receipt = (
                start_pipeline(launch_config)
                if proposal_workspace_id is None
                else start_pipeline(
                    launch_config,
                    proposal_workspace_id=proposal_workspace_id,
                )
            )
            launch_message = receipt.message
        except Exception:
            receipt = PipelineLaunchReceipt("流水线未能启动，请检查模拟方案后重试。", None, "failed")
            launch_message = receipt.message
        h = user_h + [{"role": "assistant", "content": launch_message}]
        yield _emit(h, None if receipt.state == "started" else pending_plan)
        return

    try:
        solvent_reply = _solvent_catalog_reply(message)
    except ValueError as exc:
        solvent_reply = f"⚠️ 溶剂操作被拒绝：{exc}；未修改模拟方案。"
    if solvent_reply is not None:
        yield _emit(user_h + [{"role": "assistant", "content": solvent_reply}], pending_plan)
        return

    try:
        requested_solvent = _requested_global_solvent(message)
    except ValueError as exc:
        yield _emit(user_h + [{"role": "assistant", "content": f"⚠️ 溶剂请求无效：{exc}"}], pending_plan)
        return

    try:
        requested_scale = _requested_ion_charge_scale(message)
    except ValueError as exc:
        h = user_h + [{"role": "assistant", "content": f"⚠️ 缩放请求无效：{exc}；本轮未修改或生成方案。"}]
        yield _emit(h, pending_plan)
        return

    catalog_intent = _molecule_catalog_intent(message)
    if catalog_intent is not None:
        h = user_h + [{
            "role": "assistant",
            "content": _answer_molecule_catalog_intent(catalog_intent),
        }]
        yield _emit(h, pending_plan)
        return

    # Project/framework questions stay inside the existing proposal assistant,
    # but never enter config generation or the execution-selection path.
    if _classify_framework_design_intent(message):
        answer = _answer_framework_design_question(message)
        h = user_h + [{"role": "assistant", "content": answer}]
        yield _emit(h, pending_plan)
        return

    selected_execution, execution_facts, selection_error = _trusted_execution_selection(
        execution_context,
    )
    if selection_error:
        h = user_h + [{"role": "assistant", "content": selection_error}]
        yield _emit(h, pending_plan)
        return

    # Keep an awaiting proposal alive while the user asks about it or edits it.
    # The exact pending candidate and recent turns are bound into the next LLM
    # request, so a second turn is an incremental edit rather than a stateless
    # replacement. It remains unconfirmed until an explicit launch reply.
    pending_context = _pending_config_context(pending_plan, history)
    pending_intent = _classify_pending_config_intent(message)
    revision_mode = False
    if pending_context is not None and pending_intent != "replace":
        if pending_intent == "revise":
            revision_mode = True
        else:
            answer = _answer_pending_config_question(message, pending_context)
            h = user_h + [{"role": "assistant", "content": answer}]
            yield _emit(h, pending_plan)
            return
    else:
        pending_plan = None

    previous_config = pending_context["config"] if revision_mode and pending_context else {}
    try:
        expected_scale = validate_ion_charge_scale(
            requested_scale if requested_scale is not None
            else previous_config.get("ion_charge_scale", DEFAULT_ION_CHARGE_SCALE)
        )
    except ValueError as exc:
        h = user_h + [{"role": "assistant", "content": f"⚠️ 当前方案缩放参数无效：{exc}"}]
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

    # LLM call: semantic normalization -> required input audit -> strict JSON.
    # The state transitions live here rather than in a prompt reminder, so a
    # model that emits prose in the audit phase cannot silently bypass it.
    candidate_config = None
    request_mode = "plan_revise" if revision_mode else "plan_generate"
    for attempt in range(CONFIG_MAX_ATTEMPTS):
        try:
            semantic_context = (
                _pending_revision_message(
                    message,
                    pending_context,
                    execution_facts,
                    attempt=attempt,
                )
                if revision_mode and pending_context is not None
                else _config_task_context(
                    mode=request_mode,
                    user_request=message,
                    execution_facts=execution_facts,
                    attempt=attempt,
                    stage="semantic_normalize",
                )
            )
            semantic_messages = [
                {"role": "system", "content": get_system_prompt(execution_facts, stage="semantic_normalize")},
                {"role": "user", "content": semantic_context},
            ]
            semantic_draft: dict[str, object] | None = None
            for tool_round in range(CONFIG_MAX_TOOL_ROUNDS):
                response = _DS.chat.completions.create(
                    model=_LLM_SETTINGS.model if _LLM_SETTINGS else DEFAULT_LLM_MODEL,
                    messages=semantic_messages,
                    tools=SEMANTIC_TOOLS,
                    temperature=0.1,
                    timeout=CONFIG_LLM_TIMEOUT_S,
                )
                semantic_message = response.choices[0].message
                if semantic_message.tool_calls:
                    semantic_messages.append({
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.function.name,
                                    "arguments": call.function.arguments,
                                },
                            }
                            for call in semantic_message.tool_calls
                        ],
                    })
                    for tool_call in semantic_message.tool_calls:
                        try:
                            if tool_call.function.name not in _SEMANTIC_TOOL_NAMES:
                                raise ValueError("语义阶段不允许该工具")
                            raw_args = json.loads(tool_call.function.arguments)
                            if not isinstance(raw_args, dict):
                                raise ValueError("工具参数必须是对象")
                            if tool_call.function.name == "tools_lookup_solvent":
                                result_str = json.dumps(lookup_solvent(raw_args.get("name", ""), project_root=ROOT), ensure_ascii=False)
                            else:
                                result_str = handle_tool_call(tool_call.function.name, raw_args)
                        except (json.JSONDecodeError, TypeError, ValueError) as exc:
                            result_str = json.dumps(
                                {"ok": False, "error": f"工具参数无效：{exc}"},
                                ensure_ascii=False,
                            )
                        semantic_messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result_str,
                        })
                        icon, line = _tool_summary(tool_call.function.name, result_str)
                        progress_lines.append(f"{icon} {line}")
                        progress_h = list(user_h) + [{"role": "assistant", "content": "\n".join(progress_lines)}]
                        yield "", progress_h, progress_h, pending_plan, "", _HIDE_BTN
                    continue

                config_dict = _j(semantic_message.content or "")
                err = config_dict.get("error") if isinstance(config_dict, dict) else None
                if isinstance(err, str):
                    err = {"type": err.lower().replace(" ", "_"), "detail": err}
                if isinstance(err, dict) and err.get("type"):
                    etype = str(err["type"])
                    detail = str(err.get("detail", ""))
                    if etype == "invalid_molecule" and attempt < CONFIG_MAX_ATTEMPTS - 1:
                        from willy.toolist_global import _registry
                        _registry._load()
                        break
                    if etype in {"invalid_value", "ambiguous", "invalid_quantum_input", "charge_imbalance"}:
                        hint = err.get("suggestion", "请修正后重新输入")
                        h = list(user_h) + [{"role": "assistant", "content": f"⚠ {detail}\n\n{hint}"}]
                        yield _emit(h, pending_plan)
                        return
                    avail = ", ".join(err.get("available", []))
                    h = list(user_h) + [{"role": "assistant", "content": f"❌ {detail}\n\n可用: {avail}"}]
                    yield _emit(h, pending_plan)
                    return
                if isinstance(config_dict, dict) and config_dict.get("residues"):
                    semantic_draft = config_dict
                    break
                break

            if semantic_draft is None:
                continue

            try:
                semantic_draft = _bind_ion_charge_scale(semantic_draft, expected_scale)
            except ValueError as exc:
                h = user_h + [{"role": "assistant", "content": f"⚠️ 方案未通过缩放参数校验：{exc}"}]
                yield _emit(h, pending_plan)
                return

            try:
                semantic_draft = _prepare_plan_solvents(semantic_draft, previous_config, requested_default=requested_solvent)
            except ValueError as exc:
                h = user_h + [{"role": "assistant", "content": f"⚠️ 方案未通过溶剂校验：{exc}"}]
                yield _emit(h, pending_plan)
                return

            audit_request, audit_request_error = _normalize_quantum_audit_request(semantic_draft)
            if audit_request is None:
                h = list(user_h) + [{
                    "role": "assistant",
                    "content": f"⚠️ 方案未通过量子输入审计前校验：{audit_request_error or '无法确定审计目标'}",
                }]
                yield _emit(h, pending_plan)
                return
            audit_response = _DS.chat.completions.create(
                model=_LLM_SETTINGS.model if _LLM_SETTINGS else DEFAULT_LLM_MODEL,
                messages=[
                    {"role": "system", "content": get_system_prompt(execution_facts, stage="quantum_input_audit")},
                    {"role": "user", "content": _quantum_audit_context(
                        user_request=message,
                        backend=str(audit_request["backend"]),
                        components=list(audit_request["components"]),
                        execution_facts=execution_facts,
                    )},
                ],
                tools=QUANTUM_AUDIT_TOOLS,
                # This action remains a hard safety prerequisite. The provider
                # may select automatically, while the server still accepts
                # only this exact tool with exact normalized arguments.
                tool_choice=QUANTUM_AUDIT_TOOL_CHOICE,
                temperature=0,
                timeout=CONFIG_LLM_TIMEOUT_S,
            )
            audit_message = audit_response.choices[0].message
            audit_result, audit_error = _audit_result_from_required_tool_call(audit_message, audit_request)
            if audit_error:
                public_reason = {
                    "model_no_tool_call": "模型未按要求调用原始量子输入审计工具",
                    "model_invalid_tool_call": "模型调用了不允许的审计工具",
                    "model_invalid_tool_arguments": "模型提供的审计工具参数无效",
                    "model_audit_scope_mismatch": "模型审计的后端或组分与已归一化方案不一致",
                    "quantum_input_audit_invalid_response": "原始量子输入审计工具返回无效结果",
                }.get(audit_error, "量子输入审计未完成")
                h = list(user_h) + [{
                    "role": "assistant",
                    "content": (
                        "⚠️ 方案未通过量子输入审计（invalid_quantum_input）："
                        f"{public_reason}（{audit_error}）。"
                        "本次方案已安全终止，未生成待确认配置。"
                    ),
                }]
                yield _emit(h, pending_plan)
                return
            if not audit_result.get("ok") or not _audit_covers_candidate(audit_result, semantic_draft):
                details = "；".join(str(item) for item in audit_result.get("issues", [])[:16])
                h = list(user_h) + [{
                    "role": "assistant",
                    "content": (
                        f"⚠️ 原始量子输入检查未通过：{details or '审计目标与候选方案不一致'}"
                        "\n\n请补齐或修正与所选后端对应的输入文件后重新提交。"
                    ),
                }]
                yield _emit(h, pending_plan)
                return
            # The model-facing audit is a safety prerequisite, not a user-facing
            # conclusion.  The independently re-read server audit below is the
            # only audit result rendered in the proposal conversation.

            strict_response = _DS.chat.completions.create(
                model=_LLM_SETTINGS.model if _LLM_SETTINGS else DEFAULT_LLM_MODEL,
                messages=[
                    {"role": "system", "content": get_system_prompt(execution_facts, stage="strict_json")},
                    {"role": "user", "content": _strict_config_context(
                        user_request=message,
                        semantic_draft=semantic_draft,
                        audit=audit_result,
                        execution_facts=execution_facts,
                        mode=request_mode,
                    )},
                ],
                temperature=0,
                timeout=CONFIG_LLM_TIMEOUT_S,
            )
            strict_message = strict_response.choices[0].message
            if strict_message.tool_calls:
                h = list(user_h) + [{
                    "role": "assistant",
                    "content": "⚠️ 方案未通过最终配置校验：模型在无工具阶段请求了工具调用。本次方案已安全终止。",
                }]
                yield _emit(h, pending_plan)
                return
            strict_config = _j(strict_message.content or "")
            strict_error = strict_config.get("error") if isinstance(strict_config, dict) else None
            if strict_error:
                detail = strict_error.get("detail", "配置生成未通过") if isinstance(strict_error, Mapping) else str(strict_error)
                h = list(user_h) + [{"role": "assistant", "content": f"⚠️ 方案未通过最终配置校验：{detail}"}]
                yield _emit(h, pending_plan)
                return
            if not isinstance(strict_config, Mapping) or not strict_config.get("residues"):
                h = list(user_h) + [{
                    "role": "assistant",
                    "content": "⚠️ 方案未通过最终配置校验：模型未返回有效配置。本次方案已安全终止。",
                }]
                yield _emit(h, pending_plan)
                return
            try:
                strict_config = _bind_ion_charge_scale(strict_config, expected_scale)
            except ValueError as exc:
                h = user_h + [{"role": "assistant", "content": f"⚠️ 方案未通过缩放参数校验：{exc}"}]
                yield _emit(h, pending_plan)
                return
            if not _audit_covers_candidate(audit_result, strict_config):
                h = list(user_h) + [{
                    "role": "assistant",
                    "content": "⚠️ 方案未通过最终配置校验：最终配置的后端或组分与已审计输入不一致。",
                }]
                yield _emit(h, pending_plan)
                return
            try:
                strict_config = _bind_strict_solvents(strict_config, semantic_draft)
            except ValueError as exc:
                h = user_h + [{"role": "assistant", "content": f"⚠️ 最终配置未通过溶剂校验：{exc}"}]
                yield _emit(h, pending_plan)
                return
            candidate_with_execution = _attach_trusted_execution(
                strict_config,
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
            component_count = len(candidate_config.get("residues", {}))
            progress_lines.append(
                f"✅ 服务端原始输入复核通过: {component_count} 个组分"
            )
            break
        except Exception as error:
            notice, retryable = _llm_failure_notice(error)
            if not retryable or attempt == CONFIG_MAX_ATTEMPTS - 1:
                h = list(user_h) + [{"role": "assistant", "content": notice}]
                yield _emit(h, pending_plan)
                return
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
