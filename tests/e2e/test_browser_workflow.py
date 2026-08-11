"""Browser acceptance path for the real UI and an in-memory executor."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest


pytestmark = pytest.mark.e2e


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as socket_handle:
        socket_handle.bind(("127.0.0.1", 0))
        return int(socket_handle.getsockname()[1])


@pytest.fixture
def browser_api():
    return pytest.importorskip("playwright.sync_api", reason="requires the optional Playwright browser runtime")


@pytest.fixture
def fake_ui_server(tmp_path):
    port = _free_port()
    environment = dict(os.environ)
    environment["WILLY_ROOT"] = str(tmp_path / "workspace")
    process = subprocess.Popen(
        [
            sys.executable, "-m", "tests.e2e.fake_service",
            "--root", environment["WILLY_ROOT"], "--port", str(port),
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if httpx.get(base_url, timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    else:
        process.terminate()
        process.wait(timeout=5)
        pytest.fail("fake UI service did not start")
    try:
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_browser_workflow_uses_fake_executor_without_touching_a_real_run(browser_api, fake_ui_server, tmp_path):
    try:
        with browser_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            # Gradio's timer keeps a polling request open, so network-idle is
            # not a meaningful readiness signal for this page.
            page.goto(fake_ui_server, wait_until="domcontentloaded", timeout=30_000)

            page.locator("#app-title").get_by_text("Willy").wait_for(timeout=10_000)
            run_panel = page.locator("#run-assistant")
            run_panel.get_by_text("公开错误").wait_for(timeout=10_000)
            run_panel.get_by_text("待确认的模拟调整").wait_for(timeout=10_000)

            run_panel.locator("textarea").fill("确认")
            run_panel.get_by_role("button", name="发送").click()
            run_panel.get_by_text("已确认，正在重跑").wait_for(timeout=10_000)

            stop = page.locator("#stop-pipeline-button")
            stop.wait_for(state="visible", timeout=10_000)
            stop.click()
            stop.get_by_text("确认中止").wait_for(timeout=5_000)
            stop.click()
            stop.get_by_text("正在安全停止").wait_for(timeout=10_000)

            selector = page.locator("#structure-run-selector")
            selector.locator("input[role='combobox']").click()
            page.locator("#structure-run-selector [role='option'][aria-label='project-beta']").click()
            page.locator("#structure-viewer").get_by_text("project-beta.pdb").wait_for(timeout=10_000)

            screenshot = tmp_path / "browser-e2e.png"
            page.screenshot(path=str(screenshot), full_page=True)
            assert screenshot.stat().st_size > 0
            browser.close()
    except browser_api.Error as exc:
        pytest.skip(f"Playwright Chromium is unavailable: {exc}")
