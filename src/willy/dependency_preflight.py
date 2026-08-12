"""Advisory dependency readiness report for the configuration page.

This module is deliberately separate from execution-time dependency guards.
It helps a user discover a usable local MD route before creating a task, but a
negative report never changes pipeline state or blocks a local submission.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
from typing import Iterable, TypedDict

from willy._paths import get_project_root
from willy.env_registry import AVAILABLE, ResolvedTool, resolve_tool


@dataclass(frozen=True)
class _Requirement:
    """One redacted preflight requirement."""

    requirement_id: str
    label: str
    tool_id: str | None = None
    bundled_path: tuple[str, ...] = ()
    executable: bool = False


@dataclass(frozen=True)
class _Alternative:
    """One complete toolchain that can satisfy a pipeline layer."""

    alternative_id: str
    label: str
    requirements: tuple[_Requirement, ...]


@dataclass(frozen=True)
class _Group:
    """A pipeline layer with one or more supported alternatives."""

    group_id: str
    label: str
    alternatives: tuple[_Alternative, ...]
    recommendation: str


class DependencyRequirementReport(TypedDict):
    requirement_id: str
    label: str
    status: str
    source: str
    reason: str


class DependencyAlternativeReport(TypedDict):
    alternative_id: str
    label: str
    ready: bool
    items: list[DependencyRequirementReport]


class DependencyGroupReport(TypedDict):
    group_id: str
    label: str
    ready: bool
    alternatives: list[DependencyAlternativeReport]


class DependencyPreflightReport(TypedDict):
    schema_version: int
    advisory: bool
    ready: bool
    groups: list[DependencyGroupReport]
    recommendations: list[str]
    written_defaults: list[str]
    persistence_warning: str
    markdown: str


_MULTIWFN = _Requirement("multiwfn", "内置 Multiwfn", tool_id="multiwfn")
_PACKMOL = _Requirement("packmol", "内置 Packmol", tool_id="packmol")
_SOBTOP_COMPONENTS = (
    _Requirement("sobtop", "内置 Sobtop", bundled_path=("vendor", "sobtop", "sobtop"), executable=True),
    _Requirement("atomtype", "内置 atomtype", bundled_path=("vendor", "sobtop", "atomtype"), executable=True),
    _Requirement("sobtop_ini", "Sobtop 配置", bundled_path=("vendor", "sobtop", "sobtop.ini")),
    _Requirement("sobtop_lj", "Sobtop LJ 参数", bundled_path=("vendor", "sobtop", "LJ_param.dat")),
    _Requirement("sobtop_bonded", "Sobtop 键参数", bundled_path=("vendor", "sobtop", "bonded_param.dat")),
    _Requirement("bundled_obabel_wrapper", "内置 Open Babel 启动器", bundled_path=("vendor", "obabel"), executable=True),
    _Requirement("bundled_obabel", "内置 Open Babel", bundled_path=("vendor", "obabel.bin"), executable=True),
    _Requirement("bundled_openbabel", "内置 Open Babel 运行库", bundled_path=("vendor", "libopenbabel.so.7")),
    _Requirement("bundled_coordgen", "内置 CoordGen 运行库", bundled_path=("vendor", "libcoordgen.so.3")),
)

_GROUPS = (
    _Group(
        "quantum",
        "量子化学结构与电荷",
        (
            _Alternative(
                "g16", "G16 + formchk + 内置 Multiwfn",
                (_Requirement("g16", "G16", tool_id="g16"), _Requirement("formchk", "formchk", tool_id="formchk"), _MULTIWFN),
            ),
            _Alternative(
                "g09", "G09 + formchk + 内置 Multiwfn",
                (_Requirement("g09", "G09", tool_id="g09"), _Requirement("g09_formchk", "G09 formchk", tool_id="g09_formchk"), _MULTIWFN),
            ),
            _Alternative(
                "orca", "ORCA + orca_2mkl + 内置 Multiwfn",
                (_Requirement("orca", "ORCA", tool_id="orca"), _Requirement("orca_2mkl", "orca_2mkl", tool_id="orca_2mkl"), _MULTIWFN),
            ),
        ),
        "安装并配置下列任一量子链路：G16 + formchk、G09 + formchk，或 ORCA + orca_2mkl。",
    ),
    _Group(
        "topology",
        "拓扑参数化",
        (
            _Alternative("sobtop", "内置 Sobtop（GAFF/UFF）", _SOBTOP_COMPONENTS),
            _Alternative(
                "ligpargen", "LigParGen + BOSS + Open Babel + C shell（OPLS-AA）",
                (
                    _Requirement("ligpargen", "LigParGen", tool_id="ligpargen"),
                    _Requirement("boss", "BOSS", tool_id="boss"),
                    _Requirement("obabel", "Open Babel", tool_id="obabel"),
                    _Requirement("csh", "C shell", tool_id="csh"),
                ),
            ),
        ),
        "内置 Sobtop 组件应完整；如需 OPLS-AA，请安装并配置 LigParGen、BOSS、Open Babel 和 C shell。",
    ),
    _Group(
        "simulation",
        "GROMACS 模拟",
        (
            _Alternative(
                "gromacs", "GROMACS + 内置 Packmol",
                (_Requirement("gmx", "GROMACS", tool_id="gmx"), _PACKMOL),
            ),
        ),
        "安装 GROMACS，或设置 WILLY_GMX_BIN 后重新检查。",
    ),
)

# Defaults are persisted only when discovery found a usable external program;
# bundled runtimes must remain relocatable inside the project and never enter
# ``.env``.  ORCA is normalized to its installation directory so its companion
# executable and shared libraries resolve as one installation.
_PERSISTED_ENV: dict[str, tuple[str, str]] = {
    "g16": ("WILLY_G16_BIN", "executable"),
    "formchk": ("WILLY_FORMCHK_BIN", "executable"),
    "g09": ("WILLY_G09_BIN", "executable"),
    "g09_formchk": ("WILLY_G09_FORMCHK_BIN", "executable"),
    "orca": ("WILLY_ORCA_HOME", "home"),
    "orca_2mkl": ("WILLY_ORCA_2MKL_BIN", "executable"),
    "gmx": ("WILLY_GMX_BIN", "executable"),
    "ligpargen": ("WILLY_LIGPARGEN_BIN", "executable"),
    "obabel": ("WILLY_OBABEL_BIN", "executable"),
    "csh": ("WILLY_CSH_BIN", "executable"),
    "boss": ("WILLY_BOSS_HOME", "home"),
}
_ORCA_ENV_KEYS = frozenset({"WILLY_ORCA_HOME", "WILLY_ORCA_BIN", "WILLY_ORCA_2MKL_BIN"})


def _bundled_requirement(requirement: _Requirement, root: Path) -> dict[str, str]:
    path = root.joinpath(*requirement.bundled_path)
    if not path.exists():
        return {
            "requirement_id": requirement.requirement_id,
            "label": requirement.label,
            "status": "missing",
            "source": "bundled",
            "reason": "项目内置组件缺失",
        }
    if requirement.executable and not os.access(path, os.X_OK):
        return {
            "requirement_id": requirement.requirement_id,
            "label": requirement.label,
            "status": "not_executable",
            "source": "bundled",
            "reason": "项目内置组件没有执行权限",
        }
    return {
        "requirement_id": requirement.requirement_id,
        "label": requirement.label,
        "status": AVAILABLE,
        "source": "bundled",
        "reason": "",
    }


def _resolved_requirement(requirement: _Requirement, root: Path) -> tuple[dict[str, str], ResolvedTool]:
    assert requirement.tool_id is not None
    result = resolve_tool(requirement.tool_id, project_root=root)
    return (
        {
            "requirement_id": requirement.requirement_id,
            "label": requirement.label,
            "status": result.status,
            "source": result.source,
            "reason": result.public_reason,
        },
        result,
    )


def _check_requirement(
    requirement: _Requirement,
    root: Path,
    resolved: dict[str, ResolvedTool],
) -> dict[str, str]:
    if requirement.tool_id is None:
        return _bundled_requirement(requirement, root)
    if requirement.tool_id not in resolved:
        item, tool = _resolved_requirement(requirement, root)
        resolved[requirement.tool_id] = tool
        return item
    tool = resolved[requirement.tool_id]
    return {
        "requirement_id": requirement.requirement_id,
        "label": requirement.label,
        "status": tool.status,
        "source": tool.source,
        "reason": tool.public_reason,
    }


def _check_alternative(
    alternative: _Alternative,
    root: Path,
    resolved: dict[str, ResolvedTool],
) -> dict[str, object]:
    items = [_check_requirement(requirement, root, resolved) for requirement in alternative.requirements]
    return {
        "alternative_id": alternative.alternative_id,
        "label": alternative.label,
        "ready": all(item["status"] == AVAILABLE for item in items),
        "items": items,
    }


def _existing_env_keys(path: Path) -> set[str]:
    """Return assignment keys without parsing or exposing any value."""
    if not path.is_file():
        return set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    keys: set[str] = set()
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key.startswith("export "):
            key = key.removeprefix("export ").strip()
        if key:
            keys.add(key)
    return keys


def _persist_discovered_defaults(root: Path, resolved: Iterable[ResolvedTool]) -> tuple[list[str], str]:
    """Append discovered external defaults without overwriting any setting.

    The function never mutates ``os.environ``.  A failed local write is a
    diagnostic warning only because preflight is advisory by contract.
    """
    env_path = root / ".env"
    existing = _existing_env_keys(env_path)
    pending: list[tuple[str, str]] = []
    for result in resolved:
        if result.tool_id not in _PERSISTED_ENV or not result.available:
            continue
        if result.source in {"bundled", "willy_env", "dotenv"}:
            continue
        key, field = _PERSISTED_ENV[result.tool_id]
        protected_keys = _ORCA_ENV_KEYS if result.tool_id in {"orca", "orca_2mkl"} else {key}
        if any(name in existing or name in os.environ for name in protected_keys):
            continue
        value = result.home if field == "home" else result.executable
        if value is None:
            continue
        pending.append((key, str(value)))
        existing.add(key)

    if not pending:
        return [], ""

    try:
        root.mkdir(parents=True, exist_ok=True)
        original = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        suffix = "" if not original or original.endswith("\n") else "\n"
        content = original + suffix + "".join(f"{key}={value}\n" for key, value in pending)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".env-", suffix=".tmp", dir=root)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, env_path)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError:
        return [], "无法写入本机 .env；预检结果仍可用于手动配置。"
    return [key for key, _ in pending], ""


def _markdown(
    groups: list[dict[str, object]],
    ready: bool,
    recommendations: list[str],
    written_defaults: list[str],
    persistence_warning: str,
) -> str:
    lines = ["### 本机依赖预检", "此结果仅供参考，不会阻止本地任务启动。"]
    for index, group in enumerate(groups, start=1):
        status = "满足" if group["ready"] else "不满足"
        lines.append(f"{index}. {group['label']}（{status}）")
        for alternative in group["alternatives"]:  # type: ignore[index]
            item_status = "满足" if alternative["ready"] else "不满足"  # type: ignore[index]
            lines.append(f"   - {alternative['label']}（{item_status}）")  # type: ignore[index]
            for item in alternative["items"]:  # type: ignore[index]
                item_status = "满足" if item["status"] == AVAILABLE else "不满足"
                source = item.get("source", "")
                source_text = f"；来源：{source}" if source else ""
                lines.append(f"     - {item['label']}（{item_status}{source_text}）")
    if ready:
        lines.append("检查结论：当前依赖满足完成完整 MD 流程的最小链路。")
    else:
        lines.append("检查结论：当前依赖不满足完成完整 MD 流程的最小链路。")
        lines.extend(f"推荐安装：{item}" for item in recommendations)
    if written_defaults:
        lines.append(f"本次已写入默认配置：{', '.join(written_defaults)}。")
    else:
        lines.append("本次默认配置写入：未写入。")
    if persistence_warning:
        lines.append(f"配置写入提示：{persistence_warning}")
    return "\n".join(lines)


def run_dependency_preflight(project_root: str | Path | None = None) -> DependencyPreflightReport:
    """Return a redacted, advisory local dependency report.

    It may append discovered executable defaults to the project ``.env``.  It
    neither creates a run nor calls execution-time ``ensure()`` guards, so a
    failed result has no authority to block a pipeline submission.
    """
    root = Path(project_root) if project_root is not None else get_project_root()
    resolved: dict[str, ResolvedTool] = {}
    groups: list[dict[str, object]] = []
    recommendations: list[str] = []
    for group in _GROUPS:
        alternatives = [_check_alternative(item, root, resolved) for item in group.alternatives]
        group_ready = any(item["ready"] for item in alternatives)
        groups.append({
            "group_id": group.group_id,
            "label": group.label,
            "ready": group_ready,
            "alternatives": alternatives,
        })
        if not group_ready:
            recommendations.append(group.recommendation)

    # Resolve remaining external tools so this report consistently covers all
    # configurable external dependencies, including unavailable alternatives.
    for tool_id in _PERSISTED_ENV:
        if tool_id not in resolved:
            resolved[tool_id] = resolve_tool(tool_id, project_root=root)
    written_defaults, persistence_warning = _persist_discovered_defaults(root, resolved.values())
    ready = all(group["ready"] for group in groups)
    return {
        "schema_version": 1,
        "advisory": True,
        "ready": ready,
        "groups": groups,
        "recommendations": recommendations,
        "written_defaults": written_defaults,
        "persistence_warning": persistence_warning,
        "markdown": _markdown(
            groups,
            ready,
            recommendations,
            written_defaults,
            persistence_warning,
        ),
    }
