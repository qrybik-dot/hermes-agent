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


_REMINDER_REQUEST_RE = re.compile(
    r"^\s*(?:(?:запиши|создай|поставь|добавь|сделай|установи)\w*"
    r"(?:\s+(?:мне|пожалуйста)){0,2}\s+)?(?:напомни\w*|напоминан\w*)\b",
    re.I,
)
_REMINDER_DATE_RE = re.compile(
    r"\b(?:сегодня|завтра|послезавтра|понедельник|вторник|сред[ау]|четверг|"
    r"пятниц[ау]|суббот[ау]|воскресень[еья]|кажд(?:ый|ую)\s+день|"
    r"\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?|\d{4}-\d{2}-\d{2}|"
    r"\d{1,2}\s+(?:январ[ья]|феврал[ья]|март[а]?|апрел[ья]|ма[йя]|июн[ья]|"
    r"июл[ья]|август[а]?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья]))\b",
    re.I,
)
_REMINDER_TIME_RE = re.compile(
    r"\b(?:[01]?\d|2[0-3])[:.]\d{2}\b|"
    r"\b(?:утром|дн[её]м|вечером|ночью|в\s+(?:[01]?\d|2[0-3]))\b",
    re.I,
)
_RELATIVE_REMINDER_RE = re.compile(
    r"\bчерез\s+(?:полчаса|час|день|неделю|месяц|"
    r"(?:\d+|один|одну|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять|полтора|полторы)\s*"
    r"(?:минут\w*|час\w*|дн\w*|недел\w*|месяц\w*))\b",
    re.I,
)


def _visible_user_text(text: str) -> str:
    lines = []
    for raw in str(text or "").splitlines():
        if _ATTACHMENT_NOTE_RE.match(raw):
            continue
        lines.append(raw)
    return "\n".join(lines).strip()


def is_reminder_request(text: str) -> bool:
    """Return True for an explicit user request to create a reminder."""
    return bool(_REMINDER_REQUEST_RE.search(_visible_user_text(text)))


def detect_uncertain_intent(text: str) -> str | None:
    """Return a compact uncertainty kind, or None for an actionable request."""
    visible = _visible_user_text(text)
    if not visible:
        return "missing_action"
    if _BARE_URL_RE.fullmatch(visible):
        return "bare_url"
    if is_reminder_request(visible) and not _RELATIVE_REMINDER_RE.search(visible):
        has_date = bool(_REMINDER_DATE_RE.search(visible))
        has_time = bool(_REMINDER_TIME_RE.search(visible))
        if not has_date and not has_time:
            return "missing_reminder_date_time"
        if not has_date:
            return "missing_reminder_date"
        if not has_time:
            return "missing_reminder_time"
    if _AMBIGUOUS_SAVE_RE.fullmatch(visible):
        return "ambiguous_save"
    if _AMBIGUOUS_ACTION_RE.fullmatch(visible):
        return "ambiguous_action"
    if _MISSING_SOURCE_ACTION_RE.fullmatch(visible) and not _SOURCE_REFERENCE_RE.search(str(text or "")):
        return "missing_source"
    if _ACTION_ONLY_RE.fullmatch(visible):
        return "missing_object"
    return None


def clarification_choices(text: str, kind: str) -> list[str] | None:
    """Return compact choices for deterministic pre-model clarification."""
    if kind == "missing_reminder_date_time":
        return ["Сегодня в 19:00", "Завтра в 09:00", "Завтра в 19:00"]
    if kind == "missing_reminder_date":
        return ["Сегодня", "Завтра", "Послезавтра"]
    if kind == "missing_reminder_time":
        return ["В 09:00", "В 15:00", "В 19:00"]
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

    if kind == "missing_reminder_date_time":
        return "Когда напомнить? Укажи дату и время, например: завтра в 10:00."

    if kind == "missing_reminder_date":
        return "В какой день напомнить? Укажи дату или день, например: завтра."

    if kind == "missing_reminder_time":
        return "Во сколько напомнить? Укажи точное время или часть дня."

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
