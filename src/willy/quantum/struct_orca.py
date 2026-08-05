"""
struct_orca.py — ORCA 结构优化 + 格式转换。

.gjf → .inp → orca opt freq → .gbw → orca_2mkl → .molden
"""

from pathlib import Path
import subprocess, os, json, time, re

from willy._paths import get_project_root
from willy.errors import StepResult, StepError, ErrorKind
from willy.process_lifecycle import run_managed_command
from willy.quantum._orca_utils import (
    find_orca, find_orca_2mkl, get_orca_env, format_orca_xyz_coords,
)

ROOT = get_project_root()


def _gjf_to_inp(gjf_path: Path, basis: str = "b3lyp/6-311+g(d,p)",
                mem_mb: int = 5000, nproc: int = 8) -> str:
    """将 .gjf 转换为 ORCA .inp 格式。"""
    text = gjf_path.read_text()
    lines = text.split("\n")
    charge = 0; spin = 1; coords = []
    in_charge = False; in_coords = False
    for line in lines:
        s = line.strip()
        if s.startswith("#") or s.startswith("%"): continue
        if not s: continue
        parts = s.split()
        if len(parts) == 2 and not in_charge:
            try:
                charge = int(parts[0]); spin = int(parts[1])
                in_charge = True; continue
            except ValueError: pass
        if in_charge: in_coords = True
        if in_coords and len(parts) == 4:
            coords.append(s)

    xyz_block = format_orca_xyz_coords(coords)

    # 转换 Gaussian 基组格式 → ORCA 格式 (b3lyp/6-311+g(d,p) → B3LYP 6-311+G(d,p))
    orca_basis = basis.replace("b3lyp/", "B3LYP ").replace("b3lyp", "B3LYP")
    orca_basis = orca_basis.replace("def2TZVP", "def2-TZVP").replace("def2SVP", "def2-SVP")
    return f"""! {orca_basis} Opt Freq
%pal nprocs {nproc} end
%maxcore {mem_mb}
* xyz {charge} {spin}
{xyz_block}
*"""


def run_one(name: str, cfg: dict, defaults: dict, struct_dir: str = "struct") -> StepResult:
    """单分子：gjf→inp→orca→molden。返回 StepResult。若 .molden 已存在则跳过。"""
    _start = time.time()
    std = Path(struct_dir)
    molden_out = std / f"{name}.molden"
    if molden_out.exists():
        print(f"[orca_struct] ⏭ {name}: .molden 已存在，跳过结构优化")
        return StepResult(
            step_name="struct_orca", step_index=1, success=True,
            outputs={"molden": str(molden_out)},
            artifacts=[str(molden_out)],
            duration_s=0.0,
        )

    gjf = std / f"{name}.gjf"
    if not gjf.exists():
        return StepResult(
            step_name="struct_orca", step_index=1, success=False,
            error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                            message=f"struct/{name}.gjf 不存在",
                            hint="检查 struct/ 目录，确保 .gjf 文件已放置"),
            duration_s=time.time() - _start,
        )

    basis = cfg.get("basis", "b3lyp/6-311+g(d,p)")
    mem = int((cfg.get("mem") or defaults.get("mem", "5GB")).replace("GB","000").replace("MB",""))
    nproc = cfg.get("nproc") or defaults.get("nproc", 8)
    mem_mb = max(1000, mem)

    # 写 .inp
    inp_path = std / f"{name}.inp"
    inp_path.write_text(_gjf_to_inp(gjf, basis, mem_mb, nproc))
    print(f"[orca_struct] {name}: 已生成 {inp_path.name}")

    # 运行 ORCA
    try:
        orca_bin = find_orca()
        orca_env = get_orca_env()
    except RuntimeError as e:
        return StepResult(
            step_name="struct_orca", step_index=1, success=False,
            error=StepError(kind=ErrorKind.DEPENDENCY_MISSING, message=str(e),
                            hint="安装 ORCA 并设置 WILLY_ORCA_HOME/WILLY_ORCA_BIN，或将 ORCA 加入 PATH"),
            duration_s=time.time() - _start,
        )
    try:
        result = run_managed_command(
            [orca_bin, inp_path.name],
            cwd=std,
            timeout=7200,
            env=orca_env,
            run_dir=std,
        )
    except subprocess.TimeoutExpired:
        return StepResult(
            step_name="struct_orca", step_index=1, success=False,
            error=StepError(kind=ErrorKind.TIMEOUT,
                            message=f"{name}: ORCA 计算超时 (7200s)",
                            hint="减少 nproc 或换用更小基组"),
            duration_s=time.time() - _start,
        )

    gbw = std / f"{name}.gbw"
    if not gbw.exists():
        raw = result.stderr[-500:] if result.stderr else result.stdout[-500:]
        print(f"[orca_struct] ❌ {name}: ORCA 失败\n{raw}")
        return StepResult(
            step_name="struct_orca", step_index=1, success=False,
            error=StepError(kind=ErrorKind.ORCA_CRASH,
                            message=f"{name}: ORCA 结构优化失败（.gbw 未生成）",
                            raw_output=raw,
                            hint="检查 ORCA 输出日志，尝试换基组或降低 nproc"),
            duration_s=time.time() - _start,
        )
    print(f"[orca_struct] ✅ {name}: .gbw 已生成")

    # orca_2mkl → .molden.input
    try:
        orca_2mkl = find_orca_2mkl()
    except RuntimeError:
        pass  # continue; file check below will catch if molden is missing
    else:
        try:
            run_managed_command(
                [orca_2mkl, name, "-molden"],
                cwd=std,
                timeout=60,
                env=orca_env,
                run_dir=std,
            )
        except subprocess.TimeoutExpired:
            pass  # 继续检查文件是否存在

    molden = std / f"{name}.molden.input"
    if molden.exists():
        molden2 = std / f"{name}.molden"
        molden.rename(molden2)
        duration = time.time() - _start
        print(f"[orca_struct] ✅ {name}: {molden2.name} 已生成  ({duration:.1f}s)")
        return StepResult(
            step_name="struct_orca", step_index=1, success=True,
            outputs={"molden": str(molden2), "gbw": str(gbw)},
            artifacts=[str(molden2), str(gbw), str(inp_path)],
            duration_s=duration,
        )

    print(f"[orca_struct] ❌ {name}: orca_2mkl 失败")
    return StepResult(
        step_name="struct_orca", step_index=1, success=False,
        error=StepError(kind=ErrorKind.ORCA_CRASH,
                        message=f"{name}: orca_2mkl 转换失败（.molden 未生成）",
                        hint="检查 orca_2mkl 是否在 PATH 中，或尝试手动转换"),
        artifacts=[str(gbw), str(inp_path)],
        duration_s=time.time() - _start,
    )


def run_all(config_path: str = "config.json", struct_dir: str = "struct",
            on_progress=None) -> list[StepResult]:
    """批量运行 ORCA 优化 + molden 生成。返回 List[StepResult]。

    on_progress(activity) — 每分子开始前回调，activity 为公开结构化字段。
    """
    with open(config_path) as f:
        config = json.load(f)
    molecules = config.get("molecules", {})
    defaults = config.get("defaults", {})
    total = len(molecules)

    results: list[StepResult] = []
    for i, (name, cfg) in enumerate(molecules.items(), 1):
        if on_progress:
            on_progress({
                "tool": "ORCA", "operation": "结构优化",
                "target_type": "molecule", "target": name,
                "current": i, "total": total,
            })
        gjf_path = Path(struct_dir) / f"{name}.gjf"
        if not gjf_path.exists():
            results.append(StepResult(
                step_name="struct_orca", step_index=1, success=False,
                error=StepError(
                    kind=ErrorKind.FILE_NOT_FOUND,
                    message=f"{gjf_path} 不存在",
                    hint=f"确认已从 struct/ 复制 {name}.gjf 到本次运行目录",
                ), target_type="molecule", target=name,
            ))
            continue
        print(f"\n[{i}/{total}] {name}")
        sr = run_one(name, cfg, defaults, struct_dir)
        sr.target_type = "molecule"
        sr.target = name
        results.append(sr)
    ok = sum(1 for r in results if r.success)
    print(f"\n[orca_struct] 完成: {ok}/{len(molecules)} 个分子")
    return results
