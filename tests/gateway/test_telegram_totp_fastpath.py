from types import SimpleNamespace

import pytest

from gateway.mosreg_totp_bridge import TotpBridgeStore
from plugins.platforms.telegram.adapter import TelegramAdapter


class FakeMessage:
    def __init__(self, text="123456", with_reply=True):
        self.text = text
        self.chat = SimpleNamespace(id=42)
        self.from_user = SimpleNamespace(id=7)
        self.reply_to_message = (
            SimpleNamespace(message_id=99, from_user=SimpleNamespace(is_bot=True))
            if with_reply else None
        )
        self.deleted = False

    async def delete(self):
        self.deleted = True


@pytest.mark.asyncio
async def test_totp_reply_is_consumed_before_model(monkeypatch):
    captured = {}

    def fake_put(_self, payload):
        captured.update(payload)
        return {"ok": True}

    monkeypatch.setattr(TotpBridgeStore, "put_reply_value", fake_put)
    adapter = TelegramAdapter.__new__(TelegramAdapter)
    adapter._notification_kwargs = lambda _metadata: {}
    sent = []

    async def fake_send(**kwargs):
        sent.append(kwargs)

    adapter._send_message_with_thread_fallback = fake_send
    message = FakeMessage()

    assert await adapter._try_handle_mosreg_totp_reply(message) is True
    assert message.deleted is True
    assert captured["value"] == "123456"
    assert captured["telegram_user_id"] == "7"
    assert sent and "Код принят" in sent[0]["text"]


@pytest.mark.asyncio
async def test_unrelated_six_digits_without_reply_reach_normal_flow():
    adapter = TelegramAdapter.__new__(TelegramAdapter)
    assert await adapter._try_handle_mosreg_totp_reply(FakeMessage(with_reply=False)) is False
