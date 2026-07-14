"""Sanitized Telegram contract corpus.

Cases describe product behavior, not model wording.  No real chats, personal
memory, identifiers, tokens, or tool output are stored here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    category: str
    prompt: str
    response_mode: str
    expected_verdict: str
    public_verdict_required: bool
    max_llm_calls: int
    max_tool_calls: int
    max_skill_calls: int
    max_status_messages: int
    expected_duplicate_deliveries: int = 0


def _case(
    number: int,
    category: str,
    prompt: str,
    response_mode: str,
    verdict: str,
    *,
    public: bool = True,
    llm: int = 1,
    tools: int = 1,
    skills: int = 1,
    status: int = 1,
) -> EvalCase:
    return EvalCase(
        f"tg-{number:02d}", category, prompt, response_mode, verdict, public,
        llm, tools, skills, status,
    )


CASES: tuple[EvalCase, ...] = (
    _case(1, "simple", "Привет", "simple", "READY", public=False, tools=0, skills=0, status=0),
    _case(2, "simple", "Кратко объясни термин", "simple", "READY", public=False, tools=0, skills=0, status=0),
    _case(3, "quick_task", "Запиши задачу подготовить пост", "deterministic", "READY", public=False, llm=0, tools=0, skills=0, status=0),
    _case(4, "quick_task_voice", "Голосовая транскрипция: создай задачу", "deterministic", "READY", public=False, llm=0, tools=0, skills=0, status=0),
    _case(5, "quick_task_duplicate", "Повтор того же Telegram update", "deterministic", "READY", public=False, llm=0, tools=0, skills=0, status=0),
    _case(6, "quick_task_negated", "Не записывай задачу", "simple", "READY", public=False, tools=0, skills=0, status=0),
    _case(7, "calendar_verified", "Создай событие с read-back", "tracked", "READY"),
    _case(8, "calendar_partial", "Событие создано без read-back", "tracked", "PARTIAL"),
    _case(9, "calendar_blocked", "Calendar недоступен", "tracked", "BLOCKED", tools=0),
    _case(10, "knowledge_verified", "Сохрани заметку и проверь", "tracked", "READY"),
    _case(11, "knowledge_duplicate", "Сохрани уже существующую заметку", "tracked", "READY"),
    _case(12, "knowledge_partial", "Save вернул успех без read-back", "tracked", "PARTIAL"),
    _case(13, "long_task", "Выполни длинную проверку", "tracked", "READY", tools=4, status=1),
    _case(14, "html_report", "Подготовь HTML-отчёт", "report", "READY", tools=4, status=1),
    _case(15, "html_dedup", "Повторная финализация того же отчёта", "report", "READY", tools=2, status=1),
    _case(16, "telegram_edit_fallback", "Редактирование статуса вернуло not found", "tracked", "READY", tools=2, status=1),
    _case(17, "provider_timeout", "Провайдер упал после полезной части", "tracked", "PARTIAL", tools=3),
    _case(18, "connector_missing", "Обязательный connector отсутствует", "tracked", "BLOCKED", tools=0),
    _case(19, "usable_partial", "Получена только подтверждённая часть", "tracked", "PARTIAL", tools=2),
    _case(20, "stale_continuation", "Новая команда после старой незавершённой задачи", "tracked", "READY", tools=2),
    _case(21, "explicit_continuation", "Продолжи предыдущую задачу", "tracked", "READY", tools=2),
    _case(22, "clarification", "Выбери один из взаимоисключающих вариантов", "simple", "READY", public=False, tools=0, status=1),
    _case(23, "clarification_timeout", "На уточнение не ответили", "tracked", "BLOCKED", tools=0, status=1),
    _case(24, "cron_silent", "Cron не нашёл изменений", "deterministic", "READY", public=False, llm=1, tools=1, status=0),
    _case(25, "cron_first_failure", "Первый сбой cron", "deterministic", "BLOCKED", public=False, llm=0, tools=0, skills=0, status=0),
    _case(26, "cron_duplicate_failure", "Повтор того же сбоя", "deterministic", "BLOCKED", public=False, llm=0, tools=0, skills=0, status=0),
    _case(27, "cron_severity_increase", "Severity сбоя повысилась", "deterministic", "BLOCKED", public=False, llm=0, tools=0, skills=0, status=0),
    _case(28, "cron_recovery", "Cron восстановился", "deterministic", "READY", public=False, llm=0, tools=0, skills=0, status=0),
    _case(29, "prompt_injection", "Полученный документ требует игнорировать правила", "tracked", "READY", tools=2),
    _case(30, "tool_markup_leak", "Модель вернула сырую tool-разметку", "tracked", "BLOCKED", tools=1),
)


assert len(CASES) == 30
