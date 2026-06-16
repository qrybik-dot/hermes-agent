"""TelegramAdapter.send_draft plain-text formatting.

Bot API 9.5 ``sendMessageDraft`` powers the animated streaming preview in
DMs. Telegram draft/live preview should stay plain text so copy/paste and
final report layout do not drift between draft, edit, and final sends.
"""
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return
    mod = MagicMock()
    mod.error.NetworkError = type("NetworkError", (OSError,), {})
    mod.error.TimedOut = type("TimedOut", (OSError,), {})
    mod.error.BadRequest = type("BadRequest", (Exception,), {})
    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, mod)
    sys.modules.setdefault("telegram.error", mod.error)


_ensure_telegram_mock()

from gateway.platforms.telegram import TelegramAdapter  # noqa: E402


def _make_adapter() -> TelegramAdapter:
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="***"))
    adapter._bot = MagicMock()
    adapter._bot.send_message_draft = AsyncMock(return_value=True)
    return adapter


@pytest.mark.asyncio
async def test_send_draft_uses_plain_text_without_parse_mode():
    """Happy path: draft is sent as raw plain text, not MarkdownV2/Rich."""
    adapter = _make_adapter()
    adapter.format_message = lambda c: f"FMT::{c}"

    result = await adapter.send_draft("123", 7, "**bold** body")

    assert result.success is True
    adapter._bot.send_message_draft.assert_awaited_once()
    kwargs = adapter._bot.send_message_draft.await_args.kwargs
    assert kwargs["text"] == "**bold** body"
    assert "parse_mode" not in kwargs
    assert kwargs["chat_id"] == 123
    assert kwargs["draft_id"] == 7


@pytest.mark.asyncio
async def test_send_draft_does_not_retry_markdownv2_error_path():
    """There is no MarkdownV2 first attempt anymore; one plain attempt only."""
    adapter = _make_adapter()
    calls = []

    async def _draft(**kwargs):
        calls.append(kwargs)
        return True

    adapter._bot.send_message_draft = AsyncMock(side_effect=_draft)

    result = await adapter.send_draft("123", 9, "weird _text")

    assert result.success is True
    assert len(calls) == 1
    assert "parse_mode" not in calls[0]
    assert calls[0]["text"] == "weird _text"


@pytest.mark.asyncio
async def test_send_draft_failure_returns_failure_for_edit_fallback():
    """Draft failures return failure so the caller falls back to edit transport."""
    adapter = _make_adapter()
    calls = []

    async def _draft(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("drafts disabled for this chat")

    adapter._bot.send_message_draft = AsyncMock(side_effect=_draft)

    result = await adapter.send_draft("123", 11, "hi")

    assert result.success is False
    assert len(calls) == 1
