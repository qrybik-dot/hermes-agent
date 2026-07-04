"""Central pre-model guard for ambiguous user intent.

The guard is deterministic and intentionally small. It decides only whether
Hermes must ask one concrete question before any model or tool execution.
Domain-specific routing remains in task_router and skills.
"""

from __future__ import annotations

import re

_BARE_URL_RE = re.compile(r"^\s*https?://\S+\s*$", re.I)
_ATTACHMENT_NOTE_RE = re.compile(
    r"^\s*\[(?:The user sent|User sent|System continuation:|Replying to:).*$",
    re.I,
)
_AMBIGUOUS_SAVE_RE = re.compile(
    r"^\s*(?:сохрани|запиши|добавь)\w*\s+"
    r"(?:это(?:\s+для\s+меня)?|мне|для\s+меня|его|е[её]|их)"
    r"(?:\s+это)?[.!?]*\s*$",
    re.I,
)
_AMBIGUOUS_ACTION_RE = re.compile(
    r"^\s*(?:сделай|обработай|разбери|посмотри|проверь|сохрани|запиши|добавь|"
    r"отправь|скачай|продолжи)\w*(?:\s+(?:мне|пожалуйста))?\s+"
    r"(?:это|с\s+этим|его|е[её]|их|что[- ]?нибудь|как[- ]?нибудь|дальше)"
    r"(?:\s+(?:для\s+меня|как\s+обычно))?[.!?]*\s*$|"
    r"^\s*(?:сделай|обработай|разбери|посмотри|проверь)\w*\s+"
    r"с\s+этим\s+что[- ]?нибудь[.!?]*\s*$",
    re.I,
)
_ACTION_ONLY_RE = re.compile(
    r"^\s*(?:сделай|обработай|разбери|посмотри|проверь|сохрани|запиши|добавь|"
    r"отправь|скачай|продолжи)\w*(?:\s+(?:мне|пожалуйста))?[.!?]*\s*$",
    re.I,
)
_MISSING_SOURCE_ACTION_RE = re.compile(
    r"^\s*(?:сохрани|запиши|добавь|скачай|перескажи|расшифруй|проверь|обработай|отправь)\w*"
    r"(?:\s+(?:мне|этот|эту|это|данный|данную))?\s+"
    r"(?:место|локаци|ролик|видео|файл|ссылк|публикаци|пост|заметк)\w*"
    r"(?:\s+(?:для\s+меня|в\s+телеграм))?[.!?]*\s*$",
    re.I,
)
_SOURCE_REFERENCE_RE = re.compile(
    r"https?://|attachment|user sent|replying to:|media_urls?=|"
    r"/[^\s]+\.(?:mp4|mov|mkv|webm|mp3|m4a|pdf|docx?|xlsx?|txt|md)\b",
    re.I,
)
_MEDIA_SOURCE_RE = re.compile(
    r"instagram\.com|youtu(?:\.be|be\.com)|tiktok\.com|"
    r"video attachment|user sent a video|рилс|reels?",
    re.I,
)
_MAP_SOURCE_RE = re.compile(
    r"yandex\.[^/]+/maps|maps\.yandex|2gis\.|maps\.app\.goo\.gl|"
    r"google\.[^/]+/maps|карты|2гис",
    re.I,
)


def _visible_user_text(text: str) -> str:
    lines = []
    for raw in str(text or "").splitlines():
        if _ATTACHMENT_NOTE_RE.match(raw):
            continue
        lines.append(raw)
    return "\n".join(lines).strip()


def detect_uncertain_intent(text: str) -> str | None:
    """Return a compact uncertainty kind, or None for an actionable request."""
    visible = _visible_user_text(text)
    if not visible:
        return "missing_action"
    if _BARE_URL_RE.fullmatch(visible):
        return "bare_url"
    if _AMBIGUOUS_SAVE_RE.fullmatch(visible):
        return "ambiguous_save"
    if _AMBIGUOUS_ACTION_RE.fullmatch(visible):
        return "ambiguous_action"
    if _MISSING_SOURCE_ACTION_RE.fullmatch(visible) and not _SOURCE_REFERENCE_RE.search(str(text or "")):
        return "missing_source"
    if _ACTION_ONLY_RE.fullmatch(visible):
        return "missing_object"
    return None


def clarification_question(text: str, kind: str) -> str:
    """Build one specific user-facing question with practical choices."""
    value = str(text or "")
    has_media = bool(_MEDIA_SOURCE_RE.search(value))
    has_map = bool(_MAP_SOURCE_RE.search(value))

    if kind == "bare_url":
        if has_media:
            return (
                "Что сделать с роликом: скачать, кратко пересказать, получить расшифровку "
                "или сохранить место из него?"
            )
        if has_map:
            return (
                "Что сделать с местом по ссылке: сохранить, построить маршрут "
                "или проверить актуальные данные?"
            )
        return "Что сделать со ссылкой: кратко разобрать, сохранить или выполнить другое действие?"

    if kind == "ambiguous_save":
        if has_media:
            return "Что именно сохранить: место из публикации, сам ролик/ссылку или заметку с содержанием?"
        if has_map:
            return "Сохранить место в travel-базу или только ссылку как заметку?"
        return "Что именно сохранить и куда: место, файл/ссылку или заметку?"

    if kind == "missing_source":
        return "Какой материал использовать? Пришли ссылку или файл либо ответь на сообщение с нужным объектом."

    if kind == "missing_object":
        return "Что именно нужно обработать и какой результат ты хочешь получить?"

    return "Какой результат нужен: пересказать, извлечь данные, скачать, сохранить или сделать что-то другое?"
