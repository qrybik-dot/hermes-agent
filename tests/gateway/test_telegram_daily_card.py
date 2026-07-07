from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter


@pytest.mark.asyncio
async def test_daily_card_callback_is_dispatched_before_other_callbacks():
    adapter = TelegramAdapter(PlatformConfig(enabled=True))
    called = []

    async def handler(query, data, **context):
        called.append((query, data, context))

    adapter._handle_daily_card_callback = handler
    query = SimpleNamespace(
        data="dc:d:m:20260707:abc",
        message=SimpleNamespace(
            chat_id=123,
            chat=SimpleNamespace(type="private"),
            message_thread_id=None,
        ),
        from_user=SimpleNamespace(id=1, first_name="Anton"),
    )
    update = SimpleNamespace(callback_query=query)

    await adapter._handle_callback_query(update, None)

    assert called
    assert called[0][1] == "dc:d:m:20260707:abc"


class _FakeBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(message_id=77)


@pytest.mark.asyncio
async def test_more_tasks_callback_sends_separate_full_list(monkeypatch):
    from hermes_cli import daily_card as dc
    from hermes_cli import kanban_db as kb

    adapter = TelegramAdapter(PlatformConfig(enabled=True))
    adapter._bot = _FakeBot()
    adapter._is_callback_user_authorized = lambda *args, **kwargs: True
    monkeypatch.setattr(
        dc,
        "load_settings",
        lambda: SimpleNamespace(timezone="Europe/Moscow"),
    )
    monkeypatch.setattr(
        dc,
        "render_full_task_list_from_db",
        lambda *args, **kwargs: dc.CardRender(
            text="Все активные задачи\n\nБез даты\n☐ Позвонить",
            parse_mode="HTML",
        ),
    )
    monkeypatch.setattr(kb, "kanban_db_path", lambda board: "/tmp/kanban.db")

    query = SimpleNamespace(
        from_user=SimpleNamespace(id=1),
        message=SimpleNamespace(chat_id=123, message_id=3915),
        answer=AsyncMock(),
    )
    await adapter._handle_daily_card_callback(
        query,
        "dc:l:20260707",
        query_chat_id=123,
        query_chat_type="private",
        query_thread_id=None,
        query_user_name="Anton",
    )

    assert len(adapter._bot.calls) == 1
    call = adapter._bot.calls[0]
    assert call["text"].startswith("Все активные задачи")
    assert call["parse_mode"] == "HTML"
    assert call["reply_to_message_id"] == 3915
    query.answer.assert_awaited_once_with(text="Полный список отправлен")


@pytest.mark.asyncio
async def test_legacy_kanban_marker_becomes_buttons_and_is_hidden(monkeypatch):
    import plugins.platforms.telegram.adapter as telegram_adapter

    class FakeButton:
        def __init__(self, text, callback_data):
            self.text = text
            self.callback_data = callback_data

    class FakeMarkup:
        def __init__(self, inline_keyboard):
            self.inline_keyboard = inline_keyboard

    monkeypatch.setattr(telegram_adapter, "InlineKeyboardButton", FakeButton)
    monkeypatch.setattr(telegram_adapter, "InlineKeyboardMarkup", FakeMarkup)

    adapter = TelegramAdapter(PlatformConfig(enabled=True))
    adapter._bot = _FakeBot()
    adapter.send_typing = AsyncMock()

    result = await adapter.send(
        "123",
        "Сегодня / активные:\nЗадача\n\n[ДОСТУПНЫ КНОПКИ: fam-1:Закрыть задачу]",
    )

    assert result.success is True
    assert len(adapter._bot.calls) == 1
    call = adapter._bot.calls[0]
    assert "ДОСТУПНЫ" not in call["text"]
    assert call["reply_markup"] is not None
    button = call["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Закрыть задачу"
    assert button.callback_data == "kb:done:fam-1"
