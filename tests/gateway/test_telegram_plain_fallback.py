from types import SimpleNamespace

import pytest
import telegram.error as telegram_error
from telegram.error import NetworkError

from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter


class _ParseBadRequest(NetworkError):
    pass


class _FakeBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise _ParseBadRequest("Can't parse entities: Character '(' is reserved")
        return SimpleNamespace(message_id=123)

    async def send_chat_action(self, **kwargs):
        return None


@pytest.mark.asyncio
async def test_parse_error_falls_back_once_to_plain_text(monkeypatch):
    monkeypatch.setattr(telegram_error, "BadRequest", _ParseBadRequest)
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test"))
    bot = _FakeBot()
    adapter._bot = bot

    result = await adapter.send("1", "Отчёт (финальный)", metadata={"notify": True})

    assert result.success is True
    assert len(bot.calls) == 2
    assert bot.calls[0].get("parse_mode") is not None
    assert bot.calls[1].get("parse_mode") is None
