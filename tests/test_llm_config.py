"""OpenAI-compatible LLM configuration and credential-boundary tests."""

from __future__ import annotations

import pytest

from willy.llm_config import (
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    LLMConfigError,
    LLMSettings,
    configured_llm_client,
    create_openai_client,
    load_llm_settings,
    load_llm_provider_mode,
    validate_llm_values,
)


def test_no_key_means_llm_is_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("WILLY_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    assert load_llm_settings(tmp_path) is None


def test_legacy_deepseek_key_uses_existing_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("WILLY_LLM_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "legacy-secret")

    settings = load_llm_settings(tmp_path)

    assert settings is not None
    assert settings.base_url == DEFAULT_LLM_BASE_URL
    assert settings.model == DEFAULT_LLM_MODEL
    assert settings.legacy_key is True
    assert settings.source == "environment"
    assert "legacy-secret" not in settings.public_dict().values()


def test_generic_values_override_legacy_and_read_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "WILLY_LLM_API_KEY=dotenv-secret\n"
        "WILLY_LLM_BASE_URL=http://localhost:8000/v1\n"
        "WILLY_LLM_MODEL=local-model\n"
        "DEEPSEEK_API_KEY=legacy-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("WILLY_LLM_API_KEY", "environment-secret")
    monkeypatch.delenv("WILLY_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("WILLY_LLM_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    settings = load_llm_settings(tmp_path)

    assert settings is not None
    assert settings.api_key == "environment-secret"
    assert settings.base_url == "http://localhost:8000/v1"
    assert settings.model == "local-model"
    assert settings.legacy_key is False
    assert settings.source == "environment"


@pytest.mark.parametrize(
    ("base_url", "model"),
    [
        ("not-a-url", "model"),
        ("ftp://example.com", "model"),
        ("https://example.com?token=x", "model"),
        ("https://example.com", "model\nother"),
    ],
)
def test_invalid_values_are_rejected_without_client_construction(base_url, model):
    with pytest.raises(LLMConfigError):
        validate_llm_values("secret", base_url, model)


def test_configured_client_uses_resolved_endpoint_and_model(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLY_LLM_API_KEY", "test-secret")
    monkeypatch.setenv("WILLY_LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("WILLY_LLM_MODEL", "compatible-model")
    captured = {}

    import willy.llm_config as llm_config

    def fake_client(settings):
        captured["settings"] = settings
        return object()

    monkeypatch.setattr(llm_config, "create_openai_client", fake_client)
    client, settings = configured_llm_client(tmp_path)

    assert client is not None
    assert settings is captured["settings"]
    assert settings.model == "compatible-model"
    assert settings.base_url == "https://example.test/v1"


def test_stale_managed_mode_cannot_route_to_gateway_and_falls_back_to_local_key(tmp_path, monkeypatch):
    (tmp_path / "managed_gateway.json").write_text(
        '{"schema_version":1,"profile_id":"home-gateway","label":"Home gateway","base_url":"https://gateway.example.test","model":"willy-default"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("WILLY_LLM_MODE", "managed")
    monkeypatch.setenv("WILLY_LLM_API_KEY", "must-not-be-used")

    assert load_llm_provider_mode(tmp_path) == "byok"
    settings = load_llm_settings(tmp_path)
    assert settings is not None
    assert settings.provider_mode == "byok"
    assert settings.api_key == "must-not-be-used"
    assert settings.base_url == DEFAULT_LLM_BASE_URL
    assert settings.source == "environment"


def test_manually_constructed_managed_settings_cannot_create_gateway_client():
    archived = LLMSettings(
        api_key="",
        base_url="https://gateway.example.test/v1",
        model="willy-default",
        source="managed",
        provider_mode="managed",
    )

    with pytest.raises(LLMConfigError, match="托管网关已冻结"):
        create_openai_client(archived)


def test_invalid_llm_mode_is_rejected_before_any_client_is_constructed(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLY_LLM_MODE", "somewhere-else")

    with pytest.raises(LLMConfigError, match="模式"):
        load_llm_provider_mode(tmp_path)
