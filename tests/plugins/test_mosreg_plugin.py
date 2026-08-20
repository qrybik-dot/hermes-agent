import json

import pytest

from plugins.mosreg import tool as mosreg_tool
from plugins.mosreg.tool import _failure, _normalise_code, _refine_worker_code, _sanitize_current, block_mosreg_fallback


def test_blocks_only_sensitive_mosreg_fallbacks():
    blocked = block_mosreg_fallback("terminal", {"command": "find /dev/shm -name mosreg-gateway-totp*"})
    assert blocked and blocked["action"] == "block"
    keychain = block_mosreg_fallback("terminal", {"command": "security find-generic-password -w -s mosreg-auth"})
    assert keychain and keychain["action"] == "block"
    assert block_mosreg_fallback("terminal", {"command": "cat ~/.config/mosreg/auth.live.status.json"}) is None
    assert block_mosreg_fallback("terminal", {"command": "launchctl print gui/502/com.hermes.mosreg.auth-probe"}) is None
    assert block_mosreg_fallback("execute_code", {"code": "print('mosreg health')"}) is None
    assert block_mosreg_fallback("terminal", {"command": "git status"}) is None


def test_sanitize_family_and_self():
    raw = {
        "schema_version": 1,
        "updated_at": "2026-08-19T19:00:00Z",
        "source_status": "ok",
        "authorization_status": "authenticated",
        "unexpected": "drop-me",
        "members": [
            {
                "member": "owner", "display_name": "A", "type": "adult", "source_status": "ok",
                "appointments": [{"date": "2026-09-01", "time": "16:30", "doctor": "D", "secret": "drop"}],
            },
            {
                "member": "child", "display_name": "C", "type": "child", "source_status": "ok",
                "appointments": [{"date": "2026-09-02", "clinic": "K"}],
            },
        ],
    }
    family = _sanitize_current(raw, "family")
    assert len(family["members"]) == 2
    assert "unexpected" not in family
    assert "secret" not in family["members"][0]["appointments"][0]
    self_only = _sanitize_current(raw, "self")
    assert [m["type"] for m in self_only["members"]] == ["adult"]


def test_manifest_and_schema_are_narrow():
    from plugins.mosreg.tool import MOSREG_REFRESH_SCHEMA
    params = MOSREG_REFRESH_SCHEMA["parameters"]
    assert set(params["properties"]) == {"scope"}
    assert params["additionalProperties"] is False


def test_structured_failure_is_specific_and_non_hallucinatory():
    result = _failure("KEYCHAIN_ACCESS_DENIED", evidence={"state": "FAILED"})
    assert result["success"] is False
    assert result["error_code"] == "KEYCHAIN_ACCESS_DENIED"
    assert result["stage"] == "credentials"
    assert result["retryable"] is False
    assert "Keychain" in result["diagnosis"]
    assert "current evidence" in result["response_policy"].lower()
    assert "non-secret" in result["response_policy"].lower()


def test_error_aliases_and_schema_response_policy():
    from plugins.mosreg.tool import MOSREG_REFRESH_SCHEMA
    assert _normalise_code("keychain_item_missing") == "KEYCHAIN_ITEM_MISSING"
    assert _normalise_code("FAILED") == "MOSREG_WORKER_FAILED"
    desc = MOSREG_REFRESH_SCHEMA["description"]
    assert "Do NOT tell the user that 2FA is required" in desc
    assert "error_code/stage/diagnosis/evidence" in desc
    assert "targeted non-secret" in desc
    assert "reversible recovery" in desc
    assert "only when new evidence supports it" in desc


@pytest.mark.asyncio
async def test_gui_request_uses_fixed_broker_stdin_not_inline_code(monkeypatch):
    captured = {}

    async def fake_ssh(argv, *, timeout, stdin_text=None):
        captured["argv"] = list(argv)
        captured["timeout"] = timeout
        captured["stdin_text"] = stdin_text
        return 0, '{"ok":true,"status":"queued","request_id":"native_test"}', ""

    monkeypatch.setattr(mosreg_tool, "_ssh", fake_ssh)
    payload = {
        "schema": 1,
        "request_id": "native_test",
        "mode": "synthetic",
        "expires_at": 9999999999,
    }
    rc, stderr, ack = await mosreg_tool._write_gui_request(payload)
    assert rc == 0
    assert stderr == ""
    assert ack["status"] == "queued"
    assert captured["argv"] == [
        "/usr/bin/python3", mosreg_tool.GUI_BROKER, "--enqueue-gui-request"
    ]
    assert "-c" not in captured["argv"]
    assert json.loads(captured["stdin_text"]) == payload


def test_gui_write_failure_is_not_blindly_retried():
    result = _failure("GUI_REQUEST_WRITE_FAILED", evidence={
        "ssh_exit_code": 2,
        "mac_ssh_reached": True,
        "broker_status": "missing_ack",
    })
    assert result["retryable"] is False
    assert result["stage"] == "mac_dispatch"
    assert result["evidence"]["mac_ssh_reached"] is True


def test_totp_failures_require_explicit_user_restart():
    for code in ("TOTP_TIMEOUT", "TOTP_LIMIT", "TOTP_INPUT_NOT_FOUND", "TOTP_SUBMIT_FAILED", "TOTP_REJECTED", "TOTP_POST_SUBMIT_TIMEOUT"):
        result = _failure(code)
        assert result["retryable"] is False, code
    from plugins.mosreg.tool import MOSREG_REFRESH_SCHEMA
    assert "Never auto-retry after challenge_created=true" in MOSREG_REFRESH_SCHEMA["description"]


def test_generic_failure_after_code_consumption_is_refined():
    assert _refine_worker_code("MOSREG_WORKER_FAILED", {"last_state": "SUBMITTING_TOTP"}, True) == "TOTP_SUBMIT_FAILED"
    assert _refine_worker_code("MOSREG_WORKER_FAILED", {"last_state": "AWAITING_TOTP"}, True) == "MOSREG_WORKER_FAILED"
    assert _refine_worker_code("MOSREG_WORKER_FAILED", {"last_state": "SUBMITTING_TOTP"}, False) == "MOSREG_WORKER_FAILED"
