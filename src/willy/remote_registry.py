"""Strict local registry for remote GROMACS execution profiles.

This module deliberately performs no SSH, scheduler, transfer, or subprocess
operation.  It validates the locally managed profile document and exposes a
redacted capability view for configuration and UI consumers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any
import json
import os
import re
import stat


REMOTE_PROFILE_SCHEMA_VERSION = 1
REMOTE_PROFILES_ENV = "WILLY_REMOTE_PROFILES_FILE"
DEFAULT_REMOTE_PROFILES_PATH = Path.home() / ".config" / "willy" / "remote_profiles.json"
EXECUTION_BACKENDS = frozenset({"local", "ssh", "slurm"})
# Profile IDs may preserve an operator's existing naming convention.  The
# frontend treats them as opaque values and does not normalize their case.
_PROFILE_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_SAFE_ALIAS_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}\Z")
_SAFE_SLURM_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_WALLTIME_RE = re.compile(r"([0-9]{1,3}):([0-5][0-9]):([0-5][0-9])\Z")
_UNSAFE_PATH_CHARS = frozenset("\t\r\n;|&`$<>\\\"'")


class RemoteRegistryError(ValueError):
    """A safe-to-display remote profile registry validation failure."""


class RemoteProfileUnavailable(RemoteRegistryError):
    """The local profile registry is absent or cannot be trusted."""


class RemoteProfileNotFound(RemoteRegistryError):
    """A selected public profile identifier is not registered locally."""


@dataclass(frozen=True)
class RemoteLauncher:
    """Validated launch policy; Slurm account and queue fields remain private."""

    kind: str
    partition: str = field(default="", repr=False)
    account: str = field(default="", repr=False)
    qos: str = field(default="", repr=False)
    gpus: int | None = field(default=None, repr=False)
    cpus: int | None = field(default=None, repr=False)
    memory_mb: int | None = field(default=None, repr=False)
    walltime: str = field(default="", repr=False)


@dataclass(frozen=True)
class RemoteProfile:
    """One strict, local-only remote execution profile."""

    profile_id: str
    ssh_host_alias: str = field(repr=False)
    remote_run_root: str = field(repr=False)
    transfer_method: str
    transfer_partial: bool
    gromacs_command: str = field(repr=False)
    gromacs_setup_script: str = field(repr=False)
    gpu_policy: str
    launcher: RemoteLauncher
    host_key_policy: str = field(default="strict", repr=False)

    def public_capability(self) -> dict[str, str | bool]:
        """Return only facts permitted in configuration/UI/LLM contexts."""
        return {
            "profile": self.profile_id,
            "launcher": self.launcher.kind,
            "transfer": self.transfer_method,
            "partial_transfer": self.transfer_partial,
            "gpu_policy": self.gpu_policy,
        }


@dataclass(frozen=True)
class MDExecutionConfig:
    """Validated non-secret execution choice stored in a frozen run config."""

    backend: str
    profile: str | None
    retain_remote_run: bool

    @property
    def is_remote(self) -> bool:
        return self.backend != "local"

    def as_dict(self) -> dict[str, str | bool | None]:
        return {
            "backend": self.backend,
            "profile": self.profile,
            "retain_remote_run": self.retain_remote_run,
        }


@dataclass(frozen=True)
class RemoteProfileRegistry:
    """Immutable local registry indexed by a public profile identifier."""

    profiles: Mapping[str, RemoteProfile]

    def resolve(self, profile_id: str) -> RemoteProfile:
        try:
            return self.profiles[profile_id]
        except KeyError as exc:
            raise RemoteProfileNotFound(f"远程 profile '{profile_id}' 未登记") from exc

    def public_capabilities(self) -> dict[str, dict[str, str | bool]]:
        return {
            profile_id: profile.public_capability()
            for profile_id, profile in self.profiles.items()
        }


def remote_profiles_path(path: str | Path | None = None) -> Path:
    """Resolve the local profile registry path without creating it."""
    if path is not None:
        return Path(path).expanduser()
    configured = os.environ.get(REMOTE_PROFILES_ENV, "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_REMOTE_PROFILES_PATH


def default_execution_md() -> dict[str, str | bool | None]:
    """Return the explicit, compatibility-preserving local MD execution mode."""
    return MDExecutionConfig("local", None, True).as_dict()


def merge_execution_md_defaults(payload: object) -> dict[str, object]:
    """Apply only execution defaults; validation and profile lookup stay explicit."""
    if payload is None:
        return default_execution_md()
    if not isinstance(payload, Mapping):
        raise RemoteRegistryError("execution.md 必须是对象")
    merged = dict(payload)
    defaults = default_execution_md()
    for key, value in defaults.items():
        merged.setdefault(key, value)
    return merged


def load_remote_registry(path: str | Path | None = None) -> RemoteProfileRegistry:
    """Load a secure profile file and parse all profiles without network access."""
    profile_path = remote_profiles_path(path)
    _validate_profile_file_security(profile_path)
    try:
        payload = json.loads(
            profile_path.read_text(encoding="utf-8"),
            object_pairs_hook=_json_object_without_duplicates,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RemoteRegistryError("远程 profile 注册表不是有效 JSON") from exc
    return _parse_registry(payload)


def load_remote_profiles(path: str | Path | None = None) -> dict[str, RemoteProfile]:
    """Compatibility convenience API returning parsed profiles by public ID."""
    return dict(load_remote_registry(path).profiles)


def resolve_remote_profile(
    profile_id: str,
    *,
    path: str | Path | None = None,
) -> RemoteProfile:
    """Resolve one registered profile without exposing its private fields."""
    if not _PROFILE_ID_RE.fullmatch(profile_id):
        raise RemoteProfileNotFound("远程 profile 标识无效")
    return load_remote_registry(path).resolve(profile_id)


def public_remote_capabilities(path: str | Path | None = None) -> dict[str, object]:
    """Expose a redacted registry summary suitable for public consumers."""
    try:
        registry = load_remote_registry(path)
    except RemoteRegistryError as exc:
        return {"available": False, "profiles": {}, "reason": str(exc)}
    return {
        "available": bool(registry.profiles),
        "profiles": registry.public_capabilities(),
        "reason": "" if registry.profiles else "未登记远程 profile",
    }


def get_public_execution_snapshot(path: str | Path | None = None) -> dict[str, object]:
    """Return the narrow profile view allowed to cross the frontend boundary.

    Loading this snapshot is deliberately *not* a connectivity test.  A
    profile is marked available only because its local registry entry passed
    strict parsing and permission checks; ``connection_state=unknown`` makes
    that distinction explicit until the deterministic preflight runs.
    """
    capabilities = public_remote_capabilities(path)
    raw_profiles = capabilities.get("profiles")
    profiles: list[dict[str, object]] = []
    if isinstance(raw_profiles, Mapping):
        for profile_id, capability in sorted(raw_profiles.items()):
            if not isinstance(profile_id, str) or not isinstance(capability, Mapping):
                continue
            launcher = capability.get("launcher")
            if launcher not in {"direct", "slurm"}:
                continue
            profiles.append({
                "profile_id": profile_id,
                "mode": "ssh" if launcher == "direct" else "slurm",
                "available": True,
                "connection_state": "unknown",
            })
    return {
        "profiles": profiles,
        "registry_configured": capabilities.get("available") is True,
    }


def parse_execution_md(
    payload: object,
    *,
    profile_path: str | Path | None = None,
) -> MDExecutionConfig:
    """Validate one ``execution.md`` selection against the local registry."""
    values = merge_execution_md_defaults(payload)
    _reject_unknown_keys(values, {"backend", "profile", "retain_remote_run"}, "execution.md")

    backend = values.get("backend")
    if not isinstance(backend, str) or backend not in EXECUTION_BACKENDS:
        allowed = "/".join(sorted(EXECUTION_BACKENDS))
        raise RemoteRegistryError(f"execution.md.backend 必须是 {allowed}")
    profile = values.get("profile")
    retain_remote_run = values.get("retain_remote_run")
    if not isinstance(retain_remote_run, bool):
        raise RemoteRegistryError("execution.md.retain_remote_run 必须是布尔值")

    if backend == "local":
        if profile is not None:
            raise RemoteRegistryError("execution.md.backend=local 时 profile 必须为 null")
        return MDExecutionConfig(backend, None, retain_remote_run)

    if not isinstance(profile, str) or not _PROFILE_ID_RE.fullmatch(profile):
        raise RemoteRegistryError("远程 execution.md.profile 必须是已登记的非空标识")
    resolved = resolve_remote_profile(profile, path=profile_path)
    expected_launcher = "direct" if backend == "ssh" else "slurm"
    if resolved.launcher.kind != expected_launcher:
        raise RemoteRegistryError(
            f"execution.md.backend={backend} 与 profile 启动器类型不匹配"
        )
    return MDExecutionConfig(backend, profile, retain_remote_run)


def validate_execution_md(
    payload: object,
    *,
    profile_path: str | Path | None = None,
) -> tuple[str, ...]:
    """Return public, displayable execution configuration issues."""
    try:
        parse_execution_md(payload, profile_path=profile_path)
    except RemoteRegistryError as exc:
        return (str(exc),)
    return ()


def _validate_profile_file_security(profile_path: Path) -> None:
    if not profile_path.exists():
        raise RemoteProfileUnavailable("未配置远程 profile 注册表")
    if profile_path.is_symlink():
        raise RemoteProfileUnavailable("远程 profile 注册表不能是符号链接")
    if profile_path.parent.is_symlink():
        raise RemoteProfileUnavailable("远程 profile 目录不能是符号链接")
    try:
        parent_stat = profile_path.parent.stat()
        file_stat = profile_path.stat()
    except OSError as exc:
        raise RemoteProfileUnavailable("无法读取远程 profile 注册表") from exc
    if not stat.S_ISDIR(parent_stat.st_mode) or stat.S_IMODE(parent_stat.st_mode) != 0o700:
        raise RemoteProfileUnavailable("远程 profile 目录权限必须为 0700")
    if not stat.S_ISREG(file_stat.st_mode) or stat.S_IMODE(file_stat.st_mode) != 0o600:
        raise RemoteProfileUnavailable("远程 profile 文件权限必须为 0600")
    current_uid = os.getuid()
    if parent_stat.st_uid != current_uid or file_stat.st_uid != current_uid:
        raise RemoteProfileUnavailable("远程 profile 必须由当前用户拥有")


def _parse_registry(payload: object) -> RemoteProfileRegistry:
    values = _require_mapping(payload, "远程 profile 注册表")
    _reject_unknown_keys(values, {"schema_version", "profiles"}, "远程 profile 注册表")
    if values.get("schema_version") != REMOTE_PROFILE_SCHEMA_VERSION:
        raise RemoteRegistryError(
            f"远程 profile schema_version 必须为 {REMOTE_PROFILE_SCHEMA_VERSION}"
        )
    profiles = _require_mapping(values.get("profiles"), "远程 profiles")
    parsed: dict[str, RemoteProfile] = {}
    for profile_id, profile_payload in profiles.items():
        if not isinstance(profile_id, str) or not _PROFILE_ID_RE.fullmatch(profile_id):
            raise RemoteRegistryError("远程 profile 标识必须使用字母、数字、下划线或连字符")
        parsed[profile_id] = _parse_profile(profile_id, profile_payload)
    return RemoteProfileRegistry(MappingProxyType(parsed))


def _parse_profile(profile_id: str, payload: object) -> RemoteProfile:
    values = _require_mapping(payload, f"profile '{profile_id}'")
    _reject_unknown_keys(
        values,
        {"ssh_host_alias", "remote_run_root", "transfer", "gromacs", "launcher", "ssh"},
        f"profile '{profile_id}'",
    )
    for required in ("ssh_host_alias", "remote_run_root", "transfer", "gromacs", "launcher"):
        if required not in values:
            raise RemoteRegistryError(f"profile '{profile_id}' 缺少 {required}")

    alias = _require_safe_name(values["ssh_host_alias"], "ssh_host_alias", _SAFE_ALIAS_RE)
    run_root = _require_safe_absolute_path(values["remote_run_root"], "remote_run_root")
    transfer_method, transfer_partial = _parse_transfer(values["transfer"])
    command, setup_script, gpu_policy = _parse_gromacs(values["gromacs"])
    launcher = _parse_launcher(values["launcher"])
    host_key_policy = _parse_ssh_policy(values.get("ssh", {}))
    return RemoteProfile(
        profile_id=profile_id,
        ssh_host_alias=alias,
        remote_run_root=run_root,
        transfer_method=transfer_method,
        transfer_partial=transfer_partial,
        gromacs_command=command,
        gromacs_setup_script=setup_script,
        gpu_policy=gpu_policy,
        launcher=launcher,
        host_key_policy=host_key_policy,
    )


def _parse_transfer(payload: object) -> tuple[str, bool]:
    values = _require_mapping(payload, "transfer")
    _reject_unknown_keys(values, {"method", "partial"}, "transfer")
    if values.get("method") != "rsync":
        raise RemoteRegistryError("transfer.method 仅支持 rsync")
    partial = values.get("partial")
    if not isinstance(partial, bool):
        raise RemoteRegistryError("transfer.partial 必须是布尔值")
    return "rsync", partial


def _parse_gromacs(payload: object) -> tuple[str, str, str]:
    values = _require_mapping(payload, "gromacs")
    _reject_unknown_keys(values, {"command", "setup_script", "gpu_policy"}, "gromacs")
    command = _require_safe_absolute_path(values.get("command"), "gromacs.command")
    setup_script = _require_safe_absolute_path(values.get("setup_script"), "gromacs.setup_script")
    gpu_policy = values.get("gpu_policy")
    if gpu_policy not in {"auto", "required", "disabled"}:
        raise RemoteRegistryError("gromacs.gpu_policy 必须是 auto、required 或 disabled")
    return command, setup_script, gpu_policy


def _parse_ssh_policy(payload: object) -> str:
    values = _require_mapping(payload, "ssh")
    _reject_unknown_keys(values, {"host_key_policy"}, "ssh")
    policy = values.get("host_key_policy", "strict")
    if policy != "strict":
        raise RemoteRegistryError("ssh.host_key_policy 必须为 strict")
    return "strict"


def _parse_launcher(payload: object) -> RemoteLauncher:
    values = _require_mapping(payload, "launcher")
    kind = values.get("kind")
    if kind == "direct":
        _reject_unknown_keys(values, {"kind"}, "launcher")
        return RemoteLauncher(kind="direct")
    if kind != "slurm":
        raise RemoteRegistryError("launcher.kind 必须是 direct 或 slurm")

    allowed = {"kind", "partition", "account", "qos", "gpus", "cpus", "memory_mb", "walltime"}
    _reject_unknown_keys(values, allowed, "launcher")
    required = ("partition", "account", "qos", "gpus", "cpus", "memory_mb", "walltime")
    if any(key not in values for key in required):
        raise RemoteRegistryError("Slurm launcher 必须提供完整的结构化资源字段")
    partition = _require_safe_name(values["partition"], "launcher.partition", _SAFE_SLURM_NAME_RE)
    account = _require_safe_name(values["account"], "launcher.account", _SAFE_SLURM_NAME_RE)
    qos = _require_safe_name(values["qos"], "launcher.qos", _SAFE_SLURM_NAME_RE)
    gpus = _require_bounded_int(values["gpus"], "launcher.gpus", 1, 64)
    cpus = _require_bounded_int(values["cpus"], "launcher.cpus", 1, 4096)
    memory_mb = _require_bounded_int(values["memory_mb"], "launcher.memory_mb", 256, 4_194_304)
    walltime = values["walltime"]
    if not isinstance(walltime, str) or not _WALLTIME_RE.fullmatch(walltime) or int(walltime.split(":", 1)[0]) < 1:
        raise RemoteRegistryError("launcher.walltime 必须为 HHH:MM:SS")
    return RemoteLauncher("slurm", partition, account, qos, gpus, cpus, memory_mb, walltime)


def _require_mapping(payload: object, label: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise RemoteRegistryError(f"{label} 必须是对象")
    if any(not isinstance(key, str) for key in payload):
        raise RemoteRegistryError(f"{label} 的字段名必须是字符串")
    return payload


def _json_object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON fields instead of silently keeping the last one."""
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise RemoteRegistryError("远程 profile 注册表包含重复字段")
        payload[key] = value
    return payload


def _reject_unknown_keys(payload: Mapping[str, Any], allowed: set[str], label: str) -> None:
    if set(payload) - allowed:
        # Field names originate in a private file too.  Do not echo an
        # attacker-controlled key into public capability/error payloads.
        raise RemoteRegistryError(f"{label} 包含不允许的字段")


def _require_safe_name(payload: object, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(payload, str) or not pattern.fullmatch(payload):
        raise RemoteRegistryError(f"{label} 格式无效")
    return payload


def _require_safe_absolute_path(payload: object, label: str) -> str:
    if not isinstance(payload, str) or not payload or any(char in _UNSAFE_PATH_CHARS for char in payload):
        raise RemoteRegistryError(f"{label} 必须是安全的绝对路径")
    path = PurePosixPath(payload)
    if not path.is_absolute() or ".." in path.parts or str(path) == "/":
        raise RemoteRegistryError(f"{label} 必须是安全的绝对路径")
    return str(path)


def _require_bounded_int(payload: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(payload, bool) or not isinstance(payload, int) or not minimum <= payload <= maximum:
        raise RemoteRegistryError(f"{label} 必须是 {minimum}-{maximum} 的整数")
    return payload
