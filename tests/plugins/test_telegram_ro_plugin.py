from __future__ import annotations

import pytest

from plugins.telegram_ro import register
from plugins.telegram_ro import tools
from plugins.telegram_ro.tools import (
    TELEGRAM_RO_DOWNLOAD_SCHEMA,
    TELEGRAM_RO_HISTORY_SCHEMA,
    TELEGRAM_RO_READ_SCHEMA,
    TELEGRAM_RO_SEARCH_SCHEMA,
)


def test_exactly_four_read_only_tools_registered():
    calls = []

    class Ctx:
        def register_tool(self, **kwargs):
            calls.append(kwargs)

    register(Ctx())
    assert [item["name"] for item in calls] == ["telegram_ro_search", "telegram_ro_history", "telegram_ro_read", "telegram_ro_download"]
    assert all(item["toolset"] == "telegram_ro" for item in calls)


def test_schemas_expose_no_action_or_raw_escape_hatch():
    for schema in [TELEGRAM_RO_SEARCH_SCHEMA, TELEGRAM_RO_HISTORY_SCHEMA, TELEGRAM_RO_READ_SCHEMA, TELEGRAM_RO_DOWNLOAD_SCHEMA]:
        params = schema["parameters"]
        assert params["additionalProperties"] is False
        assert "action" not in params["properties"]
        description = schema["description"].lower()
        assert "read-only" in description
        assert "delete" in description
        assert "raw" in description
        assert "mark" in description or "unread" in description


def test_search_is_bounded_and_cursor_aware():
    props = TELEGRAM_RO_SEARCH_SCHEMA["parameters"]["properties"]
    assert props["limit"]["maximum"] == 100
    assert "cursor" in props
    assert set(props["scope"]["enum"]) == {"all", "saved", "chat"}
    assert "formatted_only" in props
    assert "media_type" in props


def test_download_cache_rejects_single_file_over_quota(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "DOWNLOAD_ROOT", tmp_path)
    with pytest.raises(RuntimeError, match="cache quota"):
        tools._prepare_cache(tools.CACHE_QUOTA_BYTES + 1)


def test_download_target_is_unique_across_chats_with_same_message_id(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "DOWNLOAD_ROOT", tmp_path)
    a = tools._download_target("-100111", 42, "video.mp4")
    b = tools._download_target("-100222", 42, "video.mp4")
    assert a != b
    assert a.parent == b.parent == tmp_path
