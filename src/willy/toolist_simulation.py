"""
toolist_simulation.py
=====================
Layer 3 — Simulation Agent 的 8 个工具定义与处理函数。

工具:
  tools_retry_mdp, tools_retry_box, tools_retry_em, tools_retry_eq,
  tools_retry_prod, tools_diagnose_error_simulation,
  tools_modify_config_simulation, tools_skip_molecule_simulation
"""

from __future__ import annotations
import json as _json
from pathlib import Path

from willy._paths import get_project_root
from willy.errors import StepResult, StepError, ErrorKind
from willy.log_parsers import parse_gromacs_log

ROOT = get_project_root()

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
                    "ref_t": {"type": "number", "description": "参考温度 (K)"},
                    "ref_p": {"type": "number", "description": "参考压力 (bar)"},
                    "eq_ns": {"type": "number", "description": "平衡时长 (ns)"},
                    "prod_ns": {"type": "number", "description": "产出时长 (ns)"},
                    "nsteps": {"type": "integer", "description": "覆盖 nsteps（EM 专用）"},
                    "emtol": {"type": "number", "description": "EM 容差 (kJ/mol/nm, 默认 100)"},
                    "emstep": {"type": "number", "description": "EM 步长 (nm, 默认 0.01)"},
                    "tau_t": {"type": "number", "description": "温度耦合常数"},
                    "tau_p": {"type": "number", "description": "压力耦合常数"},
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
                    "gen_seed": {"type": "integer", "description": "速度生成随机种子"},
                },
                "required": ["stage"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_retry_box",
            "description": "用修改后的密度、盒子尺寸或容差重新构建 Packmol 模拟盒子。",
            "parameters": {
                "type": "object",
                "properties": {
                    "density": {
                        "type": "number",
                        "description": "填充密度 (分子/nm³)（如离子液体 6.0，溶剂混合 5.0）",
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
                    "add_box_sides": {
                        "type": "number",
                        "description": "每边额外填充 (Å, 默认 2.0)",
                    },
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
            "description": "用修改后的参数重试 GROMACS NPT 平衡。",
            "parameters": {
                "type": "object",
                "properties": {
                    "work_dir": {"type": "string", "description": "工作目录路径"},
                    "dt": {"type": "number", "description": "时间步长 (ps)"},
                    "eq_ns": {"type": "number", "description": "平衡时长 (ns)"},
                    "tau_t": {"type": "number"},
                    "tau_p": {"type": "number"},
                    "ref_t": {"type": "number"},
                    "ref_p": {"type": "number"},
                    "tcoupl": {"type": "string", "enum": ["V-rescale", "Nose-Hoover"]},
                    "pcoupl": {"type": "string", "enum": ["C-rescale", "Berendsen", "Parrinello-Rahman"]},
                    "gen_seed": {"type": "integer", "description": "新的速度生成随机种子"},
                    "annealing": {"type": "boolean", "description": "启用/禁用温度退火"},
                },
                "required": ["work_dir"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_retry_prod",
            "description": "重试 GROMACS 生产 MD 运行，可选择从检查点继续。",
            "parameters": {
                "type": "object",
                "properties": {
                    "work_dir": {"type": "string", "description": "工作目录路径"},
                    "from_checkpoint": {
                        "type": "boolean",
                        "description": "从已有检查点 (.cpt) 继续",
                    },
                    "append": {"type": "boolean", "description": "追加到之前的轨迹"},
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
            "description": "用修改后的模拟参数更新 config.json 中的 MD 段。更改在后续重试时保留。",
            "parameters": {
                "type": "object",
                "properties": {
                    "dt": {"type": "number"},
                    "ref_t": {"type": "number"},
                    "ref_p": {"type": "number"},
                    "eq_ns": {"type": "number"},
                    "prod_ns": {"type": "number"},
                    "tcoupl": {"type": "string"},
                    "tau_t": {"type": "number"},
                    "pcoupl": {"type": "string"},
                    "tau_p_prod": {"type": "number"},
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
            "name": "tools_skip_molecule_simulation",
            "description": "将指定分子加入跳过列表，后续步骤不再处理此分子。用于无法修复的分子致命错误。",
            "parameters": {
                "type": "object",
                "properties": {
                    "molecule_name": {
                        "type": "string",
                        "description": "要跳过的分子名",
                    },
                    "reason": {
                        "type": "string",
                        "description": "跳过原因（记录在 config.json 中）",
                    },
                },
                "required": ["molecule_name"],
            },
        },
    },
]

# ============================================================
# Tool 分类元数据
# ============================================================

TOOL_META = {
    "tools_retry_mdp":  {"category": "action",     "mutating": True,  "risk": "medium"},
    "tools_retry_box":  {"category": "action",     "mutating": True,  "risk": "high"},
    "tools_retry_em":              {"category": "action",     "mutating": True,  "risk": "high"},
    "tools_retry_eq":              {"category": "action",     "mutating": True,  "risk": "high"},
    "tools_retry_prod":            {"category": "action",     "mutating": True,  "risk": "high"},
    "tools_diagnose_error_simulation":     {"category": "diagnostic", "mutating": False, "risk": "low"},
    "tools_modify_config_simulation":      {"category": "config",     "mutating": True,  "risk": "medium"},
    "tools_skip_molecule_simulation":         {"category": "config",     "mutating": True,  "risk": "medium"},
}

# ============================================================
# Tool Handler
# ============================================================

def _step_to_dict(sr: StepResult) -> dict:
    err = sr.error
    return {
        "_step_result": True,
        "success": sr.success,
        "step_name": sr.step_name,
        "outputs": sr.outputs,
        "artifacts": sr.artifacts,
        "duration_s": sr.duration_s,
        "error_message": err.message if err else "",
        "error_kind": err.kind.value if err else "",
        "hint": err.hint if err else "",
        "raw_output": err.raw_output if err else "",
    }


def handle_simulation_tool_call(tool_name: str, args: dict) -> str:
    """Layer 3 工具调用分发器。"""

    if tool_name == "tools_retry_mdp":
        stage = args.get("stage", "all")
        overrides = {}
        for k in ["dt", "ref_t", "ref_p", "eq_ns", "prod_ns", "nsteps",
                   "emtol", "emstep", "tau_t", "tau_p", "tcoupl", "pcoupl",
                   "constraints", "rcoulomb", "rvdw", "gen_seed"]:
            if args.get(k) is not None:
                overrides[k] = args[k]

        from willy.simulation.mdp import build_all as _mdp_build
        stages = None if stage == "all" else (stage,)
        sr = _mdp_build(
            overrides=overrides if overrides else None,
            stages=stages,
        )
        return _json.dumps(_step_to_dict(sr), ensure_ascii=False)

    elif tool_name == "tools_retry_box":
        from willy.simulation.box import auto_from_config, InpGenerator
        config = auto_from_config()
        if args.get("density") is not None:
            config.box_size = None  # 让密度优先
        if args.get("box_size") is not None:
            config.box_size = args["box_size"]
        if args.get("tolerance") is not None:
            config.tolerance = args["tolerance"]
        if args.get("seed") is not None:
            config.seed = args["seed"]
        if args.get("add_box_sides") is not None:
            config.add_box_sides = args["add_box_sides"]
        gen = InpGenerator(config)
        sr = gen.run()
        return _json.dumps(_step_to_dict(sr), ensure_ascii=False)

    elif tool_name == "tools_retry_em":
        work_dir = args["work_dir"]
        from willy.simulation.em import run_em
        # 如果有覆盖参数，先生成新的 em.mdp
        if args.get("nsteps") or args.get("emtol") or args.get("constraints"):
            from willy.simulation.mdp import build_all as _mdp_build
            overrides = {k: v for k, v in {
                "nsteps": args.get("nsteps"),
                "emtol": args.get("emtol"),
                "constraints": args.get("constraints"),
            }.items() if v is not None}
            _mdp_build(output_dir=work_dir, overrides=overrides)
        sr = run_em(work_dir=work_dir)
        return _json.dumps(_step_to_dict(sr), ensure_ascii=False)

    elif tool_name == "tools_retry_eq":
        work_dir = args["work_dir"]
        from willy.simulation.eq import run_eq
        # 先更新 MDP
        overrides = {k: v for k, v in {
            "dt": args.get("dt"), "eq_ns": args.get("eq_ns"),
            "tau_t": args.get("tau_t"), "tau_p": args.get("tau_p"),
            "ref_t": args.get("ref_t"), "ref_p": args.get("ref_p"),
            "tcoupl": args.get("tcoupl"), "pcoupl": args.get("pcoupl"),
        }.items() if v is not None}
        if overrides:
            from willy.simulation.mdp import build_all as _mdp_build
            _mdp_build(output_dir=work_dir, overrides=overrides)
        sr = run_eq(work_dir=work_dir)
        return _json.dumps(_step_to_dict(sr), ensure_ascii=False)

    elif tool_name == "tools_retry_prod":
        work_dir = args["work_dir"]
        from willy.simulation.prod import run_prod
        cwd = Path(work_dir)
        # 若 from_checkpoint，使用 -cpi 参数（如果 .cpt 存在）
        extra_mdrun = []
        if args.get("from_checkpoint") and (cwd / "prod.cpt").exists():
            extra_mdrun = ["-cpi", str(cwd / "prod.cpt")]
        if args.get("append"):
            extra_mdrun.append("-append")
        sr = run_prod(work_dir=work_dir, extra_mdrun=extra_mdrun)
        return _json.dumps(_step_to_dict(sr), ensure_ascii=False)

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
            },
        )
        return _json.dumps(dr.to_dict(), ensure_ascii=False)

    elif tool_name == "tools_modify_config_simulation":
        config_path = ROOT / "config.json"
        try:
            with open(config_path) as f:
                config = _json.load(f)
        except (FileNotFoundError, _json.JSONDecodeError) as e:
            return _json.dumps({"ok": False, "error": f"无法读取 config.json: {e}"})

        md = config.setdefault("md", {})
        md_fields = ["dt", "ref_t", "ref_p", "eq_ns", "prod_ns",
                      "tcoupl", "tau_t", "pcoupl", "tau_p_prod",
                      "constraints", "rcoulomb", "rvdw", "compressibility"]
        updated = []
        for f in md_fields:
            if args.get(f) is not None:
                md[f] = args[f]
                updated.append(f)

        config_path.write_text(_json.dumps(config, indent=2, ensure_ascii=False) + "\n")
        return _json.dumps({
            "ok": True,
            "updated_fields": updated,
            "current_md": {k: md.get(k) for k in md_fields},
        }, ensure_ascii=False)

    elif tool_name == "tools_skip_molecule_simulation":
        name = args["molecule_name"]
        reason = args.get("reason", "Agent 标记跳过")
        config_path = ROOT / "config.json"
        try:
            with open(config_path) as f:
                config = _json.load(f)
        except (FileNotFoundError, _json.JSONDecodeError):
            config = {}
        skipped = config.setdefault("skipped_molecules", [])
        if name not in skipped:
            skipped.append(name)
        config.setdefault("skip_reasons", {})[name] = reason
        config_path.write_text(_json.dumps(config, indent=2, ensure_ascii=False) + "\n")
        return _json.dumps({
            "ok": True,
            "molecule": name,
            "reason": reason,
            "warning": f"⚠ {name} 已跳过，MD 模拟将排除此分子",
        }, ensure_ascii=False)

    return _json.dumps({"error": f"未知工具: {tool_name}"})
