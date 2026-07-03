from types import SimpleNamespace

import pytest

from gateway.family_intent import detect_family_intent
from plugins.platforms.family_assistant_router import (
    _handle_family_vision,
    _rewrite_family_event,
)


def _event(text: str, *, reply: str = "", media: tuple[str, ...] = ()):
    return SimpleNamespace(
        text=text,
        source=SimpleNamespace(platform=SimpleNamespace(value="telegram")),
        reply_to_text=reply or None,
        reply_to_caption=None,
        reply_to_message_id="42" if reply else None,
        media_urls=list(media),
        auto_skill=None,
        channel_prompt=None,
    )


def test_memory_write_routes_to_quality_context_and_memory_skill():
    event = _event("Запомни врача ортодонта Веры, его ФИО и где он работает")

    result = _rewrite_family_event(event=event)

    assert result["action"] == "rewrite"
    assert "task_role:" not in result["text"]
    assert "большая задача" not in result["text"]
    assert "memory-profile" in event.auto_skill
    assert "read-back" in event.channel_prompt
    assert "календаря" in event.channel_prompt


def test_memory_readback_is_not_confirmed_by_calendar():
    event = _event("Ты занёс в базу, как зовут ортодонта Веры? Проверь")

    result = _rewrite_family_event(event=event)

    assert result["action"] == "rewrite"
    assert "task_role:" not in result["text"]
    assert "memory-profile" in event.auto_skill
    assert "новым поиском" in event.channel_prompt
    assert "не подтверждает память" in event.channel_prompt


def test_screenshot_request_preserves_media_paths_and_family_contract():
    event = _event(
        "Посмотри и сделай напоминания каждому члену семьи",
        media=("/tmp/medical-1.png", "/tmp/medical-2.png"),
    )

    result = _rewrite_family_event(event=event)

    assert result["action"] == "rewrite"
    assert "task_role:" not in result["text"]
    assert "/tmp/medical-1.png" in result["text"]
    assert "/tmp/medical-2.png" in result["text"]
    assert "family_vision_analyze" in event.channel_prompt
    assert "Google Calendar" in event.channel_prompt


def test_calendar_word_in_image_request_is_deferred_until_vision():
    event = _event(
        "Посмотри скриншот и поставь все приёмы в календарь",
        media=("/tmp/medical.png",),
    )

    result = _rewrite_family_event(event=event)

    assert result["action"] == "rewrite"
    assert "семейное расписание" in result["text"]
    assert "календарь" not in result["text"].lower()
    assert "google-workspace" in event.auto_skill
    assert "family_vision_analyze" in event.channel_prompt


def test_reply_summary_is_merged_into_the_followup_turn():
    event = _event(
        "Вот самари звонка, поставь в календарь событие и напоминание за сутки",
        reply="9 июля 2026 в 09:30, Вера, ортодонт Каукин Егор Николаевич",
    )

    result = _rewrite_family_event(event=event)

    assert result["action"] == "rewrite"
    assert "task_role:" not in result["text"]
    assert "Продолжай текущую задачу" in result["text"]
    assert "9 июля 2026 в 09:30" in result["text"]
    assert "google-workspace" in event.auto_skill


def test_unrelated_telegram_message_is_untouched():
    event = _event("Как приготовить омлет?")

    assert _rewrite_family_event(event=event) == {"action": "allow"}


def test_family_intent_distinguishes_memory_calendar_and_image():
    memory = detect_family_intent("Запомни врача Веры")
    calendar = detect_family_intent("Поставь приём врача в календарь")
    image = detect_family_intent("[The user sent an image] приёмы ребёнка")

    assert memory.memory_write
    assert calendar.calendar_write
    assert image.image and image.family_sensitive
    assert image.active is False


@pytest.mark.asyncio
async def test_family_vision_tool_uses_existing_vision_router(monkeypatch):
    calls = {}

    async def fake_vision(*, image_url, user_prompt):
        calls["image_url"] = image_url
        calls["user_prompt"] = user_prompt
        return '{"success": true, "analysis": "ok"}'

    monkeypatch.setattr(
        "plugins.platforms.family_assistant_router.vision_analyze_tool",
        fake_vision,
    )

    result = await _handle_family_vision({"image_url": "/tmp/medical.png"})

    assert '"success": true' in result
    assert calls["image_url"] == "/tmp/medical.png"
    assert "дату и время каждого приёма" in calls["user_prompt"]


def test_plain_image_does_not_activate_family_router():
    event = _event("Что здесь?", media=("/tmp/plain.png",))
    assert _rewrite_family_event(event=event) == {"action": "allow"}


def test_plain_forward_does_not_activate_family_router():
    event = _event("Посмотри и скажи суть", reply="[Forwarded message] обычный пост")
    assert _rewrite_family_event(event=event) == {"action": "allow"}
