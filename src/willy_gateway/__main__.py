"""Retired launcher retained only to report the current release boundary."""

from __future__ import annotations

import sys

from .archive import GatewayServiceArchived, raise_gateway_service_archived


def main() -> None:
    """Refuse gateway startup in the current BYOK-only release."""
    raise_gateway_service_archived()


if __name__ == "__main__":
    try:
        main()
    except GatewayServiceArchived as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2) from None
