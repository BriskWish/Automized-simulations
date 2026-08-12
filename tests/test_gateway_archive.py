"""Release-boundary tests for the retained, non-runnable gateway code."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from willy_gateway.archive import ARCHIVED_GATEWAY_MESSAGE, GatewayServiceArchived


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _run_archived_command(*arguments: str) -> subprocess.CompletedProcess[str]:
    source_root = _REPOSITORY_ROOT / "src"
    python_path = os.pathsep.join(
        [str(source_root), os.environ.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    return subprocess.run(
        [sys.executable, *arguments],
        cwd=_REPOSITORY_ROOT,
        env={**os.environ, "PYTHONPATH": python_path},
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )


@pytest.mark.integration
def test_gateway_launcher_is_blocked_in_byok_only_release():
    from willy_gateway import __main__

    with pytest.raises(GatewayServiceArchived, match="当前版本不支持启动"):
        __main__.main()


@pytest.mark.integration
def test_gateway_admin_launcher_is_blocked_in_byok_only_release():
    from willy_gateway import admin

    with pytest.raises(GatewayServiceArchived, match="当前版本不支持启动"):
        admin.main()


def test_archived_message_is_stable():
    assert "后续版本归档保留" in ARCHIVED_GATEWAY_MESSAGE


@pytest.mark.integration
@pytest.mark.parametrize(
    "arguments",
    [
        ("-m", "willy_gateway"),
        ("-m", "willy_gateway.admin"),
        (
            "scripts/build_managed_client_bundle.py",
            "--profile",
            "/secure/deployment/managed_gateway.json",
            "--output",
            "/secure/releases/willy-client",
        ),
    ],
)
def test_archived_gateway_commands_fail_before_deployment(arguments):
    completed = _run_archived_command(*arguments)

    assert completed.returncode == 2
    assert "当前版本不支持" in completed.stderr
    assert "Traceback" not in completed.stderr
