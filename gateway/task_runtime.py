"""Thin gateway integration for persistent task continuation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from typing import Any

from gateway.task_continuation import (
    TaskRecord,
    TaskStateStore,
    format_choice,
    infer_execution_contract,
    is_pause_request,
    should_track_task,
)
from gateway.intent_uncertainty import clarification_question, detect_uncertain_intent
from gateway.task_router import (
    TaskRoute, route_turn, should_generate_html_report,
    travel_is_route_or_parking, travel_is_source_capture, travel_needs_web_or_browser,
)
from gateway.quick_note_capture import (
    build_location_place_note,
    canonical_place_label,
    detect_quick_note,
    format_quick_save_response,
    run_quick_save,
)

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
    r"\b(?:календар|встреч|созвон|событи)\w*\b|"
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
    location: dict[str, Any] | None = None
    reply_location: dict[str, Any] | None = None
    session_key: str | None = None


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
        location=data.get("location") if isinstance(data.get("location"), dict) else None,
        reply_location=data.get("reply_location") if isinstance(data.get("reply_location"), dict) else None,
        session_key=str(data["session_key"]) if data.get("session_key") is not None else None,
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


_CALENDAR_RESULT_CONTAINER_KEYS = (
    "calendar_evidence",
    "calendar_event_evidence",
    "result",
    "output",
    "stdout",
    "content",
    "data",
    "response",
    "body",
)


def _calendar_evidence_from_value(value: object, *, depth: int = 0) -> dict | None:
    if depth > 6:
        return None
    evidence = _coerce_calendar_evidence(value)
    if evidence is not None:
        return evidence
    if isinstance(value, str):
        for parsed in _json_objects_from_text(value):
            evidence = _calendar_evidence_from_value(parsed, depth=depth + 1)
            if evidence is not None:
                return evidence
        return None
    if isinstance(value, dict):
        for key in _CALENDAR_RESULT_CONTAINER_KEYS:
            if key not in value:
                continue
            evidence = _calendar_evidence_from_value(value[key], depth=depth + 1)
            if evidence is not None:
                return evidence
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            evidence = _calendar_evidence_from_value(item, depth=depth + 1)
            if evidence is not None:
                return evidence
    return None


def _calendar_evidence_from_candidates(candidates: Iterable[object]) -> dict | None:
    for candidate in candidates:
        evidence = _calendar_evidence_from_value(candidate)
        if evidence is not None:
            return evidence
    return None


def extract_calendar_evidence_from_tool_result(function_result: object) -> dict | None:
    return _calendar_evidence_from_value(function_result)


def persist_calendar_evidence_from_tool_result(store: TaskStateStore, task_id: str, function_result: object) -> dict | None:
    evidence = extract_calendar_evidence_from_tool_result(function_result)
    if evidence is not None:
        store.merge_metadata(task_id, calendar_evidence=evidence)
    return evidence


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
    return _calendar_evidence_from_candidates(candidates)


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
                   role: str = "no_llm", reason: str = "deterministic task state",
                   tools: list[dict] | None = None, diagnostics: dict | None = None) -> dict:
    return {
        "final_response": text,
        "messages": [],
        "api_calls": 0,
        "completed": status == "success",
        "interrupted": False,
        "partial": status != "success",
        "failed": status == "failed",
        "error": None if status == "success" else status,
        "tools": tools or [],
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
        "diagnostics": diagnostics or {},
        "context_length": 0,
        "task_id": task_id,
    }


_TRAVEL_TO_FROM_RE = re.compile(
    r"\b(?:до|в)\s+(?P<destination>.+?)\s+от\s+(?P<start>.+?)(?:\s+и\s+|[?!]|$)",
    re.I | re.S,
)
_TRAVEL_FROM_TO_RE = re.compile(
    r"\bот\s+(?P<start>.+?)\s+до\s+(?P<destination>.+?)(?:\s+и\s+|[?!]|$)",
    re.I | re.S,
)


def _parse_route_trip_request(text: str) -> tuple[str, str] | None:
    value = " ".join((text or "").strip().split())
    match = _TRAVEL_TO_FROM_RE.search(value) or _TRAVEL_FROM_TO_RE.search(value)
    if not match:
        return None
    destination = match.group("destination").strip(" ,.;")
    start = match.group("start").strip(" ,.;")
    if len(destination) < 3 or len(start) < 3:
        return None
    return start, destination


def _trip_helper_path() -> Path:
    hermes_home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return hermes_home / "skills" / "productivity" / "city-travel-concierge" / "scripts" / "city_travel_trip.py"


_TRAVEL_CONTEXT_TTL_SECONDS = 2 * 3600


class CityTravelContextStore:
    """Short-lived per-chat travel state for deterministic Telegram follow-ups."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path or Path.home() / ".hermes" / "state.db")
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS city_travel_contexts (
                    platform TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY(platform, chat_id, session_key)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_city_travel_contexts_expiry "
                "ON city_travel_contexts(expires_at)"
            )

    def save(self, *, platform: str, chat_id: str, session_key: str, context: dict[str, Any]) -> None:
        now = time.time()
        expires_at = now + _TRAVEL_CONTEXT_TTL_SECONDS
        payload = dict(context)
        if isinstance(payload.get("parking_candidates"), list):
            normalized_candidates = []
            for item in payload.get("parking_candidates") or []:
                normalized = _normalize_parking_candidate(item)
                if normalized is not None:
                    normalized_candidates.append(normalized)
            payload["parking_candidates"] = sorted(normalized_candidates, key=_parking_rank_key)
        payload["ttl_seconds"] = _TRAVEL_CONTEXT_TTL_SECONDS
        payload["expires_at_epoch"] = expires_at
        with self._connect() as conn:
            conn.execute("DELETE FROM city_travel_contexts WHERE expires_at<=?", (now,))
            conn.execute(
                """
                INSERT INTO city_travel_contexts (
                    platform, chat_id, session_key, context_json, created_at, updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, chat_id, session_key) DO UPDATE SET
                    context_json=excluded.context_json,
                    updated_at=excluded.updated_at,
                    expires_at=excluded.expires_at
                """,
                (platform, str(chat_id), session_key or "", json.dumps(payload, ensure_ascii=False, sort_keys=True), now, now, expires_at),
            )

    def get(self, *, platform: str, chat_id: str, session_key: str) -> dict[str, Any] | None:
        now = time.time()
        with self._connect() as conn:
            conn.execute("DELETE FROM city_travel_contexts WHERE expires_at<=?", (now,))
            row = conn.execute(
                "SELECT context_json FROM city_travel_contexts WHERE platform=? AND chat_id=? AND session_key=? AND expires_at>?",
                (platform, str(chat_id), session_key or "", now),
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row["context_json"] or "{}")
        except Exception:
            return None
        return value if isinstance(value, dict) else None

    def clear(self, *, platform: str, chat_id: str, session_key: str | None = None) -> int:
        with self._connect() as conn:
            if session_key is None:
                cur = conn.execute(
                    "DELETE FROM city_travel_contexts WHERE platform=? AND chat_id=?",
                    (platform, str(chat_id)),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM city_travel_contexts WHERE platform=? AND chat_id=? AND session_key=?",
                    (platform, str(chat_id), session_key or ""),
                )
            return int(cur.rowcount or 0)


_LOCATION_CONTEXT_TTL_SECONDS = 15 * 60
_PLACE_SAVE_RE = re.compile(
    r"\b(?:запомни|сохрани|зафиксируй|обнови)\w*\b.{0,80}\b(?P<label>дом|дома|работу|работа|офис|дачу|дача|место|геолокаци[юя]|точк[уа])\b|"
    r"\b(?:это|здесь|тут|теперь)\s+(?:мой|моя|моё|мое)?\s*(?P<label2>дом|домашний\s+адрес|работа|офис|дача)\b",
    re.I | re.S,
)
_AMBIGUOUS_PLACE_SAVE_RE = re.compile(r"^\s*(?:сохрани|запомни|зафиксируй)\s+(?:это|тут|здесь|эту\s+точку)\s*[.!?]*$", re.I)
_UPDATE_PLACE_RE = re.compile(r"\b(?:обнови|теперь|новый|новая)\b", re.I)
_LOCATION_ONLY_TEXT_RE = re.compile(r"^\s*\[Telegram location received\]\s*$", re.I)
_PLACE_LOOKUP_RE = re.compile(
    r"\b(?:где|покажи|показать|пришли|дай|открой|координат\w*)\b.{0,80}\b(?P<label>дом|дома|работа|работу|работы|офис|дача|дачу|дачи)\b|"
    r"\b(?P<label2>дом|работа|работы|офис|дача|дачи)\b.{0,80}\b(?:яндекс|карт|координат|ссылк)\w*",
    re.I | re.S,
)


class PendingLocationStore:
    """Short-lived Telegram location/intent state, scoped to chat, sender and session."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path or Path.home() / ".hermes" / "state.db")
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_location_contexts (
                    platform TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    used_at REAL,
                    PRIMARY KEY(platform, chat_id, session_key, sender_id, kind)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_telegram_location_contexts_expiry "
                "ON telegram_location_contexts(expires_at)"
            )

    def save(self, *, platform: str, chat_id: str, session_key: str, sender_id: str, kind: str, payload: dict[str, Any]) -> None:
        now = time.time()
        expires_at = now + _LOCATION_CONTEXT_TTL_SECONDS
        body = dict(payload)
        body["ttl_seconds"] = _LOCATION_CONTEXT_TTL_SECONDS
        body["expires_at_epoch"] = expires_at
        with self._connect() as conn:
            conn.execute("DELETE FROM telegram_location_contexts WHERE expires_at<=? OR used_at IS NOT NULL", (now,))
            conn.execute(
                """
                INSERT INTO telegram_location_contexts (
                    platform, chat_id, session_key, sender_id, kind, payload_json, created_at, updated_at, expires_at, used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(platform, chat_id, session_key, sender_id, kind) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at,
                    expires_at=excluded.expires_at,
                    used_at=NULL
                """,
                (platform, str(chat_id), session_key or "", str(sender_id or ""), kind, json.dumps(body, ensure_ascii=False, sort_keys=True), now, now, expires_at),
            )

    def get(self, *, platform: str, chat_id: str, session_key: str, sender_id: str, kind: str) -> dict[str, Any] | None:
        now = time.time()
        with self._connect() as conn:
            conn.execute("DELETE FROM telegram_location_contexts WHERE expires_at<=? OR used_at IS NOT NULL", (now,))
            row = conn.execute(
                """
                SELECT payload_json FROM telegram_location_contexts
                WHERE platform=? AND chat_id=? AND session_key=? AND sender_id=? AND kind=? AND expires_at>? AND used_at IS NULL
                """,
                (platform, str(chat_id), session_key or "", str(sender_id or ""), kind, now),
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row["payload_json"] or "{}")
        except Exception:
            return None
        return value if isinstance(value, dict) else None

    def consume(self, *, platform: str, chat_id: str, session_key: str, sender_id: str, kind: str) -> dict[str, Any] | None:
        payload = self.get(platform=platform, chat_id=chat_id, session_key=session_key, sender_id=sender_id, kind=kind)
        if payload is None:
            return None
        with self._connect() as conn:
            conn.execute(
                "UPDATE telegram_location_contexts SET used_at=?, updated_at=? WHERE platform=? AND chat_id=? AND session_key=? AND sender_id=? AND kind=?",
                (time.time(), time.time(), platform, str(chat_id), session_key or "", str(sender_id or ""), kind),
            )
        return payload

    def clear(self, *, platform: str, chat_id: str, sender_id: str | None = None, session_key: str | None = None) -> int:
        clauses = ["platform=?", "chat_id=?"]
        params: list[Any] = [platform, str(chat_id)]
        if sender_id is not None:
            clauses.append("sender_id=?")
            params.append(str(sender_id or ""))
        if session_key is not None:
            clauses.append("session_key=?")
            params.append(session_key or "")
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM telegram_location_contexts WHERE " + " AND ".join(clauses), params)
            return int(cur.rowcount or 0)


class PersonalPlaceStore:
    """Canonical personal-place index for deterministic Telegram lookup."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path or Path.home() / ".hermes" / "state.db")
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS personal_places (
                    platform TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    source TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(platform, chat_id, sender_id, label)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_personal_places_lookup "
                "ON personal_places(platform, chat_id, sender_id, label)"
            )

    def upsert(self, *, platform: str, chat_id: str, sender_id: str, label: str, latitude: float, longitude: float, payload: dict[str, Any]) -> None:
        now = time.time()
        canonical = canonical_place_label(label) or label
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO personal_places (
                    platform, chat_id, sender_id, label, latitude, longitude, source, confidence, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, chat_id, sender_id, label) DO UPDATE SET
                    latitude=excluded.latitude,
                    longitude=excluded.longitude,
                    source=excluded.source,
                    confidence=excluded.confidence,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    platform, str(chat_id), str(sender_id or ""), canonical, float(latitude), float(longitude),
                    "telegram_location", "explicit_user_location",
                    json.dumps(payload, ensure_ascii=False, sort_keys=True), now, now,
                ),
            )

    def get(self, *, platform: str, chat_id: str, sender_id: str, label: str) -> dict[str, Any] | None:
        canonical = canonical_place_label(label) or label
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM personal_places
                WHERE platform=? AND chat_id=? AND sender_id=? AND label=?
                """,
                (platform, str(chat_id), str(sender_id or ""), canonical),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except Exception:
            payload = {}
        return {
            "label": row["label"],
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
            "source": row["source"],
            "confidence": row["confidence"],
            "created_at_epoch": row["created_at"],
            "updated_at_epoch": row["updated_at"],
            "payload": payload if isinstance(payload, dict) else {},
        }


def clear_ephemeral_contexts_for_session(*, platform: str, chat_id: str, session_key: str | None = None, sender_id: str | None = None) -> dict[str, int]:
    result = {"city_travel_contexts": 0, "telegram_location_contexts": 0}
    try:
        result["city_travel_contexts"] = CityTravelContextStore().clear(platform=platform, chat_id=str(chat_id), session_key=None)
    except Exception:
        pass
    try:
        result["telegram_location_contexts"] = PendingLocationStore().clear(platform=platform, chat_id=str(chat_id), sender_id=sender_id, session_key=None)
    except Exception:
        pass
    return result


def _yandex_point_url(latitude: float, longitude: float) -> str:
    return f"https://yandex.ru/maps/?pt={float(longitude):.6f},{float(latitude):.6f}&z=18&l=map"


def _yandex_route_url(start_latitude: float, start_longitude: float, destination_latitude: float, destination_longitude: float) -> str:
    return (
        "https://yandex.ru/maps/?rtext="
        f"{float(start_latitude):.6f},{float(start_longitude):.6f}~"
        f"{float(destination_latitude):.6f},{float(destination_longitude):.6f}&rtt=auto"
    )


_PERSONAL_PLACE_ROUTE_RE = re.compile(
    r"^(?:из\s+)?(?:(?:моего|моей|мой|моя|своего|своей)\s+)?"
    r"(?P<label>дом(?:а|ой)?|работ(?:а|ы|у)|офис(?:а)?|дач(?:а|и|у))$",
    re.I,
)


def _personal_place_reference_label(value: str) -> str | None:
    normalized = " ".join(str(value or "").strip().split())
    match = _PERSONAL_PLACE_ROUTE_RE.fullmatch(normalized)
    if match is None:
        return None
    raw = match.group("label").casefold()
    if raw.startswith("дом"):
        return "Дом"
    if raw.startswith("работ") or raw.startswith("офис"):
        return "Работа"
    if raw.startswith("дач"):
        return "Дача"
    return None


def _location_coords(location: dict[str, Any] | None) -> tuple[float, float] | None:
    if not isinstance(location, dict):
        return None
    lat = _as_float(location.get("latitude"))
    lon = _as_float(location.get("longitude"))
    if lat is None or lon is None:
        return None
    return lat, lon


def _place_intent_from_text(text: str) -> dict[str, Any] | None:
    value = " ".join(str(text or "").strip().split())
    if not value:
        return None
    if _AMBIGUOUS_PLACE_SAVE_RE.fullmatch(value):
        return {"ambiguous": True}
    match = _PLACE_SAVE_RE.search(value)
    if not match:
        return None
    raw_label = match.groupdict().get("label") or match.groupdict().get("label2") or ""
    raw_label = re.sub(r"\s+адрес$", "", raw_label, flags=re.I).strip()
    if re.search(r"место|геолокаци|точк", raw_label, re.I):
        raw_label = "Место"
    label = canonical_place_label(raw_label) or "Место"
    return {"label": label, "update": bool(_UPDATE_PLACE_RE.search(value))}


def _location_source_message_id(location: dict[str, Any]) -> str | None:
    value = location.get("message_id") if isinstance(location, dict) else None
    return str(value) if value is not None else None


def _place_saved_response(label: str, *, updated: bool) -> str:
    if label == "Дом":
        return "Дом обновлён в памяти." if updated else "Дом сохранён в памяти."
    if label == "Работа":
        return "Работа обновлена в памяти." if updated else "Работа сохранена в памяти."
    if label == "Дача":
        return "Дача обновлена в памяти." if updated else "Дача сохранена в памяти."
    return f"{label} обновлено в памяти." if updated else f"{label} сохранено в памяти."


def _quick_save_succeeded(result: dict[str, Any]) -> bool:
    if not isinstance(result, dict):
        return False
    return bool(result.get("saved") or result.get("already_exists") or result.get("updated") or result.get("status") in {"saved", "success", "duplicate", "already_exists", "updated"})


def _quick_save_readback_ok(result: dict[str, Any]) -> bool:
    if not isinstance(result, dict):
        return False
    readback = result.get("readback_count")
    if readback is None:
        return bool(result.get("readback_ok") or result.get("read_back_ok") or result.get("saved") or result.get("updated") or result.get("already_exists"))
    try:
        return int(readback) > 0
    except (TypeError, ValueError):
        return False


def _place_lookup_intent(text: str) -> str | None:
    value = " ".join(str(text or "").strip().split())
    if not value:
        return None
    match = _PLACE_LOOKUP_RE.search(value)
    if not match:
        return None
    label = match.groupdict().get("label") or match.groupdict().get("label2")
    return canonical_place_label(label) if label else None


def _deterministic_place_lookup(*, text: str, route: TaskRoute, platform_key: str, chat_id: str, msg_ctx: MessageContext) -> dict | None:
    label = _place_lookup_intent(text)
    if not label:
        return None
    sender_id = str(msg_ctx.sender_id or "")
    place = PersonalPlaceStore().get(platform=platform_key, chat_id=str(chat_id), sender_id=sender_id, label=label)
    diagnostics = {"tool_call_count": 0, "html_report_created": False, "personal_place_lookup": label}
    if place is None:
        return early_response(
            f"{label} пока не сохранён. Пришли геолокацию и напиши “Запомни мой {label.casefold()}”.",
            role=route.role or "no_llm",
            reason="deterministic personal place missing",
            diagnostics=diagnostics,
        )
    url = _yandex_point_url(place["latitude"], place["longitude"])
    return early_response(
        f"{label} сохранён здесь:\nЯндекс Карты: {url}",
        role=route.role or "no_llm",
        reason="deterministic personal place lookup",
        diagnostics={**diagnostics, "personal_place_readback_ok": True},
    )


def _save_location_place(label: str, location: dict[str, Any], *, update_requested: bool, platform_key: str, chat_id: str, sender_id: str) -> tuple[str, str, dict[str, Any]]:
    coords = _location_coords(location)
    if coords is None:
        return "Не вижу координаты в геолокации. Пришли точку ещё раз.", "failed", {"tool_call_count": 0, "html_report_created": False}
    canonical = canonical_place_label(label) or label
    note = build_location_place_note(label=canonical, latitude=coords[0], longitude=coords[1], source_message_id=_location_source_message_id(location))
    quick_result: dict[str, Any] = {}
    quick_status = "not_run"
    try:
        quick_result = run_quick_save(note)
        quick_status = str(quick_result.get("status") or "unknown")
    except Exception as exc:
        quick_status = "error:" + exc.__class__.__name__
    payload = {
        "label": canonical,
        "latitude": round(float(coords[0]), 6),
        "longitude": round(float(coords[1]), 6),
        "source": "telegram_location",
        "confidence": "explicit_user_location",
        "source_message_id": _location_source_message_id(location),
        "quick_save_status": quick_status,
    }
    store = PersonalPlaceStore()
    store.upsert(platform=platform_key, chat_id=str(chat_id), sender_id=str(sender_id or ""), label=canonical, latitude=coords[0], longitude=coords[1], payload=payload)
    readback = store.get(platform=platform_key, chat_id=str(chat_id), sender_id=str(sender_id or ""), label=canonical)
    readback_ok = bool(
        readback
        and round(float(readback.get("latitude")), 6) == round(float(coords[0]), 6)
        and round(float(readback.get("longitude")), 6) == round(float(coords[1]), 6)
    )
    if not readback_ok:
        return "Не удалось подтвердить сохранение места в памяти.", "blocked", {"tool_call_count": 1, "html_report_created": False, "location_readback_ok": False, "quick_save_status": quick_status}
    updated = update_requested or bool(readback and float(readback.get("updated_at_epoch") or 0) > float(readback.get("created_at_epoch") or 0))
    return _place_saved_response(canonical, updated=updated), "success", {"tool_call_count": 1, "html_report_created": False, "location_readback_ok": True, "quick_save_status": quick_status}


def _deterministic_location_place_flow(*, text: str, route: TaskRoute, platform_key: str, chat_id: str, session_key: str, msg_ctx: MessageContext) -> dict | None:
    store = PendingLocationStore()
    sender_id = str(msg_ctx.sender_id or "")
    effective_session = str(msg_ctx.session_key or session_key or "")
    current_location = msg_ctx.location if _location_coords(msg_ctx.location) else None
    reply_location = msg_ctx.reply_location if _location_coords(msg_ctx.reply_location) else None
    location_only = current_location is not None and _LOCATION_ONLY_TEXT_RE.fullmatch(text or "") is not None
    intent = _place_intent_from_text(text)

    if travel_is_source_capture(text):
        return None

    if current_location is not None:
        store.save(platform=platform_key, chat_id=str(chat_id), session_key=effective_session, sender_id=sender_id, kind="location", payload=current_location)

    if location_only:
        pending_intent = store.consume(platform=platform_key, chat_id=str(chat_id), session_key=effective_session, sender_id=sender_id, kind="intent")
        if isinstance(pending_intent, dict) and pending_intent.get("label"):
            response, status, diagnostics = _save_location_place(str(pending_intent["label"]), current_location, update_requested=bool(pending_intent.get("update")), platform_key=platform_key, chat_id=str(chat_id), sender_id=sender_id)
            if status == "success":
                store.consume(platform=platform_key, chat_id=str(chat_id), session_key=effective_session, sender_id=sender_id, kind="location")
            return early_response(response, status=status, role=route.role or "no_llm", reason="deterministic telegram location place save", diagnostics={**diagnostics, "location_intent": "pending_text_then_location"})
        return early_response(
            "Геолокацию получил. Напиши, что сделать: запомнить место, построить маршрут или найти что-то рядом.",
            role=route.role or "no_llm",
            reason="deterministic telegram bare location",
            diagnostics={"tool_call_count": 0, "html_report_created": False, "location_intent": "bare_location"},
        )

    if intent is None:
        return None
    if intent.get("ambiguous"):
        location = reply_location or store.get(platform=platform_key, chat_id=str(chat_id), session_key=effective_session, sender_id=sender_id, kind="location")
        if location is not None:
            return early_response(
                "Как назвать место: Дом, Работа или своё название? Ответьте сообщением.",
                role=route.role or "no_llm",
                reason="deterministic ambiguous telegram location save",
                diagnostics={"tool_call_count": 0, "html_report_created": False, "location_intent": "ambiguous_place_save"},
            )
        return None

    label = str(intent.get("label") or "Место")
    location = reply_location or store.get(platform=platform_key, chat_id=str(chat_id), session_key=effective_session, sender_id=sender_id, kind="location")
    if location is None:
        store.save(platform=platform_key, chat_id=str(chat_id), session_key=effective_session, sender_id=sender_id, kind="intent", payload={"label": label, "update": bool(intent.get("update"))})
        return early_response(
            "Пришли геолокацию, и я сохраню место.",
            role=route.role or "no_llm",
            reason="deterministic place save waiting for telegram location",
            diagnostics={"tool_call_count": 0, "html_report_created": False, "location_intent": "waiting_for_location"},
        )
    response, status, diagnostics = _save_location_place(label, location, update_requested=bool(intent.get("update")), platform_key=platform_key, chat_id=str(chat_id), sender_id=sender_id)
    if status == "success" and reply_location is None:
        store.consume(platform=platform_key, chat_id=str(chat_id), session_key=effective_session, sender_id=sender_id, kind="location")
    return early_response(response, status=status, role=route.role or "no_llm", reason="deterministic telegram location place save", diagnostics={**diagnostics, "location_intent": "location_then_text" if reply_location is None else "reply_location"})


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parking_candidate_id(candidate: dict[str, Any]) -> str:
    coords = candidate.get("coordinates") if isinstance(candidate.get("coordinates"), dict) else {}
    lat = _as_float(coords.get("lat"))
    lon = _as_float(coords.get("lon"))
    if lat is not None and lon is not None:
        return f"{lat:.6f},{lon:.6f}"
    return "|".join(str(candidate.get(key) or "") for key in ("title", "address", "distance_m", "parking_status"))[:180]


def _candidate_yandex_url(candidate: dict[str, Any]) -> str:
    coords = candidate.get("coordinates") if isinstance(candidate.get("coordinates"), dict) else {}
    lat = _as_float(coords.get("lat"))
    lon = _as_float(coords.get("lon"))
    if lat is None or lon is None:
        return ""
    deep_links = candidate.get("deep_links") if isinstance(candidate.get("deep_links"), dict) else {}
    url = str(deep_links.get("yandex_maps") or "").strip()
    lowered = url.lower()
    generic_search = "text=parking" in lowered or "query=parking" in lowered or "search" in lowered and "parking" in lowered
    has_point = "pt=" in lowered or (f"{lon:.6f}" in lowered and f"{lat:.6f}" in lowered)
    if url and has_point and not generic_search:
        return url
    return _yandex_point_url(lat, lon)


def _parking_address_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        street = str(value.get("street") or "").strip()
        housenumber = str(value.get("housenumber") or "").strip()
        city = str(value.get("city") or "").strip()
        suburb = str(value.get("suburb") or "").strip()
        postcode = str(value.get("postcode") or "").strip()
        line = " ".join(part for part in (street, housenumber) if part)
        parts = [part for part in (line, suburb, city, postcode) if part]
        return ", ".join(parts)
    return ""


def _trim_parking_evidence(candidate: dict[str, Any]) -> dict[str, Any]:
    evidence = candidate.get("parking_evidence") if isinstance(candidate.get("parking_evidence"), dict) else (candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {})
    trimmed: dict[str, Any] = {}
    for key in ("source", "fee", "access", "parking_type", "reason", "checked_at"):
        if evidence.get(key) is not None:
            trimmed[key] = evidence.get(key)
    official = evidence.get("official") if isinstance(evidence.get("official"), dict) else None
    if official:
        trimmed["official"] = {key: official.get(key) for key in ("source_name", "source_url", "zone_number", "parking_name", "tariffs", "hours", "checked_at") if official.get(key) is not None}
    return trimmed


def _normalize_parking_candidate(candidate: Any) -> dict[str, Any] | None:
    if not isinstance(candidate, dict):
        return None
    coords = candidate.get("coordinates") if isinstance(candidate.get("coordinates"), dict) else {}
    lat = _as_float(coords.get("lat"))
    lon = _as_float(coords.get("lon"))
    if lat is None or lon is None:
        return None
    normalized = {
        "title": str(candidate.get("title") or "парковка").strip() or "парковка",
        "address": _parking_address_text(candidate.get("address")),
        "coordinates": {"lat": lat, "lon": lon},
        "distance_m": _as_float(candidate.get("distance_m")),
        "parking_status": str(candidate.get("parking_status") or candidate.get("verification_status") or "unverified"),
        "eligible_for_recommendation": bool(candidate.get("eligible_for_recommendation")),
        "is_free": candidate.get("is_free") if isinstance(candidate.get("is_free"), bool) else None,
        "evidence": _trim_parking_evidence(candidate),
    }
    normalized["id"] = str(candidate.get("id") or _parking_candidate_id(normalized))
    normalized["raw_yandex_maps_url"] = _candidate_yandex_url(candidate) or _candidate_yandex_url(normalized)
    route_url = str(candidate.get("route_yandex_maps_url") or "").strip()
    if route_url and "text=parking" not in route_url.lower():
        normalized["route_yandex_maps_url"] = route_url
    return normalized


def _parking_rank_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    status_priority = {"official": 0, "user_confirmed": 1, "likely_free": 2, "unverified": 3}
    return (not bool(candidate.get("eligible_for_recommendation")), status_priority.get(str(candidate.get("parking_status") or "unverified"), 9), candidate.get("distance_m") is None, candidate.get("distance_m") or 0, candidate.get("title") or "")


def _parking_distance_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    return (not bool(candidate.get("eligible_for_recommendation")), candidate.get("distance_m") is None, candidate.get("distance_m") or 0, _parking_rank_key(candidate))


def _has_official_tariff(candidate: dict[str, Any]) -> bool:
    evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}
    official = evidence.get("official") if isinstance(evidence.get("official"), dict) else {}
    return bool(official.get("tariffs"))


def _parking_cheapest_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    return (not _has_official_tariff(candidate), _parking_rank_key(candidate))


def _travel_context_from_payload(payload: dict[str, Any], *, start: str, destination: str) -> dict[str, Any]:
    route = payload.get("route") if isinstance(payload.get("route"), dict) else {}
    route_links = route.get("deep_links") if isinstance(route.get("deep_links"), dict) else {}
    coordinate_payload = payload.get("coordinates") if isinstance(payload.get("coordinates"), dict) else {}
    start_coordinates = coordinate_payload.get("start") if isinstance(coordinate_payload.get("start"), dict) else None
    destination_coordinates = coordinate_payload.get("destination") if isinstance(coordinate_payload.get("destination"), dict) else None
    start_lat = _as_float((start_coordinates or {}).get("lat"))
    start_lon = _as_float((start_coordinates or {}).get("lon"))
    candidates: list[dict[str, Any]] = []
    for item in payload.get("parking_candidates") or []:
        normalized = _normalize_parking_candidate(item)
        if normalized is None:
            continue
        coords = normalized.get("coordinates") if isinstance(normalized.get("coordinates"), dict) else {}
        parking_lat = _as_float(coords.get("lat"))
        parking_lon = _as_float(coords.get("lon"))
        if start_lat is not None and start_lon is not None and parking_lat is not None and parking_lon is not None:
            normalized["route_yandex_maps_url"] = _yandex_route_url(start_lat, start_lon, parking_lat, parking_lon)
        candidates.append(normalized)
    return {
        "source": "city-travel-concierge",
        "start_text": start,
        "resolved_start": payload.get("resolved_start"),
        "start_coordinates": start_coordinates,
        "approximate_start": bool(payload.get("approximate_start")),
        "destination_text": destination,
        "resolved_destination": payload.get("resolved_destination"),
        "destination_coordinates": destination_coordinates,
        "route_url": str(route_links.get("yandex_maps") or ""),
        "traffic_status": payload.get("traffic_status"),
        "parking_candidates": sorted(candidates, key=_parking_rank_key),
        "shown_parking_ids": [],
        "last_parking_selection_ids": [],
        "checked_at": payload.get("checked_at"),
        "degraded_sections": payload.get("degraded_sections") or [],
        "statuses": {"traffic_status": payload.get("traffic_status"), "parking_statuses": payload.get("parking_statuses") or []},
    }


_PARKING_EXPLICIT_RE = re.compile(r"\b(?:парковк\w*|припарков\w*|парковочн\w*)\b", re.I)
_PARKING_LINK_RE = re.compile(
    r"\b(?:пришл\w*|дай|дайте|открой\w*|скинь\w*|координат\w*)\b.{0,140}\b(?:парковк\w*|припарков\w*|парковочн\w*)\b|"
    r"\b(?:парковк\w*|припарков\w*|парковочн\w*)\b.{0,140}\b(?:ссылк\w*|координат\w*|яндекс\w*|карт\w*|кандидат\w*|локаци\w*|точк\w*)\b",
    re.I | re.S,
)
_PARKING_POINT_RE = re.compile(
    r"\b(?:конкретн\w*\s+(?:локаци\w*|точк\w*)|куда\s+конкретно\s+ехать|точк\w*\s+на\s+карт\w*)\b.{0,160}\b(?:парковк\w*|припарков\w*|парковочн\w*)\b|"
    r"\b(?:маршрут\w*|ехать)\b.{0,100}\bдо\b.{0,80}\b(?:парковк\w*|парковочн\w*)\b",
    re.I | re.S,
)
_PARKING_MULTIPLE_RE = re.compile(
    r"\b(?:2|3|две|два|три)\b.{0,60}\b(?:конкретн\w*\s+)?(?:парковк\w*|вариант\w*|точк\w*)\b|"
    r"\b(?:парковк\w*|вариант\w*|точк\w*)\b.{0,60}\b(?:2|3|две|два|три)\b",
    re.I | re.S,
)
_PARKING_CHOICE_RE = re.compile(
    r"\b(?:построй\w*|дай|пришл\w*|открой\w*)\b.{0,100}\b(?:перв\w*|втор\w*|трет\w*|№\s*[123])(?:\s+парковк\w*)?\b",
    re.I | re.S,
)
_MORE_PARKING_RE = re.compile(
    r"\b(?:ещ[её]|друг\w*|альтернатив\w*)\b.{0,120}\b(?:парковк\w*|бесплатн\w*|дешев\w*|вариант\w*|точк\w*)\b|"
    r"\b(?:парковк\w*)\b.{0,120}\b(?:ещ[её]|друг\w*|альтернатив\w*)\b",
    re.I | re.S,
)
_PARKING_CLOSEST_RE = re.compile(r"\b(?:сам\w*\s+близк\w*|ближайш\w*|по\s+расстоянию)\b", re.I)
_PARKING_FREE_RE = re.compile(r"\b(?:бесплатн\w*|без\s+оплаты|нулев\w*\s+тариф)\b", re.I)
_PARKING_CHEAP_RE = re.compile(r"\b(?:сам\w*\s+дешев\w*|дешев\w*|цена|тариф)\b", re.I)
_ROUTE_REPEAT_RE = re.compile(r"\b(?:пришл\w*|дай|дайте|открой\w*|повтори\w*)\b.{0,100}\b(?:маршрут\w*|ссылк\w*\s+яндекс|яндекс\s+карт\w*)\b", re.I | re.S)
_DESTINATION_CLARIFICATION_RE = re.compile(r"\b(?:парк\w*|усадьб\w*|музе\w*|вднх|останкин\w*|точнее|около|рядом)\b", re.I)
_CAFE_OR_REVIEW_RE = re.compile(r"\b(?:кафе|ресторан|отзыв\w*|рейтинг\w*|ранжир\w*|покушать|поесть)\b", re.I)


def _city_travel_followup_intent(text: str) -> str | None:
    value = " ".join((text or "").strip().split())
    if not value or _CAFE_OR_REVIEW_RE.search(value):
        return None
    if _PARKING_CHOICE_RE.search(value):
        return "parking_choice"
    if _MORE_PARKING_RE.search(value):
        return "more_parking"
    if _PARKING_MULTIPLE_RE.search(value) and _PARKING_EXPLICIT_RE.search(value):
        return "parking_options"
    if _PARKING_POINT_RE.search(value):
        return "parking_point"
    if _PARKING_LINK_RE.search(value):
        return "parking_link"
    if _ROUTE_REPEAT_RE.search(value):
        return "route_repeat"
    if _DESTINATION_CLARIFICATION_RE.search(value) and _parse_route_trip_request(value) is None:
        return "destination_clarification"
    return None


def _format_distance(distance: Any) -> str:
    value = _as_float(distance)
    if value is None:
        return "расстояние не подтверждено"
    return f"{round(value)} м от точки назначения"


def _ru_plural(count: int, one: str, few: str, many: str) -> str:
    n = abs(int(count))
    if n % 100 in {11, 12, 13, 14}:
        return many
    if n % 10 == 1:
        return one
    if n % 10 in {2, 3, 4}:
        return few
    return many


def _parking_display_title(candidate: dict[str, Any], *, index: int | None = None) -> str:
    raw = str(candidate.get("title") or "").strip()
    generic = not raw or raw.casefold() in {"parking", "парковка", "car park", "parking lot"}
    if not generic:
        return raw
    address = str(candidate.get("address") or "").strip()
    if address:
        return "Парковка-кандидат у " + address
    return f"Парковка-кандидат №{index}" if index is not None else "Парковка-кандидат"


def _parking_status_note(candidate: dict[str, Any]) -> str:
    status = str(candidate.get("parking_status") or "unverified")
    if status == "likely_free":
        return "Статус: вероятно бесплатная по OSM, но официально это не подтверждено."
    if status == "official" and candidate.get("is_free") is False:
        return "Статус: official; источник указывает платную парковку."
    if status in {"official", "user_confirmed"} and candidate.get("is_free") is True:
        return f"Статус: {status}; бесплатность подтверждена источником."
    return f"Статус: {status}; условия и тариф нужно проверить на месте."


def _parking_route_label(context: dict[str, Any]) -> str:
    return "от дома" if context.get("start_place_label") == "Дом" else "от точки старта"


def _parking_route_url(candidate: dict[str, Any], context: dict[str, Any]) -> str:
    existing = str(candidate.get("route_yandex_maps_url") or "").strip()
    if existing and "text=parking" not in existing.lower():
        return existing
    start = context.get("start_coordinates") if isinstance(context.get("start_coordinates"), dict) else {}
    coords = candidate.get("coordinates") if isinstance(candidate.get("coordinates"), dict) else {}
    start_lat = _as_float(start.get("lat"))
    start_lon = _as_float(start.get("lon"))
    parking_lat = _as_float(coords.get("lat"))
    parking_lon = _as_float(coords.get("lon"))
    if None in {start_lat, start_lon, parking_lat, parking_lon}:
        return ""
    return _yandex_route_url(start_lat, start_lon, parking_lat, parking_lon)


def _format_parking_candidate(candidate: dict[str, Any], *, context: dict[str, Any], index: int | None = None) -> str:
    prefix = f"{index}. " if index is not None else ""
    coords = candidate.get("coordinates") if isinstance(candidate.get("coordinates"), dict) else {}
    lat = _as_float(coords.get("lat"))
    lon = _as_float(coords.get("lon"))
    title = _parking_display_title(candidate, index=index)
    point_url = str(candidate.get("raw_yandex_maps_url") or "").strip()
    if not point_url and lat is not None and lon is not None:
        point_url = _yandex_point_url(lat, lon)
    route_url = _parking_route_url(candidate, context)
    lines = [f"{prefix}{title} - {_format_distance(candidate.get('distance_m'))}.", _parking_status_note(candidate)]
    if lat is not None and lon is not None:
        lines.append(f"Координаты: {lat:.6f}, {lon:.6f}")
    lines.append(f"Открыть точку в Яндекс Картах: {point_url or 'ссылка недоступна'}")
    if route_url:
        lines.append(f"Построить маршрут {_parking_route_label(context)}: {route_url}")
    evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}
    if evidence.get("reason"):
        lines.append("Основание: " + str(evidence.get("reason")))
    official = evidence.get("official") if isinstance(evidence.get("official"), dict) else None
    if official and official.get("tariffs"):
        lines.append("Официальный тариф: " + str(official.get("tariffs")))
    return "\n".join(lines)


def _save_city_travel_context(*, platform_key: str, chat_id: str, session_key: str, payload: dict[str, Any], start: str, destination: str, start_place_label: str | None = None) -> None:
    try:
        context = _travel_context_from_payload(payload, start=start, destination=destination)
        if start_place_label:
            context["start_place_label"] = start_place_label
        CityTravelContextStore().save(platform=platform_key, chat_id=str(chat_id), session_key=session_key, context=context)
    except Exception:
        pass


def _update_city_travel_context(*, platform_key: str, chat_id: str, session_key: str, context: dict[str, Any]) -> None:
    try:
        CityTravelContextStore().save(platform=platform_key, chat_id=str(chat_id), session_key=session_key, context=context)
    except Exception:
        pass


def _parking_free_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    status = str(candidate.get("parking_status") or "unverified")
    if status == "official" and candidate.get("is_free") is True:
        priority = 0
    elif status == "user_confirmed" and candidate.get("is_free") is True:
        priority = 1
    elif status == "likely_free":
        priority = 2
    else:
        priority = 9
    return (priority, candidate.get("distance_m") is None, candidate.get("distance_m") or 0)


def _free_parking_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [
            item for item in candidates
            if item.get("eligible_for_recommendation") and (
                (str(item.get("parking_status") or "") in {"official", "user_confirmed"} and item.get("is_free") is True)
                or str(item.get("parking_status") or "") == "likely_free"
            )
        ],
        key=_parking_free_key,
    )


def _parking_selection(candidates: list[dict[str, Any]], text: str) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
    eligible = [item for item in candidates if item.get("eligible_for_recommendation")]
    if not eligible:
        return None, "", None
    if _PARKING_FREE_RE.search(text or ""):
        free_candidates = _free_parking_candidates(eligible)
        if not free_candidates:
            return None, "", None
        if _PARKING_CLOSEST_RE.search(text or ""):
            selected = sorted(free_candidates, key=_parking_distance_key)[0]
        else:
            selected = free_candidates[0]
        status = str(selected.get("parking_status") or "")
        if status == "likely_free":
            return selected, "Ближайший кандидат с признаком likely_free:", None
        return selected, "Бесплатность подтверждена источником:", None
    if _PARKING_CLOSEST_RE.search(text or ""):
        sorted_items = sorted(eligible, key=_parking_distance_key)
        return sorted_items[0], "Самый близкий кандидат:", None
    if _PARKING_CHEAP_RE.search(text or "") and any(_has_official_tariff(item) for item in eligible):
        sorted_items = sorted(eligible, key=_parking_cheapest_key)
        return sorted_items[0], "Кандидат с официальным тарифом:", None
    sorted_items = sorted(eligible, key=_parking_rank_key)
    selected = sorted_items[0]
    nearest = sorted(eligible, key=_parking_distance_key)[0]
    status = str(selected.get("parking_status") or "unverified")
    if status == "likely_free":
        if nearest.get("id") != selected.get("id"):
            return selected, "Вероятно бесплатный кандидат (выбран по признаку likely_free, не как самый близкий):", nearest
        return selected, "Ближайший кандидат с признаком likely_free:", None
    if status in {"official", "user_confirmed"}:
        return selected, f"Кандидат со статусом {status}:", None
    return selected, "Самый близкий допустимый кандидат:", None


def _parking_requested_count(text: str, *, default: int = 3) -> int:
    value = str(text or "").casefold()
    if re.search(r"\b(?:2|две|два)\b", value):
        return 2
    if re.search(r"\b(?:3|три)\b", value):
        return 3
    return default


def _parking_choice_index(text: str) -> int | None:
    value = str(text or "").casefold()
    if re.search(r"\b(?:перв\w*|1)\b", value):
        return 0
    if re.search(r"\b(?:втор\w*|2)\b", value):
        return 1
    if re.search(r"\b(?:трет\w*|3)\b", value):
        return 2
    return None


def _store_last_parking_selection(context: dict[str, Any], selected: list[dict[str, Any]]) -> None:
    context["last_parking_selection_ids"] = [str(item.get("id") or _parking_candidate_id(item)) for item in selected]
    shown = set(str(x) for x in context.get("shown_parking_ids") or [])
    shown.update(context["last_parking_selection_ids"])
    context["shown_parking_ids"] = sorted(shown)


def _parking_options(candidates: list[dict[str, Any]], text: str, *, limit: int, exclude_ids: set[str] | None = None) -> list[dict[str, Any]]:
    eligible = [item for item in candidates if item.get("eligible_for_recommendation")]
    excluded = exclude_ids or set()
    eligible = [item for item in eligible if str(item.get("id") or _parking_candidate_id(item)) not in excluded]
    if _PARKING_FREE_RE.search(text or ""):
        preferred = _free_parking_candidates(eligible)
        selected = preferred[:limit]
        selected_ids = {str(item.get("id") or _parking_candidate_id(item)) for item in selected}
        alternatives = [item for item in sorted(eligible, key=_parking_distance_key) if str(item.get("id") or _parking_candidate_id(item)) not in selected_ids]
        return (selected + alternatives)[:limit]
    if _PARKING_CLOSEST_RE.search(text or ""):
        return sorted(eligible, key=_parking_distance_key)[:limit]
    if _PARKING_CHEAP_RE.search(text or "") and any(_has_official_tariff(item) for item in eligible):
        return sorted(eligible, key=_parking_cheapest_key)[:limit]
    return sorted(eligible, key=_parking_rank_key)[:limit]


def _deterministic_city_travel_followup(*, text: str, route: TaskRoute, platform_key: str, chat_id: str, session_key: str) -> dict | None:
    intent = _city_travel_followup_intent(text)
    if intent is None:
        return None
    context = CityTravelContextStore().get(platform=platform_key, chat_id=str(chat_id), session_key=session_key)
    if context is None:
        return None
    candidates = [item for item in context.get("parking_candidates") or [] if isinstance(item, dict)]
    candidates = sorted(candidates, key=_parking_rank_key)
    if intent == "route_repeat":
        route_url = str(context.get("route_url") or "").strip()
        if not route_url:
            return early_response("Сохранённого URL маршрута нет. Пришлите полный запрос с точкой старта и назначением.", role=route.role, reason="deterministic city travel follow-up missing route url", diagnostics={"tool_call_count": 0, "html_report_created": False, "travel_followup_intent": intent})
        return early_response("Маршрут в Яндекс Картах:\n" + route_url, role=route.role, reason="deterministic city travel route url repeat", diagnostics={"tool_call_count": 0, "html_report_created": False, "travel_followup_intent": intent})
    if intent == "parking_choice":
        selection_ids = [str(x) for x in context.get("last_parking_selection_ids") or []]
        index = _parking_choice_index(text)
        if index is None or index >= len(selection_ids):
            return early_response("Не вижу выбранного номера парковки. Напиши, например: “построй маршрут до второй парковки”.", role=route.role, reason="deterministic city travel parking choice missing", diagnostics={"tool_call_count": 0, "html_report_created": False, "travel_followup_intent": intent})
        selected = next((item for item in candidates if str(item.get("id") or _parking_candidate_id(item)) == selection_ids[index]), None)
        if selected is None:
            return early_response("Выбранная парковка больше недоступна в текущем контексте.", role=route.role, reason="deterministic city travel parking choice stale", diagnostics={"tool_call_count": 0, "html_report_created": False, "travel_followup_intent": intent})
        return early_response(
            f"Выбрана парковка №{index + 1}:\n" + _format_parking_candidate(selected, context=context, index=index + 1),
            role=route.role,
            reason="deterministic city travel selected parking route",
            diagnostics={"tool_call_count": 0, "html_report_created": False, "travel_followup_intent": intent, "selected_parking_index": index + 1},
        )
    if intent in {"parking_link", "parking_point"}:
        eligible = [item for item in candidates if item.get("eligible_for_recommendation")]
        if not eligible:
            return early_response("В сохранённом маршруте нет подходящих кандидатов парковки. Бесплатность или доступность не подтверждаю.", role=route.role, reason="deterministic city travel no eligible parking candidates", diagnostics={"tool_call_count": 0, "html_report_created": False, "travel_followup_intent": intent})
        selected, header, nearest = _parking_selection(candidates, text)
        if selected is None:
            return early_response("Подтверждённых или вероятно бесплатных парковок в сохранённых данных нет. Могу показать ближайшие непроверенные кандидаты.", role=route.role, reason="deterministic city travel no free parking candidate", diagnostics={"tool_call_count": 0, "html_report_created": False, "travel_followup_intent": intent})
        output = [selected]
        if nearest is not None:
            output.append(nearest)
        _store_last_parking_selection(context, output)
        _update_city_travel_context(platform_key=platform_key, chat_id=str(chat_id), session_key=session_key, context=context)
        body = [header, _format_parking_candidate(selected, context=context, index=1)]
        if nearest is not None:
            body.extend(["Самый близкий кандидат:", _format_parking_candidate(nearest, context=context, index=2)])
        if _PARKING_CHEAP_RE.search(text or "") and not any(_has_official_tariff(item) for item in eligible):
            body.append("Официальных тарифов в сохранённых данных нет; likely_free не считаю доказанной нулевой ценой.")
        body.append("Перед парковкой проверь знаки, разметку, шлагбаум и платную зону.")
        return early_response("\n\n".join(body), role=route.role, reason="deterministic city travel parking point follow-up", diagnostics={"tool_call_count": 0, "html_report_created": False, "travel_followup_intent": intent})
    if intent in {"parking_options", "more_parking"}:
        exclude = set(str(x) for x in context.get("shown_parking_ids") or []) if intent == "more_parking" else set()
        limit = _parking_requested_count(text, default=3)
        selected = _parking_options(candidates, text, limit=limit, exclude_ids=exclude)
        if not selected:
            return early_response("Других сохранённых кандидатов парковки нет. Не буду выдумывать бесплатные места без источников.", role=route.role, reason="deterministic city travel no more parking candidates", diagnostics={"tool_call_count": 0, "html_report_created": False, "travel_followup_intent": intent})
        _store_last_parking_selection(context, selected)
        _update_city_travel_context(platform_key=platform_key, chat_id=str(chat_id), session_key=session_key, context=context)
        count = len(selected)
        if intent == "more_parking":
            heading = f"Показываю ещё {count} {_ru_plural(count, 'сохранённый вариант', 'сохранённых варианта', 'сохранённых вариантов')} парковки."
        else:
            heading = f"Показываю {count} {_ru_plural(count, 'конкретный вариант', 'конкретных варианта', 'конкретных вариантов')} парковки."
        body = [heading]
        if _PARKING_FREE_RE.search(text or ""):
            body.append("Сначала идут подтверждённо или вероятно бесплатные варианты; остальные не считаю бесплатными без доказательств.")
        if _PARKING_CHEAP_RE.search(text or "") and not any(_has_official_tariff(item) for item in candidates):
            body.append("Официальных тарифов в сохранённых данных нет; likely_free не считаю доказанной нулевой ценой.")
        body.extend(_format_parking_candidate(item, context=context, index=index) for index, item in enumerate(selected, 1))
        body.append("Перед парковкой проверь знаки, разметку, шлагбаум и платную зону.")
        return early_response("\n\n".join(body), role=route.role, reason="deterministic city travel parking options follow-up", diagnostics={"tool_call_count": 0, "html_report_created": False, "travel_followup_intent": intent, "parking_options_count": count})
    if intent == "destination_clarification":
        start = str(context.get("start_text") or "").strip()
        if not start:
            return None
        return _run_city_travel_trip_fast_path(f"сколько ехать до {text} от {start}", route, platform_key=platform_key, chat_id=str(chat_id), session_key=session_key)
    return None


def _run_city_travel_trip_fast_path(text: str, route: TaskRoute, *, platform_key: str = "", chat_id: str = "", session_key: str = "", sender_id: str = "") -> dict | None:
    if "city-travel-concierge" not in route.skill_names:
        return None
    if not travel_is_route_or_parking(text) or travel_needs_web_or_browser(text):
        return None
    parsed = _parse_route_trip_request(text)
    if parsed is None:
        return None
    start, destination = parsed
    original_start = start
    start_place_label = _personal_place_reference_label(start)
    if start_place_label and platform_key and chat_id:
        place = PersonalPlaceStore().get(platform=platform_key, chat_id=str(chat_id), sender_id=str(sender_id or ""), label=start_place_label)
        if place is None:
            return early_response(
                f"{start_place_label} пока не сохранён. Пришли геолокацию и напиши “Запомни мой {start_place_label.casefold()}”.",
                role=route.role,
                reason="deterministic route missing personal place",
                diagnostics={"tool_call_count": 0, "html_report_created": False, "personal_place_start": start_place_label},
            )
        start = f"{float(place['latitude']):.6f},{float(place['longitude']):.6f}"
    helper = _trip_helper_path()
    if not helper.exists():
        return None
    command = [
        sys.executable or "python3",
        str(helper),
        "--start",
        start,
        "--destination",
        destination,
        "--parking-radius",
        "1200",
        "--parking-limit",
        "5",
        "--timeout",
        "8",
        "--parking-timeout",
        "3",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return early_response(
            "BLOCKED\ncity-travel-concierge trip helper timed out before returning route data.",
            status="blocked",
            role=route.role,
            reason="city travel trip helper timeout",
            tools=[{"name": "city_travel_trip.py", "status": "timeout"}],
        )
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    answer = str(payload.get("answer_text") or "").strip()
    if not answer or "BLOCKED" in answer or "INCOMPLETE" in answer:
        return None
    tool_meta = {
        "name": "city_travel_trip.py",
        "status": "ok",
        "answer_ready": bool(payload.get("answer_ready")),
        "degraded_sections": payload.get("degraded_sections") or [],
        "provider_status": payload.get("provider_status") or [],
    }
    if platform_key and chat_id:
        _save_city_travel_context(
            platform_key=platform_key,
            chat_id=str(chat_id),
            session_key=session_key,
            payload=payload,
            start=original_start,
            destination=destination,
            start_place_label=start_place_label,
        )
    return early_response(
        answer,
        role=route.role,
        reason="deterministic city travel route parking fast path",
        tools=[tool_meta],
        diagnostics={
            "tool_call_count": 1,
            "aggregate_helper": "city_travel_trip.py",
            "approximate_start": bool(payload.get("approximate_start")),
            "traffic_status": payload.get("traffic_status"),
            "html_report_created": False,
        },
    )


def prepare_task_turn(*, message: str, platform_key: str, chat_id: str,
                      session_key: str, session_id: str, request_id: str,
                      user_config: dict, platform_toolsets: list[str] | None,
                      message_context: dict | MessageContext | None = None) -> PreparedTaskTurn:
    store = TaskStateStore()
    original = str(message or "")
    msg_ctx = _normalize_message_context(message_context, fallback_text=original, chat_id=str(chat_id))
    current_text = msg_ctx.current_text or original
    uncertainty_context = "\n".join(
        part.strip()
        for part in (msg_ctx.reply_text or "", msg_ctx.reply_caption or "")
        if part and part.strip()
    )
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
    initial_route = route_turn(current_text, command=None, platform_key=platform_key,
                               user_config=user_config, platform_toolsets=platform_toolsets,
                               context_text=uncertainty_context)
    location_response = _deterministic_location_place_flow(
        text=current_text,
        route=initial_route,
        platform_key=platform_key,
        chat_id=str(chat_id),
        session_key=session_key,
        msg_ctx=msg_ctx,
    )
    if location_response is not None:
        return PreparedTaskTurn(original, initial_route, None, location_response, False)

    pre_continuation_uncertainty = detect_uncertain_intent(current_text, context_text=uncertainty_context)
    if pre_continuation_uncertainty is not None and not store.active(platform_key, str(chat_id)):
        return PreparedTaskTurn(
            original, initial_route, None,
            early_response(
                clarification_question(current_text, pre_continuation_uncertainty),
                role="no_llm", reason="deterministic uncertainty gate before tools",
                diagnostics={
                    "clarification_required": True,
                    "clarification_kind": pre_continuation_uncertainty,
                    "tool_call_count": 0,
                    "uncertainty_gate": "pre_model",
                },
            ),
            False,
        )

    place_lookup_response = _deterministic_place_lookup(
        text=current_text,
        route=initial_route,
        platform_key=platform_key,
        chat_id=str(chat_id),
        msg_ctx=msg_ctx,
    )
    if place_lookup_response is not None:
        return PreparedTaskTurn(original, initial_route, None, place_lookup_response, False)

    travel_followup_response = _deterministic_city_travel_followup(
        text=current_text,
        route=initial_route,
        platform_key=platform_key,
        chat_id=str(chat_id),
        session_key=session_key,
    )
    if travel_followup_response is not None:
        return PreparedTaskTurn(original, initial_route, None, travel_followup_response, False)

    pre_continuation_uncertainty = detect_uncertain_intent(current_text, context_text=uncertainty_context)
    if pre_continuation_uncertainty is not None and not store.active(platform_key, str(chat_id)):
        return PreparedTaskTurn(
            original,
            initial_route,
            None,
            early_response(
                clarification_question(current_text, pre_continuation_uncertainty),
                role="no_llm",
                reason="deterministic uncertainty gate before continuation",
                diagnostics={
                    "clarification_required": True,
                    "clarification_kind": pre_continuation_uncertainty,
                    "tool_call_count": 0,
                    "uncertainty_gate": "pre_model",
                },
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

    # Explicit calendar create commands are new actions.  A previous incomplete
    # calendar task may contribute its saved draft, but must not become the
    # execution task for a new Telegram update/message.  Idempotency for the
    # same inbound update is handled later by source_request_id.
    pending_calendar_draft_task = pending_calendar
    task = decision.task if decision.kind == "selected" else None
    if task is not None and _task_is_calendar_write(task) and not _calendar_task_sender_matches(task, msg_ctx):
        task = None
    continued = task is not None
    use_other_path = bool(re.search(r"\bдруг(?:ой|им)\s+(?:источник|способ)(?:ом)?\b", original, re.I))
    if task is not None and int(task.metadata.get("budget_exhaustions", 0) or 0) >= 2 and not use_other_path:
        return PreparedTaskTurn(
            original, None, task,
            early_response(
                "Я уже дважды дошёл до лимита без подтверждённого результата и не буду продолжать перебор. "
                "Как продолжить: сузить задачу до одного результата, использовать другой источник "
                "или остановить её?",
                task_id=task.task_id, role=task.role,
                reason="budget exhaustion clarification",
                diagnostics={
                    "clarification_required": True,
                    "clarification_kind": "budget_exhaustion",
                    "tool_call_count": 0,
                    "uncertainty_gate": "pre_model",
                },
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
        if use_other_path:
            message += "\n\nUse a different source/tool path. Do not repeat the failed search loop."

    if task is None:
        quick_note = detect_quick_note(current_text, continued=False)
        if quick_note is not None:
            try:
                quick_result = run_quick_save(quick_note)
            except Exception as exc:
                quick_result = {"status": "error", "saved": False, "message": str(exc)}
            response, status = format_quick_save_response(quick_result)
            return PreparedTaskTurn(
                original, None, None,
                early_response(
                    response,
                    status=status,
                    role="no_llm",
                    reason="deterministic quick note capture",
                ),
                False,
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
    calendar_request_draft = None
    calendar_request_source_task_id = None
    if draft is not None and _is_calendar_write_request(current_text):
        calendar_request = _format_calendar_request(current_text, draft, draft_source)
        calendar_request_draft = draft
        if task is None:
            message = calendar_request
    elif _is_calendar_write_request(current_text):
        saved_draft_source = task or pending_calendar_draft_task
        saved_draft = saved_draft_source.metadata.get("calendar_event_draft") if saved_draft_source else None
        if isinstance(saved_draft, dict):
            calendar_request = _format_calendar_request(current_text, saved_draft, "pending_task")
            calendar_request_draft = saved_draft
            calendar_request_source_task_id = saved_draft_source.task_id
            if task is None:
                message = calendar_request

    base_text = task.original_request if task else str(calendar_request or message or "")
    route = route_turn(base_text, command=None, platform_key=platform_key,
                       user_config=user_config, platform_toolsets=platform_toolsets,
                       context_text=uncertainty_context)
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
        if use_other_path:
            restored = sorted((set(restored) | set(route.toolsets)) - {"no_mcp"})
        route = TaskRoute(
            role=route.role if use_other_path else task.role,
            reason=f"continued task {task.task_id}; {route.reason}",
            toolsets=restored,
            max_iterations=route.max_iterations,
            skill_names=route.skill_names,
            skip_context_files=route.skip_context_files,
            operational_context=(
                route.operational_context
                + "\n\nIteration budget policy: the route limit is fixed; do not increase it. "
                + "Reserve the last 5 iterations for tests, Definition of Done checks, checkpoint persistence, and final delivery."
                + (" Use another source/tool path and stop after enough evidence." if use_other_path else "")
            ).strip(),
        )

    if task is None:
        uncertainty_kind = detect_uncertain_intent(base_text, context_text=uncertainty_context)
        clarify_only = route.toolsets == ["clarify"]
        if uncertainty_kind is not None or clarify_only:
            kind = uncertainty_kind or "ambiguous_action"
            return PreparedTaskTurn(
                str(message or ""),
                route,
                None,
                early_response(
                    clarification_question(base_text, kind),
                    role="no_llm",
                    reason="deterministic uncertainty gate",
                    diagnostics={
                        "clarification_required": True,
                        "clarification_kind": kind,
                        "tool_call_count": 0,
                        "uncertainty_gate": "pre_model",
                    },
                ),
                False,
            )

    if task is None:
        travel_fast_response = _run_city_travel_trip_fast_path(
            base_text,
            route,
            platform_key=platform_key,
            chat_id=str(chat_id),
            session_key=session_key,
            sender_id=str(msg_ctx.sender_id or ""),
        )
        if travel_fast_response is not None:
            return PreparedTaskTurn(
                str(message or ""),
                route,
                None,
                travel_fast_response,
                continued,
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
    if "city-travel-concierge" in route.skill_names:
        requires_execution = True
        required = tuple(sorted(set(required) | {"terminal"}))

    html_report_requested = should_generate_html_report(
        base_text, route.role, requires_execution=requires_execution,
    )
    delivery_policy = {
        "final_delivery_owner": "gateway_runtime",
        "suppress_external_finalizer": True,
        "html_report_requested": bool(html_report_requested),
        "suppress_html_report": not bool(html_report_requested),
    }
    if task is not None:
        store.merge_metadata(task.task_id, delivery_policy)

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
        safe_fallback = {"clarify", "skills", "file", "web", "terminal"}
        if platform_toolsets is not None:
            safe_fallback &= set(platform_toolsets)
        if safe_fallback - {"clarify"}:
            route = TaskRoute(
                role=route.role,
                reason=route.reason + "; universal_safe_action_fallback",
                toolsets=sorted(safe_fallback),
                max_iterations=route.max_iterations,
                skill_names=route.skill_names,
                skip_context_files=route.skip_context_files,
                operational_context=route.operational_context,
            )
            working_toolsets = [name for name in route.toolsets if name != "clarify"]
        else:
            if task is not None:
                store.update(task.task_id, status="blocked",
                             last_error="no safe execution capability available")
            return PreparedTaskTurn(
                str(message or ""), route, task,
                early_response(
                    "Не могу начать выполнение: в этом канале нет доступного инструмента. "
                    "Пришли ссылку или файл напрямую либо уточни, какой результат нужен.",
                    status="blocked", task_id=task.task_id if task else None,
                    role=route.role, reason="no safe execution capability available",
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

    if task is None and (
        calendar_request is not None
        or requires_execution
        or should_track_task(original, route.role, route.toolsets)
    ):
        title_source = calendar_request or original
        title = " ".join(title_source.split())[:120] or "Задача Hermes"
        task_metadata = dict(delivery_policy)
        source_request_id = request_id
        if calendar_request and calendar_request_draft is not None:
            source_request_id = _calendar_source_request_id(platform_key, str(chat_id), msg_ctx, request_id)
            task_metadata.update({
                "intent": "calendar_write",
                "sender_id": msg_ctx.sender_id,
                "current_message_id": msg_ctx.current_message_id,
                "platform_update_id": msg_ctx.update_id,
                "reply_message_id": msg_ctx.reply_message_id,
                "reply_sender_id": msg_ctx.reply_sender_id,
                "calendar_event_draft": calendar_request_draft,
                "execution_contract": {"type": "calendar_write"},
            })
            if calendar_request_source_task_id:
                task_metadata["calendar_draft_source_task_id"] = calendar_request_source_task_id
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
