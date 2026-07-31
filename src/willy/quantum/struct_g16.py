"""
struct_maker.py
===============
Gaussian 结构优化执行器。

流程（串行）:
  1. 读取 config.json → 获取每分子的基组/mem/nproc/电荷/自旋
  2. 修改 .gjf 文件（更新 route card、添加 %mem / %nprocshared）
  3. 运行 g16 生成 .chk
  4. 运行 formchk 将 .chk → .fchk
  5. 输出到 struct/ 目录
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional
import subprocess
import shutil
import json
import re
import os

from willy._paths import get_project_root
from willy.errors import StepResult, StepError, ErrorKind


# ── 常量 ──
G16_BIN = "g16"
FORMCHK_BIN = "formchk"
DEFAULT_MEM = "5GB"
DEFAULT_NPROC = 8

ROOT = get_project_root()
CONFIG_PATH = ROOT / "config.json"
STRUCT_DIR = ROOT / "struct"


# ============================================================
# 环境预检
# ============================================================

def check_env_ready() -> list[str]:
    """预检 g16 / formchk 是否可用。委托到 env_checker。"""
    from willy.env_checker import check_module
    return check_module("struct_maker").failed_strs()


# ============================================================
# .gjf 解析与修改
# ============================================================

def _parse_gjf(gjf_path: Path) -> dict:
    """
    解析 .gjf 文件，返回各段。

    Returns:
        {"chk_line": str, "route": str, "title": str,
         "charge_spin": str, "coords": str, "tail": str}
    """
    text = gjf_path.read_text()
    lines = text.split("\n")

    chk_line = ""
    route = ""
    title = ""
    charge_spin = ""
    coords_start = 0
    coords_end = 0

    for i, line in enumerate(lines):
        s = line.strip()

        # %chk 行
        if s.lower().startswith("%chk="):
            chk_line = s
            continue

        # route 行（# 开头）
        if s.startswith("#") and route == "":
            route = s
            continue

        # 跳过 %mem, %nprocshared, %cpu 等（后面会统一添加）
        if s.lower().startswith(("%mem=", "%nproc", "%cpu=")):
            continue

        if route and not title:
            # route 之后到 charge/spin 行之前：标题 + 空行
            if s == "" and title == "":
                continue  # route 后的空行
            if title == "":
                title = s
                continue

        if title and charge_spin == "":
            if s:
                parts = s.split()
                if len(parts) == 2:
                    try:
                        int(parts[0])
                        int(parts[1])
                        charge_spin = s
                        coords_start = i + 1
                        continue
                    except ValueError:
                        pass

        # 坐标行检测：已找到 charge/spin 后的非空行
        if coords_start and i >= coords_start:
            if s == "" and coords_end == 0:
                coords_end = i
                break

    if coords_end == 0:
        coords_end = len(lines)

    coord_lines = lines[coords_start:coords_end]

    return {
        "chk_line": chk_line,
        "route": route,
        "title": title,
        "charge_spin": charge_spin,
        "coords": "\n".join(coord_lines),
    }


def _build_gjf(parsed: dict, basis: str, mem: str,
               nproc: Optional[int], name: str,
               scf_options: str = "",
               opt_options: str = "") -> str:
    """
    用解析结果 + 新参数重建 .gjf 内容。
    scf_options / opt_options 追加到 route card 末尾。
    """
    lines = []

    # %mem
    if mem:
        lines.append(f"%mem={mem}")
    # %nprocshared
    if nproc:
        lines.append(f"%nprocshared={nproc}")
    # %chk
    lines.append(f"%chk={name}.chk")

    # route card —— 替换基组 + 追加 SCF/OPT 关键字
    route = parsed["route"]
    # 替换 method/basis：原格式 "b3lyp/6-311+g(d,p)" → 新 basis
    route_new = re.sub(r'\b\w+/[\w\-\+\(\)\*,]+', basis, route)
    if scf_options:
        route_new = route_new.rstrip() + " " + scf_options
    if opt_options:
        route_new = route_new.rstrip() + " " + opt_options
    lines.append(route_new)
    lines.append("")

    # title
    lines.append(parsed["title"])
    lines.append("")

    # charge & spin
    lines.append(parsed["charge_spin"])

    # coords
    lines.append(parsed["coords"])
    lines.append("")
    lines.append("")

    return "\n".join(lines)


# ============================================================
# 核心逻辑
# ============================================================

def run_one(name: str, cfg: dict, defaults: dict,
            struct_dir: str = "struct",
            cfg_overrides: dict = None,
            scf_options: str = "",
            opt_options: str = "") -> StepResult:
    """
    对单个分子: 修改 .gjf → 跑 g16 → formchk → 输出 .fchk。
    若 .fchk 已存在则跳过。

    Args:
        name: 分子名。
        cfg: config.json molecules 段中该分子的配置。
        defaults: config.json defaults 段。
        struct_dir: 结构文件目录。
        cfg_overrides: Agent 重试时覆盖 cfg 字段 (basis, mem, nproc)。
        scf_options: SCF 关键字追加 (如 "scf=xqc")。
        opt_options: 几何优化关键字追加 (如 "opt=calcfc")。
    """
    import time
    t0 = time.time()
    std = Path(struct_dir)
    fchk_path = std / f"{name}.fchk"
    if fchk_path.exists():
        print(f"[struct_maker] ⏭ {name}: .fchk 已存在，跳过结构优化")
        return StepResult(
            step_name="struct_g16", step_index=1, success=True,
            outputs={"fchk": str(fchk_path)},
            artifacts=[str(fchk_path)],
            duration_s=0.0,
        )

    gjf_path = std / f"{name}.gjf"
    if not gjf_path.exists():
        return StepResult(
            step_name="struct_g16", step_index=1, success=False,
            error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                            message=f"{gjf_path} 不存在",
                            hint=f"请确保 struct/{name}.gjf 存在"),
            duration_s=time.time() - t0,
        )

    # ── 参数合并 ──
    if cfg_overrides:
        cfg = {**cfg, **{k: v for k, v in cfg_overrides.items() if v is not None}}
    basis = cfg.get("basis", "b3lyp/6-311+g(d,p)")
    mem = cfg.get("mem") or defaults.get("mem", DEFAULT_MEM)
    nproc = cfg.get("nproc") or defaults.get("nproc", DEFAULT_NPROC)

    print(f"[struct_maker] {name}: basis={basis}  mem={mem}  nproc={nproc}")
    if scf_options:
        print(f"[struct_maker] {name}: scf_options={scf_options}")
    if opt_options:
        print(f"[struct_maker] {name}: opt_options={opt_options}")

    # ── 修改 .gjf ──
    parsed = _parse_gjf(gjf_path)
    new_gjf = _build_gjf(parsed, basis, mem, nproc, name,
                         scf_options=scf_options,
                         opt_options=opt_options)

    # 写入临时文件（不覆盖原始）
    work_gjf = std / f"{name}_run.gjf"
    work_gjf.write_text(new_gjf)
    print(f"[struct_maker]   已生成 {work_gjf.name}")

    # ── 运行 g16 ──
    print(f"[struct_maker]   运行 Gaussian…")
    result = subprocess.run(
        f"GAUSS_CDEF=0 OMP_NUM_THREADS=1 {G16_BIN}",
        input=new_gjf,
        shell=True,
        capture_output=True,
        text=True,
        cwd=str(std),
        timeout=7200,
    )
    out_path = std / f"{name}.log"
    out_path.write_text(result.stdout)

    if "Normal termination" not in result.stdout:
        print(f"[struct_maker] ❌ {name}: Gaussian 未正常终止")
        work_gjf.unlink(missing_ok=True)
        return StepResult(
            step_name="struct_g16", step_index=1, success=False,
            error=StepError(kind=ErrorKind.GAUSSIAN_CRASH,
                            message="Gaussian 未 Normal termination",
                            raw_output=result.stdout[-500:]),
            duration_s=time.time() - t0,
        )

    # ── 检查 .chk ──
    chk_path = std / f"{name}.chk"
    if not chk_path.exists():
        print(f"[struct_maker] ❌ {name}: .chk 未生成")
        work_gjf.unlink(missing_ok=True)
        return StepResult(
            step_name="struct_g16", step_index=1, success=False,
            error=StepError(kind=ErrorKind.GAUSSIAN_CRASH,
                            message=".chk 未生成，Gaussian 可能中途崩溃"),
            duration_s=time.time() - t0,
        )

    print(f"[struct_maker]   ✅ {name}.chk 已生成")

    # ── formchk ──（用绝对路径，避免 cwd 嵌套）
    result = subprocess.run(
        f"GAUSS_CDEF=0 OMP_NUM_THREADS=1 {FORMCHK_BIN} "
        f"{chk_path.resolve()} {fchk_path.resolve()}",
        shell=True,
        capture_output=True,
        text=True,
        cwd=str(std),
        timeout=60,
    )
    if not fchk_path.exists():
        print(f"[struct_maker] ❌ {name}: formchk 失败, {result.stderr[-200:]}")
        work_gjf.unlink(missing_ok=True)
        return StepResult(
            step_name="struct_g16", step_index=1, success=False,
            error=StepError(kind=ErrorKind.FORMCHK_FAILED,
                            message="formchk 未生成 .fchk",
                            raw_output=result.stderr[-500:]),
            duration_s=time.time() - t0,
        )

    print(f"[struct_maker]   ✅ {name}.fchk 已生成")

    # 清理临时 gjf（保留原始的）
    work_gjf.unlink(missing_ok=True)

    return StepResult(
        step_name="struct_g16", step_index=1, success=True,
        outputs={"fchk": str(fchk_path)},
        artifacts=[str(fchk_path)],
        duration_s=time.time() - t0,
    )


def run_all(config_path: str = "config.json",
            struct_dir: str = "struct",
            on_progress=None) -> list[StepResult]:
    """
    串行运行 config.json 中所有分子。
    on_progress(name, i, total) — 每分子开始前回调。
    """
    issues = check_env_ready()
    if issues:
        raise RuntimeError("\n".join(issues))

    with open(config_path) as f:
        config = json.load(f)

    molecules = config.get("molecules", {})
    defaults = config.get("defaults", {})

    if not molecules:
        print("[struct_maker] ⚠ config.json 中 molecules 为空")
        return []

    results = []
    total = len(molecules)
    for i, (name, cfg) in enumerate(molecules.items(), 1):
        print(f"\n[{i}/{total}] {name}")
        if on_progress:
            on_progress(f"分子 {i}/{total}: {name}")
        result = run_one(name, cfg, defaults, struct_dir)
        results.append(result)

    ok = sum(1 for r in results if r.success)
    print(f"\n[struct_maker] 完成: {ok}/{total} 个分子")
    return results


# ============================================================
# 测试入口
# ============================================================

if __name__ == "__main__":
    # 只验证 .gjf 修改（不真正跑 Gaussian，太耗时）
    print("===== 验证 .gjf 修改（预览，不运行） =====\n")

    with open("config.json") as f:
        config = json.load(f)

    defaults = config.get("defaults", {})
    for name, cfg in config.get("molecules", {}).items():
        gjf_path = Path("struct") / f"{name}.gjf"
        if not gjf_path.exists():
            print(f"  {name}: gjf 不存在")
            continue
        parsed = _parse_gjf(gjf_path)
        new_gjf = _build_gjf(
            parsed,
            basis=cfg.get("basis", "b3lyp/6-311+g(d,p)"),
            mem=cfg.get("mem") or defaults.get("mem", "5GB"),
            nproc=cfg.get("nproc") or defaults.get("nproc", 8),
            name=name,
        )
        # 只打印前 6 行
        print(f"── {name} ──")
        for line in new_gjf.split("\n")[:6]:
            print(f"  {line}")
        print()
