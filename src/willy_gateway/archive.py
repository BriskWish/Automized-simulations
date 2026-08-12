"""Release boundary for the retained managed-gateway implementation."""

from __future__ import annotations


ARCHIVED_GATEWAY_MESSAGE = (
    "当前版本不支持启动托管 LLM 网关；相关协议代码仅作为后续版本归档保留。"
)


class GatewayServiceArchived(RuntimeError):
    """Raised when a retired gateway launcher is invoked."""


def raise_gateway_service_archived() -> None:
    """Stop retired service launchers before reading deployment configuration."""
    raise GatewayServiceArchived(ARCHIVED_GATEWAY_MESSAGE)
