from types import SimpleNamespace

import pytest

from gateway.mosreg_totp_bridge import TotpBridgeStore
from plugins.platforms.telegram.adapter import TelegramAdapter


class FakeMessage:
    def __init__(self, text="123456", with_reply=True, reply_id=99):
        self.text = text
        self.chat = SimpleNamespace(id=42)
        self.from_user = SimpleNamespace(id=7)
        self.reply_to_message = (
            SimpleNamespace(message_id=reply_id, from_user=SimpleNamespace(is_bot=True))
            if with_reply else None
        )
        self.deleted = False

    async def delete(self):
        self.deleted = True


def _adapter():
    adapter = TelegramAdapter.__new__(TelegramAdapter)
    adapter._notification_kwargs = lambda _metadata: {}
    sent = []

    async def fake_send(**kwargs):
        sent.append(kwargs)

    adapter._send_message_with_thread_fallback = fake_send
    return adapter, sent


@pytest.mark.asyncio
async def test_totp_reply_is_consumed_before_model(monkeypatch):
    captured = {}

    def fake_put(_self, payload):
        captured.update(payload)
        return {"ok": True, "status": "stored"}

    monkeypatch.setattr(TotpBridgeStore, "put_reply_value", fake_put)
    adapter, sent = _adapter()
    message = FakeMessage()

    assert await adapter._try_handle_mosreg_totp_reply(message) is True
    assert message.deleted is True
    assert captured["value"] == "123456"
    assert captured["telegram_user_id"] == "7"
    assert sent and "Код принят" in sent[0]["text"]


@pytest.mark.asyncio
async def test_six_digits_without_active_challenge_reach_normal_flow(monkeypatch):
    monkeypatch.setattr(
        TotpBridgeStore, "put_reply_value",
        lambda _self, _payload: {"ok": False, "status": "no_active_challenge"},
    )
    adapter, sent = _adapter()
    message = FakeMessage(with_reply=False)
    assert await adapter._try_handle_mosreg_totp_reply(message) is False
    assert message.deleted is False
    assert sent == []


@pytest.mark.asyncio
async def test_six_digits_without_reply_during_active_challenge_are_consumed(monkeypatch):
    monkeypatch.setattr(
        TotpBridgeStore, "put_reply_value",
        lambda _self, _payload: {"ok": False, "status": "wrong_reply_target"},
    )
    adapter, sent = _adapter()
    message = FakeMessage(with_reply=False)
    assert await adapter._try_handle_mosreg_totp_reply(message) is True
    assert message.deleted is True
    assert "Reply" in sent[0]["text"]


@pytest.mark.asyncio
async def test_stale_replied_code_gets_exact_no_active_challenge_message(monkeypatch):
    monkeypatch.setattr(
        TotpBridgeStore, "put_reply_value",
        lambda _self, _payload: {"ok": False, "status": "no_active_challenge"},
    )
    adapter, sent = _adapter()
    message = FakeMessage(with_reply=True)
    assert await adapter._try_handle_mosreg_totp_reply(message) is True
    assert message.deleted is True
    assert "нет активного запроса 2FA" in sent[0]["text"]
