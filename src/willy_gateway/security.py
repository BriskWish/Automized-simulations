"""Cryptographic primitives shared by gateway routes and managed clients."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class SecurityError(ValueError):
    """Raised for malformed, expired, forged, or revoked credentials."""


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise SecurityError("编码无效")
    try:
        return base64.urlsafe_b64decode(value.encode("ascii") + b"=" * (-len(value) % 4))
    except (UnicodeEncodeError, ValueError) as exc:
        raise SecurityError("编码无效") from exc


def parse_public_key(encoded: str) -> bytes:
    raw = _b64decode(encoded)
    if len(raw) != 32:
        raise SecurityError("设备公钥无效")
    return raw


def public_key_fingerprint(public_key: bytes) -> str:
    return hashlib.sha256(public_key).hexdigest()


def request_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_device_request(method: str, path: str, timestamp: int, nonce: str, body: bytes) -> bytes:
    """Return the exact byte string signed to exchange a device token."""
    return "\n".join((
        "willy-gateway-v1",
        method.upper(),
        path,
        str(timestamp),
        nonce,
        request_digest(body),
    )).encode("ascii")


def verify_device_signature(
    public_key: bytes,
    *,
    method: str,
    path: str,
    timestamp: int,
    nonce: str,
    body: bytes,
    signature: str,
) -> None:
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            _b64decode(signature),
            canonical_device_request(method, path, timestamp, nonce, body),
        )
    except (ValueError, InvalidSignature, SecurityError) as exc:
        raise SecurityError("设备签名无效") from exc


def issue_access_token(
    secret: bytes,
    *,
    device_id: str,
    key_fingerprint: str,
    model_aliases: list[str],
    now_s: int | None = None,
    ttl_s: int = 600,
) -> tuple[str, dict[str, object]]:
    now = int(time.time()) if now_s is None else int(now_s)
    claims: dict[str, object] = {
        "device_id": device_id,
        "exp": now + ttl_s,
        "iat": now,
        "jti": uuid.uuid4().hex,
        "key_fingerprint": key_fingerprint,
        "models": sorted(model_aliases),
        "version": 1,
    }
    payload = _b64encode(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = _b64encode(hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest())
    return f"wg1.{payload}.{signature}", claims


def verify_access_token(secret: bytes, token: str, *, now_s: int | None = None) -> dict[str, object]:
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3 or parts[0] != "wg1":
        raise SecurityError("访问令牌无效")
    expected = _b64encode(hmac.new(secret, parts[1].encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, parts[2]):
        raise SecurityError("访问令牌无效")
    try:
        claims = json.loads(_b64decode(parts[1]).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, SecurityError) as exc:
        raise SecurityError("访问令牌无效") from exc
    if not isinstance(claims, dict):
        raise SecurityError("访问令牌无效")
    required = ("device_id", "exp", "iat", "jti", "key_fingerprint", "models", "version")
    if any(field not in claims for field in required):
        raise SecurityError("访问令牌无效")
    if not isinstance(claims["device_id"], str) or not isinstance(claims["jti"], str):
        raise SecurityError("访问令牌无效")
    if not isinstance(claims["models"], list) or not all(isinstance(value, str) for value in claims["models"]):
        raise SecurityError("访问令牌无效")
    now = int(time.time()) if now_s is None else int(now_s)
    if not isinstance(claims["exp"], int) or claims["exp"] <= now:
        raise SecurityError("访问令牌已过期")
    return claims
