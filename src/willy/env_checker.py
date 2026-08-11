"""Compatibility facade for centralized external dependency checks.

``env_registry`` resolves external and bundled runtime tools and builds their
child environment. This module keeps the historic check_all/check_module/ensure
API used by the pipeline and adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os

from willy._paths import get_project_root
from willy.env_registry import (
    AVAILABLE,
    DEFAULT_BOSS_HOME,
    MISCONFIGURED,
    MISSING,
    NOT_EXECUTABLE,
    RUNTIME_UNAVAILABLE,
    ResolvedTool,
    resolve_tool,
)
from willy.step_registry import EXECUTION_MODULE_REGISTRY


ROOT = get_project_root()
SOBTOP_DIR = ROOT / "vendor" / "sobtop"
MULTIWFN_BIN = ROOT / "vendor" / "multiwfn" / "linux-x86_64" / "3.8-dev-2025-02-14" / "Multiwfn"
# Backward-compatible import name. It is never written into os.environ.
DEFAULT_BOSSDIR = DEFAULT_BOSS_HOME


@dataclass
class DepResult:
    """Compatibility result for one required dependency."""

    name: str
    kind: str
    path: str
    status: str = "ok"
    needed_by: list[str] = field(default_factory=list)
    hint: str = ""
    source: str = ""


@dataclass
class EnvReport:
    """Environment check report consumed by existing pipeline callers."""

    results: list[DepResult]

    def is_ok(self, module: str) -> bool:
        return all(result.status == "ok" for result in self.results if module in result.needed_by)

    def failed(self) -> list[DepResult]:
        return [result for result in self.results if result.status != "ok"]

    def failed_strs(self) -> list[str]:
        messages: list[str] = []
        for result in self.failed():
            if result.status == "no_exec":
                messages.append(f"❌ {result.name} — 无执行权限，请检查安装权限")
            elif result.status == "misconfigured":
                messages.append(f"❌ {result.name} — 配置无效: {result.hint}")
            elif result.status == "runtime_unavailable":
                messages.append(f"❌ {result.name} — 运行依赖不可用: {result.hint}")
            else:
                messages.append(f"❌ {result.name} — 未找到: {result.hint}")
        return messages

    def format(self) -> str:
        lines = [
            " Environment Check",
            " ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f" {'Dependency':<20s} {'Kind':<10s} {'Status':<22s} {'Needed by'}",
            f" {'─' * 20} {'─' * 10} {'─' * 22} {'─' * 30}",
        ]
        icons = {
            "ok": "✅", "missing": "❌", "no_exec": "🔒",
            "misconfigured": "⚙", "runtime_unavailable": "⚠",
        }
        for result in self.results:
            lines.append(
                f" {result.name:<20s} {result.kind:<10s} "
                f"{icons.get(result.status, '❌'):<4s} {result.status:<18s} {', '.join(result.needed_by)}"
            )
        failed = self.failed()
        lines.append(f" {'─' * 20} {'─' * 10} {'─' * 22} {'─' * 30}")
        lines.append(
            f" {'✅' if not failed else '⚠'} {len(self.results) - len(failed)}/{len(self.results)} dependencies ready"
        )
        for result in failed:
            lines.append(f"   • {result.name}: {result.hint}" if result.hint else f"   • {result.name}")
        lines.append(" ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)


@dataclass(frozen=True)
class _DependencyDefinition:
    """One preflight display item owned by an execution-module dependency ID."""

    name: str
    kind: str
    path: str
    hint: str
    tool_id: str = ""
    bundled_dependency_id: str = ""

    def needed_by(self) -> list[str]:
        if self.tool_id:
            return list(EXECUTION_MODULE_REGISTRY.modules_for_tool(self.tool_id))
        return list(
            EXECUTION_MODULE_REGISTRY.modules_for_bundled_dependency(
                self.bundled_dependency_id
            )
        )


_DEPENDENCY_DEFINITIONS: tuple[_DependencyDefinition, ...] = (
    _DependencyDefinition("g16", "binary", "g16", "设置 WILLY_G16_BIN 或将 g16 加入 PATH", tool_id="g16"),
    _DependencyDefinition("formchk", "binary", "formchk", "设置 WILLY_FORMCHK_BIN 或将 formchk 加入 PATH", tool_id="formchk"),
    _DependencyDefinition("g09", "binary", "g09", "设置 WILLY_G09_BIN 或将 g09 加入 PATH", tool_id="g09"),
    _DependencyDefinition(
        "g09_formchk", "binary", "formchk",
        "设置 WILLY_G09_FORMCHK_BIN 或将 G09 安装目录中的 formchk 加入 PATH",
        tool_id="g09_formchk",
    ),
    _DependencyDefinition("orca", "binary", "orca", "设置 WILLY_ORCA_HOME/WILLY_ORCA_BIN，或将 orca 加入 PATH", tool_id="orca"),
    _DependencyDefinition("orca_2mkl", "binary", "orca_2mkl", "设置 WILLY_ORCA_HOME/WILLY_ORCA_2MKL_BIN，或将 orca_2mkl 加入 PATH", tool_id="orca_2mkl"),
    _DependencyDefinition(
        "Multiwfn", "file_exec", str(MULTIWFN_BIN),
        "项目内置 Multiwfn 文件缺失",
        bundled_dependency_id="multiwfn",
    ),
    _DependencyDefinition("sobtop", "file_exec", str(SOBTOP_DIR / "sobtop"), "项目内置 Sobtop 文件缺失", bundled_dependency_id="sobtop"),
    _DependencyDefinition("atomtype", "file_exec", str(SOBTOP_DIR / "atomtype"), "项目内置 Sobtop 文件无执行权限", bundled_dependency_id="atomtype"),
    _DependencyDefinition("sobtop.ini", "file", str(SOBTOP_DIR / "sobtop.ini"), "项目内置 Sobtop 配置缺失", bundled_dependency_id="sobtop_ini"),
    _DependencyDefinition("LJ_param.dat", "file", str(SOBTOP_DIR / "LJ_param.dat"), "项目内置 Sobtop LJ 参数缺失", bundled_dependency_id="sobtop_lj_parameters"),
    _DependencyDefinition("bonded_param.dat", "file", str(SOBTOP_DIR / "bonded_param.dat"), "项目内置 Sobtop 键参数缺失", bundled_dependency_id="sobtop_bonded_parameters"),
    _DependencyDefinition("LigParGen", "binary", "LigParGen", "设置 WILLY_LIGPARGEN_BIN 或将 LigParGen 加入 PATH", tool_id="ligpargen"),
    _DependencyDefinition("BOSSdir", "envvar", "BOSSdir", "设置 WILLY_BOSS_HOME；兼容 BOSSdir，默认目录为 ~/boss/boss", tool_id="boss"),
    _DependencyDefinition("Open Babel", "binary", "obabel", "设置 WILLY_OBABEL_BIN 或将带格式插件的 obabel 加入 PATH", tool_id="obabel"),
    _DependencyDefinition("C shell", "binary", "csh", "设置 WILLY_CSH_BIN 或将 csh 加入 PATH（BOSS 脚本必需）", tool_id="csh"),
    _DependencyDefinition("gmx", "binary", "gmx", "设置 WILLY_GMX_BIN 或将 gmx 加入 PATH", tool_id="gmx"),
    _DependencyDefinition("packmol", "file_exec", str(ROOT / "vendor" / "packmol"), "项目内置 Packmol 文件缺失", bundled_dependency_id="packmol"),
    _DependencyDefinition("obabel", "file_exec", str(ROOT / "vendor" / "obabel.bin"), "项目内置 OpenBabel 文件缺失", bundled_dependency_id="obabel"),
    _DependencyDefinition("libopenbabel.so", "file", str(ROOT / "vendor" / "libopenbabel.so.7"), "项目内置 OpenBabel 库缺失", bundled_dependency_id="openbabel"),
    _DependencyDefinition("libcoordgen.so", "file", str(ROOT / "vendor" / "libcoordgen.so.3"), "项目内置 OpenBabel 库缺失", bundled_dependency_id="coordgen"),
)

_DEPENDENCY_BY_NAME = {definition.name: definition for definition in _DEPENDENCY_DEFINITIONS}
_DEPENDENCIES: list[DepResult] = [
    DepResult(
        definition.name,
        definition.kind,
        definition.path,
        needed_by=definition.needed_by(),
        hint=definition.hint,
    )
    for definition in _DEPENDENCY_DEFINITIONS
]


def _copy(dep: DepResult) -> DepResult:
    return DepResult(dep.name, dep.kind, dep.path, needed_by=list(dep.needed_by), hint=dep.hint)


def _external_status(result: ResolvedTool) -> str:
    if result.status == AVAILABLE:
        return "ok"
    if result.status == NOT_EXECUTABLE:
        return "no_exec"
    if result.status == MISCONFIGURED:
        return "misconfigured"
    if result.status == RUNTIME_UNAVAILABLE:
        return "runtime_unavailable"
    return "missing"


def _check_one(dep: DepResult) -> DepResult:
    result = _copy(dep)
    definition = _DEPENDENCY_BY_NAME[dep.name]
    if definition.tool_id:
        resolved = resolve_tool(definition.tool_id)
        result.status = _external_status(resolved)
        result.source = resolved.source
        if resolved.public_reason:
            result.hint = resolved.public_reason
        return result

    path = Path(dep.path)
    if not path.exists():
        result.status = "missing"
    elif dep.kind == "file_exec" and not os.access(path, os.X_OK):
        result.status = "no_exec"
    return result


def check_all() -> EnvReport:
    """Check all registered bundled and external dependencies."""
    return EnvReport([_check_one(dependency) for dependency in _DEPENDENCIES])


def check_module(name: str) -> EnvReport:
    """Check only dependencies used by one canonical pipeline module."""
    return EnvReport([_check_one(dep) for dep in _DEPENDENCIES if name in dep.needed_by])


def ensure(module: str) -> None:
    """Raise a user-safe error before the pipeline enters an unavailable module."""
    report = check_module(module)
    if not report.is_ok(module):
        raise RuntimeError(f"[{module}] 依赖不满足:\n" + "\n".join(report.failed_strs()))


if __name__ == "__main__":
    report = check_all()
    print(report.format())
    if report.failed():
        raise SystemExit(1)
