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
from willy.charge_scaling import validate_ion_charge_scale
from willy.quantum.charge_files import (
    archive_charge_files, cached_charge_record, charge_paths, publish_charge_files,
)

ROOT = get_project_root()


def make_chg(
    fchk_path: str,
    charge: int = 0,
    spin: int = 1,
    workdir: str = None,
    output_name: str = None,
    ion_charge_scale: float = 1.0,
) -> StepResult:
    """*_opt.fchk → Multiwfn RESP(内部ESP) → .chg。

    Multiwfn 菜单: 7→18→2→y→0→0→q (单步RESP, 内部算ESP)。

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

    directory = Path(workdir).resolve()
    try:
        factor = validate_ion_charge_scale(ion_charge_scale)
        if type(charge) is not int or type(spin) is not int or spin < 1:
            raise ValueError("电荷修正需要审计后的整数净电荷和自旋")
        chg_path, raw_path, metadata_path = charge_paths(directory, output_name)
        previous = cached_charge_record(directory, output_name, fp, charge, spin)
        raw_text = raw_path.read_text(encoding="utf-8") if previous else None
        if previous is None or previous["ion_charge_scale"] != factor:
            archive_charge_files(directory, output_name, fp)
            if raw_text is not None:
                previous = publish_charge_files(directory, output_name, fp, charge, spin, factor, raw_text)
    except (OSError, ValueError) as exc:
        return StepResult(
            step_name="chg_resp", step_index=3, success=False,
            error=StepError(ErrorKind.INPUT_CONTRACT, str(exc)), duration_s=_time.time() - _start,
        )
    if previous is not None:
        print(f"[chg_resp] ⏭ {output_name}: 已验证电荷产物，修正因子 {factor:.2f}")
        return StepResult(
            step_name="chg_resp", step_index=3, success=True,
            outputs={"chg": str(chg_path), "raw_chg": str(raw_path), "charge_scaling": str(metadata_path)},
            artifacts=[str(chg_path), str(raw_path), str(metadata_path)],
            extra={"charge_scaling": previous}, duration_s=_time.time() - _start,
        )

    try:
        multiwfn = find_multiwfn()
    except RuntimeError as e:
        return StepResult(
            step_name="chg_resp", step_index=3, success=False,
            error=StepError(kind=ErrorKind.DEPENDENCY_MISSING, message=str(e)),
            duration_s=_time.time() - _start,
        )

    # Multiwfn RESP: 7→18→2→y→0→0→q.  The two zeroes leave the
    # post-fit menus cleanly so the process exits with a success status.
    commands = "7\n18\n2\ny\n0\n0\nq\n"
    try:
        result = run_managed_command(
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
    if result.returncode != 0:
        return StepResult(
            step_name="chg_resp", step_index=3, success=False,
            error=StepError(kind=ErrorKind.RESP_FAILED,
                            message=f"{output_name}: Multiwfn RESP 执行失败"),
            duration_s=_time.time() - _start,
        )

    # Check for output
    for candidate in [
        chg_path,
        Path(workdir) / f"{fp.stem}.chg",
        Path(workdir) / "gau.chg",
    ]:
        if candidate.exists() or candidate.is_symlink():
            try:
                if candidate.is_symlink() or not candidate.is_file():
                    raise ValueError("Multiwfn 电荷输出必须是独立文件")
                record = publish_charge_files(
                    directory, output_name, fp, charge, spin, factor, candidate.read_text(encoding="utf-8"),
                )
                if candidate != chg_path:
                    candidate.unlink()
            except (OSError, ValueError) as exc:
                return StepResult(
                    step_name="chg_resp", step_index=3, success=False,
                    error=StepError(ErrorKind.RESP_FAILED, str(exc)), duration_s=_time.time() - _start,
                )
            break
    else:
        record = None

    if record is None:
        return StepResult(
            step_name="chg_resp", step_index=3, success=False,
            error=StepError(kind=ErrorKind.RESP_FAILED,
                            message=f"{output_name}: Multiwfn RESP 未生成 .chg"),
            duration_s=_time.time() - _start,
        )

    print(f"[chg_resp] ✅ {output_name}: {chg_path.name}  ({_time.time()-_start:.1f}s)")

    return StepResult(
        step_name="chg_resp", step_index=3, success=True,
        outputs={"chg": str(chg_path), "raw_chg": str(raw_path), "charge_scaling": str(metadata_path)},
        artifacts=[str(chg_path), str(raw_path), str(metadata_path)],
        extra={"charge_scaling": record},
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
    except FileNotFoundError:
        cfg = {}
    except (OSError, ValueError):
        return [StepResult("chg_resp", 3, False, error=StepError(ErrorKind.CONFIG_INVALID, "RESP 配置不可读取"))]
    try:
        factor = validate_ion_charge_scale(cfg.get("ion_charge_scale", 1.0))
    except (AttributeError, ValueError) as exc:
        return [StepResult("chg_resp", 3, False, error=StepError(ErrorKind.CONFIG_INVALID, str(exc)))]
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
                       output_name=name, ion_charge_scale=factor)
        sr.target_type = "molecule"
        sr.target = name
        results.append(sr)

    ok = sum(1 for r in results if r.success)
    print(f"[chg_resp] 完成: {ok}/{total} 个 .chg")
    return results
