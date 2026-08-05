"""
topo_opls.py
======================
LigParGen OPLS-AA 调用接口 —— SMILES/mol2 → .itp + .gro。

不生成主拓扑文件（.top），后续由 top_assembly 单独构建。
与 topo_gaff 保持相同的出参契约和 StepResult 协议。

依赖:
  - LigParGen (pip install ligpargen)
  - BOSS (http://zarbi.chem.yale.edu/software.html, 需设置 $BOSSdir)
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
import subprocess
import shutil
import re
import uuid

from willy._paths import get_project_root
from willy.env_registry import EnvironmentRegistryError, build_tool_env, require_tool
from willy.errors import StepResult, StepError, ErrorKind
from willy.process_lifecycle import run_managed_command
from willy.topology.validation import (
    run_output_path,
    validate_topology_files,
    validate_topology_output_name,
)

ROOT = get_project_root()
LIGPARGEN_TMP_DIR = Path("/tmp")


# ============================================================
# 环境预检
# ============================================================

def check_ligpargen_ready() -> list[str]:
    """
    预检 LigParGen + BOSS 环境是否就绪。

    调用 run_ligpargen 前应先调用此函数，在迁移到新机器时给出清晰错误提示。
    """
    from willy.env_checker import check_module
    issues = check_module("topo_opls").failed_strs()
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
    temp_prefix: Optional[str] = None  # 可选运行级临时前缀；默认每次执行唯一


# ============================================================
# 核心逻辑
# ============================================================

def _mol2_to_smiles(mol2_path: str, *, run_dir: str | Path | None = None) -> str:
    """通过 vendored obabel 从 mol2 提取 SMILES。"""
    obabel = str(ROOT / "vendor" / "obabel.bin")
    result = run_managed_command(
        [obabel, mol2_path, "-osmi"],
        timeout=30,
        run_dir=run_dir,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"无法从 {mol2_path} 提取 SMILES: {result.stderr}")

    smiles = result.stdout.strip().split()[0]  # obabel 输出格式: "SMILES\tNAME"
    print(f"[ligpargen] 🔄 mol2 → SMILES: {smiles}")
    return smiles


def _temporary_prefix(inp: LigParGenInput) -> str:
    """Return a unique LigParGen output prefix without changing final names."""
    base = re.sub(r"[^A-Za-z0-9_]", "_", inp.output_name).strip("_") or "MOL"
    token = inp.temp_prefix or uuid.uuid4().hex
    token = re.sub(r"[^A-Za-z0-9_]", "", token)[:16] or uuid.uuid4().hex[:16]
    return f"{base[:24]}_{token}"


def _find_tmp_output(prefix: str) -> dict[str, Path]:
    """Find the current invocation's LigParGen files under its unique prefix."""
    found: dict[str, Path] = {}
    for ext in ["itp", "gro"]:
        path = LIGPARGEN_TMP_DIR / f"{prefix}.{ext}"
        if path.exists():
            found[ext] = path
    return found


def _cleanup_tmp(prefix: str) -> None:
    """Clean only files owned by the current unique LigParGen invocation."""
    for path in LIGPARGEN_TMP_DIR.glob(f"{prefix}*"):
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
        except OSError:
            pass


def _restore_moleculetype_name(itp_path: Path, residue_name: str) -> bool:
    """Restore the configured molecule name after using a temporary prefix.

    LigParGen's ``-r`` controls both its fixed temporary filenames and the
    ITP molecule type.  Only the latter belongs in the run artifacts.
    """
    lines = itp_path.read_text().splitlines(keepends=True)
    in_moleculetype = False
    for index, line in enumerate(lines):
        body, marker, comment = line.partition(";")
        stripped = body.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_moleculetype:
                return False
            in_moleculetype = stripped[1:-1].strip().lower() == "moleculetype"
            continue
        if not in_moleculetype or not stripped:
            continue
        if stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) < 2 or not fields[1].lstrip("+-").isdigit():
            return False
        leading = body[:len(body) - len(body.lstrip())]
        newline = "\n" if line.endswith("\n") else ""
        suffix = f";{comment.rstrip()}" if marker else ""
        lines[index] = f"{leading}{residue_name} {' '.join(fields[1:])}{suffix}{newline}"
        itp_path.write_text("".join(lines))
        return True
    return False


def make_itp_gro_opls(
    inp: LigParGenInput,
    output_dir: str | None = None,
) -> StepResult:
    """
    调用 LigParGen，生成 OPLS-AA .itp + .gro。

    Returns:
        StepResult (success=True 时 outputs={"itp": path, "gro": path})
    """
    import time as _time
    _start = _time.time()

    if not output_dir:
        return StepResult(
            step_name="topo_opls", step_index=4, success=False,
            error=StepError(ErrorKind.INPUT_CONTRACT, "必须提供当前 run 的 output_dir"),
            duration_s=_time.time() - _start,
        )

    # ── 参数校验 ──
    if not inp.smiles and not inp.mol2:
        return StepResult(
            step_name="topo_opls", step_index=4, success=False,
            error=StepError(kind=ErrorKind.CONFIG_INVALID,
                            message="必须提供 smiles 或 mol2",
                            hint="至少填入 LigParGenInput.smiles 或 LigParGenInput.mol2"),
            duration_s=_time.time() - _start,
        )

    if inp.net_charge not in (0, -1, 1, -2, 2):
        return StepResult(
            step_name="topo_opls", step_index=4, success=False,
            error=StepError(kind=ErrorKind.CONFIG_INVALID,
                            message=f"不支持的净电荷: {inp.net_charge}（仅支持 0, ±1, ±2）",
                            hint="将 net_charge 设为 0, ±1 或 ±2"),
            duration_s=_time.time() - _start,
        )

    try:
        resname = validate_topology_output_name(inp.output_name)
    except ValueError as exc:
        return StepResult(
            step_name="topo_opls", step_index=4, success=False,
            error=StepError(ErrorKind.CONFIG_INVALID, str(exc)),
            duration_s=_time.time() - _start,
        )

    # ── 预检 ──
    issues = check_ligpargen_ready()
    if issues:
        return StepResult(
            step_name="topo_opls", step_index=4, success=False,
            error=StepError(kind=ErrorKind.DEPENDENCY_MISSING,
                            message="LigParGen 环境未就绪",
                            raw_output="\n".join(issues),
                            hint="配置 WILLY_LIGPARGEN_BIN 与 WILLY_BOSS_HOME，或使用兼容的 BOSSdir"),
            duration_s=_time.time() - _start,
        )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    try:
        destinations = {
            ext: run_output_path(out, resname, f".{ext}")
            for ext in ("itp", "gro")
        }
    except ValueError as exc:
        return StepResult(
            step_name="topo_opls", step_index=4, success=False,
            error=StepError(ErrorKind.CONFIG_INVALID, str(exc)),
            duration_s=_time.time() - _start,
        )

    # ── 确定 SMILES ──
    smiles = inp.smiles
    if not smiles and inp.mol2:
        try:
            smiles = _mol2_to_smiles(inp.mol2, run_dir=out)
        except (RuntimeError, OSError) as e:
            return StepResult(
                step_name="topo_opls", step_index=4, success=False,
                error=StepError(kind=ErrorKind.DEPENDENCY_MISSING if isinstance(e, OSError) else ErrorKind.LIGPARGEN_FAILED,
                                message=f"{inp.output_name}: {e}",
                                hint="检查 .mol2 文件是否完整，或直接提供 SMILES"),
                duration_s=_time.time() - _start,
            )

    temporary_prefix = _temporary_prefix(inp)

    # LigParGen writes globally in /tmp.  A unique prefix is the ownership
    # boundary: it avoids both deleting and collecting another run's output.
    _cleanup_tmp(temporary_prefix)

    try:
        ligpargen = require_tool("ligpargen")
        ligpargen_env = build_tool_env("ligpargen")
    except EnvironmentRegistryError as exc:
        return StepResult(
            step_name="topo_opls", step_index=4, success=False,
            error=StepError(ErrorKind.DEPENDENCY_MISSING, f"{resname}: {exc}"),
            duration_s=_time.time() - _start,
        )

    # ── 构建命令 ──
    cmd = [
        str(ligpargen.executable),
        "-s", smiles,
        "-r", temporary_prefix,
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
        result = run_managed_command(
            cmd,
            timeout=300,  # BOSS 可能较慢
            env=ligpargen_env,
            run_dir=out,
        )
    except subprocess.TimeoutExpired:
        _cleanup_tmp(temporary_prefix)
        return StepResult(
            step_name="topo_opls", step_index=4, success=False,
            error=StepError(kind=ErrorKind.TIMEOUT,
                            message=f"{resname}: LigParGen/BOSS 超时 (300s)",
                            hint="检查分子复杂度是否过高（>200 原子），或 BOSS 是否正常运行"),
            duration_s=_time.time() - _start,
        )
    except OSError as exc:
        _cleanup_tmp(temporary_prefix)
        return StepResult(
            step_name="topo_opls", step_index=4, success=False,
            error=StepError(kind=ErrorKind.DEPENDENCY_MISSING,
                            message=f"{resname}: 无法启动 LigParGen/BOSS: {exc}",
                            hint="检查 WILLY_LIGPARGEN_BIN 与 WILLY_BOSS_HOME 配置"),
            duration_s=_time.time() - _start,
        )

    # ── 失败处理 ──
    if result.returncode != 0:
        raw = (result.stdout[-500:] if result.stdout else "") + "\n" + \
              (result.stderr[-500:] if result.stderr else "")
        print(f"[ligpargen] stderr:\n{result.stderr[-500:]}")
        print(f"[ligpargen] stdout:\n{result.stdout[-500:]}")
        if inp.cleanup_tmp:
            _cleanup_tmp(temporary_prefix)
        return StepResult(
            step_name="topo_opls", step_index=4, success=False,
            error=StepError(kind=ErrorKind.LIGPARGEN_FAILED,
                            message=f"{resname}: LigParGen 退出码={result.returncode}",
                            raw_output=raw.strip()[-500:],
                            hint="检查 WILLY_BOSS_HOME/BOSSdir 与 BOSS 安装，或尝试用 SMILES 重新输入"),
            duration_s=_time.time() - _start,
        )

    tmp_files = _find_tmp_output(temporary_prefix)
    if len(tmp_files) < 2:
        missing = [k for k in ["itp", "gro"] if k not in tmp_files]
        raw = (result.stdout[-500:] if result.stdout else "") + "\n" + \
              (result.stderr[-500:] if result.stderr else "")
        if inp.cleanup_tmp:
            _cleanup_tmp(temporary_prefix)
        return StepResult(
            step_name="topo_opls", step_index=4, success=False,
            error=StepError(kind=ErrorKind.LIGPARGEN_FAILED,
                            message=f"{resname}: LigParGen 未生成完整输出（缺失 {missing}）",
                            raw_output=raw.strip()[-500:],
                            hint="检查 SMILES 是否正确或分子是否超过 200 原子限制"),
            duration_s=_time.time() - _start,
        )

    validation = validate_topology_files(
        tmp_files["itp"], tmp_files["gro"],
        step_name="topo_opls", error_kind=ErrorKind.LIGPARGEN_FAILED,
    )
    if not validation.success:
        if inp.cleanup_tmp:
            _cleanup_tmp(temporary_prefix)
        validation.duration_s = _time.time() - _start
        return validation

    outputs: dict[str, Path] = {}
    artifacts: list[str] = []
    for ext in ["itp", "gro"]:
        dst = destinations[ext]
        dst.unlink(missing_ok=True)
        shutil.copy2(tmp_files[ext], dst)
        if ext == "itp" and not _restore_moleculetype_name(dst, resname):
            for destination in destinations.values():
                destination.unlink(missing_ok=True)
            if inp.cleanup_tmp:
                _cleanup_tmp(temporary_prefix)
            return StepResult(
                step_name="topo_opls", step_index=4, success=False,
                error=StepError(
                    ErrorKind.LIGPARGEN_FAILED,
                    f"{resname}: LigParGen ITP 缺少合法 [ moleculetype ] 数据行",
                ),
                duration_s=_time.time() - _start,
            )
        outputs[ext] = dst
        artifacts.append(str(dst))
        print(f"[ligpargen] ✅ {ext}: {dst}")
    final_validation = validate_topology_files(
        outputs["itp"], outputs["gro"],
        step_name="topo_opls", error_kind=ErrorKind.LIGPARGEN_FAILED,
        expected_moleculetype=resname,
    )
    if not final_validation.success:
        for destination in destinations.values():
            destination.unlink(missing_ok=True)
        if inp.cleanup_tmp:
            _cleanup_tmp(temporary_prefix)
        final_validation.duration_s = _time.time() - _start
        return final_validation
    if inp.cleanup_tmp:
        _cleanup_tmp(temporary_prefix)

    duration = _time.time() - _start
    return StepResult(
        step_name="topo_opls", step_index=4, success=True,
        outputs={k: str(v) for k, v in outputs.items()},
        artifacts=artifacts,
        duration_s=duration,
        extra={
            "returncode": result.returncode,
            "lbcc": inp.lbcc,
            "opt_steps": inp.opt_steps,
            "temporary_prefix": temporary_prefix,
            **final_validation.extra,
        },
    )


# ============================================================
# 快捷函数
# ============================================================

def batch_make_topo_opls(inputs: Iterable[LigParGenInput], output_dir: str) -> list[StepResult]:
    """Parameterize explicit plan inputs; this function never scans directories."""
    return [make_itp_gro_opls(inp, output_dir=output_dir) for inp in inputs]


# ============================================================
# 测试入口
# ============================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) != 4:
        print("用法: python3 topo_opls.py <SMILES> <resname> <run_dir>")
        print("示例: python3 topo_opls.py 'c1ccccc1' BNZ md_run/<run_id>")
        sys.exit(1)

    test_smiles = sys.argv[1]
    test_name = sys.argv[2]
    run_dir = Path(sys.argv[3]).resolve()

    print(f"===== 测试: SMILES={test_smiles} → {test_name} =====")
    r = make_itp_gro_opls(
        LigParGenInput(smiles=test_smiles, output_name=test_name),
        output_dir=str(run_dir),
    )
    if r.success:
        print(f"生成: {list(r.outputs.keys())}")
        for k, v in r.outputs.items():
            print(f"  {k}: {v} ({Path(v).stat().st_size} bytes)")
    else:
        print(f"❌ 测试失败: {r.error.message}")
        sys.exit(1)
