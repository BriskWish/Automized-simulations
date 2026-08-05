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

from dataclasses import dataclass
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
LEGACY_DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"


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
    source: Literal["environment", "dotenv", "form"]
    legacy_key: bool = False

    def public_dict(self) -> dict[str, str | bool]:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "source": self.source,
            "legacy_key": self.legacy_key,
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


def load_llm_settings(project_root: str | Path | None = None) -> LLMSettings | None:
    """Resolve one OpenAI-compatible service, or ``None`` when no key exists."""
    root = Path(project_root) if project_root is not None else get_project_root()
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
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - dependency declared by project
        raise LLMConfigError("未安装 OpenAI Python SDK") from exc
    return OpenAI(api_key=settings.api_key, base_url=settings.base_url)


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
    return create_openai_client(settings), settings


def llm_form_defaults(project_root: str | Path | None = None) -> tuple[str, str]:
    """Return non-secret form defaults for the configuration UI."""
    settings = load_llm_settings(project_root)
    if settings is None:
        return DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL
    return settings.base_url, settings.model
