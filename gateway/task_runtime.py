"""Thin gateway integration for persistent task continuation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import re

from gateway.task_continuation import (
    TaskRecord,
    TaskStateStore,
    format_choice,
    infer_execution_contract,
    is_pause_request,
    should_track_task,
)
from gateway.task_router import TaskRoute, route_turn

_TASK_OUTCOME_RE = re.compile(
    r"(?m)^\s*(?:[-*#>]+\s*)?(?:[^:\n]{0,40}:\s*)?"
    r"(PARTIAL|BLOCKED|INCOMPLETE)\b"
)


def task_reported_non_success(
    final_response: str,
    *,
    result_partial: bool = False,
    result_failed: bool = False,
) -> tuple[str, str] | None:
    if result_failed:
        return "blocked", "agent result flagged failed"
    if result_partial:
        return "incomplete", "agent result flagged partial"
    match = _TASK_OUTCOME_RE.search((final_response or "")[:800])
    if match is None:
        return None
    label = match.group(1)
    if label == "BLOCKED":
        return "blocked", "reported verdict BLOCKED"
    return "incomplete", f"reported verdict {label}"


_IMAGE_DEPENDENCY_RE = re.compile(
    r"\b(?:по|из|с|со)\s+(?:(?:присланн|приложенн|прикрепл[её]нн|этом|этому|данн)\w*\s+){0,3}"
    r"(?:картинк|изображен|фото|скриншот)\w*|"
    r"\b(?:на|в)\s+(?:(?:присланн|приложенн|прикрепл[её]нн|этом|этому|данн)\w*\s+){0,3}"
    r"(?:картинк|изображен|фото|скриншот)\w*|"
    r"\b(?:распознай|прочитай|извлеки|определи)\w*.*(?:картинк|изображен|фото|скриншот)\w*",
    re.I | re.S,
)
_IMAGE_CONTEXT_RE = re.compile(
    r"\[The user sent an image|vision_analyze|image_url:|media_urls?=|attachment",
    re.I,
)
_OUTPUT_SCREENSHOT_ARTIFACT_RE = re.compile(
    r"\b(?:сдела(?:й|ть)|созда(?:й|ть)|сгенерир(?:уй|овать)|получ(?:и|ить)|"
    r"рендер(?:и|ить)|capture|take|create|generate)\w*\b.{0,120}"
    r"(?:скриншот|screenshot)\w*|"
    r"(?:скриншот|screenshot)\w*.{0,120}\b(?:выходн|артефакт|artifact|output|PNG|browser|браузер)\w*",
    re.I | re.S,
)
_CALENDAR_CREATE_RE = re.compile(
    r"\b(?:созда(?:й|ть)|добав(?:ь|ить)|запиш(?:и|ите|ем)|записать|постав(?:ь|ить)|"
    r"назнач(?:ь|ить)|запланиру(?:й|йте|ем|ть))\w*\b[^\n.!?;]{0,80}"
    r"\b(?:календар|встреч|созвон|напоминан|событи)\w*\b|"
    r"\b(?:в\s+календар\w*|google\s+calendar)\b[^\n.!?;]{0,80}"
    r"\b(?:событи|встреч|созвон|напоминан)\w*\b|"
    r"\b(?:create|add|schedule)\b[^\n.!?;]{0,80}"
    r"\b(?:calendar\s+event|google\s+calendar|meeting|call|reminder)\b",
    re.I,
)
_TECHNICAL_EVENT_CONTEXT_RE = re.compile(
    r"\b(?:live-status|progress\s+events?|status\s+events?|runtime\s+events?|delivery\s+events?|"
    r"intermediate\s+events?|промежуточн\w*\s+событи\w*|обработчик\w*\s+событи\w*|"
    r"событи\w*\s+обработчик\w*|тест\w*|handler|webhook|event\s+handler)\b",
    re.I,
)
_RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_RU_MONTH_NAME_RE = "|".join(_RU_MONTHS)
_RU_MONTH_DATE_RE = re.compile(rf"\b\d{{1,2}}\s+(?:{_RU_MONTH_NAME_RE})(?:\s+\d{{4}})?\b", re.I)
_DATE_RE = re.compile(
    rf"\b(?:сегодня|завтра|послезавтра|понедельник|вторник|сред[ау]|четверг|пятниц[ау]|"
    rf"суббот[ау]|воскресень[еья]|\d{{1,2}}[./-]\d{{1,2}}(?:[./-]\d{{2,4}})?|"
    rf"\d{{4}}-\d{{2}}-\d{{2}})\b|\b\d{{1,2}}\s+(?:{_RU_MONTH_NAME_RE})(?:\s+\d{{4}})?\b",
    re.I,
)
_TIME_RE = re.compile(
    r"\b(?:[01]?\d|2[0-3])[:.]\d{2}\b|\b(?:в|на)\s+(?:[01]?\d|2[0-3])\b",
    re.I,
)
_PURPOSE_RE = re.compile(
    r"\b(?:с\s+[А-ЯA-ZЁ][\wё-]+|созвон|встреч[ауы]|при[её]м|дедлайн|"
    r"консультаци|интервью|собеседовани|звонок|обед|ужин|тренировк)\w*\b",
    re.I,
)
_CALENDAR_EVENT_REF_RE = re.compile(
    r"\b(?:event[\s_-]*id|event_id|идентификатор\s+события)\b|"
    r"\bevent_link\s*[:=]|https?://(?:calendar\.google\.com|www\.google\.com/calendar)",
    re.I,
)
_CALENDAR_ID_RE = re.compile(
    r"\b(?:calendar[\s_-]*id|calendar_id|идентификатор\s+календаря|primary)\b|"
    r"\bкалендарь\s*:\s*\S+",
    re.I,
)
_CALENDAR_SUMMARY_RE = re.compile(r"\b(?:summary|название)\"?\s*:", re.I)
_CALENDAR_START_RE = re.compile(r"\b(?:start|начало)\"?\s*:", re.I)
_CALENDAR_END_RE = re.compile(r"\b(?:end|окончание)\"?\s*:", re.I)
_CALENDAR_READBACK_RE = re.compile(
    r"\b(?:read_back\"?\s*[:=]\s*true|read[- ]?back|повторн\w*\s+(?:чтени|проверк)\w*|"
    r"подтвержден\w*\s+(?:в|через)\s+календар\w*)",
    re.I,
)


def _route_has_calendar_capability(
    route_toolsets: Iterable[str] | None = None,
    route_skills: Iterable[str] | None = None,
) -> bool:
    values = [*(route_toolsets or ()), *(route_skills or ())]
    return any("calendar" in str(value).lower() or "google-workspace" in str(value).lower() for value in values)


def _metadata_requests_calendar_write(metadata: dict | None) -> bool:
    if not isinstance(metadata, dict):
        return False
    contract = metadata.get("execution_contract")
    if isinstance(contract, dict) and contract.get("type") == "calendar_write":
        return True
    return metadata.get("execution_contract_type") == "calendar_write" or metadata.get("operation_type") == "calendar_write"


def _successful_calendar_write_tool_used(tool_calls: Iterable[object] | None = None) -> bool:
    if tool_calls is None:
        return False
    write_verbs = ("create", "add", "schedule", "update")
    for item in tool_calls:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("tool") or item.get("tool_name") or "").lower()
            if item.get("success") is False or item.get("error"):
                continue
        else:
            name = str(item).lower()
        if "calendar" in name and any(verb in name for verb in write_verbs):
            return True
    return False


def _is_calendar_write_request(text: str) -> bool:
    value = text or ""
    if not value or _TECHNICAL_EVENT_CONTEXT_RE.search(value):
        return False
    return bool(_CALENDAR_CREATE_RE.search(value))


_WRAPPER_LINE_RE = re.compile(
    r"^\s*(?:Записал\s+в\s+формате\s+события|Часовой\s+пояс\b.*не\s+указан\w*)\b.*$",
    re.I,
)
_EVENT_LINE_RE = re.compile(
    rf"(?P<date>\b(?:сегодня|завтра|послезавтра|\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}[./-]\d{{1,2}}(?:[./-]\d{{2,4}})?|\d{{1,2}}\s+(?:{_RU_MONTH_NAME_RE})(?:\s+\d{{4}})?)\b)"
    r"\s*,?\s*(?:в\s+)?(?P<time>(?:[01]?\d|2[0-3])[:.]\d{2})"
    r"(?:\s*[—-]\s*(?P<title>[^\n]+))?",
    re.I,
)


@dataclass(frozen=True)
class MessageContext:
    current_text: str = ""
    current_message_id: str | None = None
    chat_id: str | None = None
    sender_id: str | None = None
    update_id: str | None = None
    reply_text: str | None = None
    reply_caption: str | None = None
    reply_message_id: str | None = None
    reply_sender_id: str | None = None


def _normalize_message_context(value: dict | MessageContext | None, *, fallback_text: str = "", chat_id: str = "") -> MessageContext:
    if isinstance(value, MessageContext):
        return value
    data = value if isinstance(value, dict) else {}
    return MessageContext(
        current_text=str(data.get("current_text") or fallback_text or ""),
        current_message_id=str(data["current_message_id"]) if data.get("current_message_id") is not None else None,
        chat_id=str(data.get("chat_id") or chat_id or ""),
        sender_id=str(data["sender_id"]) if data.get("sender_id") is not None else None,
        update_id=str(data["update_id"]) if data.get("update_id") is not None else None,
        reply_text=str(data["reply_text"]) if data.get("reply_text") else None,
        reply_caption=str(data["reply_caption"]) if data.get("reply_caption") else None,
        reply_message_id=str(data["reply_message_id"]) if data.get("reply_message_id") is not None else None,
        reply_sender_id=str(data["reply_sender_id"]) if data.get("reply_sender_id") is not None else None,
    )


def _clean_calendar_text(text: str) -> str:
    lines = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or _WRAPPER_LINE_RE.search(line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _extract_calendar_event_draft(text: str) -> dict | None:
    clean = _clean_calendar_text(text)
    if not clean:
        return None
    match = _EVENT_LINE_RE.search(clean)
    if match is None:
        return None
    title = (match.group("title") or "").strip(" .;\t")
    if not title:
        tail = clean[match.end():].strip(" \n.;")
        title = tail.splitlines()[0].strip(" .;") if tail else ""
    if not title:
        return None
    date_text = match.group("date").strip()
    month_match = _RU_MONTH_DATE_RE.fullmatch(date_text)
    iso_date = None
    if month_match:
        parts = date_text.lower().split()
        day = int(parts[0])
        month = _RU_MONTHS[parts[1]]
        year = int(parts[2]) if len(parts) > 2 else date.today().year
        iso_date = f"{year:04d}-{month:02d}-{day:02d}"
    return {
        "date": iso_date or date_text,
        "date_text": date_text,
        "time": match.group("time").replace(".", ":"),
        "summary": title,
    }


def _calendar_context_draft(ctx: MessageContext) -> tuple[dict | None, str]:
    for source_name, source_text in (
        ("current", ctx.current_text),
        ("reply_text", ctx.reply_text),
        ("reply_caption", ctx.reply_caption),
    ):
        draft = _extract_calendar_event_draft(source_text or "")
        if draft is not None:
            return draft, source_name
    return None, ""


def _format_calendar_request(current_text: str, draft: dict, source: str) -> str:
    return (
        str(current_text or "").strip()
        + "\n\nStructured calendar event from " + source + ":\n"
        + "Date: " + str(draft.get("date") or draft.get("date_text") or "") + "\n"
        + "Time: " + str(draft.get("time") or "") + "\n"
        + "Summary: " + str(draft.get("summary") or "")
    ).strip()


def _calendar_task_sender_matches(task: TaskRecord, ctx: MessageContext) -> bool:
    saved = task.metadata.get("sender_id")
    return not (saved and ctx.sender_id and str(saved) != str(ctx.sender_id))


def _task_is_calendar_write(task: TaskRecord) -> bool:
    return bool(
        task.metadata.get("intent") == "calendar_write"
        or _metadata_requests_calendar_write(task.metadata)
        or _is_calendar_write_request(task.original_request)
    )


def _select_calendar_pending_task(store: TaskStateStore, platform_key: str, chat_id: str, ctx: MessageContext) -> TaskRecord | None:
    if not _is_calendar_write_request(ctx.current_text):
        return None
    matches = [
        task for task in store.active(platform_key, str(chat_id))
        if _task_is_calendar_write(task) and _calendar_task_sender_matches(task, ctx)
    ]
    return matches[0] if len(matches) == 1 else None


def _calendar_source_request_id(platform_key: str, chat_id: str, ctx: MessageContext, fallback: str) -> str:
    marker = ctx.update_id or ctx.current_message_id or fallback
    return f"{platform_key}:{chat_id}:{ctx.sender_id or ''}:{marker}:calendar_write"


_CALENDAR_EVIDENCE_KEYS = (
    "calendar_id", "event_id", "summary", "start", "end", "event_link", "read_back", "status"
)


def _calendar_evidence_missing(evidence: dict | None) -> tuple[str, ...]:
    if not isinstance(evidence, dict):
        return ("calendar evidence",)
    missing: list[str] = []
    labels = {
        "calendar_id": "calendar ID",
        "event_id": "event ID или штатная ссылка",
        "summary": "summary/название",
        "start": "start/начало",
        "end": "end/окончание",
        "event_link": "event ID или штатная ссылка",
    }
    for key in ("calendar_id", "event_id", "summary", "start", "end", "event_link"):
        if not evidence.get(key):
            label = labels[key]
            if label not in missing:
                missing.append(label)
    if evidence.get("read_back") is not True:
        missing.append("подтверждение read-back")
    if str(evidence.get("status") or "").lower() != "created":
        missing.append("status=created")
    return tuple(missing)


def calendar_evidence_complete(evidence: dict | None) -> bool:
    return _calendar_evidence_missing(evidence) == ()


def _coerce_calendar_evidence(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    if not ({"calendar_id", "event_id", "read_back"} <= set(value)):
        return None
    evidence = {key: value.get(key) for key in _CALENDAR_EVIDENCE_KEYS if key in value}
    if "event_link" not in evidence and value.get("htmlLink"):
        evidence["event_link"] = value.get("htmlLink")
    if "event_id" not in evidence and value.get("id"):
        evidence["event_id"] = value.get("id")
    if "calendar_id" not in evidence and value.get("calendarId"):
        evidence["calendar_id"] = value.get("calendarId")
    return evidence


def _json_objects_from_text(text: str) -> list[object]:
    value = (text or "").strip()
    if not value:
        return []
    candidates = [value]
    start = value.find("{")
    end = value.rfind("}")
    if start >= 0 and end > start:
        candidates.append(value[start:end + 1])
    out = []
    for candidate in candidates:
        try:
            out.append(json.loads(candidate))
        except Exception:
            pass
    return out


def extract_calendar_evidence_from_result(result: dict | None) -> dict | None:
    if not isinstance(result, dict):
        return None
    candidates: list[object] = []
    for key in ("calendar_evidence", "calendar_event_evidence"):
        if key in result:
            candidates.append(result[key])
    for item in result.get("tools") or result.get("tool_calls") or ():
        candidates.append(item)
    for msg in result.get("messages") or ():
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "tool" or msg.get("tool_call_id") or msg.get("name"):
            content = msg.get("content")
            if isinstance(content, str):
                candidates.extend(_json_objects_from_text(content))
            elif content is not None:
                candidates.append(content)
        elif isinstance(msg.get("content"), str) and "calendar_id" in msg.get("content", ""):
            candidates.extend(_json_objects_from_text(msg.get("content", "")))
    for candidate in candidates:
        evidence = _coerce_calendar_evidence(candidate)
        if evidence is not None:
            return evidence
    return None


def calendar_completion_evidence_missing(
    original_request: str,
    final_response: str,
    *,
    route_toolsets: Iterable[str] | None = None,
    route_skills: Iterable[str] | None = None,
    metadata: dict | None = None,
    tool_calls: Iterable[object] | None = None,
) -> tuple[str, ...]:
    if not _is_calendar_write_request(original_request or ""):
        return ()
    if not (
        _route_has_calendar_capability(route_toolsets, route_skills)
        or _metadata_requests_calendar_write(metadata)
        or _successful_calendar_write_tool_used(tool_calls)
    ):
        return ()
    evidence = (metadata or {}).get("calendar_evidence") if isinstance(metadata, dict) else None
    if evidence is not None:
        return _calendar_evidence_missing(evidence)
    response = final_response or ""
    checks = (
        (_CALENDAR_EVENT_REF_RE, "event ID или штатная ссылка"),
        (_CALENDAR_ID_RE, "calendar ID"),
        (_CALENDAR_SUMMARY_RE, "summary/название"),
        (_CALENDAR_START_RE, "start/начало"),
        (_CALENDAR_END_RE, "end/окончание"),
        (_CALENDAR_READBACK_RE, "подтверждение read-back"),
    )
    return tuple(label for pattern, label in checks if not pattern.search(response))


def _deterministic_input_preflight(text: str, route: TaskRoute) -> tuple[list[str], str] | None:
    value = text or ""
    missing: list[str] = []
    if (
        _IMAGE_DEPENDENCY_RE.search(value)
        and not _IMAGE_CONTEXT_RE.search(value)
        and not _OUTPUT_SCREENSHOT_ARTIFACT_RE.search(value)
    ):
        missing.append("доступное изображение или vision-контекст")
    if "google-workspace" in route.skill_names and _is_calendar_write_request(value):
        if not _DATE_RE.search(value):
            missing.append("конкретная дата события")
        if not _TIME_RE.search(value):
            missing.append("конкретное время события")
        if not _PURPOSE_RE.search(value):
            missing.append("назначение события")
    if missing:
        return missing, "deterministic input preflight blocked"
    return None


def _with_preloaded_skills(route: TaskRoute, task_id: str | None) -> tuple[TaskRoute, tuple[str, ...]]:
    if not route.skill_names:
        return route, ()
    from agent.skill_commands import build_preloaded_skills_prompt
    prompt, loaded, missing = build_preloaded_skills_prompt(list(route.skill_names), task_id=task_id)
    if missing:
        return route, tuple(missing)
    if not prompt.strip():
        return route, tuple(route.skill_names)
    combined = (route.operational_context + "\n\n" + prompt).strip()
    return TaskRoute(
        role=route.role,
        reason=route.reason + "; preloaded_skills=" + ",".join(loaded),
        toolsets=route.toolsets,
        max_iterations=route.max_iterations,
        skill_names=route.skill_names,
        skip_context_files=route.skip_context_files,
        operational_context=combined,
    ), ()


@dataclass(frozen=True)
class PreparedTaskTurn:
    message: str
    route: TaskRoute | None
    task: TaskRecord | None
    early_response: dict | None
    continued: bool


def early_response(text: str, *, status: str = "success", task_id: str | None = None,
                   role: str = "no_llm", reason: str = "deterministic task state") -> dict:
    return {
        "final_response": text,
        "messages": [],
        "api_calls": 0,
        "completed": status == "success",
        "interrupted": False,
        "partial": status != "success",
        "failed": status == "failed",
        "error": None if status == "success" else status,
        "tools": [],
        "history_offset": 0,
        "session_id": "",
        "model": "deterministic",
        "provider": "gateway",
        "selected_model": "deterministic",
        "selected_provider": "gateway",
        "fallback_used": False,
        "fallback_reason": None,
        "task_level": role,
        "routing_reason": reason,
        "selected_toolsets": [],
        "llm_total_ms": 0,
        "diagnostics": {},
        "context_length": 0,
        "task_id": task_id,
    }


def prepare_task_turn(*, message: str, platform_key: str, chat_id: str,
                      session_key: str, session_id: str, request_id: str,
                      user_config: dict, platform_toolsets: list[str] | None,
                      message_context: dict | MessageContext | None = None) -> PreparedTaskTurn:
    store = TaskStateStore()
    original = str(message or "")
    msg_ctx = _normalize_message_context(message_context, fallback_text=original, chat_id=str(chat_id))
    current_text = msg_ctx.current_text or original
    if re.fullmatch(r"\s*(?:почему|из-за\s+чего|в\s+ч[её]м\s+причина)\s+(?:эта\s+)?(?:ошибка|blocked|блокировка)[\s.!?]*", original, re.I):
        active = store.active(platform_key, str(chat_id))
        if len(active) == 1:
            current = active[0]
            reason = current.last_error or "причина не сохранена"
            if reason == "router did not assign execution toolset":
                reason = "короткое сообщение было ошибочно обработано как новая simple-задача, поэтому инструменты активной задачи не унаследовались"
            elif reason == "reported verdict PARTIAL":
                reason = "активная задача завершила этап с PARTIAL и ожидала следующий ввод пользователя"
            return PreparedTaskTurn(
                original, None, current,
                early_response(
                    "Причина ошибки: " + reason + "\n"
                    + "Задача: " + current.task_id + "\n"
                    + "Роль: " + current.role + "\n"
                    + "Инструменты: " + (", ".join(current.toolsets) or "не назначены"),
                    task_id=current.task_id,
                    role=current.role,
                    reason="deterministic task error explanation",
                ),
                False,
            )
    decision = store.resolve(original, platform_key, str(chat_id))
    pending_calendar = None
    if decision.kind == "none":
        pending_calendar = _select_calendar_pending_task(store, platform_key, str(chat_id), msg_ctx)
    if decision.kind == "choice":
        return PreparedTaskTurn(
            original, None, None,
            early_response(format_choice(decision.candidates), reason="continuation choice required"),
            False,
        )
    if decision.kind == "empty":
        return PreparedTaskTurn(
            original, None, None,
            early_response(
                "Незавершённых задач нет",
                reason="continuation requested without active tasks",
            ),
            False,
        )

    task = pending_calendar or (decision.task if decision.kind == "selected" else None)
    if task is not None and _task_is_calendar_write(task) and not _calendar_task_sender_matches(task, msg_ctx):
        task = None
    continued = task is not None
    if task is not None and int(task.metadata.get("budget_exhaustions", 0) or 0) >= 2:
        return PreparedTaskTurn(
            original, None, task,
            early_response(
                "BLOCKED\nАвтоматические продолжения остановлены после двух исчерпаний лимита шагов. "
                "Нужна ручная эскалация с новым планом или сужением объёма",
                status="blocked", task_id=task.task_id, role=task.role,
                reason="budget exhaustion escalation",
            ),
            True,
        )
    if task is not None and task.status == "completed":
        saved_result = task.metadata.get("final_response")
        if isinstance(saved_result, str) and saved_result.strip():
            response_text = "Задача уже выполнена.\n\n" + saved_result.strip()
            response_reason = "completed task result replay"
        else:
            response_text = (
                "Задача уже отмечена выполненной, но подтверждённый результат не сохранён. "
                "Повторный запуск не выполнялся"
            )
            response_reason = "completed task without stored evidence"
        return PreparedTaskTurn(
            original, None, task,
            early_response(
                response_text,
                task_id=task.task_id,
                role=task.role,
                reason=response_reason,
            ),
            True,
        )
    if task is not None:
        message = (
            "[System continuation: resume the saved unfinished task from its saved checkpoint. Preserve its role, "
            "tools, safety mode and completion contract. Do not repeat completed audit or discovery; "
            "continue from metadata/checkpoint and spend the last 5 iterations only on tests, DoD, checkpoint, and final delivery. "
            "Do not interpret the short continuation phrase as a new task.]\n\nSaved task:\n"
            + task.original_request
            + "\n\nSaved checkpoint:\n"
            + str(task.metadata.get("checkpoint") or task.metadata.get("final_response") or task.last_error or "not recorded")[:1800]
            + "\n\nContinuation message:\n" + original
        )

    if is_pause_request(original):
        pending = store.active(platform_key, str(chat_id))
        if len(pending) == 1:
            store.update(pending[0].task_id, status="paused")
            return PreparedTaskTurn(
                original, None, pending[0],
                early_response(
                    f"Задача поставлена на паузу: {pending[0].title}",
                    status="partial", task_id=pending[0].task_id, reason="task paused",
                ),
                False,
            )

    draft, draft_source = _calendar_context_draft(msg_ctx)
    calendar_request = None
    if draft is not None and _is_calendar_write_request(current_text):
        calendar_request = _format_calendar_request(current_text, draft, draft_source)
        if task is None:
            message = calendar_request
    elif task is not None and _is_calendar_write_request(current_text):
        saved_draft = task.metadata.get("calendar_event_draft")
        if isinstance(saved_draft, dict):
            calendar_request = _format_calendar_request(current_text, saved_draft, "pending_task")
            message = calendar_request

    base_text = task.original_request if task else str(calendar_request or message or "")
    route = route_turn(base_text, command=None, platform_key=platform_key,
                       user_config=user_config, platform_toolsets=platform_toolsets)
    if calendar_request is not None and "google-workspace" not in route.skill_names:
        allowed = set(platform_toolsets or [])
        extra_toolsets = [
            name for name in ("skills", "terminal", "file")
            if platform_toolsets is None or name in allowed
        ]
        route = TaskRoute(
            role=route.role,
            reason=route.reason + "; structured_calendar_reply",
            toolsets=sorted(set(route.toolsets) | set(extra_toolsets)),
            max_iterations=route.max_iterations,
            skill_names=tuple([*route.skill_names, "google-workspace"]),
            skip_context_files=False,
            operational_context=route.operational_context,
        )
    if task is not None:
        allowed = set(platform_toolsets or [])
        restored = [name for name in task.toolsets
                    if name == "no_mcp" or platform_toolsets is None or name in allowed]
        route = TaskRoute(
            role=task.role,
            reason=f"continued task {task.task_id}; {route.reason}",
            toolsets=restored,
            max_iterations=route.max_iterations,
            skill_names=route.skill_names,
            skip_context_files=route.skip_context_files,
            operational_context=(
                route.operational_context
                + "\n\nIteration budget policy: the route limit is fixed; do not increase it. "
                + "Reserve the last 5 iterations for tests, Definition of Done checks, checkpoint persistence, and final delivery."
            ).strip(),
        )

    preflight = _deterministic_input_preflight(base_text, route)
    if preflight is not None:
        missing_inputs, preflight_reason = preflight
        if task is not None:
            store.update(task.task_id, status="blocked", last_error="missing inputs: " + ",".join(missing_inputs))
        return PreparedTaskTurn(
            str(message or ""), route, task,
            early_response(
                "BLOCKED\nНе хватает: "
                + ", ".join(missing_inputs)
                + "\nФактические действия не выполнялись",
                status="blocked", task_id=task.task_id if task else None,
                role=route.role, reason=preflight_reason,
            ),
            continued,
        )

    route, missing_skills = _with_preloaded_skills(route, task.task_id if task else None)
    if missing_skills:
        if task is not None:
            store.update(task.task_id, status="blocked", last_error="missing skills: " + ",".join(missing_skills))
        return PreparedTaskTurn(
            str(message or ""), route, task,
            early_response(
                "BLOCKED\nДля выполнения задачи недоступен skill: "
                + ", ".join(missing_skills)
                + "\nФактические действия не выполнялись",
                status="blocked", task_id=task.task_id if task else None,
                role=route.role, reason="skill preflight blocked",
            ),
            continued,
        )

    if "google-workspace" in route.skill_names:
        contract = (
            "For calendar writes, create exactly one event, or update only when explicitly requested; "
            "use the structured calendar event if present. Then read it back and report calendar ID, "
            "summary, start, end, event ID or official link, and read-back confirmation. Otherwise return INCOMPLETE."
        )
        route = TaskRoute(
            role=route.role,
            reason=route.reason,
            toolsets=route.toolsets,
            max_iterations=route.max_iterations,
            skill_names=route.skill_names,
            skip_context_files=route.skip_context_files,
            operational_context=(route.operational_context + "\n\n" + contract).strip(),
        )

    requires_execution, required = infer_execution_contract(base_text, route.role, route.toolsets)
    if task is not None:
        requires_execution, required = task.requires_execution, task.required_toolsets

    # A classifier miss must not cause BLOCKED when the platform allowlist
    # explicitly contains the deterministic execution capability.
    missing_but_allowed = set(required) - set(route.toolsets)
    platform_allowed = set(platform_toolsets or [])
    if missing_but_allowed and (
        platform_toolsets is None or missing_but_allowed.issubset(platform_allowed)
    ):
        route = TaskRoute(
            role=route.role,
            reason=route.reason + "; restored_required=" + ",".join(sorted(missing_but_allowed)),
            toolsets=sorted(set(route.toolsets) | missing_but_allowed),
            max_iterations=route.max_iterations,
            skill_names=route.skill_names,
            skip_context_files=route.skip_context_files,
            operational_context=route.operational_context,
        )

    working_toolsets = [name for name in route.toolsets if name not in {"no_mcp", "clarify"}]
    if requires_execution and not working_toolsets:
        if task is not None:
            store.update(task.task_id, status="blocked",
                         last_error="router did not assign execution toolset")
        return PreparedTaskTurn(
            str(message or ""), route, task,
            early_response(
                "BLOCKED\nМаршрутизатор не назначил инструмент выполнения. "
                "Фактические действия не выполнялись",
                status="blocked", task_id=task.task_id if task else None,
                role=route.role, reason="execution toolset preflight blocked",
            ),
            continued,
        )
    missing = sorted(set(required) - set(route.toolsets))
    if missing:
        if task is not None:
            store.update(task.task_id, status="blocked",
                         last_error="missing capabilities: " + ",".join(missing))
        return PreparedTaskTurn(
            str(message or ""), route, task,
            early_response(
                "BLOCKED\nДля выполнения задачи недоступны обязательные возможности: "
                + ", ".join(missing) + "\nФактические действия не выполнялись",
                status="blocked", task_id=task.task_id if task else None,
                role=route.role, reason="capability preflight blocked",
            ),
            continued,
        )

    if task is not None:
        store.update(
            task.task_id,
            status="running",
            session_key=session_key,
            source_session_id=session_id,
            last_error=None,
        )

    if task is None and (calendar_request is not None or should_track_task(original, route.role, route.toolsets)):
        title_source = calendar_request or original
        title = " ".join(title_source.split())[:120] or "Задача Hermes"
        task_metadata = None
        source_request_id = request_id
        if calendar_request and draft is not None:
            source_request_id = _calendar_source_request_id(platform_key, str(chat_id), msg_ctx, request_id)
            task_metadata = {
                "intent": "calendar_write",
                "sender_id": msg_ctx.sender_id,
                "current_message_id": msg_ctx.current_message_id,
                "platform_update_id": msg_ctx.update_id,
                "reply_message_id": msg_ctx.reply_message_id,
                "reply_sender_id": msg_ctx.reply_sender_id,
                "calendar_event_draft": draft,
                "execution_contract": {"type": "calendar_write"},
            }
        task = store.create(
            platform=platform_key,
            chat_id=str(chat_id),
            session_key=session_key,
            title=title,
            original_request=calendar_request or original,
            role=route.role,
            toolsets=route.toolsets,
            required_toolsets=required,
            requires_execution=requires_execution,
            source_request_id=source_request_id,
            source_session_id=session_id,
            status="running",
            metadata=task_metadata,
        )
    return PreparedTaskTurn(str(message or ""), route, task, None, continued)
