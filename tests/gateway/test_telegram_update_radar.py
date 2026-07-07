from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from hermes_cli import update_radar_actions as actions
from plugins.platforms.telegram.adapter import TelegramAdapter


def _finding():
    return {
        "key": "cliproxy",
        "name": "CLIProxyAPI",
        "finding_type": "release",
        "current": "7.2.50",
        "latest": "7.2.51",
        "status": "ok",
        "verdict": "✅ можно обновлять",
        "reason": "new release",
        "source": "official",
    }


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(actions, "ACTION_FILE", tmp_path / "actions.json")
    monkeypatch.setattr(actions, "RADAR_STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(actions, "telegram_markup", lambda action: SimpleNamespace(action=action))


@pytest.mark.asyncio
async def test_update_radar_callback_is_dispatched_first():
    adapter = TelegramAdapter(PlatformConfig(enabled=True))
    called = []

    async def handler(query, data, **context):
        called.append((query, data, context))

    adapter._handle_update_radar_callback = handler
    query = SimpleNamespace(
        data="ur:abc123:manual",
        message=SimpleNamespace(
            chat_id=123,
            chat=SimpleNamespace(type="private"),
            message_thread_id=None,
        ),
        from_user=SimpleNamespace(id=1, first_name="Anton"),
    )

    await adapter._handle_callback_query(SimpleNamespace(callback_query=query), None)

    assert called
    assert called[0][1] == "ur:abc123:manual"


@pytest.mark.asyncio
async def test_execute_button_enqueues_authorized_normal_hermes_turn(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    action = actions.create_action([_finding()])
    actions.set_selection(action["token"], ["cliproxy"], stage="confirm")

    adapter = TelegramAdapter(PlatformConfig(enabled=True))
    adapter._is_callback_user_authorized = lambda *args, **kwargs: True
    event = SimpleNamespace(
        text="",
        source=SimpleNamespace(user_id=None, user_name=None, is_bot=True, message_id=None),
        message_id=None,
        reply_to_text=None,
        metadata={},
    )
    adapter._build_message_event = lambda *args, **kwargs: event
    adapter.handle_message = AsyncMock()

    query = SimpleNamespace(
        data=f"ur:{action['token']}:execute",
        from_user=SimpleNamespace(id=42, first_name="Anton", full_name="Anton V"),
        message=SimpleNamespace(message_id=777, text="Update Radar card"),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
    )

    await adapter._handle_update_radar_callback(
        query,
        query.data,
        query_chat_id=123,
        query_chat_type="private",
        query_thread_id=None,
        query_user_name="Anton",
    )
    await asyncio.sleep(0)

    adapter.handle_message.assert_awaited_once()
    queued_event = adapter.handle_message.await_args.args[0]
    assert queued_event.source.user_id == "42"
    assert queued_event.source.is_bot is False
    assert queued_event.metadata["update_radar_mode"] == "update"
    assert "CLIProxyAPI" in queued_event.text
    assert "Не обновляй остальные компоненты" in queued_event.text

    stored = actions.get_action(action["token"])
    assert stored["status"] == "queued"
    query.edit_message_text.assert_awaited()
