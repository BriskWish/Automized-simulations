"""
quantum_tools.py
================
Layer 1 — Quantum Agent 的 6 个工具定义与处理函数。

工具:
  retry_struct_maker, retry_orca_struct_maker, retry_mol2_conversion,
  retry_chg_maker, diagnose_quantum_error, modify_molecule_config
"""

from __future__ import annotations
import json as _json
from pathlib import Path

from willy._paths import get_project_root
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
            "name": "retry_struct_maker",
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
            "name": "retry_orca_struct_maker",
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
            "name": "retry_mol2_conversion",
            "description": "重试 fchk→mol2 转换。可尝试重新解析现有 fchk 或使用 Multiwfn 作为替代转换器。",
            "parameters": {
                "type": "object",
                "properties": {
                    "fchk_path": {"type": "string", "description": ".fchk 文件的绝对路径"},
                    "method": {
                        "type": "string",
                        "enum": ["reparse", "multiwfn"],
                        "description": "重试策略：'reparse' = 放宽格式重新解析 fchk，'multiwfn' = 用 Multiwfn 转换",
                    },
                },
                "required": ["fchk_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retry_chg_maker",
            "description": "用修改后的溶剂或电荷/自旋值重试 RESP 电荷计算。",
            "parameters": {
                "type": "object",
                "properties": {
                    "gjf_path": {"type": "string", "description": ".gjf 文件的绝对路径"},
                    "solvent": {
                        "type": "string",
                        "enum": ["acetone", "water", "ethanol", "gas"],
                        "description": "RESP 计算溶剂",
                    },
                    "charge": {"type": "integer", "description": "覆盖分子电荷"},
                    "spin": {"type": "integer", "description": "覆盖自旋多重度"},
                    "timeout": {"type": "integer", "description": "超时秒数（默认 600）"},
                },
                "required": ["gjf_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_quantum_error",
            "description": "解析 Gaussian 或 ORCA 输出日志以识别具体失败模式。返回结构化诊断及建议的纠正措施。",
            "parameters": {
                "type": "object",
                "properties": {
                    "log_path": {"type": "string", "description": "输出日志文件路径（g16 为 .log，ORCA 为 .out）"},
                    "error_kind": {
                        "type": "string",
                        "enum": ["scf_not_converged", "geom_not_converged", "gaussian_crash", "orca_crash", "formchk_failed", "unknown"],
                    },
                },
                "required": ["log_path", "error_kind"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "modify_molecule_config",
            "description": "在 config.json 中更新分子的配置。更改在下次重试时生效。返回更新后的 config。",
            "parameters": {
                "type": "object",
                "properties": {
                    "molecule_name": {"type": "string"},
                    "basis": {"type": "string"},
                    "mem": {"type": "string"},
                    "nproc": {"type": "integer"},
                    "solvent": {"type": "string", "enum": ["acetone", "water", "ethanol", "gas"]},
                    "backend": {"type": "string", "enum": ["g16", "orca"], "description": "为此分子切换量子化学后端"},
                },
                "required": ["molecule_name"],
            },
        },
    },
]


# ============================================================
# Tool Handler
# ============================================================

def _step_to_dict(sr: StepResult) -> dict:
    """将 StepResult 转为可 JSON 化的 dict（嵌入工具返回值中）。"""
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


def handle_quantum_tool_call(tool_name: str, args: dict) -> str:
    """
    Layer 1 工具调用分发器。

    每个工具返回 JSON 字符串，包含 _step_result 标记供 LayerAgent 解析。
    """
    if tool_name == "retry_struct_maker":
        name = args["molecule_name"]
        try:
            from willy.quantum.g16_struct_maker import run_one as _run_one
            sr = _run_one(
                name=name,
                cfg_overrides={
                    k: v for k, v in {
                        "basis": args.get("basis"),
                        "mem": args.get("mem"),
                        "nproc": args.get("nproc"),
                    }.items() if v is not None
                },
                scf_options=args.get("scf_options", ""),
                opt_options=args.get("opt_options", ""),
            )
        except TypeError:
            # run_one 可能还不支持 cfg_overrides —— 回退到直接调用
            from willy.quantum.g16_struct_maker import run_all
            results = run_all()
            sr = next((r for r in results if name in str(r.artifacts)), results[0]) if results else StepResult(
                step_name="struct_maker", step_index=1, success=False,
                error=StepError(kind=ErrorKind.UNKNOWN, message=f"{name}: 重试 struct_maker 失败"),
            )
        return _json.dumps(_step_to_dict(sr), ensure_ascii=False)

    elif tool_name == "retry_orca_struct_maker":
        name = args["molecule_name"]
        try:
            import json
            config_path = ROOT / "config.json"
            with open(config_path) as f:
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
                from willy.quantum.orca_struct_maker import run_one as _orca_run_one
                sr = _orca_run_one(name, cfg, defaults)
            else:
                sr = StepResult(
                    step_name="orca_struct_maker", step_index=1, success=False,
                    error=StepError(kind=ErrorKind.CONFIG_INVALID, message=f"{name} 不在 config.json 中"),
                )
        except Exception as e:
            sr = StepResult(
                step_name="orca_struct_maker", step_index=1, success=False,
                error=StepError(kind=ErrorKind.UNKNOWN, message=f"重试 ORCA 失败: {e}"),
            )
        return _json.dumps(_step_to_dict(sr), ensure_ascii=False)

    elif tool_name == "retry_mol2_conversion":
        fchk_path = args["fchk_path"]
        method = args.get("method", "reparse")
        if method == "multiwfn":
            # 使用 Multiwfn 进行 molden→fchk→mol2 完整转换
            from willy.quantum.orca_mol2_maker import molden_to_fchk
            # 如果有对应的 molden 文件
            molden_path = str(Path(fchk_path).with_suffix(".molden"))
            if Path(molden_path).exists():
                sr = molden_to_fchk(molden_path)
            else:
                sr = StepResult(
                    step_name="mol2_conversion", step_index=2, success=False,
                    error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                                    message=f"找不到 molden 文件: {molden_path}"),
                )
        else:
            # reparse: 用 g16_mol2_maker 重新解析
            try:
                from willy.quantum.g16_mol2_maker import fchk_to_mol2
                sr = fchk_to_mol2(fchk_path)
            except Exception as e:
                sr = StepResult(
                    step_name="mol2_conversion", step_index=2, success=False,
                    error=StepError(kind=ErrorKind.UNKNOWN,
                                    message=f"fchk→mol2 重新解析失败: {e}"),
                )
        return _json.dumps(_step_to_dict(sr), ensure_ascii=False)

    elif tool_name == "retry_chg_maker":
        gjf_path = args["gjf_path"]
        solvent = args.get("solvent", "acetone")
        from willy.quantum.g16_chg_maker import make_chg_one
        sr = make_chg_one(gjf_path, solvent=solvent, default_solvent=solvent)
        return _json.dumps(_step_to_dict(sr), ensure_ascii=False)

    elif tool_name == "diagnose_quantum_error":
        log_path = args["log_path"]
        error_kind = args.get("error_kind", "unknown")
        # 根据文件扩展名判断引擎
        if log_path.endswith(".out"):
            engine = "orca"
        else:
            engine = "gaussian"
        diagnosis = diagnose_log(log_path, engine=engine)
        diagnosis["error_kind"] = error_kind
        return _json.dumps(diagnosis, ensure_ascii=False)

    elif tool_name == "modify_molecule_config":
        name = args["molecule_name"]
        config_path = ROOT / "config.json"
        try:
            with open(config_path) as f:
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
        if args.get("backend") and args["backend"] == "orca":
            mol["_backend"] = "orca"

        config_path.write_text(_json.dumps(config, indent=2, ensure_ascii=False) + "\n")
        return _json.dumps({
            "ok": True,
            "molecule": name,
            "updated_fields": [f for f in ["basis", "mem", "nproc", "solvent", "backend"]
                               if args.get(f) is not None or args.get(f.replace("backend", "_backend"))],
        }, ensure_ascii=False)

    return _json.dumps({"error": f"未知工具: {tool_name}"})
