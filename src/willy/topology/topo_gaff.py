"""
topo_gaff.py
===================
Sobtop 调用接口 —— mol2 + chg → .itp + .gro。

不生成主拓扑文件（.top），后续由 top_assembly 单独构建。
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import subprocess
import shutil
import os

from willy._paths import get_project_root
from willy.errors import StepResult, StepError, ErrorKind
SOBTOP_DIR = get_project_root() / "vendor" / "sobtop"
SOBTOP_BIN = SOBTOP_DIR / "sobtop"


# ============================================================
# 环境预检
# ============================================================

def check_sobtop_ready() -> list[str]:
    """
    预检 Sobtop 环境是否就绪。委托到 env_checker。

    调用 run_sobtop 前应先调用此函数，在迁移到新机器时给出清晰错误提示。
    """
    from willy.env_checker import check_module
    issues = check_module("topo_gaff").failed_strs()
    if not issues:
        print("[sobtop] ✅ Sobtop 环境预检通过")
    return issues


# ============================================================
# 入参层
# ============================================================

@dataclass
class TopMakerInput:
    """Sobtop 的输入参数。mol2 + chg → .itp + .gro（不含 .top）"""
    mol2: str                     # .mol2 文件路径（必填）
    chg: str                      # .chg 电荷文件路径（必填）
    output_name: str = "MOL"      # 输出前缀
    gaff: bool = True             # True=GAFF, False=AMBER
    hessian: Optional[str] = None # .fchk 路径（需要自定义力常数时）


# ============================================================
# 核心逻辑
# ============================================================

def make_itp_gro(inp: TopMakerInput, output_dir: str = "topo") -> StepResult:
    """
    调用 Sobtop，生成 .itp + .gro。丢弃 .top。
    Sobtop 使用默认路径写出到自身目录，接口取回 .itp 和 .gro 到 output_dir。

    Returns:
        StepResult (success=True 时 outputs={"itp": path, "gro": path})
    """
    import time as _time
    _start = _time.time()

    # ── 预检 ──
    issues = check_sobtop_ready()
    if issues:
        return StepResult(
            step_name="topo_gaff", step_index=4, success=False,
            error=StepError(kind=ErrorKind.DEPENDENCY_MISSING,
                            message="Sobtop 环境未就绪",
                            raw_output="\n".join(issues),
                            hint="安装 Sobtop 并确保 vendor/sobtop/ 下有 sobtop 可执行文件"),
            duration_s=_time.time() - _start,
        )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ── 构建交互输入序列 ──
    lines = []
    mol2_abs = str(Path(inp.mol2).resolve())
    lines.append(mol2_abs)
    chg_abs = str(Path(inp.chg).resolve())
    lines.append("7")
    lines.append("10")
    lines.append(chg_abs)
    lines.append("0")
    lines.append("2")
    lines.append("")
    lines.append("1")
    at_opt = "2" if inp.gaff else "1"
    lines.append(at_opt)
    if inp.hessian:
        lines.append("7")
        lines.append(str(Path(inp.hessian).resolve()))
    else:
        lines.append("4")
    lines.append("")
    lines.append("")
    stdin_str = "\n".join(lines) + "\n"

    # ── 调用 Sobtop ──
    try:
        result = subprocess.run(
            [str(SOBTOP_BIN)],
            input=stdin_str,
            capture_output=True,
            text=True,
            cwd=str(SOBTOP_DIR),
            timeout=120,
            env={**os.environ, "OMP_NUM_THREADS": "1"},
        )
    except subprocess.TimeoutExpired:
        return StepResult(
            step_name="topo_gaff", step_index=4, success=False,
            error=StepError(kind=ErrorKind.TIMEOUT,
                            message=f"{inp.output_name}: Sobtop 超时 (120s)",
                            hint="检查 .mol2/.chg 文件是否过大或损坏"),
            duration_s=_time.time() - _start,
        )

    # ── 取回需要的文件（.itp + .gro），删除 .top ──
    outputs: dict[str, Path] = {}
    artifacts: list[str] = []

    src_itp = SOBTOP_DIR / f"{inp.output_name}.itp"
    if src_itp.exists():
        dst = out / f"{inp.output_name}.itp"
        shutil.copy2(src_itp, dst)
        src_itp.unlink()
        outputs["itp"] = dst
        artifacts.append(str(dst))
        print(f"[sobtop] ✅ itp: {dst}")

    src_gro = SOBTOP_DIR / f"{inp.output_name}.gro"
    if src_gro.exists():
        dst = out / f"{inp.output_name}.gro"
        shutil.copy2(src_gro, dst)
        src_gro.unlink()
        outputs["gro"] = dst
        artifacts.append(str(dst))
        print(f"[sobtop] ✅ gro: {dst}")

    src_top = SOBTOP_DIR / f"{inp.output_name}.top"
    if src_top.exists():
        src_top.unlink()

    # ── 检查 ──
    if len(outputs) < 2:
        missing = [k for k in ["itp", "gro"] if k not in outputs]
        raw = (result.stdout[-500:] if result.stdout else "") + "\n" + \
              (result.stderr[-500:] if result.stderr else "")
        print(f"[sobtop] ❌ 缺失文件: {missing}")
        print(f"[sobtop] stdout tail: {result.stdout[-500:]}")
        print(f"[sobtop] stderr tail: {result.stderr[-500:]}")
        return StepResult(
            step_name="topo_gaff", step_index=4, success=False,
            error=StepError(kind=ErrorKind.SOBTOP_FAILED,
                            message=f"{inp.output_name}: Sobtop 未生成完整输出（缺失 {missing}）",
                            raw_output=raw.strip()[-500:],
                            hint="检查 .mol2 和 .chg 格式是否正确，或尝试 LigParGen 替代"),
            artifacts=artifacts,
            duration_s=_time.time() - _start,
        )

    if result.returncode != 0:
        print(f"[sobtop] ⚠  Sobtop 退出码={result.returncode}（输出文件正常，可忽略）")

    duration = _time.time() - _start
    return StepResult(
        step_name="topo_gaff", step_index=4, success=True,
        outputs={k: str(v) for k, v in outputs.items()},
        artifacts=artifacts,
        duration_s=duration,
        extra={"returncode": result.returncode},
    )


# ============================================================
# 快捷函数
# ============================================================

def batch_make_topo(mol2_dir: str = "struct",
                    chg_dir: str = "struct",
                    output_dir: str = "topo",
                    ) -> list[StepResult]:
    """
    批量处理：对 mol2_dir 下所有 .mol2，用 chg_dir 下同名 .chg 生成 .itp + .gro。

    Returns:
        List[StepResult] —— 每个分子一个结果
    """
    results: list[StepResult] = []
    for mol2 in sorted(Path(mol2_dir).glob("*.mol2")):
        name = mol2.stem
        chg = Path(chg_dir) / f"{name}.chg"
        if not chg.exists():
            print(f"[sobtop] ⚠ {name}: 找不到 {chg}，跳过")
            results.append(StepResult(
                step_name="topo_gaff", step_index=4, success=False,
                error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                                message=f"{name}: 找不到 {chg}",
                                hint="先运行 RESP 电荷计算 (chg_maker)"),
            ))
            continue
        sr = make_itp_gro(
            TopMakerInput(mol2=str(mol2), chg=str(chg), output_name=name),
            output_dir=output_dir,
        )
        results.append(sr)

    ok = sum(1 for r in results if r.success)
    print(f"[sobtop] 完成: {ok}/{len(results)} 个分子")
    return results


# ============================================================
# 测试入口
# ============================================================

if __name__ == "__main__":
    # 测试单个：struct/DME.mol2 + struct/DME.chg
    print("===== 测试: DME.mol2 + DME.chg =====")
    r = make_itp_gro(
        TopMakerInput(mol2="struct/DME.mol2", chg="struct/DME.chg",
                      output_name="DME"),
        output_dir="topo",
    )
    print(f"生成: {list(r.keys())}")
