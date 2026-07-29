"""
topology_tools.py
=================
Layer 2 — Topology Agent 的 4 个工具定义与处理函数。

工具:
  retry_sobtop, retry_ligpargen, retry_top_assembly, diagnose_topology_error
"""

from __future__ import annotations
import json as _json
from pathlib import Path

from willy._paths import get_project_root
from willy.errors import StepResult, StepError, ErrorKind

ROOT = get_project_root()

# ============================================================
# Tool Definitions
# ============================================================

TOPOLOGY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "retry_sobtop",
            "description": "为一个或全部分子重试 Sobtop 拓扑生成（mol2+chg→itp+gro）。可在 GAFF 和 AMBER 力场之间切换。",
            "parameters": {
                "type": "object",
                "properties": {
                    "molecule_name": {
                        "type": "string",
                        "description": "要重试的分子名。省略或 'all' 则重试全部分子。",
                    },
                    "mol2_path": {"type": "string", "description": ".mol2 文件路径（可选）"},
                    "chg_path": {"type": "string", "description": ".chg 文件路径（可选）"},
                    "gaff": {"type": "boolean", "description": "True=GAFF, False=AMBER 力场"},
                    "hessian_path": {"type": "string", "description": "可选的 .fchk 路径用于基于 Hessian 的参数"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retry_ligpargen",
            "description": "使用 LigParGen（OPLS-AA）作为 Sobtop 的替代方案生成拓扑。接受 SMILES 或 mol2 输入。",
            "parameters": {
                "type": "object",
                "properties": {
                    "molecule_name": {"type": "string"},
                    "smiles": {"type": "string", "description": "分子的 SMILES 字符串（首选）"},
                    "mol2_path": {"type": "string", "description": ".mol2 文件路径（若未提供 smiles 则用于提取 SMILES）"},
                    "net_charge": {
                        "type": "integer",
                        "enum": [-2, -1, 0, 1, 2],
                        "description": "净分子电荷",
                    },
                    "lbcc": {"type": "boolean", "description": "使用 CM1A-LBCC 电荷模型（仅限中性分子）"},
                    "opt_steps": {
                        "type": "integer",
                        "enum": [0, 1, 2, 3],
                        "description": "BOSS 优化步数：0=单点，1-3=递进优化",
                    },
                },
                "required": ["molecule_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retry_top_assembly",
            "description": "重试 top_maker 拓扑组装（生成 topol.top，运行 itp_reviser）。从所有 .itp 文件收集 atomtype，去重并组装主拓扑。",
            "parameters": {
                "type": "object",
                "properties": {
                    "topo_dir": {"type": "string", "description": "包含 .itp 文件的目录"},
                    "config_path": {"type": "string", "description": "config.json 路径"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_topology_error",
            "description": "分析 Sobtop/LigParGen/top_maker 输出以确定拓扑生成失败的根本原因。检查：mol2 格式错误、缺少电荷、力场 atomtype 不匹配、结构验证失败。",
            "parameters": {
                "type": "object",
                "properties": {
                    "error_source": {
                        "type": "string",
                        "enum": ["sobtop", "ligpargen", "top_maker", "itp_reviser"],
                        "description": "哪个组件产生了错误",
                    },
                    "molecule_name": {"type": "string", "description": "失败的分子名"},
                    "raw_output": {"type": "string", "description": "失败组件的 stderr/stdout 输出（最后 1000 字符）"},
                    "mol2_path": {"type": "string", "description": ".mol2 文件路径（用于检查有效性）"},
                    "chg_path": {"type": "string", "description": ".chg 文件路径（用于检查有效性，仅 Sobtop）"},
                },
                "required": ["error_source", "raw_output"],
            },
        },
    },
]


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


def handle_topology_tool_call(tool_name: str, args: dict) -> str:
    """Layer 2 工具调用分发器。"""

    if tool_name == "retry_sobtop":
        mol_name = args.get("molecule_name", "")
        mol2_path = args.get("mol2_path")
        chg_path = args.get("chg_path")
        gaff = args.get("gaff", True)
        hessian = args.get("hessian_path")

        from willy.topology.sobtop_interface import make_itp_gro, TopMakerInput

        if mol_name and mol_name != "all" and mol2_path and chg_path:
            inp = TopMakerInput(
                mol2=mol2_path, chg=chg_path,
                output_name=mol_name, gaff=gaff,
                hessian=hessian,
            )
            sr = make_itp_gro(inp)
        else:
            # 对全部分子重试
            from willy.topology.sobtop_interface import batch_make_topo
            results = batch_make_topo()
            ok = sum(1 for r in results if r.success)
            failed = [r for r in results if not r.success]
            if not failed:
                sr = StepResult(
                    step_name="sobtop_interface", step_index=4, success=True,
                    outputs={"batch_count": str(len(results))},
                )
            else:
                first_err = failed[0].error
                sr = StepResult(
                    step_name="sobtop_interface", step_index=4, success=False,
                    error=StepError(
                        kind=ErrorKind.SOBTOP_FAILED,
                        message=f"批量 Sobtop: {ok}/{len(results)} 成功，{len(failed)} 失败",
                        raw_output=first_err.raw_output if first_err else "",
                        hint=first_err.hint if first_err else "检查单个分子输出获取详细错误",
                    ),
                )
        return _json.dumps(_step_to_dict(sr), ensure_ascii=False)

    elif tool_name == "retry_ligpargen":
        mol_name = args["molecule_name"]
        try:
            from willy.topology.ligpargen_interface import make_itp_gro_opls
            sr = make_itp_gro_opls(
                molecule_name=mol_name,
                smiles=args.get("smiles"),
                mol2_path=args.get("mol2_path"),
                net_charge=args.get("net_charge", 0),
                lbcc=args.get("lbcc", False),
                opt_steps=args.get("opt_steps", 0),
            )
        except Exception as e:
            sr = StepResult(
                step_name="ligpargen_interface", step_index=4, success=False,
                error=StepError(kind=ErrorKind.LIGPARGEN_FAILED,
                                message=f"LigParGen 失败: {e}",
                                hint="检查 BOSSdir 环境变量或回退到 Sobtop"),
            )
        return _json.dumps(_step_to_dict(sr), ensure_ascii=False)

    elif tool_name == "retry_top_assembly":
        topo_dir = args.get("topo_dir", "topo")
        config_path = args.get("config_path", "config.json")
        from willy.topology.top_maker import build as _build
        sr = _build(config_path=config_path, topo_dir=topo_dir)
        return _json.dumps(_step_to_dict(sr), ensure_ascii=False)

    elif tool_name == "diagnose_topology_error":
        error_source = args["error_source"]
        raw_output = args.get("raw_output", "")
        mol2_path = args.get("mol2_path")
        chg_path = args.get("chg_path")

        diagnosis = {
            "error_source": error_source,
            "issues": [],
            "hint": "",
        }

        # ── 检查 mol2 有效性 ──
        if mol2_path and Path(mol2_path).exists():
            mol2_text = Path(mol2_path).read_text()
            if "@<TRIPOS>ATOM" not in mol2_text:
                diagnosis["issues"].append("mol2 文件缺少 @<TRIPOS>ATOM 段")
            if "@<TRIPOS>BOND" not in mol2_text:
                diagnosis["issues"].append("mol2 文件缺少 @<TRIPOS>BOND 段")
        elif mol2_path:
            diagnosis["issues"].append(f"mol2 文件不存在: {mol2_path}")

        # ── 检查 chg 有效性 ──
        if chg_path and Path(chg_path).exists():
            chg_text = Path(chg_path).read_text()
            if not chg_text.strip():
                diagnosis["issues"].append("chg 文件为空")
        elif chg_path:
            diagnosis["issues"].append(f"chg 文件不存在: {chg_path}")

        # ── 解析 stderr 模式 ──
        if "atomtype" in raw_output.lower():
            diagnosis["issues"].append("atomtype 缺失或不匹配 —— 检查力场分配")
            diagnosis["hint"] = "在 itp 中添加缺失的 atomtype 或切换力场（GAFF ↔ AMBER）"
        if "cannot open" in raw_output.lower():
            diagnosis["issues"].append("Sobtop 无法打开输入文件 —— 检查路径")
            diagnosis["hint"] = "确保 mol2 和 chg 文件路径正确且可读"
        if "fortran" in raw_output.lower() or "rc=24" in raw_output.lower():
            diagnosis["issues"].append("Fortran 清理错误 (rc=24) —— 通常非致命")
            diagnosis["hint"] = "检查输出文件是否已生成。若已生成，可忽略此错误。"
        if "unknown atom" in raw_output.lower():
            diagnosis["issues"].append("未知原子类型 —— 力场无法覆盖某些原子")
            diagnosis["hint"] = "尝试 UFF 力场或 LigParGen (OPLS-AA)"

        if not diagnosis["issues"]:
            diagnosis["issues"].append("无特定错误模式被识别。检查原始输出。")
        if not diagnosis["hint"]:
            diagnosis["hint"] = "检查 .mol2 和 .chg 格式，或尝试 LigParGen 作为替代"

        return _json.dumps(diagnosis, ensure_ascii=False)

    return _json.dumps({"error": f"未知工具: {tool_name}"})
