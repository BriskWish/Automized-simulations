"""Managed-client identity and token-refreshing protocol contracts."""

from __future__ import annotations

import asyncio
import base64
import stat

import httpx
import pytest

from willy.managed_gateway import (
    ManagedGatewayError,
    ManagedGatewayProfile,
    ManagedGatewaySession,
    ManagedIdentityStore,
    create_managed_openai_client,
    load_managed_gateway_profile,
    managed_gateway_usage_snapshot,
    managed_identity_status,
    request_managed_registration,
)
from willy_gateway.app import create_app
from willy_gateway.config import GatewaySettings
from willy_gateway.security import verify_access_token, verify_device_signature
from willy_gateway.store import GatewayStore


def _profile() -> ManagedGatewayProfile:
    return ManagedGatewayProfile("home-gateway", "Home gateway", "https://gateway.example.test", "willy-default")


def test_profile_is_fixed_to_https_and_cannot_accept_a_remote_plain_http_endpoint(tmp_path):
    (tmp_path / "managed_gateway.json").write_text(
        '{"schema_version":1,"profile_id":"home-gateway","label":"Home gateway","base_url":"http://192.168.1.8:8787","model":"willy-default"}',
        encoding="utf-8",
    )
    with pytest.raises(ManagedGatewayError, match="requires_https"):
        load_managed_gateway_profile(tmp_path)


def test_identity_status_does_not_create_a_private_key_until_managed_mode_is_selected(tmp_path):
    store = ManagedIdentityStore(tmp_path / "identity.json")

    assert managed_identity_status(_profile(), store=store) == "not_registered"
    assert not store.path.exists()


def test_registration_sends_only_a_public_key_and_persists_a_private_machine_identity(tmp_path):
    profile = _profile()
    store = ManagedIdentityStore(tmp_path / "identity" / "device.json")
    captured = {}

    def gateway_request(method, received_profile, path, **kwargs):
        captured.update({"method": method, "profile": received_profile, "path": path, **kwargs})
        return {"device_id": "a" * 32, "status": "pending"}

    result = request_managed_registration(profile, store=store, request=gateway_request)

    assert result == "pending"
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/registrations"
    assert "invitation_code" not in captured["payload"]
    assert "private_key" not in captured["payload"]
    public_key = base64.urlsafe_b64decode(captured["payload"]["public_key"] + "==")
    assert len(public_key) == 32
    identity = store.load_or_create(profile.profile_id)
    assert identity.device_id == "a" * 32
    assert base64.urlsafe_b64encode(identity.private_key).decode("ascii") not in str(captured)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700


def test_token_exchange_is_signed_cached_and_refreshed_before_expiry(tmp_path):
    profile = _profile()
    store = ManagedIdentityStore(tmp_path / "identity.json")
    store.save(store.load_or_create(profile.profile_id))
    identity = store.load_or_create(profile.profile_id)
    store.save(type(identity)(identity.profile_id, identity.private_key, "b" * 32))
    current = [1_000.0]
    token_calls = []

    def gateway_request(method, received_profile, path, **kwargs):
        assert method == "POST"
        assert received_profile == profile
        assert path == "/api/v1/device/token"
        headers = kwargs["headers"]
        assert kwargs["content"] == b"{}"
        verify_device_signature(
            identity.public_key_bytes(),
            method="POST",
            path=path,
            timestamp=int(headers["X-Willy-Timestamp"]),
            nonce=headers["X-Willy-Nonce"],
            body=kwargs["content"],
            signature=headers["X-Willy-Signature"],
        )
        token_calls.append(headers)
        return {"access_token": f"short-token-{len(token_calls)}", "expires_in": 60, "token_type": "Bearer"}

    session = ManagedGatewaySession(profile, store=store, request=gateway_request, clock=lambda: current[0])
    assert session.access_token() == "short-token-1"
    current[0] += 20
    assert session.access_token() == "short-token-1"
    current[0] += 11
    assert session.access_token() == "short-token-2"
    assert len(token_calls) == 2
    assert token_calls[0]["X-Willy-Nonce"] != token_calls[1]["X-Willy-Nonce"]


def test_usage_snapshot_checks_the_gateway_without_calling_chat_completions(tmp_path):
    profile = _profile()
    store = ManagedIdentityStore(tmp_path / "identity.json")
    identity = store.load_or_create(profile.profile_id)
    store.save(type(identity)(identity.profile_id, identity.private_key, "d" * 32))
    calls = []

    def gateway_request(method, received_profile, path, **kwargs):
        calls.append((method, path, kwargs))
        assert received_profile == profile
        if path == "/api/v1/device/token":
            return {"access_token": "usage-check-token", "expires_in": 60}
        assert method == "GET"
        assert path == "/api/v1/me/usage"
        assert kwargs["headers"] == {"Authorization": "Bearer usage-check-token"}
        return {
            "daily": {"settled_tokens": 10, "reserved_tokens": 2, "limit": 1_000_000},
            "monthly": {"settled_tokens": 10, "reserved_tokens": 2, "limit": 10_000_000},
        }

    snapshot = managed_gateway_usage_snapshot(profile, store=store, request=gateway_request)

    assert snapshot["daily"]["limit"] == 1_000_000
    assert [path for _method, path, _kwargs in calls] == ["/api/v1/device/token", "/api/v1/me/usage"]


def test_managed_client_gets_a_token_for_each_expired_openai_call(tmp_path, monkeypatch):
    profile = _profile()
    store = ManagedIdentityStore(tmp_path / "identity.json")
    identity = store.load_or_create(profile.profile_id)
    store.save(type(identity)(identity.profile_id, identity.private_key, "c" * 32))
    calls = []

    def gateway_request(*_args, **_kwargs):
        calls.append("token")
        return {"access_token": f"short-token-{len(calls)}", "expires_in": 60}

    import willy.managed_gateway as managed_gateway

    monkeypatch.setattr(
        managed_gateway,
        "ManagedGatewaySession",
        lambda received_profile: ManagedGatewaySession(received_profile, store=store, request=gateway_request, clock=lambda: 1_000),
    )
    issued_tokens = []

    class RawClient:
        def __init__(self, token):
            self.chat = type("Chat", (), {"completions": type("Completions", (), {"create": lambda _self, **kwargs: (token, kwargs)})()})()

    client = create_managed_openai_client(profile, lambda token: issued_tokens.append(token) or RawClient(token))
    first = client.chat.completions.create(model="willy-default", messages=[])
    second = client.chat.completions.create(model="willy-default", messages=[])
    assert first[0] == second[0] == "short-token-1"
    assert issued_tokens == ["short-token-1", "short-token-1"]
    assert calls == ["token"]


def test_client_registration_and_signed_token_exchange_reach_the_real_gateway_asgi_app(tmp_path):
    settings = GatewaySettings(
        database_path=tmp_path / "gateway.sqlite3",
        token_secret=b"gateway-test-secret-should-never-ship"[:32],
        upstream_base_url="http://upstream.invalid/v1",
        upstream_api_key="provider-secret",
        model_aliases={"willy-default": "internal-deployment"},
    )
    store = GatewayStore(settings.database_path)
    gateway = create_app(settings, store=store)
    profile = ManagedGatewayProfile("home-gateway", "Home gateway", "http://localhost", "willy-default")
    identity_store = ManagedIdentityStore(tmp_path / "client-identity.json")

    def asgi_gateway_request(method, _profile, path, **kwargs):
        async def run():
            transport = httpx.ASGITransport(app=gateway)
            async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
                response = await client.request(
                    method,
                    path,
                    content=kwargs.get("content"),
                    json=kwargs.get("payload"),
                    headers=kwargs.get("headers"),
                )
            assert 200 <= response.status_code < 300, response.text
            return response.json()

        return asyncio.run(run())

    assert request_managed_registration(profile, store=identity_store, request=asgi_gateway_request) == "approved"
    assert request_managed_registration(profile, store=identity_store, request=asgi_gateway_request) == "approved"
    identity = identity_store.load_or_create(profile.profile_id)
    assert identity.device_id is not None
    token = ManagedGatewaySession(profile, store=identity_store, request=asgi_gateway_request).access_token()
    claims = verify_access_token(settings.token_secret, token)
    assert claims["device_id"] == identity.device_id
    assert claims["models"] == ["willy-default"]
