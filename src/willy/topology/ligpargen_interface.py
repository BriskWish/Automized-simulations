"""
ligpargen_interface.py
======================
LigParGen OPLS-AA 调用接口 —— SMILES/mol2 → .itp + .gro。

不生成主拓扑文件（.top），后续由 top_maker 单独构建。
与 sobtop_interface 保持相同的出参契约和 StepResult 协议。

依赖:
  - LigParGen (pip install ligpargen)
  - BOSS (http://zarbi.chem.yale.edu/software.html, 需设置 $BOSSdir)
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import subprocess
import shutil
import os
import glob as glob_mod

from willy._paths import get_project_root
from willy.errors import StepResult, StepError, ErrorKind

ROOT = get_project_root()
LIGPARGEN_BIN = shutil.which("LigParGen") or "/home/hush/.local/bin/LigParGen"


# ============================================================
# 环境预检
# ============================================================

def check_ligpargen_ready() -> list[str]:
    """
    预检 LigParGen + BOSS 环境是否就绪。

    调用 run_ligpargen 前应先调用此函数，在迁移到新机器时给出清晰错误提示。
    """
    from willy.env_checker import check_module
    issues = check_module("ligpargen_interface").failed_strs()
    if not issues:
        print("[ligpargen] ✅ LigParGen + BOSS 环境预检通过")
    return issues


# ============================================================
# 入参层
# ============================================================

@dataclass
class LigParGenInput:
    """LigParGen OPLS-AA 的输入参数。SMILES 或 mol2 → .itp + .gro"""
    smiles: Optional[str] = None       # SMILES 字符串（推荐，LigParGen 原生支持）
    mol2: Optional[str] = None         # .mol2 文件路径（备选，自动提取 SMILES）
    output_name: str = "MOL"           # 输出前缀/残基名
    net_charge: int = 0                # 净电荷: 0, ±1, ±2
    lbcc: bool = True                  # True=CM1A-LBCC (中性分子推荐), False=CM1A
    opt_steps: int = 0                 # 优化步数: 0=单点, 1-3=逐步优化
    cleanup_tmp: bool = True           # 是否清理 /tmp/ 下的临时产物


# ============================================================
# 核心逻辑
# ============================================================

def _mol2_to_smiles(mol2_path: str) -> str:
    """通过 vendored obabel 从 mol2 提取 SMILES。"""
    obabel = str(ROOT / "vendor" / "obabel.bin")
    result = subprocess.run(
        [obabel, mol2_path, "-osmi"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"无法从 {mol2_path} 提取 SMILES: {result.stderr}")

    smiles = result.stdout.strip().split()[0]  # obabel 输出格式: "SMILES\tNAME"
    print(f"[ligpargen] 🔄 mol2 → SMILES: {smiles}")
    return smiles


def _find_tmp_output(resname: str) -> dict[str, Path]:
    """在 /tmp/ 下查找 LigParGen 生成的文件。返回 {ext: Path}。"""
    found: dict[str, Path] = {}
    for ext in ["itp", "gro"]:
        path = Path(f"/tmp/{resname}.{ext}")
        if path.exists():
            found[ext] = path
    return found


def _cleanup_tmp(resname: str) -> None:
    """清理 /tmp/ 下 LigParGen 的临时产物。"""
    patterns = [
        f"/tmp/{resname}.*",
        f"/tmp/{resname}*.smi",
        f"/tmp/{resname}*.z",
    ]
    for pat in patterns:
        for f in glob_mod.glob(pat):
            try:
                os.remove(f)
            except OSError:
                pass


def make_itp_gro_opls(
    inp: LigParGenInput,
    output_dir: str = "topo",
) -> StepResult:
    """
    调用 LigParGen，生成 OPLS-AA .itp + .gro。

    Returns:
        StepResult (success=True 时 outputs={"itp": path, "gro": path})
    """
    import time as _time
    _start = _time.time()

    # ── 参数校验 ──
    if not inp.smiles and not inp.mol2:
        return StepResult(
            step_name="ligpargen_interface", step_index=4, success=False,
            error=StepError(kind=ErrorKind.CONFIG_INVALID,
                            message="必须提供 smiles 或 mol2",
                            hint="至少填入 LigParGenInput.smiles 或 LigParGenInput.mol2"),
            duration_s=_time.time() - _start,
        )

    if inp.net_charge not in (0, -1, 1, -2, 2):
        return StepResult(
            step_name="ligpargen_interface", step_index=4, success=False,
            error=StepError(kind=ErrorKind.CONFIG_INVALID,
                            message=f"不支持的净电荷: {inp.net_charge}（仅支持 0, ±1, ±2）",
                            hint="将 net_charge 设为 0, ±1 或 ±2"),
            duration_s=_time.time() - _start,
        )

    # ── 预检 ──
    issues = check_ligpargen_ready()
    if issues:
        return StepResult(
            step_name="ligpargen_interface", step_index=4, success=False,
            error=StepError(kind=ErrorKind.DEPENDENCY_MISSING,
                            message="LigParGen 环境未就绪",
                            raw_output="\n".join(issues),
                            hint="pip install ligpargen 并设置 $BOSSdir 指向 BOSS 安装目录"),
            duration_s=_time.time() - _start,
        )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ── 确定 SMILES ──
    smiles = inp.smiles
    if not smiles and inp.mol2:
        try:
            smiles = _mol2_to_smiles(inp.mol2)
        except RuntimeError as e:
            return StepResult(
                step_name="ligpargen_interface", step_index=4, success=False,
                error=StepError(kind=ErrorKind.LIGPARGEN_FAILED,
                                message=f"{inp.output_name}: {e}",
                                hint="检查 .mol2 文件是否完整，或直接提供 SMILES"),
                duration_s=_time.time() - _start,
            )

    resname = inp.output_name

    # ── 清理 /tmp/ 残留 ──
    _cleanup_tmp(resname)

    # ── 构建命令 ──
    cmd = [
        str(LIGPARGEN_BIN),
        "-s", smiles,
        "-r", resname,
        "-c", str(inp.net_charge),
        "-o", str(inp.opt_steps),
    ]
    if inp.lbcc and inp.net_charge == 0:
        cmd.append("-l")
    elif inp.lbcc and inp.net_charge != 0:
        print("[ligpargen] ⚠ LBCC 仅适用于中性分子，自动切换为 CM1A")

    print(f"[ligpargen] 🚀 运行: {' '.join(cmd)}")

    # ── 调用 LigParGen ──
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # BOSS 可能较慢
            env={**os.environ},
        )
    except subprocess.TimeoutExpired:
        _cleanup_tmp(resname)
        return StepResult(
            step_name="ligpargen_interface", step_index=4, success=False,
            error=StepError(kind=ErrorKind.TIMEOUT,
                            message=f"{resname}: LigParGen/BOSS 超时 (300s)",
                            hint="检查分子复杂度是否过高（>200 原子），或 BOSS 是否正常运行"),
            duration_s=_time.time() - _start,
        )

    # ── 取回产物 ──
    outputs: dict[str, Path] = {}
    artifacts: list[str] = []

    tmp_files = _find_tmp_output(resname)
    for ext in ["itp", "gro"]:
        if ext in tmp_files:
            dst = out / f"{resname}.{ext}"
            shutil.copy2(tmp_files[ext], dst)
            outputs[ext] = dst
            artifacts.append(str(dst))
            print(f"[ligpargen] ✅ {ext}: {dst}")

    # ── 清理临时文件 ──
    if inp.cleanup_tmp:
        _cleanup_tmp(resname)

    # ── 失败处理 ──
    if result.returncode != 0:
        raw = (result.stdout[-500:] if result.stdout else "") + "\n" + \
              (result.stderr[-500:] if result.stderr else "")
        print(f"[ligpargen] stderr:\n{result.stderr[-500:]}")
        print(f"[ligpargen] stdout:\n{result.stdout[-500:]}")
        return StepResult(
            step_name="ligpargen_interface", step_index=4, success=False,
            error=StepError(kind=ErrorKind.LIGPARGEN_FAILED,
                            message=f"{resname}: LigParGen 退出码={result.returncode}",
                            raw_output=raw.strip()[-500:],
                            hint="检查 $BOSSdir 设置和 BOSS 安装，或尝试用 SMILES 重新输入"),
            artifacts=artifacts,
            duration_s=_time.time() - _start,
        )

    if len(outputs) < 2:
        missing = [k for k in ["itp", "gro"] if k not in outputs]
        raw = (result.stdout[-500:] if result.stdout else "") + "\n" + \
              (result.stderr[-500:] if result.stderr else "")
        return StepResult(
            step_name="ligpargen_interface", step_index=4, success=False,
            error=StepError(kind=ErrorKind.LIGPARGEN_FAILED,
                            message=f"{resname}: LigParGen 未生成完整输出（缺失 {missing}）",
                            raw_output=raw.strip()[-500:],
                            hint="检查 SMILES 是否正确或分子是否超过 200 原子限制"),
            artifacts=artifacts,
            duration_s=_time.time() - _start,
        )

    duration = _time.time() - _start
    return StepResult(
        step_name="ligpargen_interface", step_index=4, success=True,
        outputs={k: str(v) for k, v in outputs.items()},
        artifacts=artifacts,
        duration_s=duration,
        extra={"returncode": result.returncode, "lbcc": inp.lbcc},
    )


# ============================================================
# 快捷函数
# ============================================================

def batch_make_topo_opls(
    mol2_dir: str = "struct",
    output_dir: str = "topo",
    net_charge: int = 0,
    lbcc: bool = True,
) -> list[StepResult]:
    """
    批量处理：对 mol2_dir 下所有 .mol2，用 LigParGen 生成 OPLS-AA .itp + .gro。

    与 sobtop_interface.batch_make_topo 接口对齐。

    Returns:
        List[StepResult] —— 每个分子一个结果
    """
    results: list[StepResult] = []
    for mol2 in sorted(Path(mol2_dir).glob("*.mol2")):
        name = mol2.stem
        sr = make_itp_gro_opls(
            LigParGenInput(
                mol2=str(mol2),
                output_name=name,
                net_charge=net_charge,
                lbcc=lbcc,
            ),
            output_dir=output_dir,
        )
        results.append(sr)

    ok = sum(1 for r in results if r.success)
    print(f"[ligpargen] 完成: {ok}/{len(results)} 个分子")
    return results


# ============================================================
# 测试入口
# ============================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python3 ligpargen_interface.py <SMILES> [resname]")
        print("示例: python3 ligpargen_interface.py 'c1ccccc1' BNZ")
        sys.exit(1)

    test_smiles = sys.argv[1]
    test_name = sys.argv[2] if len(sys.argv) > 2 else "TEST"

    print(f"===== 测试: SMILES={test_smiles} → {test_name} =====")
    r = make_itp_gro_opls(
        LigParGenInput(smiles=test_smiles, output_name=test_name),
        output_dir="topo",
    )
    if r.success:
        print(f"生成: {list(r.outputs.keys())}")
        for k, v in r.outputs.items():
            print(f"  {k}: {v} ({Path(v).stat().st_size} bytes)")
    else:
        print(f"❌ 测试失败: {r.error.message}")
        sys.exit(1)
