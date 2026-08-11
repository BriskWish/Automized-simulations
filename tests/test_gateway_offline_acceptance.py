"""Offline acceptance gate for the private managed gateway.

The fake upstream below is an in-memory scripted event trace, not an SSE
server.  The gateway intentionally rejects ``stream=true`` in this release.
"""

from __future__ import annotations

import asyncio
import base64
from collections import deque
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import time

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from willy_gateway.app import create_app
from willy_gateway.config import GatewaySettings
from willy_gateway.security import canonical_device_request
import willy_gateway.store as gateway_store_module
from willy_gateway.store import DeviceGrant, GatewayStore
from willy_gateway.upstream import UpstreamResponse, UpstreamTimeout


pytestmark = pytest.mark.gateway_acceptance

_TOKEN_SECRET = b"offline-gateway-acceptance-secret"[:32]
_UPSTREAM_KEY = "offline-upstream-key-must-not-be-persisted"


class FakeUpstreamStream:
    """Scripted in-memory upstream outcomes and a non-sensitive event trace."""

    def __init__(self, *outcomes: UpstreamResponse | BaseException):
        self._outcomes = deque(outcomes)
        self.calls: list[dict[str, object]] = []
        self.events: list[str] = []

    async def chat_completions(self, payload):
        self.calls.append(dict(payload))
        self.events.append("request_received")
        if not self._outcomes:
            raise AssertionError("fake upstream script exhausted")
        outcome = self._outcomes.popleft()
        if isinstance(outcome, BaseException):
            self.events.append("request_timed_out" if isinstance(outcome, UpstreamTimeout) else "request_failed")
            raise outcome
        self.events.append("response_returned")
        return outcome


def _call(app, method: str, path: str, *, headers=None, payload=None):
    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
            return await client.request(method, path, headers=headers, json=payload)

    return asyncio.run(run())


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _acceptance_fixture(tmp_path: Path, upstream: FakeUpstreamStream, *, grant: DeviceGrant):
    settings = GatewaySettings(
        database_path=tmp_path / "gateway.sqlite3",
        token_secret=_TOKEN_SECRET,
        upstream_base_url="http://upstream.invalid/v1",
        upstream_api_key=_UPSTREAM_KEY,
        model_aliases={"willy-default": "internal-deployment"},
        auto_approve_limit=0,
    )
    store = GatewayStore(settings.database_path)
    app = create_app(settings, store=store, upstream=upstream)
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    registration = _call(
        app,
        "POST",
        "/api/v1/registrations",
        payload={
            "public_key": _b64(public_key),
            "device_label": "offline acceptance device",
            "client_version": "acceptance-1.0",
        },
    )
    assert registration.status_code == 202, registration.text
    device_id = registration.json()["device_id"]
    assert registration.json()["status"] == "pending"
    store.approve_device(
        device_id,
        DeviceGrant(
            device_id=device_id,
            model_aliases=grant.model_aliases,
            expires_at=grant.expires_at,
            daily_token_limit=grant.daily_token_limit,
            monthly_token_limit=grant.monthly_token_limit,
            max_concurrency=grant.max_concurrency,
            max_requests_per_minute=grant.max_requests_per_minute,
        ),
    )
    return app, store, private_key, device_id


def _token(app, private_key: Ed25519PrivateKey, device_id: str, *, nonce: str) -> httpx.Response:
    body = b"{}"
    timestamp = int(time.time())
    signature = _b64(private_key.sign(
        canonical_device_request("POST", "/api/v1/device/token", timestamp, nonce, body)
    ))
    return _call(
        app,
        "POST",
        "/api/v1/device/token",
        headers={
            "content-type": "application/json",
            "x-willy-device-id": device_id,
            "x-willy-timestamp": str(timestamp),
            "x-willy-nonce": nonce,
            "x-willy-signature": signature,
        },
        payload={},
    )


def _chat(app, token: str, payload: dict[str, object]):
    return _call(app, "POST", "/v1/chat/completions", headers={"authorization": f"Bearer {token}"}, payload=payload)


def _usage(app, token: str):
    return _call(app, "GET", "/api/v1/me/usage", headers={"authorization": f"Bearer {token}"})


def _database_text(store: GatewayStore) -> str:
    with sqlite3.connect(store.path) as connection:
        rows = []
        for table, in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
            rows.extend(connection.execute(f'SELECT * FROM "{table}"').fetchall())
    return repr(rows)


def test_device_token_replay_and_revocation_are_enforced_offline(tmp_path):
    upstream = FakeUpstreamStream()
    app, store, private_key, device_id = _acceptance_fixture(
        tmp_path,
        upstream,
        grant=DeviceGrant(device_id="pending", model_aliases=("willy-default",), expires_at=None,
                          daily_token_limit=10_000, monthly_token_limit=10_000,
                          max_concurrency=2, max_requests_per_minute=20),
    )
    issued = _token(app, private_key, device_id, nonce="acceptance-token-001")
    assert issued.status_code == 200
    token = issued.json()["access_token"]
    assert _token(app, private_key, device_id, nonce="acceptance-token-001").status_code == 401
    assert _call(app, "GET", "/v1/models", headers={"authorization": f"Bearer {token}"}).status_code == 200

    store.revoke_device(device_id)
    revoked = _call(app, "GET", "/v1/models", headers={"authorization": f"Bearer {token}"})
    assert revoked.status_code == 401
    assert revoked.json()["error"]["code"] == "invalid_access_token"
    rejected_chat = _chat(app, token, {"model": "willy-default", "messages": [{"role": "user", "content": "revoked"}]})
    assert rejected_chat.status_code == 401
    assert upstream.calls == []


def test_quota_and_streaming_guard_block_fake_upstream_before_forwarding(tmp_path):
    upstream = FakeUpstreamStream()
    app, _store, private_key, device_id = _acceptance_fixture(
        tmp_path,
        upstream,
        grant=DeviceGrant("pending", ("willy-default",), None, 1, 1, 2, 20),
    )
    token = _token(app, private_key, device_id, nonce="acceptance-quota-001").json()["access_token"]
    payload = {"model": "willy-default", "messages": [{"role": "user", "content": "x"}], "max_tokens": 1}

    quota = _chat(app, token, payload)
    assert quota.status_code == 429
    assert quota.json()["error"]["code"] == "quota_exceeded"
    streaming = _chat(app, token, {**payload, "stream": True})
    assert streaming.status_code == 400
    assert streaming.json()["error"]["code"] == "streaming_not_supported"
    assert upstream.calls == []


def test_timeout_keeps_then_expires_unknown_reservation_without_sleeping(tmp_path, monkeypatch):
    tool_response = UpstreamResponse(
        200,
        {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
    )
    upstream = FakeUpstreamStream(UpstreamTimeout("private timeout"), tool_response)
    app, store, private_key, device_id = _acceptance_fixture(
        tmp_path,
        upstream,
        grant=DeviceGrant("pending", ("willy-default",), None, 10_000, 10_000, 2, 20),
    )
    token = _token(app, private_key, device_id, nonce="acceptance-timeout-001").json()["access_token"]
    payload = {"model": "willy-default", "messages": [{"role": "user", "content": "timeout"}], "max_tokens": 1}

    timed_out = _chat(app, token, payload)
    assert timed_out.status_code == 504
    assert _usage(app, token).json()["daily"]["reserved_tokens"] > 0

    current = gateway_store_module._utc_now()
    monkeypatch.setattr(gateway_store_module, "_utc_now", lambda: current + timedelta(seconds=901))
    completed = _chat(app, token, payload)
    assert completed.status_code == 200
    assert _usage(app, token).json()["daily"]["reserved_tokens"] == 0
    assert upstream.events == ["request_received", "request_timed_out", "request_received", "response_returned"]


def test_tool_call_forwarding_and_audit_redaction_are_observable_offline(tmp_path):
    prompt_canary = "prompt-audit-canary-must-not-persist"
    argument_canary = "argument-audit-canary"
    provider_canary = "provider-error-canary-must-not-reach-client"
    tool_arguments = json.dumps({"q": argument_canary})
    upstream = FakeUpstreamStream(
        UpstreamResponse(
            200,
            {
                "id": "fake-completion",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "fake-call",
                            "type": "function",
                            "function": {"name": "tools_lookup_molecule", "arguments": tool_arguments},
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            },
        ),
        UpstreamResponse(500, {"error": provider_canary}),
    )
    app, store, private_key, device_id = _acceptance_fixture(
        tmp_path,
        upstream,
        grant=DeviceGrant("pending", ("willy-default",), None, 10_000, 10_000, 2, 20),
    )
    token = _token(app, private_key, device_id, nonce="acceptance-tool-001").json()["access_token"]
    tool = {"type": "function", "function": {"name": "tools_lookup_molecule", "parameters": {}}}
    response = _chat(app, token, {
        "model": "willy-default",
        "messages": [{"role": "user", "content": prompt_canary}],
        "tools": [tool],
        "tool_choice": {"type": "function", "function": {"name": "tools_lookup_molecule"}},
        "max_tokens": 8,
    })
    assert response.status_code == 200
    assert response.json()["model"] == "willy-default"
    assert response.json()["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == tool_arguments
    assert upstream.calls[0]["model"] == "internal-deployment"
    assert upstream.calls[0]["messages"][0]["content"] == prompt_canary
    assert upstream.calls[0]["tools"] == [tool]
    assert upstream.calls[0]["tool_choice"] == {"type": "function", "function": {"name": "tools_lookup_molecule"}}

    unavailable = _chat(app, token, {"model": "willy-default", "messages": [{"role": "user", "content": "error"}]})
    assert unavailable.status_code == 502
    assert provider_canary not in unavailable.text

    database = _database_text(store)
    assert prompt_canary not in database
    assert argument_canary not in database
    assert provider_canary not in database
    assert _UPSTREAM_KEY not in database
    assert token not in database
    assert _UPSTREAM_KEY not in response.text
