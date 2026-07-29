"""
env_checker.py
==============
统一环境检查器 —— 所有外部依赖的唯一定义源。

用法:
  CLI:  python3 -m willy.env_checker
  API:  from willy.env_checker import check_all, check_module, ensure
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import shutil
import os

from willy._paths import get_project_root

ROOT = get_project_root()


# ============================================================
# 数据模型
# ============================================================

@dataclass
class DepResult:
    """单个依赖的检查结果。"""
    name: str                                    # "g16", "sobtop", ...
    kind: str                                    # "binary" | "file" | "file_exec"
    path: str                                    # 实际检查的路径/命令名
    status: str = "ok"                            # "ok" | "missing" | "no_exec"
    needed_by: list[str] = field(default_factory=list)
    hint: str = ""


@dataclass
class EnvReport:
    """环境检查报告。"""
    results: list[DepResult]

    def is_ok(self, module: str) -> bool:
        """指定模块所需的所有依赖是否都就绪。"""
        for r in self.results:
            if module in r.needed_by and r.status != "ok":
                return False
        return True

    def failed(self) -> list[DepResult]:
        """返回所有未就绪的依赖。"""
        return [r for r in self.results if r.status != "ok"]

    def failed_strs(self) -> list[str]:
        """返回格式化的错误字符串列表（兼容旧 check_*_ready() 接口）。"""
        out = []
        for r in self.failed():
            if r.status == "missing":
                out.append(f"❌ {r.path} — {r.name} 不存在")
            elif r.status == "no_exec":
                out.append(f"❌ {r.path} — 无执行权限，请运行: chmod +x {r.path}")
        return out

    def format(self) -> str:
        """格式化为终端友好的表格。"""
        lines = []
        lines.append(" Environment Check")
        lines.append(" ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f" {'Dependency':<20s} {'Kind':<10s} {'Status':<10s} {'Needed by'}")
        lines.append(f" {'─'*20} {'─'*10} {'─'*10} {'─'*30}")

        status_icon = {"ok": "✅", "missing": "❌", "no_exec": "🔒"}

        for r in self.results:
            modules = ", ".join(r.needed_by)
            lines.append(
                f" {r.name:<20s} {r.kind:<10s} "
                f"{status_icon[r.status]:<4s} {r.status:<6s} {modules}"
            )

        lines.append(f" {'─'*20} {'─'*10} {'─'*10} {'─'*30}")

        ok_count = sum(1 for r in self.results if r.status == "ok")
        bad_count = len(self.results) - ok_count
        if bad_count == 0:
            lines.append(f" ✅ All {ok_count} dependencies ready")
        else:
            lines.append(f" ⚠  {ok_count}/{len(self.results)} ready, {bad_count} issues")
            lines.append("")
            for r in self.failed():
                lines.append(f"   • {r.name}: {r.hint}" if r.hint else f"   • {r.name}")

        lines.append(" ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)


# ============================================================
# 依赖注册表
# ============================================================

SOBTOP_DIR = get_project_root() / "vendor" / "sobtop"

_DEPENDENCIES: list[DepResult] = [
    # --- struct_maker (g16 or ORCA) ---
    DepResult(name="g16",       kind="binary",    path="g16",
              needed_by=["struct_maker", "chg_maker"],
              hint="Gaussian 16: 确保 g16 在 PATH 中"),
    DepResult(name="orca",      kind="binary",    path="orca",
              needed_by=["struct_maker"],
              hint="ORCA 6.x: https://orcaforum.kofo.mpg.de/"),
    DepResult(name="orca_2mkl", kind="binary",    path="orca_2mkl",
              needed_by=["struct_maker"],
              hint="ORCA 自带"),
    # ---
    DepResult(name="formchk",   kind="binary",    path="formchk",
              needed_by=["struct_maker", "chg_maker"],
              hint="formchk 随 Gaussian 安装，确保在 PATH 中"),

    # --- chg_maker ---
    DepResult(name="Multiwfn",  kind="binary",    path="Multiwfn",
              needed_by=["chg_maker"],
              hint="http://sobereva.com/multiwfn/ 下载并加入 PATH"),
    DepResult(name="RESP_noopt.sh", kind="file_exec",
              path=str(ROOT / "RESP_noopt.sh"),
              needed_by=["chg_maker"],
              hint="项目根目录自带，确保有执行权限 (chmod +x)"),

    # --- sobtop_interface ---
    DepResult(name="sobtop",    kind="file_exec",
              path=str(SOBTOP_DIR / "sobtop"),
              needed_by=["sobtop_interface"],
              hint="https://sobereva.com/soft/sobtop/ 下载"),
    DepResult(name="atomtype",  kind="file_exec",
              path=str(SOBTOP_DIR / "atomtype"),
              needed_by=["sobtop_interface"],
              hint=f"chmod +x {SOBTOP_DIR}/atomtype"),
    DepResult(name="sobtop.ini", kind="file",
              path=str(SOBTOP_DIR / "sobtop.ini"),
              needed_by=["sobtop_interface"],
              hint="Sobtop 配置文件，随 Sobtop 分发"),
    DepResult(name="LJ_param.dat", kind="file",
              path=str(SOBTOP_DIR / "LJ_param.dat"),
              needed_by=["sobtop_interface"],
              hint="GAFF LJ 参数文件，随 Sobtop 分发"),
    DepResult(name="bonded_param.dat", kind="file",
              path=str(SOBTOP_DIR / "bonded_param.dat"),
              needed_by=["sobtop_interface"],
              hint="GAFF 键合参数文件，随 Sobtop 分发"),

    # --- ligpargen_interface ---
    DepResult(name="LigParGen", kind="binary", path="LigParGen",
              needed_by=["ligpargen_interface"],
              hint="pip install ligpargen"),
    DepResult(name="BOSSdir", kind="envvar", path="BOSSdir",
              needed_by=["ligpargen_interface"],
              hint="从 http://zarbi.chem.yale.edu/software.html 下载 BOSS，"
                    "解压后设置 export BOSSdir=/path/to/boss"),

    # --- inp_generator ---
    DepResult(name="gmx",       kind="binary",    path="gmx",
              needed_by=["inp_generator"],
              hint="GROMACS: apt install gromacs 或 conda install -c bioconda gromacs"),
    DepResult(name="packmol",   kind="file_exec",
              path=str(ROOT / "vendor" / "packmol"),
              needed_by=["inp_generator"],
              hint="https://github.com/mcubeg/packmol 下载编译"),
    DepResult(name="obabel",   kind="file_exec",
              path=str(ROOT / "vendor" / "obabel.bin"),
              needed_by=["sobtop_interface"],
              hint="OpenBabel CLI, vendored"),
    DepResult(name="libopenbabel.so", kind="file",
              path=str(ROOT / "vendor" / "libopenbabel.so.7"),
              needed_by=["sobtop_interface"],
              hint="OpenBabel 共享库, vendored"),
    DepResult(name="libcoordgen.so", kind="file",
              path=str(ROOT / "vendor" / "libcoordgen.so.3"),
              needed_by=["sobtop_interface"],
              hint="OpenBabel 依赖库, vendored"),
]


# ============================================================
# 公共 API
# ============================================================

def _check_one(dep: DepResult) -> DepResult:
    """检查单个依赖，返回带 status 的副本。"""
    result = DepResult(
        name=dep.name, kind=dep.kind, path=dep.path,
        needed_by=list(dep.needed_by), hint=dep.hint, status="ok",
    )
    p = Path(dep.path)

    if dep.kind == "binary":
        if shutil.which(dep.path) is None:
            result.status = "missing"
    elif dep.kind == "envvar":
        if not os.environ.get(dep.path, ""):
            result.status = "missing"
        else:
            env_val = os.environ[dep.path]
            if not Path(env_val).exists():
                result.status = "missing"
                result.hint = f"${dep.path}={env_val} 目录不存在"
    elif dep.kind in ("file", "file_exec"):
        if not p.exists():
            result.status = "missing"
        elif dep.kind == "file_exec" and not os.access(p, os.X_OK):
            result.status = "no_exec"

    return result


def check_all() -> EnvReport:
    """检查所有已注册的依赖。"""
    return EnvReport([_check_one(d) for d in _DEPENDENCIES])


def check_module(name: str) -> EnvReport:
    """检查指定模块所需的依赖。"""
    deps = [d for d in _DEPENDENCIES if name in d.needed_by]
    return EnvReport([_check_one(d) for d in deps])


def ensure(module: str) -> None:
    """
    确保某模块的依赖就绪，否则 raise RuntimeError。

    在流水线步骤执行前调用，比跑到 Fortran 崩溃更友好。
    """
    report = check_module(module)
    if not report.is_ok(module):
        msg = f"[{module}] 依赖不满足:\n"
        msg += "\n".join(report.failed_strs())
        raise RuntimeError(msg)


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    report = check_all()
    print(report.format())
    if report.failed():
        exit(1)
