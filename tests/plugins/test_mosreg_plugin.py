import json

from plugins.mosreg.tool import _sanitize_current, block_mosreg_fallback


def test_blocks_mosreg_terminal_fallback_only():
    blocked = block_mosreg_fallback("terminal", {"command": "find /dev/shm -name mosreg-gateway-totp*"})
    assert blocked and blocked["action"] == "block"
    assert block_mosreg_fallback("execute_code", {"code": "open(/tmp/mosreg_state)"})["action"] == "block"
    assert block_mosreg_fallback("terminal", {"command": "git status"}) is None
    assert block_mosreg_fallback("read_file", {"path": "/tmp/mosreg"}) is None


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
