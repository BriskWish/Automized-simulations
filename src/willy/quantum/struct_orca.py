"""ORCA 结构优化 + 格式转换。

用户上传的 ``.inp`` 是唯一原始输入：
``.inp → orca opt freq → .gbw → orca_2mkl → .molden``。
不会从 Gaussian ``.gjf`` 静默转换，避免跨后端丢失电荷、自旋或关键词。
"""

from pathlib import Path
import shutil
import subprocess, os, json, time, re

from willy._paths import get_project_root
from willy.errors import StepResult, StepError, ErrorKind
from willy.process_lifecycle import run_managed_command
from willy.execution_resources import memory_to_mb, resolve_nproc
from willy.quantum._orca_utils import (
    find_orca, find_orca_2mkl, get_orca_env,
)

ROOT = get_project_root()


def _single_atom_input(path: Path) -> bool:
    """Return whether an inline ``* xyz`` input contains exactly one atom.

    ORCA normally writes a ``.gbw`` for a molecular optimization.  Its
    single-atom ``Opt`` special case can terminate normally without writing
    that optimization artifact, so it needs a deterministic single-point
    fallback.  Inputs using ``* xyzfile`` are left on the normal path because
    their atom count is not available without executing an external parser.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return False
    for index, line in enumerate(lines):
        match = re.match(r"^\s*\*\s+xyz\s+[-+]?\d+\s+\d+\s*$", line, re.IGNORECASE)
        if not match:
            continue
        atoms = 0
        for atom_line in lines[index + 1:]:
            if atom_line.strip() == "*":
                break
            fields = atom_line.split()
            if len(fields) >= 4:
                try:
                    float(fields[1])
                    float(fields[2])
                    float(fields[3])
                except ValueError:
                    continue
                atoms += 1
        return atoms == 1
    return False


def _single_atom_sp_input(inp_path: Path) -> tuple[Path, bool]:
    """Create an isolated SP input for a single-atom ``Opt`` request."""
    if not _single_atom_input(inp_path):
        return inp_path, False
    lines = inp_path.read_text(encoding="utf-8").splitlines(keepends=True)
    rewritten = False
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("!"):
            continue
        replacement, substitutions = re.subn(
            r"\bOpt\b", "SP", line, count=1, flags=re.IGNORECASE,
        )
        if substitutions:
            lines[index] = replacement
            rewritten = True
        break
    if not rewritten:
        return inp_path, False
    temporary = inp_path.with_name(f"{inp_path.stem}__single_atom_sp.inp")
    temporary.write_text("".join(lines), encoding="utf-8")
    return temporary, True


def _cleanup_single_atom_execution(path: Path) -> None:
    """Remove only artifacts created under the private single-atom prefix."""
    stems = [path.stem]
    if path.stem.endswith("__willy_run"):
        source_stem = path.stem.removesuffix("__willy_run")
        if source_stem.endswith("__single_atom_sp"):
            stems.append(source_stem)
    for stem in stems:
        for artifact in path.parent.glob(f"{stem}.*"):
            try:
                if artifact.is_file() or artifact.is_symlink():
                    artifact.unlink()
            except OSError:
                pass


def _configured_nproc(cfg: dict, defaults: dict) -> int:
    """Resolve the ORCA CPU width from molecule or workflow configuration."""
    return resolve_nproc(cfg.get("nproc") or defaults.get("nproc"))


def _configured_mem_mb(cfg: dict, defaults: dict) -> int:
    """Resolve shared workflow memory as ORCA's per-core ``%maxcore`` value."""
    return memory_to_mb(cfg.get("mem") or defaults.get("mem", "5GB"))


def _prepare_runtime_input(inp_path: Path, nproc: int, mem_mb: int) -> Path:
    """Create a private input with a deterministic ORCA parallel override.

    The uploaded ``.inp`` remains immutable. Existing resource directives are
    removed from the execution copy so stale input cannot override config.
    """
    lines = inp_path.read_text(encoding="utf-8").splitlines(keepends=True)
    cleaned: list[str] = []
    in_pal_block = False
    for line in lines:
        stripped = line.strip().lower()
        if stripped.startswith("%pal"):
            in_pal_block = "end" not in stripped
            continue
        if stripped.startswith("%maxcore"):
            continue
        if in_pal_block:
            if stripped == "end":
                in_pal_block = False
            continue
        cleaned.append(line)
    insertion = next(
        (index for index, line in enumerate(cleaned)
         if line.strip().lower().startswith(("* xyz ", "* xyzfile "))),
        len(cleaned),
    )
    cleaned[insertion:insertion] = [
        f"%maxcore {mem_mb}\n",
        f"%pal nprocs {nproc} end\n",
    ]
    runtime = inp_path.with_name(f"{inp_path.stem}__willy_run.inp")
    runtime.write_text("".join(cleaned), encoding="utf-8")
    return runtime


def run_one(name: str, cfg: dict, defaults: dict, struct_dir: str = "struct") -> StepResult:
    """单分子：inp→orca→molden。若 .molden 已存在则跳过。"""
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

    inp_path = std / f"{name}.inp"
    if not inp_path.exists():
        return StepResult(
            step_name="struct_orca", step_index=1, success=False,
            error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                            message=f"struct/{name}.inp 不存在",
                            hint="ORCA 链路需要同名 .inp 原始输入；请检查 struct/ 目录"),
            duration_s=time.time() - _start,
        )

    # A one-atom geometry has no optimization degrees of freedom.  ORCA can
    # terminate ``Opt`` normally while omitting the optimization ``.gbw``;
    # use a single point to produce the wavefunction needed by orca_2mkl.
    execution_input = inp_path
    single_atom_fallback = False
    try:
        execution_input, single_atom_fallback = _single_atom_sp_input(inp_path)
    except (OSError, UnicodeError) as exc:
        return StepResult(
            step_name="struct_orca", step_index=1, success=False,
            error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                            message=f"{name}: 无法准备 ORCA 单原子特例输入",
                            raw_output=str(exc)),
            duration_s=time.time() - _start,
        )
    try:
        nproc = _configured_nproc(cfg, defaults)
        mem_mb = _configured_mem_mb(cfg, defaults)
        execution_input = _prepare_runtime_input(execution_input, nproc, mem_mb)
    except (OSError, UnicodeError) as exc:
        _cleanup_single_atom_execution(execution_input)
        return StepResult(
            step_name="struct_orca", step_index=1, success=False,
            error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                            message=f"{name}: 无法准备 ORCA 并行运行输入",
                            raw_output=str(exc)),
            duration_s=time.time() - _start,
        )

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
            [orca_bin, execution_input.name],
            cwd=std,
            timeout=7200,
            env=orca_env,
            run_dir=std,
        )
    except subprocess.TimeoutExpired:
        _cleanup_single_atom_execution(execution_input)
        return StepResult(
            step_name="struct_orca", step_index=1, success=False,
            error=StepError(kind=ErrorKind.TIMEOUT,
                            message=f"{name}: ORCA 计算超时 (7200s)",
                            hint="减少 nproc 或换用更小基组"),
            duration_s=time.time() - _start,
        )

    execution_gbw = std / f"{execution_input.stem}.gbw"
    gbw = std / f"{name}.gbw"
    if not execution_gbw.exists():
        raw = ((result.stderr or "") + "\n" + (result.stdout or ""))[-500:]
        print(f"[orca_struct] ❌ {name}: ORCA 失败\n{raw}")
        _cleanup_single_atom_execution(execution_input)
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
                [orca_2mkl, execution_input.stem, "-molden"],
                cwd=std,
                timeout=60,
                env=orca_env,
                run_dir=std,
            )
        except subprocess.TimeoutExpired:
            pass  # 继续检查文件是否存在

    # Keep the runtime basename available until orca_2mkl has consumed its
    # .gbw, then publish the canonical molecule-named artifact.
    if execution_gbw.exists() and execution_gbw != gbw:
        gbw.unlink(missing_ok=True)
        shutil.move(str(execution_gbw), str(gbw))
    molden = std / f"{execution_input.stem}.molden.input"
    if molden.exists():
        molden2 = std / f"{name}.molden"
        molden.rename(molden2)
        duration = time.time() - _start
        print(f"[orca_struct] ✅ {name}: {molden2.name} 已生成  ({duration:.1f}s)")
        extra = {"nproc": nproc}
        if single_atom_fallback:
            extra["single_atom_fallback"] = "sp"
        _cleanup_single_atom_execution(execution_input)
        return StepResult(
            step_name="struct_orca", step_index=1, success=True,
            outputs={"molden": str(molden2), "gbw": str(gbw)},
            artifacts=[str(molden2), str(gbw), str(inp_path)],
            duration_s=duration,
            extra=extra,
        )

    print(f"[orca_struct] ❌ {name}: orca_2mkl 失败")
    _cleanup_single_atom_execution(execution_input)
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
        inp_path = Path(struct_dir) / f"{name}.inp"
        if not inp_path.exists():
            results.append(StepResult(
                step_name="struct_orca", step_index=1, success=False,
                error=StepError(
                    kind=ErrorKind.FILE_NOT_FOUND,
                    message=f"{inp_path} 不存在",
                    hint=f"确认已从 struct/ 复制 {name}.inp 到本次运行目录",
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
