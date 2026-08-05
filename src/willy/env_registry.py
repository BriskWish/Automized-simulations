"""Resolve external runtime tools without exposing machine-local details.

This module owns external executable discovery and the environment passed to
their subprocesses.  It is deliberately separate from ``toolist_*.py``:
those modules describe LLM function-calling tools, while this module describes
software installed in the execution environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
import os
import shutil

from willy._paths import get_project_root


AVAILABLE = "available"
MISSING = "missing"
MISCONFIGURED = "misconfigured"
NOT_EXECUTABLE = "not_executable"
RUNTIME_UNAVAILABLE = "runtime_unavailable"
_UNAVAILABLE = {MISSING, MISCONFIGURED, NOT_EXECUTABLE, RUNTIME_UNAVAILABLE}
_DOTENV_KEYS = {
    "DEEPSEEK_API_KEY",
    "WILLY_LLM_API_KEY",
    "WILLY_LLM_BASE_URL",
    "WILLY_LLM_MODEL",
    "WILLY_SERVER_PORT",
    "WILLY_G16_BIN",
    "WILLY_FORMCHK_BIN",
    "WILLY_ORCA_BIN",
    "WILLY_ORCA_2MKL_BIN",
    "WILLY_ORCA_HOME",
    "WILLY_MULTIWFN_BIN",
    "WILLY_GMX_BIN",
    "WILLY_LIGPARGEN_BIN",
    "WILLY_BOSS_HOME",
}

DEFAULT_BOSS_HOME = Path.home() / "boss" / "boss"


@dataclass(frozen=True)
class ToolSpec:
    """Static contract for one non-bundled executable or installation."""

    tool_id: str
    label: str
    executable_name: str = ""
    binary_env: str = ""
    home_env: str = ""
    legacy_env: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedTool:
    """A resolved external tool. ``as_public_dict`` never exposes paths."""

    tool_id: str
    label: str
    status: str
    executable: Path | None = None
    home: Path | None = None
    source: str = ""
    public_reason: str = ""
    version: str | None = None

    @property
    def available(self) -> bool:
        return self.status == AVAILABLE

    def as_public_dict(self) -> dict[str, str | None]:
        return {
            "tool_id": self.tool_id,
            "label": self.label,
            "status": self.status,
            "source": self.source,
            "reason": self.public_reason,
            "version": self.version,
        }


class EnvironmentRegistryError(RuntimeError):
    """Raised when a caller asks to execute a tool that is not usable."""


TOOL_SPECS: dict[str, ToolSpec] = {
    "g16": ToolSpec("g16", "Gaussian 16", "g16", "WILLY_G16_BIN"),
    "formchk": ToolSpec("formchk", "Gaussian formchk", "formchk", "WILLY_FORMCHK_BIN"),
    "orca": ToolSpec(
        "orca", "ORCA", "orca", "WILLY_ORCA_BIN", "WILLY_ORCA_HOME", ("ORCA_DIR",),
    ),
    "orca_2mkl": ToolSpec(
        "orca_2mkl", "ORCA orca_2mkl", "orca_2mkl", "WILLY_ORCA_2MKL_BIN",
        "WILLY_ORCA_HOME", ("ORCA_DIR",),
    ),
    "multiwfn": ToolSpec(
        "multiwfn", "Multiwfn", "Multiwfn", "WILLY_MULTIWFN_BIN", "", ("MULTIWFN_BIN",),
    ),
    "gmx": ToolSpec("gmx", "GROMACS", "gmx", "WILLY_GMX_BIN"),
    "ligpargen": ToolSpec("ligpargen", "LigParGen", "LigParGen", "WILLY_LIGPARGEN_BIN"),
    "boss": ToolSpec("boss", "BOSS", "BOSS", "", "WILLY_BOSS_HOME", ("BOSSdir",)),
}


def _project_root(project_root: str | Path | None) -> Path:
    return Path(project_root) if project_root is not None else get_project_root()


def _read_dotenv(project_root: str | Path | None = None) -> dict[str, str]:
    """Read only supported keys. This is not a general-purpose dotenv loader."""
    path = _project_root(project_root) / ".env"
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key in _DOTENV_KEYS:
                values[key] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    return values


def dotenv_value(name: str, project_root: str | Path | None = None) -> str:
    """Return a whitelisted ``.env`` value only when process env has no value."""
    process_value = os.environ.get(name, "")
    if process_value:
        return process_value
    return _read_dotenv(project_root).get(name, "")


def _configured_value(name: str, project_root: str | Path | None = None) -> tuple[str, str]:
    if os.environ.get(name):
        return os.environ[name], "willy_env"
    value = _read_dotenv(project_root).get(name, "")
    return (value, "dotenv") if value else ("", "")


def _legacy_value(names: tuple[str, ...]) -> tuple[str, str]:
    for name in names:
        value = os.environ.get(name, "")
        if value:
            return value, "legacy_env"
    return "", ""


def _path_from_value(value: str) -> Path | None:
    candidate = Path(value).expanduser()
    if candidate.is_absolute() or "/" in value:
        return candidate
    found = shutil.which(value)
    return Path(found) if found else None


def _binary_result(
    spec: ToolSpec,
    value: str,
    source: str,
    *,
    strict: bool,
) -> ResolvedTool:
    path = _path_from_value(value)
    if path is None or not path.exists():
        status = MISCONFIGURED if strict else MISSING
        reason = "配置的可执行文件不存在" if strict else "未找到可执行文件"
        return ResolvedTool(spec.tool_id, spec.label, status, source=source, public_reason=reason)
    if not path.is_file():
        return ResolvedTool(
            spec.tool_id, spec.label, MISCONFIGURED, source=source,
            public_reason="配置项必须指向可执行文件",
        )
    if not os.access(path, os.X_OK):
        return ResolvedTool(
            spec.tool_id, spec.label, NOT_EXECUTABLE, source=source,
            public_reason="可执行文件没有执行权限",
        )
    return ResolvedTool(spec.tool_id, spec.label, AVAILABLE, executable=path.resolve(), source=source)


def _home_result(
    spec: ToolSpec,
    value: str,
    source: str,
    *,
    strict: bool,
) -> ResolvedTool:
    home = Path(value).expanduser()
    if not home.is_dir():
        status = MISCONFIGURED if strict else MISSING
        reason = "配置的安装目录不存在" if strict else "未找到安装目录"
        return ResolvedTool(spec.tool_id, spec.label, status, source=source, public_reason=reason)
    executable = home / spec.executable_name
    if not executable.is_file():
        return ResolvedTool(
            spec.tool_id, spec.label, RUNTIME_UNAVAILABLE, home=home.resolve(), source=source,
            public_reason="安装目录缺少所需可执行文件",
        )
    if not os.access(executable, os.X_OK):
        return ResolvedTool(
            spec.tool_id, spec.label, NOT_EXECUTABLE, home=home.resolve(), source=source,
            public_reason="安装目录中的可执行文件没有执行权限",
        )
    return ResolvedTool(
        spec.tool_id, spec.label, AVAILABLE, executable=executable.resolve(), home=home.resolve(), source=source,
    )


def _resolve_orca_helper(spec: ToolSpec, project_root: str | Path | None) -> ResolvedTool:
    binary_value, source = _configured_value(spec.binary_env, project_root)
    if binary_value:
        result = _binary_result(spec, binary_value, source, strict=True)
        if result.available:
            return ResolvedTool(**{**result.__dict__, "home": result.executable.parent})
        return result
    home_value, source = _configured_value(spec.home_env, project_root)
    if home_value:
        return _home_result(spec, home_value, source, strict=True)
    legacy_value, source = _legacy_value(spec.legacy_env)
    if legacy_value:
        return _home_result(spec, legacy_value, source, strict=True)
    if spec.tool_id == "orca_2mkl":
        orca = resolve_tool("orca", project_root=project_root)
        if orca.available and orca.home is not None:
            candidate = _home_result(spec, str(orca.home), orca.source, strict=False)
            if candidate.available:
                return candidate
    found = shutil.which(spec.executable_name)
    if found:
        result = _binary_result(spec, found, "path", strict=False)
        return ResolvedTool(**{**result.__dict__, "home": result.executable.parent}) if result.available else result
    return ResolvedTool(spec.tool_id, spec.label, MISSING, source="path", public_reason="未在 PATH 中找到可执行文件")


def _resolve_binary(spec: ToolSpec, project_root: str | Path | None) -> ResolvedTool:
    value, source = _configured_value(spec.binary_env, project_root)
    if value:
        return _binary_result(spec, value, source, strict=True)
    legacy_value, source = _legacy_value(spec.legacy_env)
    if legacy_value:
        return _binary_result(spec, legacy_value, source, strict=True)
    found = shutil.which(spec.executable_name)
    if found:
        return _binary_result(spec, found, "path", strict=False)
    return ResolvedTool(spec.tool_id, spec.label, MISSING, source="path", public_reason="未在 PATH 中找到可执行文件")


def _resolve_boss(project_root: str | Path | None) -> ResolvedTool:
    spec = TOOL_SPECS["boss"]
    value, source = _configured_value(spec.home_env, project_root)
    if value:
        return _home_result(spec, value, source, strict=True)
    legacy_value, source = _legacy_value(spec.legacy_env)
    if legacy_value:
        return _home_result(spec, legacy_value, source, strict=True)
    if DEFAULT_BOSS_HOME.is_dir():
        return _home_result(spec, str(DEFAULT_BOSS_HOME), "default", strict=False)
    return ResolvedTool(spec.tool_id, spec.label, MISSING, source="default", public_reason="未配置 BOSS 安装目录")


def resolve_tool(tool_id: str, project_root: str | Path | None = None) -> ResolvedTool:
    """Resolve one supported external tool without changing global process state."""
    try:
        spec = TOOL_SPECS[tool_id]
    except KeyError as exc:
        raise KeyError(f"未知外部工具: {tool_id}") from exc
    if tool_id == "boss":
        return _resolve_boss(project_root)
    if tool_id in {"orca", "orca_2mkl"}:
        return _resolve_orca_helper(spec, project_root)
    return _resolve_binary(spec, project_root)


def resolve_all(project_root: str | Path | None = None) -> dict[str, ResolvedTool]:
    """Discover all non-bundled tools. Discovery never raises for missing tools."""
    return {tool_id: resolve_tool(tool_id, project_root=project_root) for tool_id in TOOL_SPECS}


def require_tool(tool_id: str, project_root: str | Path | None = None) -> ResolvedTool:
    """Return a usable tool or a user-safe exception for execution adapters."""
    result = resolve_tool(tool_id, project_root=project_root)
    if result.available:
        return result
    detail = result.public_reason or "运行环境不可用"
    raise EnvironmentRegistryError(f"{result.label} 不可用: {detail}")


def build_tool_env(
    tool_id: str,
    *,
    base_env: Mapping[str, str] | None = None,
    project_root: str | Path | None = None,
) -> dict[str, str]:
    """Return an isolated child environment for one resolved external tool."""
    result = require_tool(tool_id, project_root=project_root)
    env = dict(os.environ if base_env is None else base_env)

    if tool_id in {"g16", "formchk"}:
        env["GAUSS_CDEF"] = "0"
        env["OMP_NUM_THREADS"] = "1"
    if tool_id in {"orca", "orca_2mkl"}:
        home = result.home or (result.executable.parent if result.executable else None)
        if home is not None:
            current = env.get("LD_LIBRARY_PATH", "")
            env["LD_LIBRARY_PATH"] = f"{home}:{current}" if current else str(home)
    if tool_id == "ligpargen":
        boss = require_tool("boss", project_root=project_root)
        if boss.home is not None:
            # LigParGen itself requires the legacy spelling.
            env["BOSSdir"] = str(boss.home)
    return env


def public_capabilities(project_root: str | Path | None = None) -> dict[str, dict[str, str | None]]:
    """Return the redacted capability payload suitable for manifests and Agents."""
    return {tool_id: result.as_public_dict() for tool_id, result in resolve_all(project_root).items()}
