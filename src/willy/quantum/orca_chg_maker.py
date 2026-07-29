"""
orca_chg_maker.py — ORCA 链路的 RESP 电荷生成。

当前实现: 从 ORCA 优化的 .gjf 出发，调用 RESP_noopt.sh (Gaussian SP + Multiwfn RESP)。
未来改进: 直接从 .molden 读取波函数拟合 RESP (待 Multiwfn 兼容 ORCA molden)。
"""
from pathlib import Path
import subprocess
import json
import time as _time

from willy.errors import StepResult, StepError, ErrorKind

RESP_SCRIPT = None  # 延迟初始化


def _get_resp_script():
    global RESP_SCRIPT
    if RESP_SCRIPT is None:
        from willy._paths import get_project_root
        RESP_SCRIPT = str(get_project_root() / "RESP_noopt.sh")
    return RESP_SCRIPT


def run_one(gjf_path: str, charge: int = None, spin: int = None,
            solvent: str = "acetone") -> StepResult:
    """单分子: .gjf → RESP_noopt.sh → .chg。返回 StepResult。"""
    _start = _time.time()
    gp = Path(gjf_path)
    if not gp.exists():
        return StepResult(
            step_name="orca_chg_maker", step_index=3, success=False,
            error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                            message=f"{gp} 不存在",
                            hint="确认 ORCA 结构优化已完成"),
            duration_s=_time.time() - _start,
        )

    # 从 .gjf 读取 charge/spin (若未指定)
    if charge is None or spin is None:
        text = gp.read_text()
        for line in text.split("\n"):
            parts = line.strip().split()
            if len(parts) == 2:
                try:
                    if charge is None: charge = int(parts[0])
                    if spin is None: spin = int(parts[1])
                    break
                except ValueError:
                    pass
    charge = charge or 0
    spin = spin or 1

    script = _get_resp_script()
    cmd = f"bash {script} {gp.resolve()} {charge} {spin} {solvent}"
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            cwd=str(gp.parent), timeout=600)
    except subprocess.TimeoutExpired:
        return StepResult(
            step_name="orca_chg_maker", step_index=3, success=False,
            error=StepError(kind=ErrorKind.TIMEOUT,
                            message=f"{gp.stem}: RESP 计算超时 (600s)",
                            hint="减少基组大小或检查 Gaussian/Multiwfn"),
            duration_s=_time.time() - _start,
        )

    chg_path = gp.parent / f"{gp.stem}.chg"
    if chg_path.exists():
        duration = _time.time() - _start
        print(f"[orca_chg] ✅ {gp.stem}: .chg 已生成  ({duration:.1f}s)")
        return StepResult(
            step_name="orca_chg_maker", step_index=3, success=True,
            outputs={"chg": str(chg_path)},
            artifacts=[str(chg_path)],
            duration_s=duration,
        )

    raw = result.stderr[-500:] if result.stderr else ""
    print(f"[orca_chg] ❌ {gp.stem}: RESP 失败\n{raw}")
    return StepResult(
        step_name="orca_chg_maker", step_index=3, success=False,
        error=StepError(kind=ErrorKind.RESP_FAILED,
                        message=f"{gp.stem}: RESP 电荷计算失败",
                        raw_output=raw,
                        hint=f"检查 Gaussian/Multiwfn 输出，尝试更换溶剂（当前={solvent}）"),
        duration_s=_time.time() - _start,
    )


def batch_make_chg(struct_dir: str = "struct",
                   config_path: str = "config.json") -> list[StepResult]:
    """批量: struct/ 下所有 .gjf → .chg。返回 List[StepResult]。"""
    with open(config_path) as f:
        config = json.load(f)
    molecules = config.get("molecules", {})

    results: list[StepResult] = []
    for gjf in sorted(Path(struct_dir).glob("*.gjf")):
        name = gjf.stem
        mol_info = molecules.get(name, {})
        charge = mol_info.get("charge")
        spin = mol_info.get("spin")
        solvent = mol_info.get("solvent", "acetone")
        sr = run_one(str(gjf), charge=charge, spin=spin, solvent=solvent)
        results.append(sr)

    ok = sum(1 for r in results if r.success)
    print(f"[orca_chg] 完成: {ok}/{len(results)} 个 .chg")
    return results
