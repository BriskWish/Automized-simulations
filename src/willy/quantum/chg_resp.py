"""
chg_resp.py
===========
统一的 RESP 电荷生成 (G16 + ORCA 共用)。

两步法 Step 3: *_opt.fchk → Multiwfn RESP(内部ESP) → .chg。

与 fchk_mol2.py 对称——都从统一的 *_opt.fchk 出发。
"""

import json
import subprocess
import time as _time
from pathlib import Path

from willy._paths import get_project_root
from willy.errors import StepResult, StepError, ErrorKind
from willy.process_lifecycle import run_managed_command
from willy.quantum._orca_utils import find_multiwfn

ROOT = get_project_root()


def make_chg(
    fchk_path: str,
    charge: int = 0,
    spin: int = 1,
    workdir: str = None,
    output_name: str = None,
) -> StepResult:
    """*_opt.fchk → Multiwfn RESP(内部ESP) → .chg。

    Multiwfn 菜单: 7→18→2→y→q (单步RESP, 内部算ESP)。

    Args:
        fchk_path: *_opt.fchk 路径。
        charge: 净电荷。
        spin: 自旋多重度。
        workdir: 工作目录，默认与 fchk 同目录。
        output_name: 输出文件名(不含扩展名)，默认从 fchk 推导。

    Returns:
        StepResult: outputs["chg"] = .chg 路径。
    """
    _start = _time.time()
    fp = Path(fchk_path)
    if not fp.exists():
        return StepResult(
            step_name="chg_resp", step_index=3, success=False,
            error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                            message=f"{fp} 不存在",
                            hint="确认 Step 2 (SP + mol2) 已成功运行"),
            duration_s=_time.time() - _start,
        )

    if workdir is None:
        workdir = str(fp.parent)
    if output_name is None:
        output_name = fp.stem.replace("_opt", "")

    chg_path = Path(workdir) / f"{output_name}.chg"
    if chg_path.exists():
        print(f"[chg_resp] ⏭ {output_name}: .chg 已存在")
        return StepResult(
            step_name="chg_resp", step_index=3, success=True,
            outputs={"chg": str(chg_path)},
            artifacts=[str(chg_path)], duration_s=0.0,
        )

    try:
        multiwfn = find_multiwfn()
    except RuntimeError as e:
        return StepResult(
            step_name="chg_resp", step_index=3, success=False,
            error=StepError(kind=ErrorKind.DEPENDENCY_MISSING, message=str(e)),
            duration_s=_time.time() - _start,
        )

    # Multiwfn RESP: 7→18→2→y→q
    commands = "7\n18\n2\ny\nq\n"
    try:
        run_managed_command(
            [multiwfn, str(fp.resolve()), "-ispecial", "1"],
            input_text=commands,
            cwd=workdir,
            timeout=600,
            run_dir=workdir,
        )
    except subprocess.TimeoutExpired:
        return StepResult(
            step_name="chg_resp", step_index=3, success=False,
            error=StepError(kind=ErrorKind.TIMEOUT,
                            message=f"{output_name}: Multiwfn RESP 超时 (600s)"),
            duration_s=_time.time() - _start,
        )

    # Check for output
    for candidate in [
        Path(workdir) / f"{fp.stem}.chg",
        Path(workdir) / "gau.chg",
    ]:
        if candidate.exists() and candidate != chg_path:
            candidate.rename(chg_path)
            break

    if not chg_path.exists():
        return StepResult(
            step_name="chg_resp", step_index=3, success=False,
            error=StepError(kind=ErrorKind.RESP_FAILED,
                            message=f"{output_name}: Multiwfn RESP 未生成 .chg"),
            duration_s=_time.time() - _start,
        )

    print(f"[chg_resp] ✅ {output_name}: {chg_path.name}  ({_time.time()-_start:.1f}s)")

    return StepResult(
        step_name="chg_resp", step_index=3, success=True,
        outputs={"chg": str(chg_path)},
        artifacts=[str(chg_path)],
        duration_s=_time.time() - _start,
    )


def batch_make_chg(
    struct_dir: str = "struct",
    config_path: str = "config.json",
    on_progress=None,
) -> list[StepResult]:
    """批量: *_opt.fchk → Multiwfn RESP → .chg。"""
    try:
        with open(config_path) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    registered = list(cfg.get("molecules", {}).keys())
    workspace = Path(struct_dir)
    if registered:
        expected_fchks = [(name, workspace / f"{name}_opt.fchk") for name in registered]
        missing = [(name, path) for name, path in expected_fchks if not path.is_file()]
        if missing:
            return [StepResult(
                step_name="chg_resp", step_index=3, success=False,
                error=StepError(
                    kind=ErrorKind.FILE_NOT_FOUND,
                    message=f"{name}_opt.fchk 不存在，需先完成 Step 2 单点计算与 mol2 转换",
                ),
                target_type="molecule", target=name,
            ) for name, _ in missing]
        fchks = [path for _, path in expected_fchks]
    else:
        # Keep the standalone utility usable when no config is supplied.
        fchks = sorted(workspace.glob("*_opt.fchk"))
    if not fchks:
        print(f"[chg_resp] ⚠ {struct_dir}/ 下没有 *_opt.fchk")
        return [StepResult(
            step_name="chg_resp", step_index=3, success=False,
            error=StepError(
                kind=ErrorKind.FILE_NOT_FOUND,
                message=f"{struct_dir}/ 下没有可用于 RESP 的 *_opt.fchk",
                hint="确认本次运行的 Step 2 已生成完整 FCHK 文件。",
            ),
        )]

    results = []
    total = len(fchks)
    for i, fchk in enumerate(fchks, 1):
        name = fchk.stem.replace("_opt", "")
        mol_info = cfg.get("molecules", {}).get(name, {})
        if on_progress:
            on_progress({
                "tool": "Multiwfn", "operation": "RESP 电荷计算",
                "target_type": "molecule", "target": name,
                "current": i, "total": total,
            })
        sr = make_chg(str(fchk), charge=mol_info.get("charge", 0),
                       spin=mol_info.get("spin", 1),
                       output_name=name)
        sr.target_type = "molecule"
        sr.target = name
        results.append(sr)

    ok = sum(1 for r in results if r.success)
    print(f"[chg_resp] 完成: {ok}/{total} 个 .chg")
    return results
