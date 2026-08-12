"""Client-side device identity and token-refreshing OpenAI facade.

This module deliberately owns the managed gateway protocol rather than
teaching browser code or agents about device signatures.  A distributed,
read-only ``managed_gateway.json`` selects one fixed gateway; the server
remains the authority for device approval, model access, and quotas.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
from typing import Callable, Mapping
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from willy._paths import get_project_root
from willy_gateway.security import canonical_device_request


MANAGED_GATEWAY_PROFILE_FILENAME = "managed_gateway.json"
_PROFILE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_MODEL_ALIAS = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_DEVICE_ID = re.compile(r"^[a-f0-9]{32}$")
_CLIENT_VERSION = "0.3.0"
_REQUEST_TIMEOUT_S = 12.0


class ManagedGatewayError(RuntimeError):
    """A stable, non-secret reason why managed access is unavailable."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ManagedGatewayProfile:
    """Fixed endpoint and alias supplied with a Willy deployment."""

    profile_id: str
    label: str
    base_url: str
    model: str


@dataclass(frozen=True)
class ManagedDeviceIdentity:
    """The local half of one device registration; private material never leaves it."""

    profile_id: str
    private_key: bytes
    device_id: str | None = None

    def signing_key(self) -> Ed25519PrivateKey:
        try:
            return Ed25519PrivateKey.from_private_bytes(self.private_key)
        except ValueError as exc:  # pragma: no cover - persistent corruption guard
            raise ManagedGatewayError("managed_identity_invalid") from exc

    def public_key_bytes(self) -> bytes:
        return self.signing_key().public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: object) -> bytes:
    if not isinstance(value, str) or not value:
        raise ManagedGatewayError("managed_identity_invalid")
    try:
        return base64.urlsafe_b64decode(value.encode("ascii") + b"=" * (-len(value) % 4))
    except (UnicodeEncodeError, ValueError) as exc:
        raise ManagedGatewayError("managed_identity_invalid") from exc


def _validate_profile_value(value: object, label: str, *, pattern=None, maximum: int = 160) -> str:
    if not isinstance(value, str):
        raise ManagedGatewayError("managed_profile_invalid")
    result = value.strip()
    if not result or len(result) > maximum or "\n" in result or "\r" in result:
        raise ManagedGatewayError("managed_profile_invalid")
    if pattern is not None and not pattern.fullmatch(result):
        raise ManagedGatewayError("managed_profile_invalid")
    return result


def _validate_base_url(value: object) -> str:
    result = _validate_profile_value(value, "base_url", maximum=512).rstrip("/")
    parsed = urlparse(result)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ManagedGatewayError("managed_profile_invalid")
    if parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.path not in {"", "/"}:
        raise ManagedGatewayError("managed_profile_invalid")
    loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    if parsed.scheme != "https" and not loopback:
        raise ManagedGatewayError("managed_profile_requires_https")
    return result


def load_managed_gateway_profile(project_root: str | Path | None = None) -> ManagedGatewayProfile:
    """Read the deployment-owned profile without accepting UI endpoint input."""
    root = Path(project_root) if project_root is not None else get_project_root()
    path = root / MANAGED_GATEWAY_PROFILE_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManagedGatewayError("managed_profile_missing") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ManagedGatewayError("managed_profile_invalid") from exc
    if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
        raise ManagedGatewayError("managed_profile_invalid")
    return ManagedGatewayProfile(
        profile_id=_validate_profile_value(raw.get("profile_id"), "profile_id", pattern=_PROFILE_ID, maximum=64),
        label=_validate_profile_value(raw.get("label"), "label", maximum=80),
        base_url=_validate_base_url(raw.get("base_url")),
        model=_validate_profile_value(raw.get("model"), "model", pattern=_MODEL_ALIAS, maximum=80),
    )


def _default_identity_path() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME", "").strip()
    root = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return root / "willy" / "managed_gateway_identity.json"


class ManagedIdentityStore:
    """A private-file fallback identity store with injectable paths for tests.

    The identity is machine-local instead of project-local.  The parent and
    file permissions are set explicitly on every write; a missing or malformed
    file is never silently treated as a registered device.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else _default_identity_path()

    def _read(self) -> ManagedDeviceIdentity | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManagedGatewayError("managed_identity_invalid") from exc
        if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
            raise ManagedGatewayError("managed_identity_invalid")
        profile_id = _validate_profile_value(raw.get("profile_id"), "profile_id", pattern=_PROFILE_ID, maximum=64)
        device_id = raw.get("device_id")
        if device_id is not None and (not isinstance(device_id, str) or not _DEVICE_ID.fullmatch(device_id)):
            raise ManagedGatewayError("managed_identity_invalid")
        private_key = _b64decode(raw.get("private_key"))
        if len(private_key) != 32:
            raise ManagedGatewayError("managed_identity_invalid")
        return ManagedDeviceIdentity(profile_id=profile_id, private_key=private_key, device_id=device_id)

    def load(self, profile_id: str) -> ManagedDeviceIdentity | None:
        """Read an existing identity without creating key material as a side effect."""
        existing = self._read()
        if existing is not None and existing.profile_id != profile_id:
            raise ManagedGatewayError("managed_identity_profile_mismatch")
        return existing

    def load_or_create(self, profile_id: str) -> ManagedDeviceIdentity:
        existing = self.load(profile_id)
        if existing is not None:
            return existing
        identity = ManagedDeviceIdentity(
            profile_id=profile_id,
            private_key=Ed25519PrivateKey.generate().private_bytes(
                serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
            ),
        )
        self.save(identity)
        return identity

    def save(self, identity: ManagedDeviceIdentity) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        payload = {
            "schema_version": 1,
            "profile_id": identity.profile_id,
            "device_id": identity.device_id,
            "private_key": _b64encode(identity.private_key),
        }
        descriptor, temp_name = tempfile.mkstemp(prefix=".managed-gateway-", suffix=".tmp", dir=self.path.parent)
        try:
            os.chmod(temp_name, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
            os.replace(temp_name, self.path)
            os.chmod(self.path, 0o600)
        finally:
            Path(temp_name).unlink(missing_ok=True)


def _request_json(
    method: str,
    profile: ManagedGatewayProfile,
    path: str,
    *,
    content: bytes | None = None,
    payload: Mapping[str, object] | None = None,
    headers: Mapping[str, str] | None = None,
) -> Mapping[str, object]:
    request_headers = {"Accept": "application/json", **dict(headers or {})}
    try:
        # The gateway profile is deployment-owned. Do not let a user's global
        # HTTP(S) proxy silently redirect a private registration request.
        with httpx.Client(timeout=_REQUEST_TIMEOUT_S, follow_redirects=False, trust_env=False) as client:
            response = client.request(
                method,
                f"{profile.base_url}{path}",
                content=content,
                json=dict(payload) if payload is not None else None,
                headers=request_headers,
            )
    except httpx.TimeoutException as exc:
        raise ManagedGatewayError("managed_gateway_timeout") from exc
    except httpx.HTTPError as exc:
        raise ManagedGatewayError("managed_gateway_unreachable") from exc
    try:
        error_body = response.json()
    except ValueError:
        error_body = None
    error_code = error_body.get("error", {}).get("code") if isinstance(error_body, Mapping) else None
    if response.status_code == 403 and error_code == "registration_denied":
        raise ManagedGatewayError("managed_registration_denied")
    if response.status_code in {401, 403}:
        raise ManagedGatewayError("managed_access_denied")
    if response.status_code == 429:
        if error_code == "registration_limit_reached":
            raise ManagedGatewayError("managed_registration_limit_reached")
        if error_code == "registration_rate_limited":
            raise ManagedGatewayError("managed_registration_rate_limited")
        raise ManagedGatewayError("managed_quota_exceeded")
    if not 200 <= response.status_code < 300:
        raise ManagedGatewayError("managed_gateway_rejected")
    try:
        body = response.json()
    except ValueError as exc:
        raise ManagedGatewayError("managed_gateway_protocol") from exc
    if not isinstance(body, Mapping):
        raise ManagedGatewayError("managed_gateway_protocol")
    return body


GatewayRequest = Callable[..., Mapping[str, object]]


def _device_label() -> str:
    label = " ".join(socket.gethostname().split())[:80]
    return label or "Willy device"


def request_managed_registration(
    profile: ManagedGatewayProfile,
    *,
    store: ManagedIdentityStore | None = None,
    request: GatewayRequest = _request_json,
    client_version: str = _CLIENT_VERSION,
) -> str:
    """Request a server-counted registration for this device public key."""
    identity_store = store or ManagedIdentityStore()
    identity = identity_store.load_or_create(profile.profile_id)
    body = request(
        "POST",
        profile,
        "/api/v1/registrations",
        payload={
            "public_key": _b64encode(identity.public_key_bytes()),
            "device_label": _device_label(),
            "client_version": client_version,
        },
    )
    device_id = body.get("device_id")
    status = body.get("status")
    if not isinstance(device_id, str) or not _DEVICE_ID.fullmatch(device_id) or status not in {"pending", "approved"}:
        raise ManagedGatewayError("managed_gateway_protocol")
    identity_store.save(ManagedDeviceIdentity(profile.profile_id, identity.private_key, device_id))
    return str(status)


class ManagedGatewaySession:
    """Obtains short-lived access tokens by signing each token exchange."""

    def __init__(
        self,
        profile: ManagedGatewayProfile,
        *,
        store: ManagedIdentityStore | None = None,
        request: GatewayRequest = _request_json,
        clock: Callable[[], float] = time.time,
    ):
        self.profile = profile
        self._store = store or ManagedIdentityStore()
        self._request = request
        self._clock = clock
        self._lock = threading.Lock()
        self._access_token = ""
        self._expires_at = 0.0

    def _identity(self) -> ManagedDeviceIdentity:
        identity = self._store.load_or_create(self.profile.profile_id)
        if identity.device_id is None:
            raise ManagedGatewayError("managed_device_not_registered")
        return identity

    def access_token(self) -> str:
        with self._lock:
            now = self._clock()
            if self._access_token and now < self._expires_at:
                return self._access_token
            identity = self._identity()
            body = b"{}"
            timestamp = int(now)
            nonce = secrets.token_urlsafe(24)
            signature = _b64encode(identity.signing_key().sign(
                canonical_device_request("POST", "/api/v1/device/token", timestamp, nonce, body)
            ))
            response = self._request(
                "POST",
                self.profile,
                "/api/v1/device/token",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Willy-Device-ID": identity.device_id,
                    "X-Willy-Timestamp": str(timestamp),
                    "X-Willy-Nonce": nonce,
                    "X-Willy-Signature": signature,
                },
            )
            token = response.get("access_token")
            expires_in = response.get("expires_in")
            if not isinstance(token, str) or not token or isinstance(expires_in, bool) or not isinstance(expires_in, int):
                raise ManagedGatewayError("managed_gateway_protocol")
            if not 60 <= expires_in <= 600:
                raise ManagedGatewayError("managed_gateway_protocol")
            self._access_token = token
            self._expires_at = now + max(1, expires_in - 30)
            return token


def managed_gateway_usage_snapshot(
    profile: ManagedGatewayProfile,
    *,
    store: ManagedIdentityStore | None = None,
    request: GatewayRequest = _request_json,
) -> Mapping[str, object]:
    """Fetch the authenticated device's quota snapshot without calling the LLM."""
    session = ManagedGatewaySession(profile, store=store, request=request)
    token = session.access_token()
    return request(
        "GET",
        profile,
        "/api/v1/me/usage",
        headers={"Authorization": f"Bearer {token}"},
    )


class _ManagedChatCompletions:
    def __init__(self, session: ManagedGatewaySession, client_factory: Callable[[str], object]):
        self._session = session
        self._client_factory = client_factory

    def create(self, *args, **kwargs):
        client = self._client_factory(self._session.access_token())
        return client.chat.completions.create(*args, **kwargs)


class ManagedOpenAIClient:
    """OpenAI-compatible facade that never stores a long-lived bearer token."""

    def __init__(self, session: ManagedGatewaySession, client_factory: Callable[[str], object]):
        self.chat = SimpleNamespace(completions=_ManagedChatCompletions(session, client_factory))


def create_managed_openai_client(
    profile: ManagedGatewayProfile,
    client_factory: Callable[[str], object],
) -> ManagedOpenAIClient:
    """Return a facade that refreshes a device-bound token before each call."""
    return ManagedOpenAIClient(ManagedGatewaySession(profile), client_factory)


def managed_identity_status(profile: ManagedGatewayProfile, *, store: ManagedIdentityStore | None = None) -> str:
    """Return a coarse local state suitable for the configuration UI."""
    identity = (store or ManagedIdentityStore()).load(profile.profile_id)
    if identity is None:
        return "not_registered"
    return "pending_or_approved" if identity.device_id is not None else "not_registered"
