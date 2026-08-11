"""
toolist_simulation.py
=====================
Layer 3 — Simulation Agent 的 14 个工具定义与处理函数。

工具:
  tools_retry_mdp, tools_retry_box, tools_retry_em, tools_retry_eq,
  tools_retry_prod, tools_run_em_simulation, tools_run_eq_simulation,
  tools_run_prod_simulation,
  tools_configure_outputs_simulation,
  tools_configure_prod_simulation, tools_diagnose_error_simulation,
  tools_modify_config_simulation, tools_migrate_md_config_simulation,
  tools_lookup_mdrun_knowledge
"""

from __future__ import annotations
import json as _json
from pathlib import Path
from typing import Callable

from willy._paths import get_project_root
from willy.config_store import write_json
from willy.errors import StepResult, StepError, ErrorKind
from willy.log_parsers import parse_gromacs_log
from willy.step_registry import EM_STEP, EQ_STEP, MDP_STEP, PACKMOL_STEP, PROD_STEP, STEP_REGISTRY

ROOT = get_project_root()


MDRUN_KNOWLEDGE_TOOL = {
    "type": "function",
    "function": {
        "name": "tools_lookup_mdrun_knowledge",
        "description": "只读检索 GROMACS mdrun 知识条目。必须同时提供条目 number 和完整 name；单次最多 3 条。",
        "parameters": {
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "properties": {
                            "number": {"type": "integer", "description": "知识库条目数字编号"},
                            "name": {"type": "string", "description": "与索引完全对应的条目名称"},
                        },
                        "required": ["number", "name"],
                        "additionalProperties": False,
                    },
                    "description": "只从提示词提供的 number + name 索引中选择，最多 3 条",
                },
            },
            "required": ["entries"],
            "additionalProperties": False,
        },
    },
}


def _gromacs_run_tool(stage: str, description: str) -> dict:
    """Build the schema shared by the explicit GROMACS stage tools."""
    return {
        "type": "function",
        "function": {
            "name": f"tools_run_{stage}_simulation",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "work_dir": {
                        "type": "string",
                        "description": "当前 run 工作目录；已绑定工作区时忽略此值",
                    },
                    "top_path": {
                        "type": "string",
                        "description": "topol.top 路径，默认当前工作目录的 topol.top",
                    },
                    "itp_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "参与 grompp 的 .itp 文件路径；默认当前 run 的全部 .itp",
                    },
                    "mdp_path": {
                        "type": "string",
                        "description": f"{stage}.mdp 路径，默认当前工作目录",
                    },
                    "structure_path": {
                        "type": "string",
                        "description": "EM 使用 model.pdb；EQ 使用 em.gro；PROD 使用 eq.gro",
                    },
                    "pdb_path": {
                        "type": "string",
                        "description": "EM 起始 PDB 路径；与 structure_path 等价，保留此字段以明确 GROMACS 输入契约",
                    },
                    "tpr_path": {
                        "type": "string",
                        "description": f"临时 {stage}.tpr 输出路径，默认当前工作目录",
                    },
                },
                "required": [],
            },
        },
    }

# ============================================================
# Tool Definitions
# ============================================================

SIMULATION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "tools_retry_mdp",
            "description": "用修改后的参数重新生成一个或全部 MDP 文件（em.mdp, eq.mdp, prod.mdp）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "stage": {
                        "type": "string",
                        "enum": ["em", "eq", "prod", "all"],
                        "description": "要重新生成哪个 MDP 文件",
                    },
                    "dt": {"type": "number", "description": "时间步长 (ps)"},
                    "ref_p": {"type": "number", "description": "参考压力 (bar)"},
                    "eq_high_temperature": {"type": "number", "description": "EQ 退火高温 (K)"},
                    "eq_transition_temperature": {"type": "number", "description": "EQ 退火过渡温度 (K)"},
                    "eq_target_temperature": {"type": "number", "description": "EQ 目标温度 (K)"},
                    "eq_segments_ns": {"type": "object", "description": "六段 EQ 时长：heat/hold_high/cool_transition/hold_transition/cool_target/hold_target（ns）"},
                    "eq_acceptance": {"type": "object", "description": "EQ 最终保持段验收窗口和统计阈值"},
                    "prod_duration_ns": {"type": "number", "description": "PROD 时长 (ns, 2-200)"},
                    "prod_temperature": {"type": "number", "description": "PROD 温度；必须等于 EQ 目标温度"},
                    "nsteps": {"type": "integer", "description": "覆盖 nsteps（EM 专用）"},
                    "emtol": {"type": "number", "description": "EM 容差 (kJ/mol/nm, 默认 100)"},
                    "emstep": {"type": "number", "description": "EM 步长 (nm, 默认 0.01)"},
                    "tau_t": {"type": "number", "description": "温度耦合常数"},
                    "eq_tau_p": {"type": "number", "description": "EQ 压力耦合常数"},
                    "prod_tau_p": {"type": "number", "description": "PROD 压力耦合常数"},
                    "tcoupl": {
                        "type": "string",
                        "enum": ["V-rescale", "Nose-Hoover", "Berendsen", "Andersen"],
                    },
                    "pcoupl": {
                        "type": "string",
                        "enum": ["C-rescale", "Berendsen", "Parrinello-Rahman"],
                    },
                    "constraints": {
                        "type": "string",
                        "enum": ["hbonds", "all-bonds", "none"],
                    },
                    "rcoulomb": {"type": "number", "description": "库仑截断 (nm)"},
                    "rvdw": {"type": "number", "description": "VDW 截断 (nm)"},
                    "run_seed": {"type": "integer", "description": "速度生成随机种子"},
                },
                "required": ["stage"],
            },
        },
    },
    _gromacs_run_tool(
        "em",
        "执行 GROMACS 能量最小化。运行前校验 topol.top、.itp、em.mdp、model.pdb；成功时至少返回 em.tpr/em.gro/em.xtc/em.edr。",
    ),
    _gromacs_run_tool(
        "eq",
        "执行 GROMACS 三点式退火平衡。运行前校验 topol.top、.itp、eq.mdp、em.gro；仅在最终保持段温度均值和势能线性斜率验收通过时成功。",
    ),
    _gromacs_run_tool(
        "prod",
        "执行 GROMACS 生产模拟。运行前校验 topol.top、.itp、prod.mdp、已验收 EQ 的 eq.gro/eq.cpt；成功时至少返回 prod.tpr/prod.gro/prod.xtc/prod.edr。",
    ),
    {
        "type": "function",
        "function": {
            "name": "tools_retry_box",
            "description": "用目标质量密度、显式盒边长或容差重新构建 Packmol 周期模拟盒子。实际边长由当前 .itp 质量计算并审计。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_mass_density_g_cm3": {
                        "type": "number",
                        "description": "初始目标质量密度 (g/cm3)，默认 0.7；仅用于初始建盒，不替代 EQ 验收",
                    },
                    "packing_number_density_nm3": {
                        "type": "number",
                        "description": "历史配置兼容：Packmol 填充数密度 (分子/nm³)；新方案优先使用目标质量密度",
                    },
                    "box_size": {
                        "type": "number",
                        "description": "显式盒子边长 (Å)（设置后覆盖密度）",
                    },
                    "tolerance": {
                        "type": "number",
                        "description": "Packmol 分子间最小距离 (Å, 默认 2.0)",
                    },
                    "seed": {"type": "integer", "description": "Packmol 随机种子（-1 = 随机）"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_configure_outputs_simulation",
            "description": "在任何 GROMACS 阶段启动前配置可选模拟产物。必需的 .gro/.xtc/.edr 不可关闭；当前支持可选全精度 .trr。",
            "parameters": {
                "type": "object",
                "properties": {
                    "trr": {"type": "boolean", "description": "是否额外输出全精度 .trr 轨迹"},
                },
                "required": ["trr"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_configure_prod_simulation",
            "description": "在 prod.tpr 生成前修改生产协议。全局字段会重建所有受影响的下游 MDP 并失效旧阶段产物。",
            "parameters": {
                "type": "object",
                "properties": {
                    "dt": {"type": "number", "description": "生产阶段时间步长 (ps)"},
                    "prod_duration_ns": {"type": "number", "description": "生产阶段时长 (ns, 2-200)"},
                    "prod_temperature": {"type": "number", "description": "生产温度 (K，必须等于 EQ 目标温度)"},
                    "ref_p": {"type": "number", "description": "参考压力 (bar)"},
                    "tcoupl": {"type": "string"},
                    "tau_t": {"type": "number"},
                    "pcoupl": {"type": "string"},
                    "prod_tau_p": {"type": "number"},
                    "constraints": {"type": "string"},
                    "lincs_iter": {"type": "integer"},
                    "lincs_order": {"type": "integer"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_retry_em",
            "description": "用修改后的参数重试 GROMACS 能量最小化。",
            "parameters": {
                "type": "object",
                "properties": {
                    "work_dir": {"type": "string", "description": "工作目录路径"},
                    "nsteps": {"type": "integer", "description": "覆盖最大 EM 步数"},
                    "emtol": {"type": "number", "description": "覆盖 EM 力容差"},
                    "constraints": {
                        "type": "string",
                        "enum": ["none", "hbonds"],
                        "description": "EM 期间的约束算法（通常为 'none'）",
                    },
                },
                "required": ["work_dir"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_retry_eq",
            "description": "用修改后的参数重试 GROMACS 三点式退火平衡。",
            "parameters": {
                "type": "object",
                "properties": {
                    "work_dir": {"type": "string", "description": "工作目录路径"},
                    "dt": {"type": "number", "description": "时间步长 (ps)"},
                    "eq_segments_ns": {"type": "object", "description": "六段 EQ 时长对象"},
                    "eq_acceptance": {"type": "object", "description": "EQ 验收窗口和统计阈值"},
                    "tau_t": {"type": "number"},
                    "eq_tau_p": {"type": "number"},
                    "eq_high_temperature": {"type": "number", "description": "退火高温 (K)"},
                    "eq_transition_temperature": {"type": "number", "description": "退火过渡温度 (K)"},
                    "eq_target_temperature": {"type": "number"},
                    "ref_p": {"type": "number"},
                    "tcoupl": {"type": "string", "enum": ["V-rescale", "Nose-Hoover"]},
                    "pcoupl": {"type": "string", "enum": ["C-rescale", "Berendsen", "Parrinello-Rahman"]},
                    "run_seed": {"type": "integer", "description": "新的运行级速度随机种子"},
                },
                "required": ["work_dir"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_retry_prod",
            "description": "重试 GROMACS 生产 MD；仅当 manifest 指纹一致时才会使用 checkpoint append。",
            "parameters": {
                "type": "object",
                "properties": {
                    "work_dir": {"type": "string", "description": "工作目录路径"},
                    "dt": {"type": "number", "description": "时间步长 (ps)"},
                },
                "required": ["work_dir"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_diagnose_error_simulation",
            "description": "分析 GROMACS 日志文件以确定 MD 模拟失败的根本原因。检查：LINCS 警告、温度/密度不稳定、原子冲突、PME 问题、域分解问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "step": {
                        "type": "string",
                        "enum": ["em", "eq", "prod"],
                        "description": "哪个 MD 步骤失败了",
                    },
                    "work_dir": {"type": "string", "description": "包含日志文件的工作目录"},
                    "error_kind": {
                        "type": "string",
                        "enum": ["grompp_failed", "mdrun_failed", "em_not_converged", "eq_not_converged", "unknown"],
                    },
                    "grompp_stderr": {"type": "string", "description": "grompp stderr 输出（最后 1000 字符）"},
                    "mdrun_stderr": {"type": "string", "description": "mdrun stderr 输出（最后 1000 字符）"},
                },
                "required": ["step", "work_dir"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_modify_config_simulation",
            "description": "用修改后的模拟参数更新 config.json 中的 MD 段。提供 work_dir 时会重建受影响 MDP 并失效旧阶段产物。",
            "parameters": {
                "type": "object",
                "properties": {
                    "work_dir": {"type": "string", "description": "当前 run；省略仅允许为尚未启动的新 run 配置"},
                    "dt": {"type": "number"},
                    "ref_p": {"type": "number"},
                    "eq_high_temperature": {"type": "number"},
                    "eq_transition_temperature": {"type": "number"},
                    "eq_target_temperature": {"type": "number"},
                    "eq_segments_ns": {"type": "object"},
                    "eq_acceptance": {"type": "object"},
                    "prod_duration_ns": {"type": "number"},
                    "prod_temperature": {"type": "number"},
                    "tcoupl": {"type": "string"},
                    "tau_t": {"type": "number"},
                    "pcoupl": {"type": "string"},
                    "eq_tau_p": {"type": "number"},
                    "prod_tau_p": {"type": "number"},
                    "constraints": {"type": "string"},
                    "rcoulomb": {"type": "number"},
                    "rvdw": {"type": "number"},
                    "compressibility": {"type": "string"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_migrate_md_config_simulation",
            "description": "显式迁移旧 eq_ns/prod_ns 配置。采用迁移后的 v2 协议必须经过服务端用户确认授权；工具参数不能授权该操作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_path": {"type": "string", "description": "旧配置路径，默认当前 config.json"},
                    "output_path": {"type": "string", "description": "v2 配置输出路径，默认 config.v2.json"},
                    "adopt": {"type": "boolean", "description": "原子替换 source_path 为已验证的 v2 配置"},
                },
                "required": [],
            },
        },
    },
    MDRUN_KNOWLEDGE_TOOL,
]

# ============================================================
# Tool 分类元数据
# ============================================================

TOOL_META = {
    "tools_retry_mdp":                   {"category": "action",     "mutating": True,  "risk": "medium", "requires_confirmation": True, "effect": "requires_confirmation"},
    "tools_retry_box":                   {"category": "action",     "mutating": True,  "risk": "high", "effect": "requires_confirmation"},
    "tools_retry_em":                    {"category": "action",     "mutating": True,  "risk": "high", "effect": "retry_safe"},
    "tools_retry_eq":                    {"category": "action",     "mutating": True,  "risk": "high", "requires_confirmation": True, "effect": "requires_confirmation"},
    "tools_retry_prod":                  {"category": "action",     "mutating": True,  "risk": "high", "requires_confirmation": True, "effect": "requires_confirmation"},
    "tools_run_em_simulation":           {"category": "action",     "mutating": True,  "risk": "high", "effect": "retry_safe"},
    "tools_run_eq_simulation":           {"category": "action",     "mutating": True,  "risk": "high", "effect": "retry_safe"},
    "tools_run_prod_simulation":         {"category": "action",     "mutating": True,  "risk": "high", "effect": "retry_safe"},
    "tools_configure_outputs_simulation": {"category": "config",     "mutating": True,  "risk": "medium", "requires_confirmation": True, "effect": "requires_confirmation"},
    "tools_configure_prod_simulation":    {"category": "config",     "mutating": True,  "risk": "medium", "requires_confirmation": True, "effect": "requires_confirmation"},
    "tools_diagnose_error_simulation":    {"category": "diagnostic", "mutating": False, "risk": "low", "effect": "read_only"},
    "tools_modify_config_simulation":     {"category": "config",     "mutating": True,  "risk": "medium", "requires_confirmation": True, "effect": "requires_confirmation"},
    "tools_migrate_md_config_simulation": {"category": "config",     "mutating": True,  "risk": "medium", "requires_confirmation": True, "effect": "requires_confirmation"},
    "tools_lookup_mdrun_knowledge":       {"category": "diagnostic", "mutating": False, "risk": "low", "effect": "read_only"},
}


def _tool_step_error(step_name: str, step_index: int, message: str) -> str:
    result = StepResult(
        step_name=step_name,
        step_index=step_index,
        success=False,
        error=StepError(kind=ErrorKind.CONFIG_INVALID, message=message),
    )
    return _json.dumps(result.to_dict(), ensure_ascii=False)


def _execution_dir(args: dict, workspace: Path | None) -> Path | None:
    if workspace is not None:
        return workspace.resolve()
    value = args.get("work_dir")
    return Path(value).resolve() if value else None


def _workspace_path(workspace: Path, value: object, label: str) -> str | None:
    """Resolve an optional tool file argument without permitting path escape."""
    if value is None:
        return None
    candidate = Path(str(value))
    candidate = candidate if candidate.is_absolute() else workspace / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} 必须位于当前 run 工作目录") from exc
    return str(resolved)


def _load_v2_config(path: Path) -> dict:
    try:
        config = _json.loads(path.read_text())
    except (OSError, _json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 config.json: {exc}") from exc
    from willy.simulation.protocol import require_valid_md_config
    try:
        normalized, _ = require_valid_md_config(config.get("md", {}))
    except ValueError as exc:
        raise ValueError(f"MD v2 配置无效: {exc}") from exc
    config["md"] = normalized
    return config


def _apply_v2_md_updates(config: dict, args: dict) -> list[str]:
    """Apply explicit protocol paths and return the persisted field names."""
    md = config["md"]
    updated: list[str] = []
    direct = ("dt", "ref_p", "tcoupl", "tau_t", "pcoupl", "constraints", "rcoulomb", "rvdw", "compressibility", "lincs_iter", "lincs_order", "run_seed", "nsteps", "emtol", "emstep")
    for name in direct:
        if args.get(name) is not None:
            md[name] = args[name]
            updated.append(name)
    nested = {
        "eq_high_temperature": ("eq", "high_temperature"),
        "eq_transition_temperature": ("eq", "transition_temperature"),
        "eq_target_temperature": ("eq", "target_temperature"),
        "eq_tau_p": ("eq", "tau_p"),
        "prod_duration_ns": ("prod", "duration_ns"),
        "prod_temperature": ("prod", "temperature"),
        "prod_tau_p": ("prod", "tau_p"),
    }
    for name, (section, field) in nested.items():
        if args.get(name) is not None:
            md[section][field] = args[name]
            updated.append(name)
    if args.get("eq_segments_ns") is not None:
        if not isinstance(args["eq_segments_ns"], dict):
            raise ValueError("eq_segments_ns 必须是对象")
        md["eq"]["segments_ns"].update(args["eq_segments_ns"])
        updated.append("eq_segments_ns")
    if args.get("eq_acceptance") is not None:
        if not isinstance(args["eq_acceptance"], dict):
            raise ValueError("eq_acceptance 必须是对象")
        md["eq"]["acceptance"].update(args["eq_acceptance"])
        updated.append("eq_acceptance")
    from willy.simulation.protocol import require_valid_md_config
    normalized, _ = require_valid_md_config(md)
    config["md"] = normalized
    return updated


def _write_config(path: Path, config: dict) -> None:
    write_json(path, config)


def _notify_config_updated(
    callback: Callable[[dict, dict, list[str]], None] | None,
    before: dict,
    after: dict,
    updated: list[str],
) -> None:
    """Publish a bounded in-process notification after an atomic config write.

    The callback feeds the state machine only; failures here must not turn a
    valid MD repair into a failed scientific operation.
    """
    if callback is None or not updated:
        return
    try:
        callback(before, after, updated)
    except Exception as exc:
        print(f"[simulation] ⚠ 无法发布配置修复状态: {exc}")


_MD_STAGE_ORDER = ("em", "eq", "prod")
_EM_PROTOCOL_FIELDS = {
    "nsteps", "emtol", "emstep", "constraints", "outputs",
    "rcoulomb", "rvdw", "coulombtype", "vdwtype", "dispersion_correction",
}
_EQ_PROTOCOL_FIELDS = {
    "eq_high_temperature", "eq_transition_temperature", "eq_target_temperature",
    "eq_segments_ns", "eq_tau_p", "eq_acceptance", "run_seed",
}
_PROD_PROTOCOL_FIELDS = {"prod_duration_ns", "prod_temperature", "prod_tau_p"}


def _earliest_affected_stage(updated: list[str]) -> str | None:
    """Return the first stage whose MDP/physical output must be replaced."""
    if not updated:
        return None
    values = set(updated)
    if values & _EM_PROTOCOL_FIELDS:
        return "em"
    # These controls appear in both equilibrium and production MDPs, and some
    # (dt/pressure/thermostat) also determine the accepted EQ state.
    if values & _EQ_PROTOCOL_FIELDS:
        return "eq"
    if values & _PROD_PROTOCOL_FIELDS:
        return "prod"
    return "em"


def _rebuild_affected_mdps(
    *,
    config_path: Path,
    execution_dir: Path | None,
    updated: list[str],
    requested_stage: str | None = None,
) -> tuple[object | None, tuple[str, ...], list[str]]:
    """Regenerate dependent MDPs and invalidate stale accepted outputs.

    Global MD configuration is run-wide.  Consequently a request such as
    ``stage=eq, dt=...`` cannot leave a stale ``prod.mdp`` behind.
    """
    if execution_dir is None:
        return None, (), []
    earliest = _earliest_affected_stage(updated)
    if earliest is None:
        if requested_stage in (None, "all"):
            stages = _MD_STAGE_ORDER
            earliest = "em"
        else:
            stages = (requested_stage,)
            earliest = requested_stage
    else:
        stages = _MD_STAGE_ORDER[_MD_STAGE_ORDER.index(earliest):]

    from willy.simulation.mdp import build_all as _mdp_build
    result = _mdp_build(
        config_path=str(config_path), output_dir=str(execution_dir), stages=stages,
    )
    invalidated: list[str] = []
    if getattr(result, "success", False):
        from willy.simulation.manifest import invalidate_stages_from, manifest_exists
        if not manifest_exists(execution_dir):
            return result, stages, invalidated
        invalidated = invalidate_stages_from(
            execution_dir,
            earliest,
            reason="MD 配置已更新: " + ", ".join(updated or stages),
        )
    if getattr(result, "extra", None) is not None:
        result.extra.update({
            "regenerated_stages": list(stages),
            "invalidated_stages": invalidated,
        })
    return result, stages, invalidated


_PROTOCOL_FIELD_LABELS = {
    "dt": "时间步长",
    "ref_p": "参考压力",
    "eq_high_temperature": "EQ 高温",
    "eq_transition_temperature": "EQ 过渡温度",
    "eq_target_temperature": "EQ 目标温度",
    "eq_segments_ns": "EQ 各阶段时长",
    "eq_acceptance": "EQ 验收条件",
    "prod_duration_ns": "生产模拟时长",
    "prod_temperature": "生产模拟温度",
    "nsteps": "能量最小化步数",
    "emtol": "能量最小化收敛阈值",
    "emstep": "能量最小化步长",
    "tcoupl": "恒温器",
    "tau_t": "恒温耦合时间",
    "pcoupl": "压力耦合器",
    "eq_tau_p": "EQ 压力耦合时间",
    "prod_tau_p": "生产压力耦合时间",
    "constraints": "键约束",
    "rcoulomb": "库仑截断",
    "rvdw": "范德华截断",
    "compressibility": "可压缩率",
    "lincs_iter": "LINCS 迭代次数",
    "lincs_order": "LINCS 阶数",
    "run_seed": "随机种子",
    "trr": "全精度轨迹输出",
    "adopt": "采用迁移后的 MD 协议",
}

_TOOL_PROTOCOL_FIELDS = {
    "tools_retry_mdp": frozenset(_PROTOCOL_FIELD_LABELS) - {"trr", "adopt"},
    "tools_retry_eq": frozenset({
        "dt", "eq_segments_ns", "eq_acceptance", "tau_t", "eq_tau_p",
        "eq_high_temperature", "eq_transition_temperature", "eq_target_temperature",
        "ref_p", "tcoupl", "pcoupl", "run_seed",
    }),
    "tools_retry_prod": frozenset({"dt"}),
    "tools_configure_outputs_simulation": frozenset({"trr"}),
    "tools_configure_prod_simulation": frozenset({
        "dt", "prod_duration_ns", "prod_temperature", "ref_p", "tcoupl", "tau_t",
        "pcoupl", "prod_tau_p", "constraints", "lincs_iter", "lincs_order",
    }),
    "tools_modify_config_simulation": frozenset(_PROTOCOL_FIELD_LABELS) - {"trr", "adopt"},
}


def protocol_change_request(tool_name: str, args: dict) -> tuple[str, ...]:
    """Return the protocol fields requested by a tool call, without values.

    The result is deliberately derived from a fixed allow-list.  It can be
    used in public status without reflecting arbitrary model-provided text.
    """
    if tool_name == "tools_migrate_md_config_simulation":
        return ("adopt",) if args.get("adopt") is True else ()
    fields = _TOOL_PROTOCOL_FIELDS.get(tool_name, frozenset())
    return tuple(name for name in _PROTOCOL_FIELD_LABELS if name in fields and args.get(name) is not None)


def _protocol_confirmation_result(tool_name: str, fields: tuple[str, ...]) -> str:
    step_name, step_index = {
        "tools_retry_mdp": ("mdp", MDP_STEP),
        "tools_configure_outputs_simulation": ("mdp", MDP_STEP),
        "tools_retry_eq": ("eq", EQ_STEP),
        "tools_retry_prod": ("prod", PROD_STEP),
        "tools_configure_prod_simulation": ("prod", PROD_STEP),
        "tools_modify_config_simulation": ("mdp", MDP_STEP),
        "tools_migrate_md_config_simulation": ("mdp", MDP_STEP),
    }.get(tool_name, ("mdp", MDP_STEP))
    result = StepResult(
        step_name=step_name,
        step_index=step_index,
        success=False,
        error=StepError(
            kind=ErrorKind.USER_CONFIRMATION_REQUIRED,
            message="模拟协议变更需要用户确认；自动修复未写入配置。",
        ),
        extra={
            "requires_user_confirmation": True,
            "protocol_fields": [_PROTOCOL_FIELD_LABELS[field] for field in fields],
        },
    )
    return _json.dumps(result.to_dict(), ensure_ascii=False)


def _run_gromacs_stage_tool(stage: str, args: dict, workspace: Path | None) -> str:
    """Dispatch an explicit stage tool with an input contract constrained to its run."""
    execution_dir = _execution_dir(args, workspace)
    if execution_dir is None:
        return _tool_step_error(f"md_{stage}", STEP_REGISTRY.index_for_stage(stage) or EM_STEP, "缺少 work_dir")
    try:
        topol = _workspace_path(execution_dir, args.get("top_path"), "top_path")
        mdp = _workspace_path(execution_dir, args.get("mdp_path"), "mdp_path")
        structure_value = args.get("structure_path", args.get("pdb_path"))
        structure = _workspace_path(execution_dir, structure_value, "structure_path")
        tpr = _workspace_path(execution_dir, args.get("tpr_path"), "tpr_path")
        raw_itps = args.get("itp_paths")
        itps = None if raw_itps is None else [
            _workspace_path(execution_dir, value, "itp_paths") for value in raw_itps
        ]
    except (TypeError, ValueError) as exc:
        return _tool_step_error(f"md_{stage}", STEP_REGISTRY.index_for_stage(stage) or EM_STEP, str(exc))

    if stage == "em":
        from willy.simulation.em import run_em
        result = run_em(work_dir=str(execution_dir), mdp=mdp, conf=structure, topol=topol or "topol.top", itps=itps, tpr=tpr)
    elif stage == "eq":
        from willy.simulation.eq import run_eq
        result = run_eq(work_dir=str(execution_dir), mdp=mdp, conf=structure, topol=topol or "topol.top", itps=itps, tpr=tpr)
    else:
        from willy.simulation.prod import run_prod
        result = run_prod(work_dir=str(execution_dir), mdp=mdp, conf=structure, topol=topol or "topol.top", itps=itps, tpr=tpr)
    return _json.dumps(result.to_dict(), ensure_ascii=False)


# ============================================================
# Tool Handler
# ============================================================

def handle_simulation_tool_call(
    tool_name: str,
    args: dict,
    work_dir: str | None = None,
    config_path: str | None = None,
    on_config_updated: Callable[[dict, dict, list[str]], None] | None = None,
    protocol_change_authorized: bool = False,
) -> str:
    """Layer 3 工具调用分发器。

    ``protocol_change_authorized`` is a server-side capability granted only by
    a real user-confirmation workflow. It is intentionally not a tool
    argument, so an LLM cannot self-authorize a protocol change.
    """

    workspace = Path(work_dir) if work_dir else None
    active_config = Path(config_path) if config_path else ROOT / "config.json"
    isolated_context = work_dir is not None or config_path is not None

    if tool_name == "tools_lookup_mdrun_knowledge":
        from willy.simulation.mdrun_knowledge import lookup_mdrun_knowledge
        entries = args.get("entries") if isinstance(args, dict) else None
        return _json.dumps(lookup_mdrun_knowledge(entries), ensure_ascii=False)

    requested_fields = protocol_change_request(tool_name, args)
    if requested_fields and not protocol_change_authorized:
        return _protocol_confirmation_result(tool_name, requested_fields)

    if tool_name in {
        "tools_run_em_simulation",
        "tools_run_eq_simulation",
        "tools_run_prod_simulation",
    }:
        stage = tool_name.removeprefix("tools_run_").removesuffix("_simulation")
        return _run_gromacs_stage_tool(stage, args, workspace)

    if tool_name == "tools_configure_outputs_simulation":
        execution_dir = _execution_dir(args, workspace)
        if execution_dir is None:
            return _tool_step_error("mdp", MDP_STEP, "缺少 run 工作目录，不能配置本次模拟输出")
        if any((execution_dir / f"{stage}.tpr").exists() for stage in ("em", "eq", "prod")):
            return _tool_step_error("mdp", MDP_STEP, "已有 GROMACS 阶段开始，不能修改本次 run 的输出选项")
        try:
            config = _load_v2_config(active_config)
        except ValueError as exc:
            return _tool_step_error("mdp", MDP_STEP, str(exc))
        before = _json.loads(_json.dumps(config))
        outputs = config["md"].setdefault("outputs", {})
        outputs["trr"] = bool(args["trr"])
        _write_config(active_config, config)
        _notify_config_updated(on_config_updated, before, config, ["outputs.trr"])
        result, _, _ = _rebuild_affected_mdps(
            config_path=active_config,
            execution_dir=execution_dir,
            updated=["outputs"],
        )
        assert result is not None
        return _json.dumps(result.to_dict(), ensure_ascii=False)

    if tool_name == "tools_configure_prod_simulation":
        execution_dir = _execution_dir(args, workspace)
        if execution_dir is None:
            return _tool_step_error("prod", PROD_STEP, "缺少 run 工作目录，不能配置 prod.mdp")
        if (execution_dir / "prod.tpr").exists():
            return _tool_step_error("prod", PROD_STEP, "prod.tpr 已生成；生产运行开始后不能再修改 prod.mdp")
        try:
            config = _load_v2_config(active_config)
            before = _json.loads(_json.dumps(config))
            updated = _apply_v2_md_updates(config, args)
            _write_config(active_config, config)
        except ValueError as exc:
            return _tool_step_error("prod", PROD_STEP, str(exc))
        _notify_config_updated(on_config_updated, before, config, updated)
        result, _, _ = _rebuild_affected_mdps(
            config_path=active_config,
            execution_dir=execution_dir,
            updated=updated,
            requested_stage="prod",
        )
        if result is None:
            return _tool_step_error("prod", PROD_STEP, "缺少 run 工作目录")
        return _json.dumps(result.to_dict(), ensure_ascii=False)

    if tool_name == "tools_retry_mdp":
        stage = args.get("stage", "all")
        try:
            config = _load_v2_config(active_config)
            before = _json.loads(_json.dumps(config))
            updated = _apply_v2_md_updates(config, args)
            _write_config(active_config, config)
        except ValueError as exc:
            return _tool_step_error("mdp", MDP_STEP, str(exc))
        _notify_config_updated(on_config_updated, before, config, updated)
        execution_dir = workspace or Path("process")
        sr, _, _ = _rebuild_affected_mdps(
            config_path=active_config,
            execution_dir=execution_dir,
            updated=updated,
            requested_stage=stage,
        )
        assert sr is not None
        return _json.dumps(sr.to_dict(), ensure_ascii=False)

    elif tool_name == "tools_retry_box":
        from willy.simulation.box import auto_from_config, InpGenerator
        try:
            config_data = _load_v2_config(active_config)
            before = _json.loads(_json.dumps(config_data))
            box = config_data.setdefault("box", {})
            if args.get("target_mass_density_g_cm3") is not None:
                box["target_mass_density_g_cm3"] = args["target_mass_density_g_cm3"]
                if args.get("box_size") is None:
                    box["box_size"] = None
            elif args.get("packing_number_density_nm3") is not None:
                box["packing_number_density_nm3"] = args["packing_number_density_nm3"]
                box.pop("target_mass_density_g_cm3", None)
                if args.get("box_size") is None:
                    box["box_size"] = None
            if args.get("box_size") is not None:
                box["box_size"] = args["box_size"]
            if args.get("tolerance") is not None:
                box["tolerance"] = args["tolerance"]
            if args.get("seed") is not None:
                box["seed"] = args["seed"]
            _write_config(active_config, config_data)
        except ValueError as exc:
            return _tool_step_error("box", PACKMOL_STEP, str(exc))
        _notify_config_updated(
            on_config_updated,
            before,
            config_data,
            [
                f"box.{name}"
                for name in ("target_mass_density_g_cm3", "packing_number_density_nm3", "box_size", "tolerance", "seed")
                if args.get(name) is not None
            ],
        )
        if workspace is None:
            return _tool_step_error("box", PACKMOL_STEP, "Packmol 重试必须绑定当前 run workspace")
        config = auto_from_config(
            config_path=str(active_config),
            gro_dir=str(workspace),
            pdb_dir=str(workspace),
            output_dir=str(workspace),
        )
        gen = InpGenerator(config)
        sr = gen.run()
        if workspace is not None:
            from willy.simulation.manifest import (
                ManifestError,
                invalidate_stages_from,
                manifest_exists,
                record_box_attempt,
                record_box_execution,
            )
            if manifest_exists(workspace):
                try:
                    evidence = sr.extra.get("box_execution")
                    if not isinstance(evidence, dict):
                        raise ManifestError("Packmol 未返回执行证据")
                    record_box_execution(workspace, evidence)
                except (OSError, ValueError, ManifestError) as exc:
                    return _tool_step_error("box", PACKMOL_STEP, f"无法记录建盒执行证据: {exc}")
        if sr.success and workspace is not None:
            from willy.simulation.manifest import invalidate_stages_from, manifest_exists, record_box_attempt
            if manifest_exists(workspace):
                record_box_attempt(workspace, sr.extra.get("box_parameters", {}))
                invalidated = invalidate_stages_from(
                    workspace,
                    "em",
                    reason="Packmol 建盒参数已更新",
                )
                sr.extra["invalidated_stages"] = invalidated
        return _json.dumps(sr.to_dict(), ensure_ascii=False)

    elif tool_name == "tools_retry_em":
        execution_dir = str(workspace) if workspace else args["work_dir"]
        from willy.simulation.em import run_em
        if args.get("nsteps") is not None or args.get("emtol") is not None or args.get("constraints") is not None:
            from willy.simulation.mdp import build_all as _mdp_build
            try:
                config = _load_v2_config(active_config)
                before = _json.loads(_json.dumps(config))
                updated = _apply_v2_md_updates(config, args)
                _write_config(active_config, config)
            except ValueError as exc:
                return _tool_step_error("em", EM_STEP, str(exc))
            _notify_config_updated(on_config_updated, before, config, updated)
            _rebuild_affected_mdps(
                config_path=active_config,
                execution_dir=Path(execution_dir),
                updated=updated,
                requested_stage="em",
            )
        sr = run_em(work_dir=execution_dir)
        return _json.dumps(sr.to_dict(), ensure_ascii=False)

    elif tool_name == "tools_retry_eq":
        execution_dir = str(workspace) if workspace else args["work_dir"]
        from willy.simulation.eq import run_eq
        if any(args.get(key) is not None for key in (
            "dt", "eq_segments_ns", "eq_acceptance", "tau_t", "eq_tau_p", "eq_high_temperature",
            "eq_transition_temperature", "eq_target_temperature",
            "ref_p", "tcoupl", "pcoupl", "run_seed",
        )):
            from willy.simulation.mdp import build_all as _mdp_build
            try:
                config = _load_v2_config(active_config)
                before = _json.loads(_json.dumps(config))
                updated = _apply_v2_md_updates(config, args)
                _write_config(active_config, config)
            except ValueError as exc:
                return _tool_step_error("eq", EQ_STEP, str(exc))
            _notify_config_updated(on_config_updated, before, config, updated)
            _rebuild_affected_mdps(
                config_path=active_config,
                execution_dir=Path(execution_dir),
                updated=updated,
                requested_stage="eq",
            )
        sr = run_eq(work_dir=execution_dir)
        return _json.dumps(sr.to_dict(), ensure_ascii=False)

    elif tool_name == "tools_retry_prod":
        execution_dir = str(workspace) if workspace else args["work_dir"]
        from willy.simulation.prod import run_prod
        if args.get("dt") is not None:
            from willy.simulation.mdp import build_all as _mdp_build
            try:
                config = _load_v2_config(active_config)
                before = _json.loads(_json.dumps(config))
                updated = _apply_v2_md_updates(config, args)
                _write_config(active_config, config)
            except ValueError as exc:
                return _tool_step_error("prod", PROD_STEP, str(exc))
            _notify_config_updated(on_config_updated, before, config, updated)
            _rebuild_affected_mdps(
                config_path=active_config,
                execution_dir=Path(execution_dir),
                updated=updated,
                requested_stage="prod",
            )
        sr = run_prod(work_dir=execution_dir)
        return _json.dumps(sr.to_dict(), ensure_ascii=False)

    elif tool_name == "tools_diagnose_error_simulation":
        step = args.get("step", "em")
        work_dir = args.get("work_dir", ".")
        log_path = Path(work_dir) / f"{step}.log"

        raw = parse_gromacs_log(str(log_path), stage=step)

        # 额外的 grompp/mdrun stderr 分析
        if args.get("grompp_stderr"):
            gs = args["grompp_stderr"]
            if "atomtype" in gs.lower():
                raw.setdefault("anomalies", []).append("grompp: atomtype 缺失 — 检查 .top/.itp 文件")
            if "moleculetype" in gs.lower():
                raw.setdefault("anomalies", []).append("grompp: moleculetype 不匹配")
        if args.get("mdrun_stderr"):
            ms = args["mdrun_stderr"]
            if "nan" in ms.lower():
                raw.setdefault("anomalies", []).append("mdrun: 检测到 NaN — 模拟数值不稳定")

        severity = "error"
        if raw.get("completed") and not raw.get("anomalies"):
            severity = "info"
        elif raw.get("completed") and raw.get("anomalies"):
            severity = "warning"

        box_parameters = None
        from willy.simulation.manifest import manifest_exists
        if manifest_exists(work_dir):
            try:
                from willy.simulation.manifest import load_manifest
                attempts = load_manifest(work_dir).get("box_attempts", [])
                if isinstance(attempts, list) and attempts and isinstance(attempts[-1], dict):
                    box_parameters = attempts[-1]
            except (OSError, ValueError):
                # Diagnostic evidence remains useful even if optional box
                # provenance was written by an older run format.
                box_parameters = None

        from willy.errors import DiagnosisResult
        dr = DiagnosisResult(
            source="simulation",
            severity=severity,
            issues=raw.get("anomalies", []),
            evidence=raw.get("evidence_lines", []),
            hint=raw.get("hint", ""),
            extra={
                "step": step,
                "completed": raw.get("completed"),
                "lincs_warnings": raw.get("lincs_warnings"),
                "temp_extremes": raw.get("temp_extremes"),
                "density_extremes": raw.get("density_extremes"),
                "box_parameters": box_parameters,
            },
        )
        return _json.dumps(dr.to_dict(), ensure_ascii=False)

    elif tool_name == "tools_modify_config_simulation":
        try:
            config = _load_v2_config(active_config)
            before = _json.loads(_json.dumps(config))
            updated = _apply_v2_md_updates(config, args)
            _write_config(active_config, config)
        except ValueError as exc:
            return _json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        _notify_config_updated(on_config_updated, before, config, updated)
        execution_dir = _execution_dir(args, workspace)
        result, regenerated, invalidated = _rebuild_affected_mdps(
            config_path=active_config,
            execution_dir=execution_dir,
            updated=updated,
        )
        return _json.dumps({
            "ok": True,
            "updated_fields": updated,
            "current_md": config["md"],
            "regenerated_stages": list(regenerated),
            "invalidated_stages": invalidated,
            "warning": (
                "未指定 work_dir；更新仅适用于尚未启动的新 run。"
                if result is None else ""
            ),
        }, ensure_ascii=False)

    elif tool_name == "tools_migrate_md_config_simulation":
        try:
            source = Path(args.get("source_path") or active_config)
            if not source.is_absolute():
                source = (workspace or ROOT) / source
            adopt = args.get("adopt") is True
            output = args.get("output_path")
            if adopt and output:
                raise ValueError("adopt=true 时不能指定 output_path；活动配置将原子替换")
            output_path = None
            if output:
                output_path = Path(output)
                if not output_path.is_absolute():
                    output_path = (workspace or ROOT) / output_path
            if workspace is not None:
                source.resolve().relative_to(workspace.resolve())
                if output_path is not None:
                    output_path.resolve().relative_to(workspace.resolve())
            if adopt:
                if source.resolve() != active_config.resolve():
                    raise ValueError("adopt=true 只能采用当前活动 config.json")
                from willy.workflow_config import migrate_and_adopt_md_config
                target, backup, mapping, report = migrate_and_adopt_md_config(source)
            else:
                from willy.workflow_config import migrate_md_config
                target, mapping, report = migrate_md_config(source, output_path)
        except (OSError, ValueError) as exc:
            return _json.dumps({"ok": False, "error": f"迁移失败: {exc}"}, ensure_ascii=False)
        return _json.dumps({
            "ok": True,
            "config_path": str(target),
            "mapping_path": str(mapping),
            "migration": report,
            "adopted": adopt,
            **({"backup_path": str(backup)} if adopt else {}),
        }, ensure_ascii=False)

    elif tool_name == "tools_skip_molecule_simulation":
        return _json.dumps({
            "ok": False,
            "error": "模拟层不支持跳过分子；未原子重建 residues、topol.top、.itp 与 Packmol 盒子时不能排除组分。",
        }, ensure_ascii=False)

    return _json.dumps({"error": f"未知工具: {tool_name}"})
