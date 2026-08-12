"""
toolist_quantum.py
==================
Layer 1 — Quantum Agent 的 8 个工具定义与处理函数。

工具:
  tools_retry_struct_g16, tools_retry_struct_g09, tools_retry_struct_orca, tools_retry_mol2_conversion,
  tools_retry_chg_g16, tools_retry_chg_g09, tools_retry_chg_orca, tools_diagnose_error_quantum,
  tools_modify_config_molecule, tools_skip_molecule_quantum
"""

from __future__ import annotations
import json as _json
from pathlib import Path

from willy._paths import get_project_root
from willy.config_store import write_json
from willy.errors import StepResult, StepError, ErrorKind
from willy.log_parsers import diagnose_log

ROOT = get_project_root()

# ============================================================
# Tool Definitions (JSON Schema for DeepSeek/OpenAI)
# ============================================================

QUANTUM_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "tools_retry_struct_g16",
            "description": "用修改后的参数为指定分子重试 Gaussian 结构优化。重新构建 .gjf 文件并运行 g16 + formchk。",
            "parameters": {
                "type": "object",
                "properties": {
                    "molecule_name": {"type": "string", "description": "分子名（如 'Li', 'TFSI'）"},
                    "basis": {"type": "string", "description": "基组覆盖（如 'b3lyp/6-31g(d)'）。空则使用 config.json 中的值。"},
                    "mem": {"type": "string", "description": "内存覆盖（如 '2GB', '8GB'）"},
                    "nproc": {"type": "integer", "description": "CPU 核数覆盖"},
                    "scf_options": {"type": "string", "description": "SCF 关键字（如 'scf=xqc', 'scf=maxcycle=256'）"},
                    "opt_options": {"type": "string", "description": "优化关键字（如 'opt=calcfc', 'opt=gdiis'）"},
                },
                "required": ["molecule_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_retry_struct_orca",
            "description": "用修改后的参数为指定分子重试 ORCA 结构优化。",
            "parameters": {
                "type": "object",
                "properties": {
                    "molecule_name": {"type": "string"},
                    "basis": {"type": "string", "description": "基组覆盖"},
                    "mem_mb": {"type": "integer", "description": "每核内存（MB）"},
                    "nproc": {"type": "integer"},
                },
                "required": ["molecule_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_retry_struct_g09",
            "description": "用修改后的参数为指定分子重试 Gaussian 09 结构优化。重新构建 .gjf 文件并运行 g09 + G09 formchk。",
            "parameters": {
                "type": "object",
                "properties": {
                    "molecule_name": {"type": "string", "description": "分子名（如 'Li', 'TFSI'）"},
                    "basis": {"type": "string", "description": "基组覆盖（如 'b3lyp/6-31g(d)'）。空则使用 config.json 中的值。"},
                    "mem": {"type": "string", "description": "内存覆盖（如 '2GB', '8GB'）"},
                    "nproc": {"type": "integer", "description": "CPU 核数覆盖"},
                    "scf_options": {"type": "string", "description": "SCF 关键字（如 'scf=xqc', 'scf=maxcycle=256'）"},
                    "opt_options": {"type": "string", "description": "优化关键字（如 'opt=calcfc', 'opt=gdiis'）"},
                },
                "required": ["molecule_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_retry_mol2_conversion",
            "description": "重试 Step 2 的 mol2 转换。ORCA 优先使用对应 *_opt.molden 保留连通性；G16/G09 使用 *_opt.fchk。",
            "parameters": {
                "type": "object",
                "properties": {
                    "fchk_path": {"type": "string", "description": "Step 2 生成的 *_opt.fchk 绝对路径"},
                    "molden_path": {"type": "string", "description": "ORCA Step 2 生成的 *_opt.molden 绝对路径；存在时优先使用"},
                },
                "anyOf": [
                    {"required": ["fchk_path"]},
                    {"required": ["molden_path"]},
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_retry_chg_g16",
            "description": "从 Step 2 生成的 *_opt.fchk 重试 RESP 电荷计算。G16 与 ORCA 使用同一 RESP 实现。",
            "parameters": {
                "type": "object",
                "properties": {
                    "fchk_path": {"type": "string", "description": "Step 2 生成的 *_opt.fchk 绝对路径"},
                    "charge": {"type": "integer", "description": "覆盖分子电荷"},
                    "spin": {"type": "integer", "description": "覆盖自旋多重度"},
                },
                "required": ["fchk_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_retry_chg_orca",
            "description": "从 Step 2 生成的 *_opt.fchk 重试 RESP 电荷计算。G16 与 ORCA 使用同一 RESP 实现。",
            "parameters": {
                "type": "object",
                "properties": {
                    "fchk_path": {"type": "string", "description": "Step 2 生成的 *_opt.fchk 绝对路径"},
                    "charge": {"type": "integer", "description": "覆盖分子电荷"},
                    "spin": {"type": "integer", "description": "覆盖自旋多重度"},
                },
                "required": ["fchk_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_retry_chg_g09",
            "description": "从 Step 2 生成的 *_opt.fchk 重试 Gaussian 09 RESP 电荷计算。使用与 G16/ORCA 相同的 RESP 实现。",
            "parameters": {
                "type": "object",
                "properties": {
                    "fchk_path": {"type": "string", "description": "Step 2 生成的 *_opt.fchk 绝对路径"},
                    "charge": {"type": "integer", "description": "覆盖分子电荷"},
                    "spin": {"type": "integer", "description": "覆盖自旋多重度"},
                },
                "required": ["fchk_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_diagnose_error_quantum",
            "description": "解析 Gaussian 或 ORCA 输出日志以识别具体失败模式。返回结构化诊断及建议的纠正措施。",
            "parameters": {
                "type": "object",
                "properties": {
                    "log_path": {"type": "string", "description": "输出日志文件路径（g16 为 .log，ORCA 为 .out）"},
                    "error_kind": {
                        "type": "string",
                        "enum": ["scf_not_converged", "geom_not_converged", "gaussian_crash", "orca_crash", "formchk_failed", "resp_failed", "unknown"],
                    },
                },
                "required": ["log_path", "error_kind"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_modify_config_molecule",
            "description": "在 config.json 中更新分子的配置。更改在下次重试时生效。返回更新后的 config。",
            "parameters": {
                "type": "object",
                "properties": {
                    "molecule_name": {"type": "string"},
                    "basis": {"type": "string"},
                    "mem": {"type": "string"},
                    "nproc": {"type": "integer"},
                    "solvent": {"type": "string", "enum": ["acetone", "water", "ethanol", "gas"]},
                    "backend": {"type": "string", "enum": ["g16", "g09", "orca"], "description": "为此分子切换量子化学后端"},
                },
                "required": ["molecule_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_skip_molecule_quantum",
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
    "tools_retry_struct_g16":        {"category": "action",     "mutating": True,  "risk": "high", "effect": "retry_safe", "parameter_effects": {"basis": "requires_confirmation"}},
    "tools_retry_struct_g09":        {"category": "action",     "mutating": True,  "risk": "high", "effect": "retry_safe", "parameter_effects": {"basis": "requires_confirmation"}},
    "tools_retry_struct_orca":       {"category": "action",     "mutating": True,  "risk": "high", "effect": "retry_safe", "parameter_effects": {"basis": "requires_confirmation"}},
    "tools_retry_mol2_conversion":  {"category": "action",     "mutating": True,  "risk": "medium", "effect": "retry_safe"},
    "tools_retry_chg_g16":          {"category": "action",     "mutating": True,  "risk": "high", "effect": "retry_safe", "parameter_effects": {"charge": "requires_fork", "spin": "requires_fork"}},
    "tools_retry_chg_g09":          {"category": "action",     "mutating": True,  "risk": "high", "effect": "retry_safe", "parameter_effects": {"charge": "requires_fork", "spin": "requires_fork"}},
    "tools_retry_chg_orca":         {"category": "action",     "mutating": True,  "risk": "high", "effect": "retry_safe", "parameter_effects": {"charge": "requires_fork", "spin": "requires_fork"}},
    "tools_diagnose_error_quantum": {"category": "diagnostic", "mutating": False, "risk": "low", "effect": "read_only"},
    "tools_modify_config_molecule": {"category": "config",     "mutating": True,  "risk": "medium", "effect": "requires_confirmation"},
    "tools_skip_molecule_quantum":  {"category": "config",     "mutating": True,  "risk": "medium", "effect": "requires_fork"},
}

# ============================================================
# Tool Handler
# ============================================================

def handle_quantum_tool_call(
    tool_name: str,
    args: dict,
    work_dir: str | None = None,
    config_path: str | None = None,
) -> str:
    """
    Layer 1 工具调用分发器。

    每个工具返回 JSON 字符串，包含 _step_result 标记供 LayerAgent 解析。
    """
    workspace = Path(work_dir) if work_dir else ROOT / "struct"
    active_config = Path(config_path) if config_path else ROOT / "config.json"

    if tool_name == "tools_retry_struct_g16":
        name = args["molecule_name"]
        from willy.quantum.struct_g16 import run_one as _run_one
        import json
        try:
            with active_config.open() as f:
                config = json.load(f)
        except Exception:
            config = {}
        molecules = config.get("molecules", {})
        defaults = config.get("defaults", {})
        cfg = molecules.get(name, {})
        sr = _run_one(
            name=name, cfg=cfg, defaults=defaults,
            cfg_overrides={
                k: v for k, v in {
                    "basis": args.get("basis"),
                    "mem": args.get("mem"),
                    "nproc": args.get("nproc"),
                }.items() if v is not None
            },
            scf_options=args.get("scf_options", ""),
            opt_options=args.get("opt_options", ""),
            struct_dir=str(workspace),
        )
        return _json.dumps(sr.to_dict(), ensure_ascii=False)

    elif tool_name == "tools_retry_struct_g09":
        name = args["molecule_name"]
        from willy.quantum.struct_g09 import run_one as _run_one
        import json
        try:
            with active_config.open() as f:
                config = json.load(f)
        except Exception:
            config = {}
        molecules = config.get("molecules", {})
        defaults = config.get("defaults", {})
        cfg = molecules.get(name, {})
        sr = _run_one(
            name=name, cfg=cfg, defaults=defaults,
            cfg_overrides={
                k: v for k, v in {
                    "basis": args.get("basis"),
                    "mem": args.get("mem"),
                    "nproc": args.get("nproc"),
                }.items() if v is not None
            },
            scf_options=args.get("scf_options", ""),
            opt_options=args.get("opt_options", ""),
            struct_dir=str(workspace),
        )
        return _json.dumps(sr.to_dict(), ensure_ascii=False)

    elif tool_name == "tools_retry_struct_orca":
        name = args["molecule_name"]
        try:
            import json
            with active_config.open() as f:
                config = json.load(f)
            molecules = config.get("molecules", {})
            defaults = config.get("defaults", {})
            if name in molecules:
                cfg = dict(molecules[name])
                if args.get("basis"):
                    cfg["basis"] = args["basis"]
                if args.get("mem_mb"):
                    cfg["mem"] = f"{args['mem_mb']}MB"
                if args.get("nproc"):
                    cfg["nproc"] = args["nproc"]
                from willy.quantum.struct_orca import run_one as _orca_run_one
                sr = _orca_run_one(name, cfg, defaults, struct_dir=str(workspace))
            else:
                sr = StepResult(
                    step_name="struct_orca", step_index=1, success=False,
                    error=StepError(kind=ErrorKind.CONFIG_INVALID, message=f"{name} 不在 config.json 中"),
                )
        except Exception as e:
            sr = StepResult(
                step_name="struct_orca", step_index=1, success=False,
                error=StepError(kind=ErrorKind.UNKNOWN, message=f"重试 ORCA 失败: {e}"),
            )
        return _json.dumps(sr.to_dict(), ensure_ascii=False)

    elif tool_name == "tools_retry_mol2_conversion":
        fchk_path = args.get("fchk_path")
        molden_path = args.get("molden_path")
        if not molden_path and fchk_path:
            candidate = Path(fchk_path).with_suffix(".molden")
            if candidate.is_file():
                molden_path = str(candidate)
        if molden_path:
            from willy.quantum.molden_mol2 import convert as molden_to_mol2
            sr = molden_to_mol2(molden_path)
        elif fchk_path:
            from willy.quantum.fchk_mol2 import convert as fchk_to_mol2
            sr = fchk_to_mol2(fchk_path)
        else:
            sr = StepResult(
                step_name="mol2_conversion", step_index=2, success=False,
                error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                                message="缺少 fchk_path 或 molden_path"),
            )
        return _json.dumps(sr.to_dict(), ensure_ascii=False)

    elif tool_name in {"tools_retry_chg_g16", "tools_retry_chg_g09", "tools_retry_chg_orca"}:
        fchk_path = Path(args["fchk_path"])
        name = fchk_path.stem.removesuffix("_opt")
        if not fchk_path.exists():
            sr = StepResult(
                step_name="chg_resp", step_index=3, success=False,
                error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                                message=f"{fchk_path} 不存在",
                                hint="确认 Step 2 (SP + mol2) 已成功运行"),
            )
            return _json.dumps(sr.to_dict(), ensure_ascii=False)

        from willy.quantum.chg_resp import make_chg
        charge, spin = 0, 1
        try:
            with active_config.open() as f:
                config = _json.load(f)
            mol_cfg = config.get("molecules", {}).get(name, {})
            charge = mol_cfg.get("charge", 0)
            spin = mol_cfg.get("spin", 1)
        except Exception:
            pass
        charge = args.get("charge", charge)
        spin = args.get("spin", spin)
        sr = make_chg(str(fchk_path), charge=charge, spin=spin, output_name=name)
        return _json.dumps(sr.to_dict(), ensure_ascii=False)

    elif tool_name == "tools_diagnose_error_quantum":
        log_path = args["log_path"]
        error_kind = args.get("error_kind", "unknown")
        if log_path.endswith(".out"):
            engine = "orca"
        else:
            engine = "gaussian"
        raw = diagnose_log(log_path, engine=engine)
        from willy.errors import DiagnosisResult
        dr = DiagnosisResult(
            source="quantum",
            severity="error" if raw.get("error_patterns") else "info",
            issues=raw.get("error_patterns", []),
            evidence=raw.get("evidence_lines", []),
            hint=raw.get("hint", ""),
            extra={
                "error_kind": error_kind,
                "termination": raw.get("termination", "unknown"),
                "scf_converged": raw.get("scf_converged"),
                "geom_converged": raw.get("geom_converged"),
            },
        )
        return _json.dumps(dr.to_dict(), ensure_ascii=False)

    elif tool_name == "tools_modify_config_molecule":
        name = args["molecule_name"]
        try:
            with active_config.open() as f:
                config = _json.load(f)
        except (FileNotFoundError, _json.JSONDecodeError) as e:
            return _json.dumps({"ok": False, "error": f"无法读取 config.json: {e}"})

        molecules = config.setdefault("molecules", {})
        if name not in molecules:
            molecules[name] = {}
        mol = molecules[name]
        for field in ["basis", "mem", "nproc", "solvent"]:
            if args.get(field) is not None:
                mol[field] = args[field]
        if args.get("backend") in {"g09", "orca"}:
            mol["_backend"] = args["backend"]

        write_json(active_config, config)
        return _json.dumps({
            "ok": True,
            "molecule": name,
            "updated_fields": [f for f in ["basis", "mem", "nproc", "solvent", "backend"]
                               if args.get(f) is not None or args.get(f.replace("backend", "_backend"))],
        }, ensure_ascii=False)

    elif tool_name == "tools_skip_molecule_quantum":
        name = args["molecule_name"]
        reason = args.get("reason", "Agent 标记跳过")
        try:
            with active_config.open() as f:
                config = _json.load(f)
        except (FileNotFoundError, _json.JSONDecodeError):
            config = {}
        skipped = config.setdefault("skipped_molecules", [])
        if name not in skipped:
            skipped.append(name)
        config.setdefault("skip_reasons", {})[name] = reason
        write_json(active_config, config)
        return _json.dumps({
            "ok": True,
            "molecule": name,
            "reason": reason,
            "warning": f"⚠ {name} 已跳过，MD 模拟将排除此分子",
        }, ensure_ascii=False)

    return _json.dumps({"error": f"未知工具: {tool_name}"})
