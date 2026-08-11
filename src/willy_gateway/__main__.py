"""Explicit launcher for a separately deployed private gateway service."""

from __future__ import annotations

from .app import create_app
from .config import GatewaySettings


def main() -> None:
    import uvicorn

    settings = GatewaySettings.from_environment()
    uvicorn.run(
        create_app(settings),
        host=settings.bind_host,
        port=settings.bind_port,
        ssl_certfile=str(settings.tls_certfile) if settings.tls_certfile is not None else None,
        ssl_keyfile=str(settings.tls_keyfile) if settings.tls_keyfile is not None else None,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
