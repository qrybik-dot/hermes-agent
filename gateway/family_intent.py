"""Deterministic policy for natural-language family assistant requests."""

from __future__ import annotations

import re
from dataclasses import dataclass

_MEMORY_WRITE_RE = re.compile(
    r"\b(?:запомни|сохрани|запиши|зафиксируй|внеси|добавь)\w*\b.{0,140}"
    r"\b(?:памят|баз[ауеы]|профил|врач|доктор|ортодонт|контакт|данн)\w*|"
    r"^\s*(?:запомни|сохрани|запиши|зафиксируй)\w*\b",
    re.I | re.S,
)
_MEMORY_READBACK_RE = re.compile(
    r"\b(?:проверь|найди|покажи|скажи)\w*\b.{0,160}"
    r"\b(?:памят|баз[аеуы]|сохранил|запомнил|зан[её]с|врач|доктор|ортодонт)\w*|"
    r"\b(?:ты\s+)?(?:запомнил|сохранил|зан[её]с)\w*\b",
    re.I | re.S,
)
_MEDICAL_RE = re.compile(
    r"\b(?:при[её]м|запис[ьи]|врач|доктор|стоматолог|ортодонт|лор|"
    r"оториноларинголог|невролог|офтальмолог|хирург|рентгенолог)\w*\b",
    re.I,
)
_CALENDAR_WRITE_RE = re.compile(
    r"\b(?:сделай|создай|добавь|поставь|занеси|внеси|запиши|запланируй)\w*\b"
    r".{0,160}\b(?:календар|событи|при[её]м|запис|врач|напоминан)\w*",
    re.I | re.S,
)
_REMINDER_RE = re.compile(r"\b(?:напомни|напоминан|напоминать)\w*\b", re.I)
_IMAGE_RE = re.compile(
    r"\[The user sent an image|vision_analyze|image_url:|media_urls?=|attachment|"
    r"скриншот|фото|изображен|картинк",
    re.I,
)
_FORWARD_RE = re.compile(
    r"\[(?:Forwarded|Пересланн)[^\]]*\]|пересланн\w*\s+сообщени|"
    r"вот\s+(?:самари|сообщение|переписка)",
    re.I,
)
_FAMILY_RE = re.compile(
    r"\b(?:семь|реб[её]нок|дети|сын|дочь|ф[её]дор|вера|дата\s+рождения|полис)\w*\b",
    re.I,
)


@dataclass(frozen=True)
class FamilyIntent:
    memory_write: bool = False
    memory_readback: bool = False
    medical: bool = False
    calendar_write: bool = False
    reminder: bool = False
    image: bool = False
    forwarded: bool = False
    family_sensitive: bool = False

    @property
    def requires_quality_model(self) -> bool:
        return bool(
            self.memory_write
            or self.memory_readback
            or self.calendar_write
            or (self.image and self.family_sensitive)
        )

    @property
    def active(self) -> bool:
        return any(self.__dict__.values())


def detect_family_intent(text: str) -> FamilyIntent:
    value = str(text or "")
    image = bool(_IMAGE_RE.search(value))
    reminder = bool(_REMINDER_RE.search(value))
    medical = bool(_MEDICAL_RE.search(value))
    family_sensitive = medical or bool(_FAMILY_RE.search(value))
    return FamilyIntent(
        memory_write=bool(_MEMORY_WRITE_RE.search(value)),
        memory_readback=bool(_MEMORY_READBACK_RE.search(value)),
        medical=medical,
        calendar_write=bool(_CALENDAR_WRITE_RE.search(value)) or (image and reminder),
        reminder=reminder,
        image=image,
        forwarded=bool(_FORWARD_RE.search(value)),
        family_sensitive=family_sensitive,
    )


def operational_context(intent: FamilyIntent) -> str:
    if not intent.active:
        return ""
    return (
        "Контракт семейного ассистента: собери одну задачу из текущего текста, ближайшего reply, "
        "пересланного сообщения, подписи и всех приложенных изображений. Не требуй шаблонный промпт "
        "и не проси повторить данные, которые уже есть в этих источниках. Сначала распознай изображения "
        "через vision и извлеки людей, даты, время, врача, специальность и место. Медицинская запись с "
        "датой и временем является событием Google Calendar, а не отдельным cron-напоминанием. Для "
        "будущего медицинского приёма по умолчанию добавь уведомление за сутки, если пользователь не "
        "указал другое. Обычное напоминание без медицинской записи выполняй через cronjob. Команды "
        "«запомни», «сохрани в базу» и «проверь, занёс ли» относятся к постоянной памяти. После записи "
        "сделай read-back из того же хранилища. Результат календаря не подтверждает память, а результат "
        "памяти не подтверждает календарь. В Telegram верни короткий итог без сырого JSON, имён моделей, "
        "провайдеров, tool completed и внутренних task ID."
    )
