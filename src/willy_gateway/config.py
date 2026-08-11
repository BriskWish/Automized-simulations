"""Explicit configuration for the independently deployed gateway service."""

from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlparse


class GatewayConfigError(ValueError):
    """Raised when the gateway process lacks a safe, complete configuration."""


DEFAULT_AUTO_APPROVE_LIMIT = 50
DEFAULT_DAILY_TOKEN_LIMIT = 1_000_000
DEFAULT_MONTHLY_TOKEN_LIMIT = 10_000_000
DEFAULT_MAX_CONCURRENCY = 2
DEFAULT_MAX_REQUESTS_PER_MINUTE = 20


def _single_line(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise GatewayConfigError(f"{label} 必须是文本")
    result = value.strip()
    if not result or "\n" in result or "\r" in result:
        raise GatewayConfigError(f"{label} 不能为空且不能包含换行")
    return result


def _validate_url(value: object, label: str) -> str:
    result = _single_line(value, label).rstrip("/")
    parsed = urlparse(result)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise GatewayConfigError(f"{label} 必须是完整的 http(s) 地址")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise GatewayConfigError(f"{label} 不能包含凭据、查询参数或片段")
    return result


def _validate_aliases(values: Mapping[str, str]) -> Mapping[str, str]:
    if not values:
        raise GatewayConfigError("必须至少配置一个模型别名")
    validated: dict[str, str] = {}
    for alias, upstream_model in values.items():
        alias_value = _single_line(alias, "模型别名")
        if not all(character.isalnum() or character in {"-", "_"} for character in alias_value):
            raise GatewayConfigError("模型别名只能包含字母、数字、连字符或下划线")
        validated[alias_value] = _single_line(upstream_model, "上游模型名")
    return MappingProxyType(validated)


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _positive_int(value: object, label: str, *, maximum: int = 65_535) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise GatewayConfigError(f"{label} 必须是整数") from exc
    if not 1 <= parsed <= maximum:
        raise GatewayConfigError(f"{label} 超出允许范围")
    return parsed


def _nonnegative_int(value: object, label: str, *, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise GatewayConfigError(f"{label} 必须是整数") from exc
    if not 0 <= parsed <= maximum:
        raise GatewayConfigError(f"{label} 超出允许范围")
    return parsed


@dataclass(frozen=True)
class GatewaySettings:
    """All configuration needed by one gateway process.

    Values are supplied by deployment configuration, not by Willy browser forms
    or client-side ``.env`` files. The initial SQLite backend is intentionally
    restricted to private development and test deployments.
    """

    database_path: Path
    token_secret: bytes
    upstream_base_url: str
    upstream_api_key: str
    model_aliases: Mapping[str, str]
    registration_limit: int = 500
    auto_approve_limit: int = DEFAULT_AUTO_APPROVE_LIMIT
    default_daily_token_limit: int = DEFAULT_DAILY_TOKEN_LIMIT
    default_monthly_token_limit: int = DEFAULT_MONTHLY_TOKEN_LIMIT
    default_max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    default_max_requests_per_minute: int = DEFAULT_MAX_REQUESTS_PER_MINUTE
    pending_registration_ttl_s: int = 86_400
    registration_requests_per_window: int = 10
    registration_rate_window_s: int = 3_600
    token_ttl_s: int = 600
    signature_window_s: int = 60
    unknown_reservation_ttl_s: int = 900
    request_body_limit_bytes: int = 131_072
    max_completion_tokens: int = 4_096
    default_completion_tokens: int = 1_024
    max_tool_definitions: int = 16
    upstream_timeout_s: float = 30.0
    bind_host: str = "127.0.0.1"
    bind_port: int = 8787
    tls_certfile: Path | None = None
    tls_keyfile: Path | None = None
    admin_secret: str | None = field(default=None, repr=False)
    admin_port: int = 8788

    def __post_init__(self) -> None:
        object.__setattr__(self, "database_path", Path(self.database_path))
        if len(self.token_secret) < 32:
            raise GatewayConfigError("令牌签名密钥至少需要 32 字节")
        object.__setattr__(self, "upstream_base_url", _validate_url(self.upstream_base_url, "上游 Base URL"))
        object.__setattr__(self, "upstream_api_key", _single_line(self.upstream_api_key, "上游 API Key"))
        object.__setattr__(self, "model_aliases", _validate_aliases(self.model_aliases))
        object.__setattr__(self, "registration_limit", _positive_int(self.registration_limit, "注册名额上限", maximum=1_000_000))
        object.__setattr__(self, "auto_approve_limit", _nonnegative_int(self.auto_approve_limit, "自动批准名额", maximum=1_000_000))
        object.__setattr__(
            self, "default_daily_token_limit",
            _positive_int(self.default_daily_token_limit, "默认每日 Token 限额", maximum=2_000_000_000),
        )
        object.__setattr__(
            self, "default_monthly_token_limit",
            _positive_int(self.default_monthly_token_limit, "默认每月 Token 限额", maximum=2_000_000_000),
        )
        object.__setattr__(self, "default_max_concurrency", _positive_int(self.default_max_concurrency, "默认最大并发", maximum=1_000))
        object.__setattr__(
            self,
            "default_max_requests_per_minute",
            _positive_int(self.default_max_requests_per_minute, "默认每分钟请求数", maximum=100_000),
        )
        object.__setattr__(
            self,
            "pending_registration_ttl_s",
            _positive_int(self.pending_registration_ttl_s, "待审批申请有效期", maximum=31_536_000),
        )
        object.__setattr__(
            self,
            "registration_requests_per_window",
            _positive_int(self.registration_requests_per_window, "注册请求次数上限", maximum=10_000),
        )
        object.__setattr__(
            self,
            "registration_rate_window_s",
            _positive_int(self.registration_rate_window_s, "注册速率窗口", maximum=86_400),
        )
        if not 60 <= self.token_ttl_s <= 600:
            raise GatewayConfigError("令牌有效期必须在 60 到 600 秒之间")
        if self.signature_window_s <= 0 or self.unknown_reservation_ttl_s <= 0:
            raise GatewayConfigError("签名和未知请求的有效期必须为正数")
        if self.request_body_limit_bytes < 1_024:
            raise GatewayConfigError("请求体限制过小")
        if not 1 <= self.default_completion_tokens <= self.max_completion_tokens:
            raise GatewayConfigError("默认 max_tokens 必须小于等于最大值")
        if self.max_tool_definitions < 0 or self.upstream_timeout_s <= 0:
            raise GatewayConfigError("网关限制参数无效")
        host = _single_line(self.bind_host, "网关监听地址")
        object.__setattr__(self, "bind_host", host)
        object.__setattr__(self, "bind_port", _positive_int(self.bind_port, "网关监听端口"))
        object.__setattr__(self, "admin_port", _positive_int(self.admin_port, "管理页面端口"))
        certfile = Path(self.tls_certfile) if self.tls_certfile is not None else None
        keyfile = Path(self.tls_keyfile) if self.tls_keyfile is not None else None
        if (certfile is None) != (keyfile is None):
            raise GatewayConfigError("TLS 证书和私钥必须同时配置")
        if not _is_loopback_host(host) and (certfile is None or keyfile is None):
            raise GatewayConfigError("非 loopback 网关监听必须配置 TLS 证书和私钥")
        object.__setattr__(self, "tls_certfile", certfile)
        object.__setattr__(self, "tls_keyfile", keyfile)
        if self.admin_secret is not None:
            admin_secret = _single_line(self.admin_secret, "管理员秘密")
            if len(admin_secret.encode("utf-8")) < 32:
                raise GatewayConfigError("管理员秘密至少需要 32 字节")
            object.__setattr__(self, "admin_secret", admin_secret)

    @classmethod
    def from_environment(cls) -> "GatewaySettings":
        """Load deployment-only configuration without falling back to defaults."""
        aliases_raw = _single_line(os.environ.get("WILLY_GATEWAY_MODEL_ALIASES", ""), "WILLY_GATEWAY_MODEL_ALIASES")
        try:
            aliases = json.loads(aliases_raw)
        except json.JSONDecodeError as exc:
            raise GatewayConfigError("WILLY_GATEWAY_MODEL_ALIASES 必须是 JSON 对象") from exc
        if not isinstance(aliases, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in aliases.items()):
            raise GatewayConfigError("WILLY_GATEWAY_MODEL_ALIASES 必须是字符串映射")
        secret = _single_line(os.environ.get("WILLY_GATEWAY_TOKEN_SECRET", ""), "WILLY_GATEWAY_TOKEN_SECRET").encode("utf-8")
        return cls(
            database_path=Path(_single_line(os.environ.get("WILLY_GATEWAY_DATABASE", ""), "WILLY_GATEWAY_DATABASE")),
            token_secret=secret,
            upstream_base_url=os.environ.get("WILLY_GATEWAY_UPSTREAM_BASE_URL", ""),
            upstream_api_key=os.environ.get("WILLY_GATEWAY_UPSTREAM_API_KEY", ""),
            model_aliases=aliases,
            registration_limit=_positive_int(
                os.environ.get("WILLY_GATEWAY_REGISTRATION_LIMIT", "500"),
                "WILLY_GATEWAY_REGISTRATION_LIMIT",
                maximum=1_000_000,
            ),
            auto_approve_limit=_nonnegative_int(
                os.environ.get("WILLY_GATEWAY_AUTO_APPROVE_LIMIT", str(DEFAULT_AUTO_APPROVE_LIMIT)),
                "WILLY_GATEWAY_AUTO_APPROVE_LIMIT",
                maximum=1_000_000,
            ),
            default_daily_token_limit=_positive_int(
                os.environ.get("WILLY_GATEWAY_DEFAULT_DAILY_TOKEN_LIMIT", str(DEFAULT_DAILY_TOKEN_LIMIT)),
                "WILLY_GATEWAY_DEFAULT_DAILY_TOKEN_LIMIT",
                maximum=2_000_000_000,
            ),
            default_monthly_token_limit=_positive_int(
                os.environ.get("WILLY_GATEWAY_DEFAULT_MONTHLY_TOKEN_LIMIT", str(DEFAULT_MONTHLY_TOKEN_LIMIT)),
                "WILLY_GATEWAY_DEFAULT_MONTHLY_TOKEN_LIMIT",
                maximum=2_000_000_000,
            ),
            default_max_concurrency=_positive_int(
                os.environ.get("WILLY_GATEWAY_DEFAULT_MAX_CONCURRENCY", str(DEFAULT_MAX_CONCURRENCY)),
                "WILLY_GATEWAY_DEFAULT_MAX_CONCURRENCY",
                maximum=1_000,
            ),
            default_max_requests_per_minute=_positive_int(
                os.environ.get("WILLY_GATEWAY_DEFAULT_REQUESTS_PER_MINUTE", str(DEFAULT_MAX_REQUESTS_PER_MINUTE)),
                "WILLY_GATEWAY_DEFAULT_REQUESTS_PER_MINUTE",
                maximum=100_000,
            ),
            pending_registration_ttl_s=_positive_int(
                os.environ.get("WILLY_GATEWAY_PENDING_REGISTRATION_TTL_S", "86400"),
                "WILLY_GATEWAY_PENDING_REGISTRATION_TTL_S",
                maximum=31_536_000,
            ),
            registration_requests_per_window=_positive_int(
                os.environ.get("WILLY_GATEWAY_REGISTRATION_REQUESTS_PER_WINDOW", "10"),
                "WILLY_GATEWAY_REGISTRATION_REQUESTS_PER_WINDOW",
                maximum=10_000,
            ),
            registration_rate_window_s=_positive_int(
                os.environ.get("WILLY_GATEWAY_REGISTRATION_RATE_WINDOW_S", "3600"),
                "WILLY_GATEWAY_REGISTRATION_RATE_WINDOW_S",
                maximum=86_400,
            ),
            bind_host=os.environ.get("WILLY_GATEWAY_BIND_HOST", "127.0.0.1"),
            bind_port=_positive_int(os.environ.get("WILLY_GATEWAY_BIND_PORT", "8787"), "WILLY_GATEWAY_BIND_PORT"),
            tls_certfile=Path(value) if (value := os.environ.get("WILLY_GATEWAY_TLS_CERTFILE", "").strip()) else None,
            tls_keyfile=Path(value) if (value := os.environ.get("WILLY_GATEWAY_TLS_KEYFILE", "").strip()) else None,
            admin_secret=os.environ.get("WILLY_GATEWAY_ADMIN_SECRET", "").strip() or None,
            admin_port=_positive_int(os.environ.get("WILLY_GATEWAY_ADMIN_PORT", "8788"), "WILLY_GATEWAY_ADMIN_PORT"),
        )
