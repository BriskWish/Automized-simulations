"""Fixed OpenAI-compatible upstream adapter; no client-controlled URL exists."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

import httpx


class UpstreamError(RuntimeError):
    """Network or protocol failure from the fixed upstream service."""


class UpstreamTimeout(UpstreamError):
    """The provider may have received the request, so its reservation is retained."""


@dataclass(frozen=True)
class UpstreamResponse:
    status_code: int
    body: Mapping[str, object] | None


class UpstreamTransport(Protocol):
    async def chat_completions(self, payload: Mapping[str, object]) -> UpstreamResponse:
        """Send a validated request only to the configured model service."""


class HttpxUpstream:
    """Async OpenAI-compatible adapter used directly by the ASGI application."""

    def __init__(self, *, base_url: str, api_key: str, timeout_s: float):
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._timeout_s = timeout_s

    async def chat_completions(self, payload: Mapping[str, object]) -> UpstreamResponse:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(
                    self._url,
                    json=dict(payload),
                    headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                )
        except httpx.TimeoutException as exc:
            raise UpstreamTimeout("上游请求超时") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError("上游不可达") from exc
        try:
            parsed = response.json()
        except ValueError:
            parsed = None
        return UpstreamResponse(status_code=response.status_code, body=parsed if isinstance(parsed, Mapping) else None)
