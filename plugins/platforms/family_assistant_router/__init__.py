"""Natural-language routing helper for the Telegram family assistant."""

from __future__ import annotations

import re
from typing import Any

from gateway.family_intent import FamilyIntent, detect_family_intent, operational_context
from tools.registry import tool_error
from tools.vision_tools import vision_analyze_tool

_CONTINUATION_RE = re.compile(
    r"^\s*(?:вот\s+(?:самари|сообщение|переписка)|теперь\b|добавь\s+ещ[её]|"
    r"ещ[её]\b|а\s+теперь\b|после\s+этого\b|продолж|уточнен|дополнен)",
    re.I,
)
_CALENDAR_WORD_RE = re.compile(
    r"google\s+calendar|календар(?:ь|я|е|ю|ём|ем)",
    re.I,
)

_FAMILY_VISION_SCHEMA = {
    "name": "family_vision_analyze",
    "description": (
        "Read one family or medical screenshot from a local Telegram image path. "
        "Extract every visible person, birth date, appointment date, time, "
        "specialty, doctor and place. Mark uncertain fields instead of guessing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "image_url": {
                "type": "string",
                "description": "Local image path or HTTP(S) URL from the current message",
            },
            "instruction": {
                "type": "string",
                "description": "Optional extraction or verification instruction",
            },
        },
        "required": ["image_url"],
    },
}


def _platform_name(event: Any) -> str:
    source = getattr(event, "source", None)
    platform = getattr(source, "platform", "")
    return str(getattr(platform, "value", platform) or "").strip().lower()


def _reply_context(event: Any) -> str:
    values = (
        getattr(event, "reply_to_text", None),
        getattr(event, "reply_to_caption", None),
    )
    return "\n".join(str(value).strip() for value in values if str(value or "").strip())


def _merge_auto_skills(event: Any, intent: FamilyIntent) -> None:
    current = getattr(event, "auto_skill", None)
    if isinstance(current, str):
        skills = [current]
    elif isinstance(current, list):
        skills = [str(item) for item in current if item]
    else:
        skills = []
    if intent.memory_write or intent.memory_readback:
        skills.append("memory-profile")
    if intent.calendar_write or (intent.medical and intent.reminder):
        skills.append("google-workspace")
    if skills:
        event.auto_skill = list(dict.fromkeys(skills))


def _channel_contract(intent: FamilyIntent) -> str:
    parts = [operational_context(intent)]
    if intent.image:
        parts.append(
            "Для каждого пути изображения сначала вызови family_vision_analyze. "
            "Объедини результаты всех изображений с подписью и reply-контекстом."
        )
    if intent.medical and (intent.reminder or intent.calendar_write or intent.image):
        parts.append(
            "После распознавания используй google-workspace. Будущие медицинские "
            "записи создавай отдельными событиями Google Calendar с уведомлением "
            "за 24 часа; прошедшие записи не создавай без явной просьбы."
        )
    if intent.memory_write:
        parts.append(
            "Сохрани факт через memory-profile/Hermes Knowledge и затем найди эту "
            "же запись повторным поиском. Только такой read-back подтверждает сохранение."
        )
    if intent.memory_readback:
        parts.append(
            "Проверь факт новым поиском в memory-profile/Hermes Knowledge. Не используй "
            "результат календаря или другого инструмента как подтверждение памяти."
        )
    return "\n\n".join(part for part in parts if part)


def _rewrite_family_event(*, event: Any, **_: Any) -> dict[str, str]:
    if _platform_name(event) != "telegram":
        return {"action": "allow"}

    original = str(getattr(event, "text", "") or "").strip()
    reply = _reply_context(event)
    media_urls = [
        str(item).strip()
        for item in (getattr(event, "media_urls", None) or [])
        if str(item).strip()
    ]
    combined = "\n".join(part for part in (original, reply) if part)
    if media_urls:
        combined += "\n[The user sent an image]"
    intent = detect_family_intent(combined)
    if not intent.active:
        return {"action": "allow"}

    _merge_auto_skills(event, intent)
    existing_prompt = str(getattr(event, "channel_prompt", "") or "").strip()
    event.channel_prompt = "\n\n".join(
        part for part in (existing_prompt, _channel_contract(intent)) if part
    )

    routed_original = original
    if intent.image and _CALENDAR_WORD_RE.search(routed_original):
        routed_original = _CALENDAR_WORD_RE.sub("семейное расписание", routed_original)

    parts: list[str] = ["task_role: planning"]
    if reply and (_CONTINUATION_RE.search(original) or getattr(event, "reply_to_message_id", None)):
        parts.append("Продолжай текущую задачу с учётом нового сообщения.")
    parts.append(routed_original)
    if reply:
        parts.append("Контекст сообщения, на которое ответил пользователь:\n" + reply)
    if media_urls:
        parts.append(
            "Изображения текущей задачи:\n"
            + "\n".join(f"- {path}" for path in media_urls)
        )
    parts.append(
        "Выполни задачу целиком, не требуя от пользователя шаблонного промпта. "
        "Внутренние этапы не показывай, если они не нужны для короткого статуса."
    )
    if intent.memory_write or intent.memory_readback:
        parts.append("Нужна постоянная память и проверка записи из того же хранилища.")

    return {"action": "rewrite", "text": "\n\n".join(parts).strip()}


async def _handle_family_vision(args: dict, **_: Any) -> str:
    image_url = str((args or {}).get("image_url") or "").strip()
    if not image_url:
        return tool_error("image_url is required")
    instruction = str((args or {}).get("instruction") or "").strip()
    if not instruction:
        instruction = (
            "Извлеки все видимые факты без догадок: ФИО или член семьи, дату "
            "рождения, дату и время каждого приёма, специальность или врача, "
            "клинику и адрес. Отдельно перечисли сомнительные поля."
        )
    return await vision_analyze_tool(image_url=image_url, user_prompt=instruction)


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _rewrite_family_event)
    ctx.register_tool(
        name="family_vision_analyze",
        toolset="file",
        schema=_FAMILY_VISION_SCHEMA,
        handler=_handle_family_vision,
        check_fn=lambda: True,
        is_async=True,
        description="Extract structured family and medical data from screenshots",
        emoji="🖼️",
    )
