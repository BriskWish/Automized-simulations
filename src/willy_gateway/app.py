"""ASGI application for the restricted managed-LLM gateway."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
import json
import re
from threading import Lock
import time
from typing import Mapping
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import GatewaySettings
from .security import (
    SecurityError,
    issue_access_token,
    parse_public_key,
    verify_access_token,
    verify_device_signature,
)
from .store import (
    AccessDenied,
    DeviceGrant,
    GatewayStore,
    GatewayStoreError,
    QuotaExceeded,
    RegistrationDenied,
    RegistrationLimitReached,
)
from .upstream import HttpxUpstream, UpstreamError, UpstreamResponse, UpstreamTimeout, UpstreamTransport


_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_DEVICE_LABEL = re.compile(r"^[^\r\n]{1,80}$")
_CLIENT_VERSION = re.compile(r"^[A-Za-z0-9._+-]{1,80}$")
_FORWARDED_CHAT_FIELDS = frozenset({
    "frequency_penalty", "messages", "parallel_tool_calls", "presence_penalty",
    "response_format", "seed", "temperature", "tool_choice", "tools", "top_p",
})


class GatewayHTTPError(RuntimeError):
    """Public API failure containing only a stable error code and status."""

    def __init__(self, status_code: int, code: str):
        self.status_code = status_code
        self.code = code


class RegistrationRateLimiter:
    """Per-process registration throttle; deployment proxies provide stronger perimeter controls."""

    _MAX_SOURCES = 4_096

    def __init__(self, *, limit: int, window_s: int):
        self._limit = limit
        self._window_s = window_s
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, source: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if source not in self._requests and len(self._requests) >= self._MAX_SOURCES:
                cutoff = now - self._window_s
                for candidate, candidate_entries in list(self._requests.items()):
                    while candidate_entries and candidate_entries[0] <= cutoff:
                        candidate_entries.popleft()
                    if not candidate_entries:
                        del self._requests[candidate]
                if len(self._requests) >= self._MAX_SOURCES:
                    return False
            entries = self._requests[source]
            cutoff = now - self._window_s
            while entries and entries[0] <= cutoff:
                entries.popleft()
            if len(entries) >= self._limit:
                return False
            entries.append(now)
        return True


def _default_auto_approve_model_aliases(settings: GatewaySettings) -> tuple[str, ...]:
    """Grant only one deterministic safe alias to automatically approved devices."""
    alias = "willy-default" if "willy-default" in settings.model_aliases else sorted(settings.model_aliases)[0]
    return (alias,)


def _error_response(error: GatewayHTTPError, request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        headers={"X-Request-ID": request_id},
        content={"error": {"code": error.code, "request_id": request_id}},
    )


async def _read_json_object(request: Request, max_bytes: int) -> tuple[bytes, dict[str, object]]:
    body = await request.body()
    if len(body) > max_bytes:
        raise GatewayHTTPError(413, "request_too_large")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise GatewayHTTPError(400, "invalid_json") from exc
    if not isinstance(parsed, dict):
        raise GatewayHTTPError(400, "invalid_request")
    return body, parsed


def _get_text(payload: Mapping[str, object], field: str, *, pattern: re.Pattern[str] | None = None) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip() or (pattern is not None and not pattern.fullmatch(value)):
        raise GatewayHTTPError(400, "invalid_request")
    return value.strip()


def _bearer_claims(request: Request, settings: GatewaySettings, store: GatewayStore) -> tuple[dict[str, object], DeviceGrant]:
    authorization = request.headers.get("authorization", "")
    prefix = "Bearer "
    if not authorization.startswith(prefix) or not authorization[len(prefix):].strip():
        raise GatewayHTTPError(401, "authentication_required")
    try:
        claims = verify_access_token(settings.token_secret, authorization[len(prefix):].strip())
        grant = store.validate_access(claims)
    except (SecurityError, AccessDenied):
        raise GatewayHTTPError(401, "invalid_access_token") from None
    return claims, grant


def _validate_chat_request(payload: Mapping[str, object], settings: GatewaySettings, grant: DeviceGrant) -> tuple[str, dict[str, object], int]:
    alias = payload.get("model")
    if not isinstance(alias, str) or alias not in settings.model_aliases or alias not in grant.model_aliases:
        raise GatewayHTTPError(403, "model_not_allowed")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > 64 or not all(isinstance(message, dict) for message in messages):
        raise GatewayHTTPError(400, "invalid_request")
    if payload.get("stream") is True:
        raise GatewayHTTPError(400, "streaming_not_supported")
    tools = payload.get("tools", [])
    if not isinstance(tools, list) or len(tools) > settings.max_tool_definitions or not all(isinstance(tool, dict) for tool in tools):
        raise GatewayHTTPError(400, "invalid_request")
    supplied_max_tokens = payload.get("max_tokens", settings.default_completion_tokens)
    if isinstance(supplied_max_tokens, bool) or not isinstance(supplied_max_tokens, int) or not 1 <= supplied_max_tokens <= settings.max_completion_tokens:
        raise GatewayHTTPError(400, "invalid_request")
    forwarded: dict[str, object] = {"model": settings.model_aliases[alias], "messages": messages, "max_tokens": supplied_max_tokens}
    for field in _FORWARDED_CHAT_FIELDS:
        if field in payload:
            forwarded[field] = payload[field]
    try:
        # One reserved token per UTF-8 byte is deliberately conservative and
        # avoids an unpinned tokenizer becoming a quota-bypass dependency.
        prompt_reservation = len(json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise GatewayHTTPError(400, "invalid_request") from exc
    return alias, forwarded, prompt_reservation + supplied_max_tokens


def _usage_or_reservation(body: Mapping[str, object], reserved_tokens: int) -> tuple[int, int, int]:
    usage = body.get("usage")
    if not isinstance(usage, Mapping):
        return reserved_tokens, 0, reserved_tokens
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    total = usage.get("total_tokens")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (prompt, completion, total)):
        return reserved_tokens, 0, reserved_tokens
    # The reservation is released down to the provider's reported usage. A
    # provider that reports more than the estimate is charged for that larger
    # total; the conservative reservation only controls admission.
    return prompt, completion, total


def create_app(
    settings: GatewaySettings,
    *,
    store: GatewayStore | None = None,
    upstream: UpstreamTransport | None = None,
) -> FastAPI:
    """Create an app without opening listeners or obtaining client credentials."""
    gateway_store = store or GatewayStore(settings.database_path)
    gateway_store.initialize()
    gateway_store.apply_pending_registration_ttl(settings.pending_registration_ttl_s)
    registration_limiter = RegistrationRateLimiter(
        limit=settings.registration_requests_per_window,
        window_s=settings.registration_rate_window_s,
    )
    upstream_client = upstream or HttpxUpstream(
        base_url=settings.upstream_base_url,
        api_key=settings.upstream_api_key,
        timeout_s=settings.upstream_timeout_s,
    )
    auto_approve_model_aliases = _default_auto_approve_model_aliases(settings)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.gateway_store = gateway_store

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        return await call_next(request)

    @app.exception_handler(GatewayHTTPError)
    async def public_gateway_error(request: Request, error: GatewayHTTPError):
        return _error_response(error, getattr(request.state, "request_id", uuid.uuid4().hex))

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception):
        return _error_response(GatewayHTTPError(500, "gateway_internal_error"), getattr(request.state, "request_id", uuid.uuid4().hex))

    @app.get("/healthz")
    async def healthz(request: Request):
        return JSONResponse({"status": "ok"}, headers={"X-Request-ID": request.state.request_id})

    @app.post("/api/v1/registrations", status_code=202)
    async def request_registration(request: Request):
        _, payload = await _read_json_object(request, settings.request_body_limit_bytes)
        label = _get_text(payload, "device_label", pattern=_DEVICE_LABEL)
        client_version = _get_text(payload, "client_version", pattern=_CLIENT_VERSION)
        source = request.client.host if request.client is not None else "unknown"
        if not registration_limiter.allow(source):
            raise GatewayHTTPError(429, "registration_rate_limited")
        try:
            public_key = parse_public_key(_get_text(payload, "public_key"))
            device = gateway_store.request_registration(
                public_key=public_key,
                label=label,
                client_version=client_version,
                registration_limit=settings.registration_limit,
                pending_ttl_s=settings.pending_registration_ttl_s,
                auto_approve_limit=settings.auto_approve_limit,
                auto_approve_model_aliases=auto_approve_model_aliases,
                auto_approve_daily_token_limit=settings.default_daily_token_limit,
                auto_approve_monthly_token_limit=settings.default_monthly_token_limit,
                auto_approve_max_concurrency=settings.default_max_concurrency,
                auto_approve_max_requests_per_minute=settings.default_max_requests_per_minute,
            )
        except SecurityError:
            raise GatewayHTTPError(400, "invalid_request") from None
        except RegistrationLimitReached:
            raise GatewayHTTPError(429, "registration_limit_reached") from None
        except RegistrationDenied:
            raise GatewayHTTPError(403, "registration_denied") from None
        return JSONResponse(
            status_code=202,
            headers={"X-Request-ID": request.state.request_id},
            content={"device_id": device.device_id, "status": device.status},
        )

    @app.post("/api/v1/device/token")
    async def issue_device_token(request: Request):
        body, _ = await _read_json_object(request, settings.request_body_limit_bytes)
        device_id = request.headers.get("x-willy-device-id", "")
        nonce = request.headers.get("x-willy-nonce", "")
        signature = request.headers.get("x-willy-signature", "")
        timestamp_raw = request.headers.get("x-willy-timestamp", "")
        if not isinstance(device_id, str) or not _NONCE.fullmatch(nonce) or not signature:
            raise GatewayHTTPError(401, "invalid_device_signature")
        try:
            timestamp = int(timestamp_raw)
        except (TypeError, ValueError):
            raise GatewayHTTPError(401, "invalid_device_signature") from None
        now_s = int(time.time())
        if abs(now_s - timestamp) > settings.signature_window_s:
            raise GatewayHTTPError(401, "invalid_device_signature")
        try:
            device = gateway_store.get_device(device_id)
            verify_device_signature(
                device.public_key, method="POST", path="/api/v1/device/token", timestamp=timestamp,
                nonce=nonce, body=body, signature=signature,
            )
            if not gateway_store.consume_nonce(device_id, nonce, datetime.now(timezone.utc) + timedelta(seconds=settings.signature_window_s)):
                raise SecurityError("nonce 已使用")
            grant = gateway_store.active_grant_for_device(device_id)
        except (SecurityError, AccessDenied):
            raise GatewayHTTPError(401, "invalid_device_signature") from None
        token, claims = issue_access_token(
            settings.token_secret, device_id=device.device_id, key_fingerprint=device.key_fingerprint,
            model_aliases=list(grant.model_aliases), now_s=now_s, ttl_s=settings.token_ttl_s,
        )
        return JSONResponse(
            headers={"X-Request-ID": request.state.request_id},
            content={"access_token": token, "expires_in": settings.token_ttl_s, "token_type": "Bearer"},
        )

    @app.get("/v1/models")
    async def models(request: Request):
        claims, grant = _bearer_claims(request, settings, gateway_store)
        token_models = claims["models"]
        allowed = sorted(alias for alias in grant.model_aliases if alias in token_models and alias in settings.model_aliases)
        return JSONResponse(
            headers={"X-Request-ID": request.state.request_id},
            content={"object": "list", "data": [{"id": alias, "object": "model"} for alias in allowed]},
        )

    @app.get("/api/v1/me/usage")
    async def usage(request: Request):
        claims, _ = _bearer_claims(request, settings, gateway_store)
        snapshot = gateway_store.usage_snapshot(str(claims["device_id"]))
        return JSONResponse(headers={"X-Request-ID": request.state.request_id}, content=snapshot)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        claims, grant = _bearer_claims(request, settings, gateway_store)
        _, payload = await _read_json_object(request, settings.request_body_limit_bytes)
        alias, forwarded, reserved_tokens = _validate_chat_request(payload, settings, grant)
        request_id = request.state.request_id
        try:
            gateway_store.reserve(
                request_id=request_id, device_id=str(claims["device_id"]), token_jti=str(claims["jti"]),
                model_alias=alias, token_estimate=reserved_tokens,
                unknown_reservation_ttl_s=settings.unknown_reservation_ttl_s,
            )
        except QuotaExceeded:
            raise GatewayHTTPError(429, "quota_exceeded") from None
        except AccessDenied:
            raise GatewayHTTPError(403, "model_not_allowed") from None
        try:
            response = await upstream_client.chat_completions(forwarded)
        except UpstreamTimeout:
            gateway_store.mark_unknown(request_id)
            raise GatewayHTTPError(504, "upstream_timeout") from None
        except UpstreamError:
            gateway_store.release(request_id)
            raise GatewayHTTPError(502, "upstream_unavailable") from None
        if not 200 <= response.status_code < 300 or response.body is None:
            gateway_store.release(request_id)
            raise GatewayHTTPError(502, "upstream_unavailable")
        response_body = dict(response.body)
        input_tokens, output_tokens, total_tokens = _usage_or_reservation(response_body, reserved_tokens)
        try:
            gateway_store.settle(
                request_id, input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens
            )
        except GatewayStoreError:
            # The provider may have completed. Keep its reservation intact rather
            # than returning a successful response with an untracked debit.
            raise GatewayHTTPError(502, "gateway_accounting_unavailable") from None
        response_body["model"] = alias
        return JSONResponse(status_code=response.status_code, headers={"X-Request-ID": request_id}, content=response_body)

    return app
