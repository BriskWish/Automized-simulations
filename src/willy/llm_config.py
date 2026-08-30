"""OpenAI-compatible LLM connection configuration.

This module is the single authority for the LLM endpoint, model, credential
lookup, and OpenAI client construction.  It intentionally does not contain
any simulation ``config.json`` validation; that responsibility lives in
``workflow_config.py``.

Environment precedence for every setting is process environment, then the
project-local ``.env``.  ``DEEPSEEK_API_KEY`` remains a read-only legacy
fallback so existing deployments continue to work after the migration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
import os

from willy._paths import get_project_root
from willy.env_registry import dotenv_value


DEFAULT_LLM_BASE_URL = "https://api.deepseek.com"
DEFAULT_LLM_MODEL = "deepseek-v4-pro"
LLM_API_KEY_ENV = "WILLY_LLM_API_KEY"
LLM_BASE_URL_ENV = "WILLY_LLM_BASE_URL"
LLM_MODEL_ENV = "WILLY_LLM_MODEL"
LLM_PROVIDER_MODE_ENV = "WILLY_LLM_MODE"
LEGACY_DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"
# The managed gateway protocol is retained as an archived implementation, but
# this release deliberately exposes only the local BYOK provider.
MANAGED_GATEWAY_ENABLED = False


class LLMConfigError(ValueError):
    """Raised when a configured OpenAI-compatible endpoint is invalid."""


@dataclass(frozen=True)
class LLMSettings:
    """Resolved settings for one OpenAI-compatible chat-completions service.

    ``api_key`` is intentionally retained only in this in-memory object.  Its
    public status representation never includes it.
    """

    api_key: str
    base_url: str
    model: str
    source: Literal["environment", "dotenv", "form", "managed"]
    legacy_key: bool = False
    provider_mode: Literal["byok", "managed"] = "byok"
    managed_profile: object | None = field(default=None, repr=False, compare=False)

    def public_dict(self) -> dict[str, str | bool]:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "source": self.source,
            "legacy_key": self.legacy_key,
            "provider_mode": self.provider_mode,
        }


def _configured_value(name: str, project_root: str | Path | None) -> tuple[str, str]:
    """Resolve one whitelisted setting and report only its non-secret source."""
    value = os.environ.get(name, "").strip()
    if value:
        return value, "environment"
    value = dotenv_value(name, project_root).strip()
    return (value, "dotenv") if value else ("", "")


def _validate_single_line(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise LLMConfigError(f"{label} 必须是文本")
    cleaned = value.strip()
    if not cleaned or "\n" in cleaned or "\r" in cleaned:
        raise LLMConfigError(f"{label} 不能为空且不能包含换行")
    return cleaned


def validate_llm_values(api_key: str, base_url: str, model: str) -> tuple[str, str, str]:
    """Validate UI or environment values without constructing a network client."""
    key = _validate_single_line(api_key, "API Key")
    url = _validate_single_line(base_url, "Base URL").rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LLMConfigError("Base URL 必须是完整的 http(s) 地址")
    if parsed.query or parsed.fragment:
        raise LLMConfigError("Base URL 不能包含查询参数或片段")
    resolved_model = _validate_single_line(model, "Model")
    return key, url, resolved_model


def load_llm_provider_mode(project_root: str | Path | None = None) -> Literal["byok"]:
    """Resolve the local provider mode.

    ``managed`` is retained as an archived selector for old ``.env`` files.
    In the frozen release it is ignored and safely falls back to local BYOK;
    no gateway profile is read and no file is rewritten here.
    """
    value, _ = _configured_value(LLM_PROVIDER_MODE_ENV, project_root)
    mode = value.lower() if value else "byok"
    if mode == "managed" and not MANAGED_GATEWAY_ENABLED:
        return "byok"
    if mode != "byok":
        raise LLMConfigError("LLM 模式只能是 byok")
    return "byok"


def load_llm_settings(project_root: str | Path | None = None) -> LLMSettings | None:
    """Resolve one OpenAI-compatible service, or ``None`` when no key exists."""
    root = Path(project_root) if project_root is not None else get_project_root()
    mode = load_llm_provider_mode(root)
    if mode == "managed":  # pragma: no cover - reserved for a future release
        from willy.managed_gateway import ManagedGatewayError, load_managed_gateway_profile

        try:
            profile = load_managed_gateway_profile(root)
        except ManagedGatewayError as exc:
            raise LLMConfigError("托管网关描述不可用，请联系部署者。") from exc
        return LLMSettings(
            api_key="",
            base_url=f"{profile.base_url}/v1",
            model=profile.model,
            source="managed",
            provider_mode="managed",
            managed_profile=profile,
        )
    api_key, source = _configured_value(LLM_API_KEY_ENV, root)
    legacy_key = False
    if not api_key:
        api_key, source = _configured_value(LEGACY_DEEPSEEK_API_KEY_ENV, root)
        legacy_key = bool(api_key)
    if not api_key:
        return None

    base_url, _ = _configured_value(LLM_BASE_URL_ENV, root)
    model, _ = _configured_value(LLM_MODEL_ENV, root)
    key, url, resolved_model = validate_llm_values(
        api_key,
        base_url or DEFAULT_LLM_BASE_URL,
        model or DEFAULT_LLM_MODEL,
    )
    return LLMSettings(
        api_key=key,
        base_url=url,
        model=resolved_model,
        source=source,  # source describes the credential, never its value.
        legacy_key=legacy_key,
    )


def create_openai_client(settings: LLMSettings):
    """Construct the SDK client for a resolved OpenAI-compatible endpoint."""
    if settings.provider_mode == "managed":
        if not MANAGED_GATEWAY_ENABLED:
            raise LLMConfigError("当前版本仅支持本地 API Key，托管网关已冻结")
        from willy.managed_gateway import ManagedGatewayError, create_managed_openai_client

        if settings.managed_profile is None:
            raise LLMConfigError("托管网关描述不可用，请联系部署者。")
        try:
            return create_managed_openai_client(
                settings.managed_profile,  # type: ignore[arg-type]
                lambda token: _create_sdk_client(token, settings.base_url),
            )
        except ManagedGatewayError as exc:
            raise LLMConfigError("托管设备身份不可用，请重新注册。") from exc
    return _create_sdk_client(settings.api_key, settings.base_url)


def _create_sdk_client(api_key: str, base_url: str):
    """Construct one raw SDK client; managed callers receive fresh credentials."""
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - dependency declared by project
        raise LLMConfigError("未安装 OpenAI Python SDK") from exc
    except Exception as exc:
        raise LLMConfigError(
            "OpenAI Python SDK 或其传输依赖不兼容，请使用项目虚拟环境重新安装依赖"
        ) from exc
    try:
        return OpenAI(api_key=api_key, base_url=base_url)
    except Exception as exc:
        raise LLMConfigError(
            "OpenAI Python SDK 初始化失败，请使用项目虚拟环境重新安装依赖"
        ) from exc


def form_llm_settings(api_key: str, base_url: str, model: str) -> LLMSettings:
    """Build ephemeral settings from form values without reading or writing .env."""
    key, url, resolved_model = validate_llm_values(api_key, base_url, model)
    return LLMSettings(
        api_key=key,
        base_url=url,
        model=resolved_model,
        source="form",
    )


def configured_llm_client(project_root: str | Path | None = None):
    """Return ``(client, settings)`` or ``(None, None)`` when not configured."""
    settings = load_llm_settings(project_root)
    if settings is None:
        return None, None
    if settings.provider_mode == "managed":
        if not MANAGED_GATEWAY_ENABLED:
            raise LLMConfigError("当前版本仅支持本地 API Key，托管网关已冻结")
        from willy.managed_gateway import managed_identity_status

        if settings.managed_profile is None or managed_identity_status(settings.managed_profile) == "not_registered":
            return None, None
    return create_openai_client(settings), settings


def llm_form_defaults(project_root: str | Path | None = None) -> tuple[str, str]:
    """Return non-secret form defaults for the configuration UI."""
    try:
        settings = load_llm_settings(project_root)
    except LLMConfigError:
        return DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL
    if settings is None:
        return DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL
    return settings.base_url, settings.model
