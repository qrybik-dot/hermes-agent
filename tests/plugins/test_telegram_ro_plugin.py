from __future__ import annotations

from plugins.telegram_ro import register
from plugins.telegram_ro.tools import TELEGRAM_RO_DOWNLOAD_SCHEMA, TELEGRAM_RO_READ_SCHEMA


def test_only_read_and_download_tools_registered():
    calls = []

    class Ctx:
        def register_tool(self, **kwargs):
            calls.append(kwargs)

    register(Ctx())
    assert [item["name"] for item in calls] == ["telegram_ro_read", "telegram_ro_download"]
    assert all(item["toolset"] == "telegram_ro" for item in calls)


def test_schemas_are_narrow_and_read_only():
    for schema in [TELEGRAM_RO_READ_SCHEMA, TELEGRAM_RO_DOWNLOAD_SCHEMA]:
        params = schema["parameters"]
        assert params["required"] == ["url"]
        assert params["additionalProperties"] is False
        assert set(params["properties"]) == {"url"}
        description = schema["description"].lower()
        assert "read-only" in description
        assert "delete" in description
        assert "raw" in description
