"""
orca_mol2_maker.py — ORCA .molden → .fchk → .mol2。

通过 Multiwfn 将 molden 转为 fchk，再复用 g16_mol2_maker 的解析逻辑。
"""
from pathlib import Path
import subprocess
import time as _time

from willy.errors import StepResult, StepError, ErrorKind

MULTIWFN_BIN = "Multiwfn"


def molden_to_fchk(molden_path: str, output_path: str = None) -> StepResult:
    """Multiwfn: .molden → .fchk (function 100→2→7)。返回 StepResult。"""
    _start = _time.time()
    mp = Path(molden_path)
    if not mp.exists():
        return StepResult(
            step_name="orca_mol2_maker", step_index=2, success=False,
            error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                            message=f"{mp} 不存在",
                            hint="确认 ORCA 结构优化已成功运行"),
            duration_s=_time.time() - _start,
        )

    if output_path is None:
        output_path = str(mp.with_suffix(".fchk"))
    out = Path(output_path)

    commands = f"100\n2\n7\n{out.resolve()}\n0\nq\n"
    try:
        result = subprocess.run(
            [MULTIWFN_BIN, str(mp.resolve())],
            input=commands, capture_output=True, text=True,
            cwd=str(mp.parent), timeout=120)
    except subprocess.TimeoutExpired:
        return StepResult(
            step_name="orca_mol2_maker", step_index=2, success=False,
            error=StepError(kind=ErrorKind.TIMEOUT,
                            message=f"{mp.name}: Multiwfn molden→fchk 超时 (120s)",
                            hint="检查 Multiwfn 是否正常安装"),
            duration_s=_time.time() - _start,
        )

    if out.exists():
        duration = _time.time() - _start
        print(f"[orca_mol2] ✅ {mp.name} → {out.name} ({out.stat().st_size} bytes, {duration:.1f}s)")
        return StepResult(
            step_name="orca_mol2_maker", step_index=2, success=True,
            outputs={"fchk": str(out)},
            artifacts=[str(out)],
            duration_s=duration,
        )

    raw = result.stderr[-500:] if result.stderr else ""
    print(f"[orca_mol2] ❌ molden→fchk 失败\n{raw}")
    return StepResult(
        step_name="orca_mol2_maker", step_index=2, success=False,
        error=StepError(kind=ErrorKind.ORCA_CRASH,
                        message=f"{mp.name}: Multiwfn molden→fchk 转换失败",
                        raw_output=raw,
                        hint="检查 .molden 文件是否完整，或尝试手动 Multiwfn 转换"),
        duration_s=_time.time() - _start,
    )


def fchk_to_mol2(fchk_path: str, output_path: str = None) -> StepResult:
    """复用 g16_mol2_maker 的 fchk→mol2 逻辑。返回 StepResult。"""
    from willy.quantum.g16_mol2_maker import fchk_to_mol2 as _g16_fchk_to_mol2
    result = _g16_fchk_to_mol2(fchk_path, output_path)
    if isinstance(result, StepResult):
        return result
    # 兼容旧版返回 Path 的情况
    if result is not None:
        return StepResult(
            step_name="orca_mol2_maker", step_index=2, success=True,
            outputs={"mol2": str(result)},
            artifacts=[str(result)],
        )
    return StepResult(
        step_name="orca_mol2_maker", step_index=2, success=False,
        error=StepError(kind=ErrorKind.UNKNOWN,
                        message="fchk→mol2 转换失败",
                        hint="检查 .fchk 文件完整性"),
    )


def run_one(molden_path: str) -> StepResult:
    """单文件: .molden → .fchk → .mol2。返回 StepResult。"""
    sr = molden_to_fchk(molden_path)
    if not sr.success:
        return sr
    sr2 = fchk_to_mol2(sr.outputs["fchk"])
    if sr2.success:
        print(f"[orca_mol2] ✅ {Path(sr2.outputs['mol2']).name} 已生成")
        sr2.artifacts = sr.artifacts + sr2.artifacts
        sr2.duration_s += sr.duration_s
    return sr2


def batch_convert(struct_dir: str = "struct") -> list[StepResult]:
    """批量: struct/ 下所有 .molden → .mol2。返回 List[StepResult]。"""
    results: list[StepResult] = []
    for molden in sorted(Path(struct_dir).glob("*.molden")):
        sr = run_one(str(molden))
        results.append(sr)
    ok = sum(1 for r in results if r.success)
    print(f"[orca_mol2] 完成: {ok} 个 .mol2")
    return results
