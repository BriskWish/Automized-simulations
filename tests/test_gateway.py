"""Deterministic contract tests for the private managed-LLM gateway slice."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
import sqlite3
import time

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from willy_gateway.app import create_app
from willy_gateway.config import GatewaySettings
from willy_gateway.security import canonical_device_request, issue_access_token, public_key_fingerprint
from willy_gateway.store import DeviceGrant, GatewayStore
from willy_gateway.upstream import UpstreamResponse, UpstreamTimeout


class FakeUpstream:
    def __init__(self, response: UpstreamResponse | None = None):
        self.calls: list[dict[str, object]] = []
        self.response = response or UpstreamResponse(
            200,
            {
                "id": "chatcmpl-fake",
                "model": "internal-deployment",
                "choices": [{"index": 0, "message": {"role": "assistant", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "tools_lookup_molecule", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
            },
        )

    async def chat_completions(self, payload):
        self.calls.append(dict(payload))
        return self.response


def _call(app, method: str, path: str, *, headers=None, payload=None):
    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
            return await client.request(method, path, headers=headers, json=payload)

    return asyncio.run(run())


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _gateway(tmp_path: Path, *, upstream: FakeUpstream | None = None):
    fake = upstream or FakeUpstream()
    settings = GatewaySettings(
        database_path=tmp_path / "gateway.sqlite3",
        token_secret=b"gateway-test-secret-should-never-ship"[:32],
        upstream_base_url="http://upstream.invalid/v1",
        upstream_api_key="provider-secret-not-in-responses",
        model_aliases={"willy-default": "internal-deployment"},
    )
    store = GatewayStore(settings.database_path)
    app = create_app(settings, store=store, upstream=fake)
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    registration = _call(
        app,
        "POST",
        "/api/v1/registrations",
        payload={"public_key": _b64(public_key), "device_label": "test device", "client_version": "0.1.0"},
    )
    assert registration.status_code == 202, registration.text
    device_id = registration.json()["device_id"]
    store.approve_device(device_id, DeviceGrant(device_id, ("willy-default",), None, 100_000, 500_000, 2, 20))
    return app, store, fake, private_key, device_id


def _token(app, private_key: Ed25519PrivateKey, device_id: str, *, nonce: str = "nonce-0123456789"):
    body = b"{}"
    timestamp = int(time.time())
    signature = _b64(private_key.sign(canonical_device_request("POST", "/api/v1/device/token", timestamp, nonce, body)))
    return _call(
        app,
        "POST",
        "/api/v1/device/token",
        headers={"content-type": "application/json", "x-willy-device-id": device_id, "x-willy-timestamp": str(timestamp), "x-willy-nonce": nonce, "x-willy-signature": signature},
        payload={},
    )


def test_gateway_default_auto_approval_and_grant_limits_are_configured(tmp_path):
    settings = GatewaySettings(
        database_path=tmp_path / "gateway.sqlite3",
        token_secret=b"gateway-test-secret-should-never-ship"[:32],
        upstream_base_url="http://upstream.invalid/v1",
        upstream_api_key="secret",
        model_aliases={"willy-default": "internal"},
    )

    assert settings.auto_approve_limit == 50
    assert settings.default_daily_token_limit == 1_000_000
    assert settings.default_monthly_token_limit == 10_000_000
    assert settings.default_max_concurrency == 2
    assert settings.default_max_requests_per_minute == 20


def test_managed_flow_forwards_alias_and_tool_calls_without_prompt_persistence(tmp_path):
    app, store, upstream, private_key, device_id = _gateway(tmp_path)
    assert _call(app, "GET", "/healthz").json() == {"status": "ok"}
    token_response = _token(app, private_key, device_id)
    assert token_response.status_code == 200
    token = token_response.json()["access_token"]
    models = _call(app, "GET", "/v1/models", headers={"authorization": f"Bearer {token}"})
    assert [item["id"] for item in models.json()["data"]] == ["willy-default"]
    prompt = "private prompt must not enter the gateway ledger"
    response = _call(
        app,
        "POST",
        "/v1/chat/completions",
        headers={"authorization": f"Bearer {token}"},
        payload={"model": "willy-default", "messages": [{"role": "user", "content": prompt}], "tools": [{"type": "function", "function": {"name": "tools_lookup_molecule", "parameters": {}}}], "max_tokens": 32},
    )
    assert response.status_code == 200
    assert response.json()["model"] == "willy-default"
    assert response.json()["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "tools_lookup_molecule"
    assert upstream.calls[0]["model"] == "internal-deployment"
    assert "provider-secret-not-in-responses" not in response.text
    usage = _call(app, "GET", "/api/v1/me/usage", headers={"authorization": f"Bearer {token}"}).json()
    assert usage["daily"]["settled_tokens"] == 12
    assert usage["daily"]["reserved_tokens"] == 0
    with sqlite3.connect(store.path) as connection:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        dump = " ".join(str(row) for table in tables for row in connection.execute(f"SELECT * FROM {table}"))
    assert prompt not in dump


def test_device_signature_nonce_replay_and_revocation_are_denied(tmp_path):
    app, store, _, private_key, device_id = _gateway(tmp_path)
    first = _token(app, private_key, device_id)
    assert first.status_code == 200
    replay = _token(app, private_key, device_id)
    assert replay.status_code == 401
    token = first.json()["access_token"]
    store.revoke_device(device_id)
    revoked = _call(app, "GET", "/v1/models", headers={"authorization": f"Bearer {token}"})
    assert revoked.status_code == 401
    assert "device" not in revoked.text.lower()


def test_token_request_body_tampering_and_expiry_are_denied(tmp_path):
    app, store, _, private_key, device_id = _gateway(tmp_path)
    timestamp = int(time.time())
    nonce = "nonce-tampered-1234"
    signature = _b64(private_key.sign(canonical_device_request("POST", "/api/v1/device/token", timestamp, nonce, b"{\"signed\":true}")))
    tampered = _call(
        app, "POST", "/api/v1/device/token",
        headers={"content-type": "application/json", "x-willy-device-id": device_id, "x-willy-timestamp": str(timestamp), "x-willy-nonce": nonce, "x-willy-signature": signature},
        payload={},
    )
    assert tampered.status_code == 401
    device = store.get_device(device_id)
    grant = store.active_grant_for_device(device_id)
    expired, _ = issue_access_token(
        b"gateway-test-secret-should-never-ship"[:32], device_id=device_id,
        key_fingerprint=device.key_fingerprint, model_aliases=list(grant.model_aliases), now_s=int(time.time()) - 20, ttl_s=1,
    )
    response = _call(app, "GET", "/v1/models", headers={"authorization": f"Bearer {expired}"})
    assert response.status_code == 401


def test_upstream_failure_is_redacted_and_timeout_keeps_reservation(tmp_path):
    failing = FakeUpstream(UpstreamResponse(500, {"error": "provider-secret-internal-stack"}))
    app, _, upstream, private_key, device_id = _gateway(tmp_path, upstream=failing)
    token = _token(app, private_key, device_id).json()["access_token"]
    headers = {"authorization": f"Bearer {token}"}
    response = _call(app, "POST", "/v1/chat/completions", headers=headers, payload={"model": "willy-default", "messages": [{"role": "user", "content": "x"}]})
    assert response.status_code == 502
    assert "provider-secret-internal-stack" not in response.text
    assert upstream.calls

    class TimeoutUpstream(FakeUpstream):
        async def chat_completions(self, payload):
            self.calls.append(dict(payload))
            raise UpstreamTimeout("private provider detail")

    timeout_upstream = TimeoutUpstream()
    timeout_app, _, _, timeout_key, timeout_device_id = _gateway(tmp_path / "timeout", upstream=timeout_upstream)
    timeout_token = _token(timeout_app, timeout_key, timeout_device_id).json()["access_token"]
    timed_out = _call(timeout_app, "POST", "/v1/chat/completions", headers={"authorization": f"Bearer {timeout_token}"}, payload={"model": "willy-default", "messages": [{"role": "user", "content": "x"}]})
    assert timed_out.status_code == 504
    usage = _call(timeout_app, "GET", "/api/v1/me/usage", headers={"authorization": f"Bearer {timeout_token}"}).json()
    assert usage["daily"]["reserved_tokens"] > 0


def test_model_allowlist_quota_and_request_policy_prevent_proxy_abuse(tmp_path):
    app, store, upstream, private_key, device_id = _gateway(tmp_path)
    store.approve_device(device_id, DeviceGrant(device_id, ("willy-default",), None, 10, 10, 2, 20))
    token = _token(app, private_key, device_id, nonce="nonce-abcdefghijkl").json()["access_token"]
    headers = {"authorization": f"Bearer {token}"}
    unknown_model = _call(app, "POST", "/v1/chat/completions", headers=headers, payload={"model": "https://evil", "messages": [{"role": "user", "content": "x"}]})
    assert unknown_model.status_code == 403
    streaming = _call(app, "POST", "/v1/chat/completions", headers=headers, payload={"model": "willy-default", "messages": [{"role": "user", "content": "x"}], "stream": True})
    assert streaming.status_code == 400
    quota = _call(app, "POST", "/v1/chat/completions", headers=headers, payload={"model": "willy-default", "messages": [{"role": "user", "content": "x"}], "max_tokens": 10})
    assert quota.status_code == 429
    assert upstream.calls == []


def test_self_service_registration_uses_server_counted_slots_and_reuses_the_same_public_key(tmp_path):
    settings = GatewaySettings(
        database_path=tmp_path / "gateway.sqlite3", token_secret=b"gateway-test-secret-should-never-ship"[:32],
        upstream_base_url="http://upstream.invalid/v1", upstream_api_key="secret",
        model_aliases={"willy-default": "internal"}, registration_limit=1, auto_approve_limit=0,
    )
    store = GatewayStore(settings.database_path)
    app = create_app(settings, store=store, upstream=FakeUpstream())
    key = Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    payload = {"public_key": _b64(key), "device_label": "test", "client_version": "0.1"}
    first = _call(app, "POST", "/api/v1/registrations", payload=payload)
    assert first.status_code == 202
    duplicate = _call(app, "POST", "/api/v1/registrations", payload=payload)
    assert duplicate.status_code == 202
    assert duplicate.json()["device_id"] == first.json()["device_id"]

    another = Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    full = _call(
        app,
        "POST",
        "/api/v1/registrations",
        payload={"public_key": _b64(another), "device_label": "another", "client_version": "0.1"},
    )
    assert full.status_code == 429
    assert full.json()["error"]["code"] == "registration_limit_reached"

    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE devices SET pending_expires_at = ?", ("2000-01-01T00:00:00+00:00",))
    released = _call(
        app,
        "POST",
        "/api/v1/registrations",
        payload={"public_key": _b64(another), "device_label": "another", "client_version": "0.1"},
    )
    assert released.status_code == 202


def test_first_registered_devices_are_auto_approved_with_default_grants(tmp_path):
    settings = GatewaySettings(
        database_path=tmp_path / "gateway.sqlite3",
        token_secret=b"gateway-test-secret-should-never-ship"[:32],
        upstream_base_url="http://upstream.invalid/v1",
        upstream_api_key="secret",
        model_aliases={"willy-default": "internal", "willy-fast": "internal-fast"},
        registration_limit=3,
        auto_approve_limit=2,
    )
    store = GatewayStore(settings.database_path)
    app = create_app(settings, store=store, upstream=FakeUpstream())

    def register(label: str):
        key = Ed25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        return _call(
            app,
            "POST",
            "/api/v1/registrations",
            payload={"public_key": _b64(key), "device_label": label, "client_version": "0.1"},
        )

    first, second, third = register("first"), register("second"), register("third")
    assert [response.json()["status"] for response in (first, second, third)] == ["approved", "approved", "pending"]
    grant = store.active_grant_for_device(first.json()["device_id"])
    assert grant.model_aliases == ("willy-default",)
    assert grant.daily_token_limit == 1_000_000
    assert grant.monthly_token_limit == 10_000_000
    assert grant.max_concurrency == 2
    assert grant.max_requests_per_minute == 20
    snapshot = store.admin_snapshot(registration_limit=3, auto_approve_limit=2)["summary"]
    assert snapshot["auto_approve_window_used"] == 2
    assert snapshot["auto_approved_devices"] == 2
    assert snapshot["auto_approve_slots_available"] == 0


def test_existing_manual_registration_consumes_the_automatic_approval_window(tmp_path):
    settings = GatewaySettings(
        database_path=tmp_path / "gateway.sqlite3",
        token_secret=b"gateway-test-secret-should-never-ship"[:32],
        upstream_base_url="http://upstream.invalid/v1",
        upstream_api_key="secret",
        model_aliases={"willy-default": "internal"},
        registration_limit=3,
        auto_approve_limit=0,
    )
    store = GatewayStore(settings.database_path)
    manual_app = create_app(settings, store=store, upstream=FakeUpstream())

    def payload(label: str):
        key = Ed25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        return {"public_key": _b64(key), "device_label": label, "client_version": "0.1"}

    assert _call(manual_app, "POST", "/api/v1/registrations", payload=payload("manual")).json()["status"] == "pending"
    auto_settings = GatewaySettings(
        database_path=settings.database_path,
        token_secret=settings.token_secret,
        upstream_base_url=settings.upstream_base_url,
        upstream_api_key=settings.upstream_api_key,
        model_aliases=settings.model_aliases,
        registration_limit=3,
        auto_approve_limit=1,
    )
    auto_app = create_app(auto_settings, store=store, upstream=FakeUpstream())

    second = _call(auto_app, "POST", "/api/v1/registrations", payload=payload("second"))

    assert second.json()["status"] == "pending"
    snapshot = store.admin_snapshot(registration_limit=3, auto_approve_limit=1)["summary"]
    assert snapshot["auto_approve_window_used"] == 1
    assert snapshot["auto_approved_devices"] == 0
    assert snapshot["auto_approve_slots_available"] == 0


def test_self_service_registration_is_rate_limited_before_it_can_fill_slots(tmp_path):
    settings = GatewaySettings(
        database_path=tmp_path / "gateway.sqlite3",
        token_secret=b"gateway-test-secret-should-never-ship"[:32],
        upstream_base_url="http://upstream.invalid/v1",
        upstream_api_key="secret",
        model_aliases={"willy-default": "internal"},
        registration_limit=3,
        registration_requests_per_window=1,
        registration_rate_window_s=3_600,
    )
    app = create_app(settings, upstream=FakeUpstream())

    def request_for_key():
        key = Ed25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return _call(
            app,
            "POST",
            "/api/v1/registrations",
            payload={"public_key": _b64(key), "device_label": "test", "client_version": "0.1"},
        )

    assert request_for_key().status_code == 202
    limited = request_for_key()
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "registration_rate_limited"


def test_gateway_startup_assigns_expiry_to_legacy_pending_devices(tmp_path):
    settings = GatewaySettings(
        database_path=tmp_path / "gateway.sqlite3",
        token_secret=b"gateway-test-secret-should-never-ship"[:32],
        upstream_base_url="http://upstream.invalid/v1",
        upstream_api_key="secret",
        model_aliases={"willy-default": "internal"},
    )
    store = GatewayStore(settings.database_path)
    store.initialize()
    key = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    with store._transaction() as connection:
        connection.execute(
            """INSERT INTO devices(device_id, public_key, key_fingerprint, status, label, client_version, created_at)
            VALUES (?, ?, ?, 'pending', ?, ?, ?)""",
            ("a" * 32, key, public_key_fingerprint(key), "legacy", "0.0", "2000-01-01T00:00:00+00:00"),
        )
    create_app(settings, store=store, upstream=FakeUpstream())
    with sqlite3.connect(store.path) as connection:
        expires_at = connection.execute("SELECT pending_expires_at FROM devices WHERE device_id = ?", ("a" * 32,)).fetchone()[0]
    assert expires_at is not None
