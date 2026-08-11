"""Contract tests for the loopback-only gateway administrator control plane."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from willy_gateway.admin_app import _ADMIN_PAGE, create_admin_app
from willy_gateway.app import create_app
from willy_gateway.config import GatewayConfigError, GatewaySettings
from willy_gateway.store import GatewayStore
from willy_gateway.upstream import UpstreamResponse


class _FakeUpstream:
    async def chat_completions(self, payload):
        return UpstreamResponse(200, {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


def _call(app, method: str, path: str, *, headers=None, payload=None):
    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://admin.test") as client:
            return await client.request(method, path, headers=headers, json=payload)

    return asyncio.run(run())


def _settings(tmp_path: Path, *, admin_secret: str | None = "administrator-secret-must-be-32-bytes") -> GatewaySettings:
    return GatewaySettings(
        database_path=tmp_path / "gateway.sqlite3",
        token_secret=b"gateway-test-secret-should-never-ship"[:32],
        upstream_base_url="http://upstream.invalid/v1",
        upstream_api_key="provider-secret-not-in-responses",
        model_aliases={"willy-default": "internal-deployment", "willy-fast": "internal-fast"},
        auto_approve_limit=0,
        admin_secret=admin_secret,
    )


def _enroll_pending_device(gateway_app):
    public_key = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    encoded = base64.urlsafe_b64encode(public_key).rstrip(b"=").decode("ascii")
    response = _call(
        gateway_app,
        "POST",
        "/api/v1/registrations",
        payload={
            "public_key": encoded,
            "device_label": "remote laptop",
            "client_version": "0.1.0",
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["device_id"]


def test_loopback_admin_requires_a_separate_secret_and_controls_device_grants(tmp_path):
    settings = _settings(tmp_path)
    store = GatewayStore(settings.database_path)
    gateway_app = create_app(settings, store=store, upstream=_FakeUpstream())
    device_id = _enroll_pending_device(gateway_app)
    admin_app = create_admin_app(settings, store=store)
    admin_headers = {"x-willy-gateway-admin-secret": settings.admin_secret}

    denied = _call(admin_app, "GET", "/api/v1/admin/snapshot")
    assert denied.status_code == 401
    page = _call(admin_app, "GET", "/")
    assert page.status_code == 200
    assert settings.admin_secret not in page.text

    before = _call(admin_app, "GET", "/api/v1/admin/snapshot", headers=admin_headers)
    assert before.status_code == 200
    pending = before.json()["devices"][0]
    assert pending["device_id"] == device_id
    assert pending["status"] == "pending"
    assert pending["model_aliases"] == []
    assert pending["grant"] is None
    assert before.json()["summary"]["registration_limit"] == 500
    assert before.json()["summary"]["registration_slots_used"] == 1
    assert before.json()["summary"]["auto_approve_limit"] == 0
    assert before.json()["summary"]["auto_approve_window_used"] == 0
    assert before.json()["summary"]["auto_approved_devices"] == 0
    assert "provider-secret-not-in-responses" not in before.text

    approved = _call(
        admin_app,
        "POST",
        f"/api/v1/admin/devices/{device_id}/approve",
        headers=admin_headers,
        payload={
            "model_aliases": ["willy-default"],
            "daily_token_limit": 100_000,
            "monthly_token_limit": 500_000,
            "max_concurrency": 2,
            "max_requests_per_minute": 20,
            "expires_at": None,
        },
    )
    assert approved.status_code == 200
    after = _call(admin_app, "GET", "/api/v1/admin/snapshot", headers=admin_headers).json()
    device = after["devices"][0]
    assert device["status"] == "approved"
    assert device["model_aliases"] == ["willy-default"]
    assert device["grant"]["model_aliases"] == ["willy-default"]
    assert after["summary"]["approved_devices"] == 1

    store.reserve(
        request_id="traffic-1",
        device_id=device_id,
        token_jti="admin-test-token",
        model_alias="willy-default",
        token_estimate=20,
        unknown_reservation_ttl_s=900,
    )
    active = _call(admin_app, "GET", "/api/v1/admin/snapshot", headers=admin_headers).json()
    assert active["summary"]["active_requests"] == 1
    assert active["summary"]["today_reserved_tokens"] == 20
    assert active["devices"][0]["active_requests"] == 1
    store.settle("traffic-1", input_tokens=6, output_tokens=4, total_tokens=10)
    settled = _call(admin_app, "GET", "/api/v1/admin/snapshot", headers=admin_headers).json()
    assert settled["summary"]["active_requests"] == 0
    assert settled["summary"]["today_settled_tokens"] == 10
    assert settled["devices"][0]["today_reserved_tokens"] == 0

    revoked = _call(admin_app, "POST", f"/api/v1/admin/devices/{device_id}/revoke", headers=admin_headers)
    assert revoked.status_code == 200
    final = _call(admin_app, "GET", "/api/v1/admin/snapshot", headers=admin_headers).json()
    assert final["devices"][0]["status"] == "revoked"
    assert final["summary"]["revoked_devices"] == 1


def test_admin_page_preselects_the_only_allowed_model_for_a_pending_device():
    assert "data.model_aliases.length === 1" in _ADMIN_PAGE
    assert "grantDefaults.daily_token_limit" in _ADMIN_PAGE
    assert "grantDefaults.monthly_token_limit" in _ADMIN_PAGE
    assert 'data-field="daily_token_limit" type="text" inputmode="numeric"' in _ADMIN_PAGE
    assert 'data-field="monthly_token_limit" type="text" inputmode="numeric"' in _ADMIN_PAGE
    assert "replace(/[\\s,]/g, '')" in _ADMIN_PAGE
    assert "summary.auto_approve_window_used" in _ADMIN_PAGE
    assert "summary.auto_approve_slots_available" in _ADMIN_PAGE


def test_admin_snapshot_reports_automatic_approval_capacity(tmp_path):
    settings = GatewaySettings(
        database_path=tmp_path / "gateway.sqlite3",
        token_secret=b"gateway-test-secret-should-never-ship"[:32],
        upstream_base_url="http://upstream.invalid/v1",
        upstream_api_key="provider-secret-not-in-responses",
        model_aliases={"willy-default": "internal-deployment"},
        registration_limit=3,
        auto_approve_limit=2,
        admin_secret="administrator-secret-must-be-32-bytes",
    )
    store = GatewayStore(settings.database_path)
    gateway_app = create_app(settings, store=store, upstream=_FakeUpstream())
    _enroll_pending_device(gateway_app)
    admin_app = create_admin_app(settings, store=store)
    snapshot = _call(
        admin_app,
        "GET",
        "/api/v1/admin/snapshot",
        headers={"x-willy-gateway-admin-secret": settings.admin_secret},
    ).json()

    assert snapshot["summary"]["auto_approve_limit"] == 2
    assert snapshot["summary"]["auto_approve_window_used"] == 1
    assert snapshot["summary"]["auto_approved_devices"] == 1
    assert snapshot["summary"]["auto_approve_slots_available"] == 1
    assert snapshot["devices"][0]["grant"]["daily_token_limit"] == 1_000_000
    assert snapshot["grant_defaults"]["monthly_token_limit"] == 10_000_000


def test_admin_grants_reject_unknown_models_and_nonloopback_gateway_requires_tls(tmp_path):
    settings = _settings(tmp_path)
    store = GatewayStore(settings.database_path)
    gateway_app = create_app(settings, store=store, upstream=_FakeUpstream())
    device_id = _enroll_pending_device(gateway_app)
    admin_app = create_admin_app(settings, store=store)
    response = _call(
        admin_app,
        "POST",
        f"/api/v1/admin/devices/{device_id}/approve",
        headers={"x-willy-gateway-admin-secret": settings.admin_secret},
        payload={
            "model_aliases": ["https://not-an-upstream"],
            "daily_token_limit": 1,
            "monthly_token_limit": 1,
            "max_concurrency": 1,
            "max_requests_per_minute": 1,
        },
    )
    assert response.status_code == 400

    with pytest.raises(GatewayConfigError, match="TLS"):
        GatewaySettings(
            database_path=tmp_path / "nonloopback.sqlite3",
            token_secret=b"gateway-test-secret-should-never-ship"[:32],
            upstream_base_url="https://upstream.invalid/v1",
            upstream_api_key="provider-secret",
            model_aliases={"willy-default": "internal"},
            bind_host="192.168.1.5",
        )

    missing_secret = _settings(tmp_path / "secret", admin_secret=None)
    with pytest.raises(ValueError, match="ADMIN_SECRET"):
        create_admin_app(missing_secret)


def test_expired_pending_registration_releases_its_slot_and_cannot_be_approved(tmp_path):
    settings = _settings(tmp_path)
    store = GatewayStore(settings.database_path)
    gateway_app = create_app(settings, store=store, upstream=_FakeUpstream())
    device_id = _enroll_pending_device(gateway_app)
    with store._transaction() as connection:
        connection.execute("UPDATE devices SET pending_expires_at = ? WHERE device_id = ?", ("2000-01-01T00:00:00+00:00", device_id))
    admin_app = create_admin_app(settings, store=store)
    expired = _call(
        admin_app,
        "POST",
        f"/api/v1/admin/devices/{device_id}/approve",
        headers={"x-willy-gateway-admin-secret": settings.admin_secret},
        payload={
            "model_aliases": ["willy-default"],
            "daily_token_limit": 100_000,
            "monthly_token_limit": 500_000,
            "max_concurrency": 2,
            "max_requests_per_minute": 20,
        },
    )
    assert expired.status_code == 409
    assert expired.json()["error"]["code"] == "registration_expired"
    snapshot = _call(
        admin_app,
        "GET",
        "/api/v1/admin/snapshot",
        headers={"x-willy-gateway-admin-secret": settings.admin_secret},
    ).json()
    assert snapshot["summary"]["pending_devices"] == 0
    assert snapshot["summary"]["expired_pending_devices"] == 1
