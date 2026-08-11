"""Build a client bundle with a deployment-owned managed gateway profile."""

from __future__ import annotations

import hashlib
import ipaddress
import json
from pathlib import Path
import re
import shutil
from typing import Mapping
from urllib.parse import urlparse


class ManagedGatewayBundleError(ValueError):
    """Raised when a client bundle cannot be built without unsafe inputs."""


_PROFILE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_MODEL_ALIAS = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_EXCLUDED_NAMES = {
    ".git",
    ".env",
    ".venv",
    ".willy",
    ".agents",
    ".codex",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "__pycache__",
    "venv",
    "env",
    "build",
    "dist",
    "htmlcov",
    "logs",
    "artifacts",
    "node_modules",
    "md_run",
    "gateway-data",
    "gateway-state",
    ".gateway",
    "managed_gateway.json",
    "managed_gateway_identity.json",
    "config.json",
    "config.json.bak",
    "status.json",
    "startup_audit.json",
    ".pipeline.lock",
    ".pipeline.pid",
    "stop.request",
}
_EXCLUDED_SUFFIXES = (
    ".sqlite", ".sqlite3", ".db", ".pem", ".key", ".crt", ".p12", ".pfx",
    ".log", ".jsonl", ".pid", ".lock", ".pyc", ".pyo", ".tar", ".tar.gz",
    ".tgz", ".zip", ".whl",
)


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _read_profile(path: Path) -> tuple[dict[str, object], str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManagedGatewayBundleError("托管网关 profile 不是有效 JSON") from exc
    if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
        raise ManagedGatewayBundleError("托管网关 profile schema_version 必须为 1")
    profile_id = raw.get("profile_id")
    label = raw.get("label")
    model = raw.get("model")
    base_url = raw.get("base_url")
    if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
        raise ManagedGatewayBundleError("托管网关 profile_id 无效")
    if not isinstance(label, str) or not label.strip() or len(label.strip()) > 80:
        raise ManagedGatewayBundleError("托管网关 label 无效")
    if not isinstance(model, str) or not _MODEL_ALIAS.fullmatch(model):
        raise ManagedGatewayBundleError("托管网关 model 无效")
    if not isinstance(base_url, str):
        raise ManagedGatewayBundleError("托管网关 base_url 无效")
    parsed = urlparse(base_url.rstrip("/"))
    if parsed.scheme != "https" or not parsed.netloc or _is_loopback(parsed.hostname):
        raise ManagedGatewayBundleError("客户端部署包的 base_url 必须是非 loopback HTTPS 地址")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ManagedGatewayBundleError("托管网关 base_url 不能包含路径、凭据、查询参数或片段")
    normalized = {
        "schema_version": 1,
        "profile_id": profile_id,
        "label": label.strip(),
        "base_url": base_url.rstrip("/"),
        "model": model,
    }
    return normalized, hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _ignore(_directory: str, names: list[str]) -> set[str]:
    ignored = set()
    for name in names:
        if name in _EXCLUDED_NAMES or (name.startswith(".") and name != ".env.example"):
            ignored.add(name)
        elif name.lower().endswith(_EXCLUDED_SUFFIXES):
            ignored.add(name)
    return ignored


def build_managed_client_bundle(
    source_root: str | Path,
    profile_path: str | Path,
    output_dir: str | Path,
) -> tuple[Path, str]:
    """Copy a clean client tree and install one deployment-owned profile."""
    source = Path(source_root).resolve()
    profile = Path(profile_path).resolve()
    output = Path(output_dir).resolve()
    if not source.is_dir():
        raise ManagedGatewayBundleError("source_root 不是目录")
    if not profile.is_file():
        raise ManagedGatewayBundleError("profile 文件不存在")
    if source in profile.parents:
        raise ManagedGatewayBundleError("profile 必须位于 source_root 外，且不得进入源码发布树")
    if output == source or source in output.parents:
        raise ManagedGatewayBundleError("output_dir 不能位于 source_root 内")
    if output.exists():
        raise ManagedGatewayBundleError("output_dir 已存在，请使用新的空目录")
    normalized, fingerprint = _read_profile(profile)
    shutil.copytree(source, output, ignore=_ignore)
    (output / "managed_gateway.json").write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return output, fingerprint
