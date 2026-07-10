"""Deterministic router for explicit save/remember commands.

This module is intentionally small and dependency-free so it can be used before
LLM routing in Telegram/gateway flows. The router does not save anything by
itself; it decides whether a turn should be handled by Knowledge, delegated to a
reminder/task route, or clarified with compact buttons.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Iterable, Literal

Route = Literal[
    "save_knowledge",
    "delegate_action",
    "ask_clarification",
    "ignore",
]

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# Common Russian user phrasing and typo variants. Keep this broad, but do not
# include destructive verbs such as delete/remove.
_SAVE_RE = re.compile(
    r"\b("
    r"сохрани(?:ть)?|сохрарни|запиши|запомни|добавь|зафиксируй|"
    r"занеси|оставь|кинь|закинь|не\s+потеряй|"
    r"save|remember|record|add"
    r")\b",
    re.IGNORECASE,
)

_KNOWLEDGE_HINT_RE = re.compile(
    r"\b("
    r"в\s+баз[уы]|в\s+knowledge|в\s+памят[ьи]|в\s+знани[ея]|"
    r"инф[аоу]|информаци[яю]|ссылк[ауи]|стать[яюи]|материал|заметк[ауи]|"
    r"карточк[ауи]|конспект|исследовани[ея]|источник|reference|kb|base"
    r")\b",
    re.IGNORECASE,
)

_EXPLICIT_ACTION_HINT_RE = re.compile(
    r"\b("
    r"напоминани[ея]|напомни|reminder|"
    r"задач[ауи]|todo|to-do|таск|task|"
    r"календар[ьяею]|calendar|"
    r"встреч[ауи]|созвон|event|meeting"
    r")\b",
    re.IGNORECASE,
)

_SOFT_TASK_HINT_RE = re.compile(
    r"\b("
    r"купить|сделать|разобраться|проверить|позвонить|написать|оплатить|"
    r"забрать|отправить|оформить|подготовить|починить"
    r")\b",
    re.IGNORECASE,
)

_DESTRUCTIVE_RE = re.compile(
    r"\b(удали|сотри|очисти|забудь|delete|remove|drop|trash)\b",
    re.IGNORECASE,
)


def _clean(text: str | None) -> str:
    return " ".join(str(text or "").strip().split())


def _has_substantial_context(values: Iterable[str | None]) -> bool:
    for value in values:
        text = _clean(value)
        if not text:
            continue
        if _URL_RE.search(text):
            return True
        # Enough content to save from a reply/citation even if it is not a URL.
        if len(text) >= 20:
            return True
    return False


def _text_after_save_verb(text: str) -> str:
    match = _SAVE_RE.search(text)
    if not match:
        return ""
    return text[match.end():].strip(" :—-.,\n\t")


@dataclass(frozen=True)
class SaveIntentDecision:
    matched: bool
    route: Route
    needs_clarification: bool
    reason: str
    question: str = ""
    buttons: tuple[dict[str, str], ...] = ()
    terminal_allowed: bool = False
    llm_allowed: bool = False

    def asdict(self) -> dict:
        data = asdict(self)
        data["buttons"] = list(self.buttons)
        return data


def _clarification(question: str, *, reason: str) -> SaveIntentDecision:
    return SaveIntentDecision(
        matched=True,
        route="ask_clarification",
        needs_clarification=True,
        reason=reason,
        question=question,
        buttons=(
            {"text": "В базу", "callback_data": "save_intent:knowledge"},
            {"text": "Напоминание", "callback_data": "save_intent:reminder"},
            {"text": "Задача", "callback_data": "save_intent:task"},
            {"text": "Отмена", "callback_data": "save_intent:cancel"},
        ),
        terminal_allowed=False,
        llm_allowed=False,
    )


def detect_save_intent(command_text: str, *context_texts: str | None) -> SaveIntentDecision:
    """Classify save-like commands before LLM routing.

    Rules:
    - explicit Knowledge hints or usable context => save via Knowledge;
    - explicit reminder/task/calendar hints => delegate to the action route;
    - ambiguous one-word/short commands => ask with compact buttons;
    - never route this class through terminal/shell fallbacks.
    """
    command = _clean(command_text)
    if not command:
        return SaveIntentDecision(False, "ignore", False, "empty")

    if _DESTRUCTIVE_RE.search(command):
        return SaveIntentDecision(False, "ignore", False, "destructive intent is not a save command")

    if not _SAVE_RE.search(command):
        return SaveIntentDecision(False, "ignore", False, "no save-like intent")

    has_context = _has_substantial_context(context_texts)
    has_url = bool(_URL_RE.search(command)) or any(bool(_URL_RE.search(_clean(x))) for x in context_texts)
    has_knowledge_hint = bool(_KNOWLEDGE_HINT_RE.search(command))
    has_explicit_action_hint = bool(_EXPLICIT_ACTION_HINT_RE.search(command))
    tail = _text_after_save_verb(command)
    tail_is_substantial = bool(_URL_RE.search(tail)) or len(tail) >= 12
    tail_has_soft_task = bool(_SOFT_TASK_HINT_RE.search(tail))

    if has_explicit_action_hint and not has_knowledge_hint:
        return SaveIntentDecision(
            matched=True,
            route="delegate_action",
            needs_clarification=False,
            reason="explicit reminder/task/calendar hint",
            terminal_allowed=False,
            llm_allowed=False,
        )

    if has_knowledge_hint or has_url or has_context:
        if tail_has_soft_task and not has_knowledge_hint and not has_url and not has_context:
            return _clarification(
                "Куда записать: в базу, как напоминание или как задачу?",
                reason="soft task wording without explicit destination",
            )
        return SaveIntentDecision(
            matched=True,
            route="save_knowledge",
            needs_clarification=False,
            reason="explicit Knowledge hint or saveable context",
            terminal_allowed=False,
            llm_allowed=False,
        )

    if tail_is_substantial and tail_has_soft_task:
        return _clarification(
            "Куда записать: в базу, как напоминание или как задачу?",
            reason="substantial but action-like text",
        )

    if tail_is_substantial:
        return SaveIntentDecision(
            matched=True,
            route="save_knowledge",
            needs_clarification=False,
            reason="substantial note text after save verb",
            terminal_allowed=False,
            llm_allowed=False,
        )

    return _clarification(
        "Что именно сохранить: ссылку, текст выше или сделать из этого задачу?",
        reason="save command without saveable object",
    )


__all__ = ["SaveIntentDecision", "detect_save_intent"]
