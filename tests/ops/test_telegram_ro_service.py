from __future__ import annotations

import ast
import asyncio
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE_PATH = ROOT / "ops" / "telegram_ro" / "service.py"
READ_API_PATH = ROOT / "ops" / "telegram_ro" / "read_api.py"


def _load_service():
    spec = importlib.util.spec_from_file_location("telegram_ro_service", SERVICE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_read_api():
    spec = importlib.util.spec_from_file_location("telegram_ro_read_api", READ_API_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_message_url_parsing_private_and_public():
    service = _load_service()
    assert service.parse_message_url("https://t.me/c/1166593449/224981") == ("-1001166593449", 224981)
    assert service.parse_message_url("https://t.me/example_channel/123") == ("example_channel", 123)
    assert service.parse_message_url("https://t.me/s/example_channel/123") == ("example_channel", 123)


def test_chat_reference_parsing_supports_saved_private_and_public():
    api = _load_read_api()
    assert api.parse_chat_ref("saved") == "me"
    assert api.parse_chat_ref("Избранное") == "me"
    assert api.parse_chat_ref("https://t.me/c/1166593449/224981") == "-1001166593449"
    assert api.parse_chat_ref("@example_channel") == "example_channel"


def test_non_message_urls_rejected():
    service = _load_service()
    for url in ["http://t.me/example_channel/123", "https://example.com/c/1166593449/224981", "https://t.me/+invite", "https://t.me/c/not-a-number/123"]:
        try:
            service.parse_message_url(url)
        except service.RequestError:
            pass
        else:
            raise AssertionError(url)


def test_protocol_is_read_allowlist_only():
    service = _load_service()
    assert service.ALLOWED_ACTIONS == frozenset({"health", "message", "download", "search", "history"})
    for action in ["send", "send_message", "reply", "forward", "edit", "delete", "reaction", "join", "leave", "raw", "invoke", "mark_read", "typing", "story_view"]:
        try:
            service.validate_action(action)
        except service.RequestError as exc:
            assert str(exc) == "action_not_allowed"
        else:
            raise AssertionError(f"forbidden action accepted: {action}")


def test_no_mutator_or_read_state_api_escape_hatches_in_service_sources():
    source = SERVICE_PATH.read_text(encoding="utf-8") + "\n" + READ_API_PATH.read_text(encoding="utf-8")
    banned_tokens = {
        "send_message", "send_file", "forward_messages", "edit_message", "delete_messages", "send_reaction",
        "join_channel", "leave_channel", "send_read_acknowledge", "mark_read", "read_history", "read_message_contents",
        "read_reactions", "read_mentions", "update_status", "set_typing", "get_messages_views", "read_stories",
        "increment_story_views", "received_messages", "report_read_metrics", "report_music_listen", "ReadHistoryRequest",
        "ReadMessageContentsRequest", "ReadReactionsRequest", "ReadMentionsRequest", "UpdateStatusRequest", "SetTypingRequest",
        "GetMessagesViewsRequest", "ReadStoriesRequest", "IncrementStoryViewsRequest", "ReceivedMessagesRequest",
    }
    request_classes = {token for token in banned_tokens if token.endswith("Request")}
    assert not {token for token in request_classes if token in source}
    banned_attrs = {token for token in banned_tokens if not token.endswith("Request")} | {"__call__", "invoke"}
    for path in (SERVICE_PATH, READ_API_PATH):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert attrs.isdisjoint(banned_attrs), attrs & banned_attrs


def test_telethon_client_disables_update_stream_and_bounds_flood_sleep():
    source = SERVICE_PATH.read_text(encoding="utf-8")
    assert "receive_updates=False" in source
    assert "flood_sleep_threshold=20" in source


def test_broad_history_scan_requires_since_bound():
    api = _load_read_api()

    class NeverUsedClient:
        pass

    try:
        asyncio.run(api.run_search(NeverUsedClient(), {"scope": "all", "query": ""}))
    except api.ReadApiError as exc:
        assert str(exc) == "since_required_for_all_history_scan"
    else:
        raise AssertionError("unbounded all-history scan was accepted")


def test_cursor_roundtrip_is_opaque_and_stable():
    api = _load_read_api()
    cursor = api._cursor_encode("-1001166593449", 224981)
    assert api._cursor_decode(cursor) == {"peer": "-1001166593449", "offset_id": 224981}


def test_all_scope_cursor_roundtrip_tracks_dialog_page_and_message_offset():
    api = _load_read_api()
    from datetime import datetime, timezone
    stamp = datetime(2026, 9, 16, 10, 30, tzinfo=timezone.utc)
    cursor = api._all_cursor_encode(page_peer="-100123", page_offset_id=77, page_offset_date=stamp, page_index=4, message_offset_id=55)
    decoded = api._all_cursor_decode(cursor)
    assert decoded["page_peer"] == "-100123"
    assert decoded["page_offset_id"] == 77
    assert decoded["page_offset_date"] == stamp
    assert decoded["page_index"] == 4
    assert decoded["message_offset_id"] == 55


def test_dialog_page_is_hard_bounded_to_one_small_page():
    api = _load_read_api()
    from types import SimpleNamespace
    class Client:
        def iter_dialogs(self, **kwargs):
            assert kwargs["limit"] == api.MAX_DIALOGS_PER_REQUEST + 1
            async def gen():
                for i in range(api.MAX_DIALOGS_PER_REQUEST + 1):
                    entity = SimpleNamespace(id=i + 1)
                    yield SimpleNamespace(id=i + 1, entity=entity, input_entity=entity, date=None, message=SimpleNamespace(id=1000-i))
            return gen()
    page, more = asyncio.run(api.dialog_page(Client(), {"page_peer": None, "page_offset_id": 0, "page_offset_date": None}))
    assert len(page) == api.MAX_DIALOGS_PER_REQUEST
    assert more is True


def test_no_unbounded_dialog_fallback_exists():
    source = SERVICE_PATH.read_text(encoding="utf-8") + "\n" + READ_API_PATH.read_text(encoding="utf-8")
    assert "iter_dialogs()" not in source
    assert "limit=MAX_PEER_RESOLVE_DIALOGS" in source


def test_dialog_continuation_excludes_pinned_to_avoid_page_duplicates():
    api = _load_read_api()
    from types import SimpleNamespace
    class Client:
        async def get_input_entity(self, value):
            return value
        def iter_dialogs(self, **kwargs):
            assert kwargs["ignore_pinned"] is True
            async def gen():
                return
                yield
            return gen()
    cursor = {"page_peer": "-100123", "page_offset_id": 77, "page_offset_date": None}
    page, more = asyncio.run(api.dialog_page(Client(), cursor))
    assert page == []
    assert more is False


def test_download_rejects_expiring_and_paid_media():
    service = _load_service()
    from types import SimpleNamespace

    expiring = SimpleNamespace(media=SimpleNamespace(ttl_seconds=30))
    try:
        service._downloadable_media(expiring)
    except service.RequestError as exc:
        assert str(exc) == "expiring_media_download_disabled"
    else:
        raise AssertionError("expiring media download was accepted")

    Paid = type("MessageMediaPaidMedia", (), {})
    paid = SimpleNamespace(media=Paid())
    try:
        service._downloadable_media(paid)
    except service.RequestError as exc:
        assert str(exc) == "paid_media_download_disabled"
    else:
        raise AssertionError("locked paid media download was accepted")
