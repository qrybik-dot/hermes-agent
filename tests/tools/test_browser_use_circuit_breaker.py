from __future__ import annotations

import pytest

from tools import browser_tool


class _FakeBrowserUseProvider:
    def __init__(self):
        self.calls = 0

    def is_configured(self):
        return True

    def create_session(self, task_id):
        self.calls += 1
        raise RuntimeError("HTTP 402 BILLING_ERROR insufficient_funds")


class _FakeBrowserbaseProvider:
    def is_configured(self):
        return False


@pytest.fixture(autouse=True)
def _reset_browser_tool_state(monkeypatch):
    browser_tool._active_sessions.clear()
    browser_tool._cached_cloud_provider = None
    browser_tool._cloud_provider_resolved = False
    browser_tool._reset_browser_use_billing_circuit_for_tests()
    monkeypatch.setattr(browser_tool, "_cloud_fallback_to_local", lambda: True)
    yield
    browser_tool._active_sessions.clear()
    browser_tool._cached_cloud_provider = None
    browser_tool._cloud_provider_resolved = False
    browser_tool._reset_browser_use_billing_circuit_for_tests()


def test_browser_use_billing_error_opens_circuit_without_local_fallback(monkeypatch):
    fake = _FakeBrowserUseProvider()
    browser_tool._cached_cloud_provider = fake
    browser_tool._cloud_provider_resolved = True

    with pytest.raises(RuntimeError, match="billing_insufficient"):
        browser_tool._get_session_info("billing-task-1")
    assert fake.calls == 1

    with pytest.raises(RuntimeError, match="billing_insufficient"):
        browser_tool._get_session_info("billing-task-2")
    assert fake.calls == 1

    state = browser_tool._browser_use_billing_circuit_state()
    assert state["available"] is False
    assert state["reason"] == "billing_insufficient"
    assert state["retry_after"] > state["timestamp"]
