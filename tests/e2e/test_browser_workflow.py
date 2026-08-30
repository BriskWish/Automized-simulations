"""Browser acceptance for the built FastAPI/React workbench."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


def _browser_unavailable(message: str) -> None:
    if os.environ.get("WILLY_E2E_REQUIRED", "").strip().lower() in {"1", "true", "yes"}:
        pytest.fail(message)
    pytest.skip(message)


def _run_frontend_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=FRONTEND,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )


def test_browser_workflow_uses_the_built_react_workbench() -> None:
    if shutil.which("npm") is None:
        _browser_unavailable("requires npm and the frontend dependencies")
    build = _run_frontend_command(["npm", "run", "build"])
    if build.returncode:
        pytest.fail(f"React production build failed:\n{build.stdout}\n{build.stderr}")
    browser = _run_frontend_command(["npm", "run", "visual-check"])
    if browser.returncode:
        combined = f"{browser.stdout}\n{browser.stderr}"
        if "Executable doesn't exist" in combined or "Cannot find package 'playwright'" in combined:
            _browser_unavailable("requires the Playwright browser runtime")
        pytest.fail(f"React browser acceptance failed:\n{combined}")
    assert '"方案助理"' in browser.stdout
    assert '"日志"' in browser.stdout
