"""Regression tests for the optional-local, mandatory-CI browser gate."""

from __future__ import annotations

import pytest

from tests.e2e import test_browser_workflow


def test_browser_runtime_absence_skips_when_the_gate_is_not_required(monkeypatch):
    monkeypatch.delenv("WILLY_E2E_REQUIRED", raising=False)

    with pytest.raises(pytest.skip.Exception):
        test_browser_workflow._browser_unavailable("runtime unavailable")


def test_browser_runtime_absence_fails_when_the_gate_is_required(monkeypatch):
    monkeypatch.setenv("WILLY_E2E_REQUIRED", "1")

    with pytest.raises(pytest.fail.Exception):
        test_browser_workflow._browser_unavailable("runtime unavailable")
