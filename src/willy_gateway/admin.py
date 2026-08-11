"""Launcher for the private loopback-only gateway administrator page."""

from __future__ import annotations

from .admin_app import create_admin_app
from .config import GatewayConfigError, GatewaySettings


def main() -> None:
    import uvicorn

    settings = GatewaySettings.from_environment()
    if settings.admin_secret is None:
        raise GatewayConfigError("启动管理页面必须设置 WILLY_GATEWAY_ADMIN_SECRET")
    uvicorn.run(create_admin_app(settings), host="127.0.0.1", port=settings.admin_port, log_level="warning")


if __name__ == "__main__":
    main()
