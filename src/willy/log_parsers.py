"""
log_parsers.py
==============
结构化解析外部程序日志：Gaussian、ORCA、GROMACS。

每个解析函数返回 dict，含 error_kind、建议修复动作、原始证据行。
供各层 Agent 的 diagnose_* 工具调用。
"""

from __future__ import annotations
from pathlib import Path
import re
from typing import Optional


# ============================================================
# Gaussian
# ============================================================

def parse_gaussian_log(log_path: str) -> dict:
    """
    解析 Gaussian .log 文件，提取 SCF/几何收敛状态。

    Returns:
        {
            "termination": "normal" | "error" | "unknown",
            "scf_converged": bool,
            "geom_converged": bool,
            "error_patterns": [str, ...],   # 匹配到的错误模式
            "last_scf_energy": float|None,
            "n_opt_cycles": int|None,
            "evidence_lines": [str, ...],   # 关键行原文
            "hint": str,
        }
    """
    p = Path(log_path)
    if not p.exists():
        return {
            "termination": "unknown", "scf_converged": False, "geom_converged": False,
            "error_patterns": [f"日志文件不存在: {log_path}"],
            "evidence_lines": [],
            "hint": "检查 Gaussian 是否正常启动，.log 文件是否生成",
        }

    text = p.read_text()
    lines = text.split("\n")

    result = {
        "termination": "unknown",
        "scf_converged": False,
        "geom_converged": False,
        "error_patterns": [],
        "last_scf_energy": None,
        "n_opt_cycles": None,
        "evidence_lines": [],
        "hint": "",
    }

    # ── 正常终止 ──
    if "Normal termination" in text:
        result["termination"] = "normal"
        result["scf_converged"] = True
        result["geom_converged"] = True

    # ── SCF 收敛检查 ──
    # 在每个 opt cycle 后查找最后的 SCF Done
    scf_done_matches = list(re.finditer(r"SCF Done:\s+E\(\w+\)\s*=\s*([\-\d.]+)", text))
    if scf_done_matches:
        result["last_scf_energy"] = float(scf_done_matches[-1].group(1))

    # ── 几何收敛 ──
    if re.search(r"Maximum Force.*YES", text) and \
       re.search(r"RMS\s+Force.*YES", text):
        result["geom_converged"] = True

    # 检查 "Optimization completed"
    if re.search(r"Optimization completed", text):
        result["geom_converged"] = True

    # ── 错误模式 ──
    error_checks = [
        (r"Convergence failure.*wavefunction", "SCF 收敛失败",
         "换更小基组 (6-311+g(d,p) → 6-31g(d))，或加 scf=xqc"),
        (r"Error termination", "异常终止",
         "检查 .log 尾部错误信息，常见原因：基组不匹配、内存不足、坐标不合理"),
        (r"exceeded.*maximum.*number.*steps", "SCF 步数耗尽",
         "加 scf=maxcycle=256 或换基组"),
        (r"out-of-memory", "内存不足",
         "增大 %mem 或减小基组"),
        (r"Illegal basis set", "基组不合法",
         "检查基组名称拼写，尝试用 6-31g(d) 替代"),
        (r"end of file", "输入文件不完整",
         "检查 .gjf 格式，确保坐标段完整"),
    ]

    for pattern, desc, hint in error_checks:
        if re.search(pattern, text, re.IGNORECASE):
            result["error_patterns"].append(desc)
            if not result["hint"]:
                result["hint"] = hint

    if result["error_patterns"] and result["termination"] == "unknown":
        result["termination"] = "error"

    # ── 提取证据行 ──
    for i, line in enumerate(lines):
        if any(kw in line for kw in ["SCF Done:", "Error termination",
                                       "Convergence failure", "Optimization completed",
                                       "Normal termination", "Maximum Force"]):
            result["evidence_lines"].append(f"L{i+1}: {line.strip()[:120]}")
        if len(result["evidence_lines"]) >= 15:
            break

    # ── opt cycles 计数 ──
    opt_steps = re.findall(r"Step number\s+(\d+)", text)
    if opt_steps:
        result["n_opt_cycles"] = len(set(opt_steps))

    # ── 自动 hint ──
    if not result["hint"]:
        if not result["scf_converged"] and result["termination"] != "normal":
            result["hint"] = "换更小基组或加 scf=xqc 重试"
        elif not result["geom_converged"] and result["termination"] != "normal":
            result["hint"] = "加 opt=calcfc 或检查初始几何是否合理"

    return result


# ============================================================
# ORCA
# ============================================================

def parse_orca_output(log_path: str) -> dict:
    """
    解析 ORCA .out 输出文件。

    Returns:
        {
            "termination": "normal" | "error" | "unknown",
            "scf_converged": bool,
            "geom_converged": bool,
            "error_patterns": [str, ...],
            "evidence_lines": [str, ...],
            "hint": str,
        }
    """
    p = Path(log_path)
    if not p.exists():
        return {
            "termination": "unknown", "scf_converged": False, "geom_converged": False,
            "error_patterns": [f"输出文件不存在: {log_path}"],
            "evidence_lines": [],
            "hint": "检查 ORCA 是否正常启动",
        }

    text = p.read_text()
    lines = text.split("\n")

    result = {
        "termination": "unknown",
        "scf_converged": False,
        "geom_converged": False,
        "error_patterns": [],
        "evidence_lines": [],
        "hint": "",
    }

    # ── 正常终止 ──
    if "****ORCA TERMINATED NORMALLY****" in text:
        result["termination"] = "normal"
        result["scf_converged"] = True
        result["geom_converged"] = True

    # ── SCF 收敛 ──
    if re.search(r"MAYBE CONVERGED|ENERGY CONVERGED", text):
        result["scf_converged"] = True

    # ── 几何收敛 ──
    if re.search(r"THE OPTIMIZATION HAS CONVERGED", text):
        result["geom_converged"] = True

    # ── 错误模式 ──
    error_checks = [
        (r"FATAL ERROR", "ORCA 致命错误",
         "读取 FATAL ERROR 附近的详细信息"),
        (r"SCF NOT CONVERGED|SCF NOT CONVERGENT|SCF FAILED TO CONVERGE", "SCF 不收敛",
         "换更小基组 (def2-SVP)、增大 maxiter、或用 g16 作为替代后端"),
        (r"NOT ENOUGH MEMORY|out of memory", "内存不足",
         "增加 %maxcore 或减少并行核数"),
        (r"FILE.*NOT FOUND|cannot open", "输入文件缺失",
         "检查 .inp 文件中的文件路径"),
        (r"TIMEOUT|wall time", "作业超时",
         "增加时间限制或减小基组/分子"),
        (r"COULD NOT CONVERGE GEOMETRY", "几何不收敛",
         "尝试更宽松的收敛标准或换初始结构"),
    ]

    for pattern, desc, hint in error_checks:
        if re.search(pattern, text, re.IGNORECASE):
            result["error_patterns"].append(desc)
            if not result["hint"]:
                result["hint"] = hint

    if result["error_patterns"] and result["termination"] == "unknown":
        result["termination"] = "error"

    # ── 证据行 ──
    for i, line in enumerate(lines):
        if any(kw in line for kw in ["FATAL ERROR", "SCF NOT CONVERGED",
                                       "TERMINATED NORMALLY", "THE OPTIMIZATION HAS CONVERGED",
                                       "ENERGY CONVERGED", "MAYBE CONVERGED",
                                       "out of memory", "TIMEOUT"]):
            result["evidence_lines"].append(f"L{i+1}: {line.strip()[:120]}")
        if len(result["evidence_lines"]) >= 15:
            break

    if not result["hint"] and result["termination"] == "error":
        result["hint"] = "检查 ORCA 输出尾部错误信息，或尝试切换到 g16 后端"

    return result


# ============================================================
# GROMACS
# ============================================================

def parse_gromacs_log(log_path: str, stage: str = "em") -> dict:
    """
    解析 GROMACS .log 文件，提取运行状态和异常。

    Args:
        log_path: .log 文件路径 (如 em.log, eq.log, prod.log)
        stage: 阶段名 (em/eq/prod)

    Returns:
        {
            "completed": bool,
            "lincs_warnings": int,
            "temp_extremes": {"min": float, "max": float}|None,
            "density_extremes": {"min": float, "max": float}|None,
            "anomalies": [str, ...],
            "evidence_lines": [str, ...],
            "hint": str,
        }
    """
    p = Path(log_path)
    if not p.exists():
        return {
            "completed": False, "lincs_warnings": 0,
            "temp_extremes": None, "density_extremes": None,
            "anomalies": [f"日志不存在: {log_path}"],
            "evidence_lines": [], "hint": "检查 GROMACS 是否正常启动",
        }

    text = p.read_text()
    lines = text.split("\n")

    result = {
        "completed": False,
        "lincs_warnings": 0,
        "temp_extremes": None,
        "density_extremes": None,
        "anomalies": [],
        "evidence_lines": [],
        "hint": "",
    }

    # ── 完成状态 ──
    if "Finished mdrun" in text or "Writing final coordinates" in text:
        result["completed"] = True

    # ── LINCS 警告 ──
    lincs_matches = re.findall(r"LINCS WARNING", text)
    result["lincs_warnings"] = len(lincs_matches)
    if result["lincs_warnings"] > 0:
        result["anomalies"].append(f"检测到 {result['lincs_warnings']} 个 LINCS 警告")
        if result["lincs_warnings"] > 100:
            result["hint"] = "大量 LINCS 警告：减小 dt (如 1fs → 0.5fs)、增加 lincs_iter/lincs_order、或检查原子重叠"

    # ── 温度异常 ──
    # 从 log 中提取温度范围
    temps = []
    for m in re.finditer(r"Temperature[:\s]+([\d.]+)", text):
        temps.append(float(m.group(1)))
    if temps:
        result["temp_extremes"] = {"min": min(temps), "max": max(temps)}
        if max(temps) > 1000:
            result["anomalies"].append(f"温度爆炸: 最高 {max(temps):.0f}K")
            if not result["hint"]:
                result["hint"] = "温度爆炸：减小 dt、检查原子重叠、增大 tau_t"

    # ── 密度异常 ──
    densities = []
    for m in re.finditer(r"Density[:\s]+([\d.]+)", text):
        densities.append(float(m.group(1)))
    if densities:
        result["density_extremes"] = {"min": min(densities), "max": max(densities)}
        # 密度下降低于 0.1 g/mL 或超过 5 g/mL 视为异常
        if min(densities) < 100:
            result["anomalies"].append(f"密度异常低: {min(densities):.1f} g/L")
            if not result["hint"]:
                result["hint"] = "密度过低：盒子太大或分子数不足，增大密度参数或减小 box_size"
        if max(densities) > 5000:
            result["anomalies"].append(f"密度异常高: {max(densities):.1f} g/L")
            if not result["hint"]:
                result["hint"] = "密度过高：盒子太小或分子过多，降低密度参数或增大 box_size"

    # ── 其他异常模式 ──
    other_checks = [
        (r"nan|inf", "检测到 NaN/Inf —— 模拟崩溃"),
        (r"too many lincs warnings", "LINCS 警告超过 GROMACS 阈值 —— 模拟中止"),
        (r"Segmentation fault", "段错误 (Segfault) —— GROMACS 崩溃"),
        (r"Domain decomposition.*failed|There is no domain decomposition", "域分解失败 —— 调 rcoulomb/rvdw 或换 mdrun 参数"),
        (r"Particle .* moved too far", "粒子移动过远 —— 减小 dt 或检查初始结构重叠"),
        (r"largest distance between excluded atoms", "排除原子距离过大 —— 检查分子是否跨越周期边界或拓扑是否错误"),
        (r"PME load.*imbalance", "PME 负载不均衡 —— 调整 PME 网格或处理器分配"),
        (r"step.*PME", "PME 步长问题 —— 检查 coulombtype 和 rcoulomb 设置"),
        (r"Water molecule.*not settled", "水分子未稳定 (SETTLE 约束失败) —— 减小 dt"),
        (r"pressure scaling.*> 1", "压力缩放过大 —— 增大 tau_p 或检查密度"),
    ]

    for pattern, desc in other_checks:
        if re.search(pattern, text, re.IGNORECASE):
            result["anomalies"].append(desc)

    # ── 证据行 ──
    for i, line in enumerate(lines):
        if any(kw in line for kw in ["LINCS WARNING", "Finished mdrun",
                                       "Temperature", "Density",
                                       "Fatal error", "Segmentation fault",
                                       "nan", "Domain decomposition",
                                       "Writing final coordinates"]):
            result["evidence_lines"].append(f"L{i+1}: {line.strip()[:120]}")
        if len(result["evidence_lines"]) >= 20:
            break

    # ── 针对阶段的 hint ──
    if not result["hint"]:
        if stage == "em" and not result["completed"]:
            result["hint"] = "EM 未收敛：增加 nsteps、增大 emtol、或重建盒子（增大 tolerance）"
        elif stage == "eq" and not result["completed"]:
            result["hint"] = "EQ 失败：检查温度/密度趋势；经用户确认后调整 md.eq 的保持段、tau_p 或 tau_t"
        elif stage == "prod" and not result["completed"]:
            result["hint"] = "PROD 崩溃：尝试从 checkpoint 重启或减小 dt"

    return result


# ============================================================
# 统一入口
# ============================================================

def diagnose_log(log_path: str, engine: str = "gaussian",
                 stage: str = "em") -> dict:
    """
    统一日志诊断入口。

    Args:
        log_path: 日志文件路径
        engine: "gaussian" | "orca" | "gromacs"
        stage: 仅对 gromacs 有效 (em/eq/prod)

    Returns:
        对应引擎的解析结果 dict
    """
    if engine == "gaussian":
        return parse_gaussian_log(log_path)
    elif engine == "orca":
        return parse_orca_output(log_path)
    elif engine == "gromacs":
        return parse_gromacs_log(log_path, stage=stage)
    else:
        return {"error": f"未知引擎: {engine}", "hint": "支持的引擎: gaussian, orca, gromacs"}
