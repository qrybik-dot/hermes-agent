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
from gateway.task_router import TaskRoute, route_turn, travel_is_route_or_parking, travel_needs_web_or_browser
from gateway.quick_note_capture import (
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
    r"\b(?:до|в)\s+(?P<destination>.+?)\s+от\s+(?P<start>.+?)(?:\s+и\s+|[?.!]|$)",
    re.I | re.S,
)


def _parse_route_trip_request(text: str) -> tuple[str, str] | None:
    value = " ".join((text or "").strip().split())
    match = _TRAVEL_TO_FROM_RE.search(value)
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
    deep_links = candidate.get("deep_links") if isinstance(candidate.get("deep_links"), dict) else {}
    url = str(deep_links.get("yandex_maps") or "").strip()
    if url:
        return url
    coords = candidate.get("coordinates") if isinstance(candidate.get("coordinates"), dict) else {}
    lat = _as_float(coords.get("lat"))
    lon = _as_float(coords.get("lon"))
    if lat is None or lon is None:
        return ""
    return f"https://yandex.ru/maps/?pt={lon:.6f},{lat:.6f}&z=18&l=map"


def _trim_parking_evidence(candidate: dict[str, Any]) -> dict[str, Any]:
    evidence = candidate.get("parking_evidence") if isinstance(candidate.get("parking_evidence"), dict) else {}
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
        "address": str(candidate.get("address") or "").strip(),
        "coordinates": {"lat": lat, "lon": lon},
        "distance_m": _as_float(candidate.get("distance_m")),
        "parking_status": str(candidate.get("parking_status") or candidate.get("verification_status") or "unverified"),
        "eligible_for_recommendation": bool(candidate.get("eligible_for_recommendation")),
        "is_free": candidate.get("is_free") if isinstance(candidate.get("is_free"), bool) else None,
        "evidence": _trim_parking_evidence(candidate),
    }
    normalized["id"] = _parking_candidate_id(normalized)
    normalized["raw_yandex_maps_url"] = _candidate_yandex_url(candidate) or _candidate_yandex_url(normalized)
    return normalized


def _parking_rank_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    status_priority = {"official": 0, "user_confirmed": 1, "likely_free": 2, "unverified": 3}
    return (not bool(candidate.get("eligible_for_recommendation")), status_priority.get(str(candidate.get("parking_status") or "unverified"), 9), candidate.get("distance_m") is None, candidate.get("distance_m") or 0, candidate.get("title") or "")


def _travel_context_from_payload(payload: dict[str, Any], *, start: str, destination: str) -> dict[str, Any]:
    route = payload.get("route") if isinstance(payload.get("route"), dict) else {}
    route_links = route.get("deep_links") if isinstance(route.get("deep_links"), dict) else {}
    candidates = []
    for item in payload.get("parking_candidates") or []:
        normalized = _normalize_parking_candidate(item)
        if normalized is not None:
            candidates.append(normalized)
    return {
        "source": "city-travel-concierge",
        "start_text": start,
        "resolved_start": payload.get("resolved_start"),
        "approximate_start": bool(payload.get("approximate_start")),
        "destination_text": destination,
        "resolved_destination": payload.get("resolved_destination"),
        "destination_coordinates": ((payload.get("coordinates") or {}).get("destination") if isinstance(payload.get("coordinates"), dict) else None),
        "route_url": str(route_links.get("yandex_maps") or ""),
        "traffic_status": payload.get("traffic_status"),
        "parking_candidates": sorted(candidates, key=_parking_rank_key),
        "shown_parking_ids": [],
        "checked_at": payload.get("checked_at"),
        "degraded_sections": payload.get("degraded_sections") or [],
        "statuses": {"traffic_status": payload.get("traffic_status"), "parking_statuses": payload.get("parking_statuses") or []},
    }


_PARKING_LINK_RE = re.compile(r"\b(?:пришл\w*|дай|дайте|открой\w*|скинь\w*|координат\w*)\b.{0,120}\b(?:парковк\w*|яндекс\w*|карт\w*)\b|\b(?:парковк\w*)\b.{0,80}\b(?:ссылк\w*|координат\w*|яндекс\w*)\b", re.I | re.S)
_MORE_PARKING_RE = re.compile(r"\b(?:ещ[её]|друг\w*|альтернатив\w*)\b.{0,120}\b(?:парковк\w*|бесплатн\w*|дешев\w*|вариант\w*)\b|\b(?:парковк\w*)\b.{0,120}\b(?:ещ[её]|друг\w*|альтернатив\w*)\b", re.I | re.S)
_ROUTE_REPEAT_RE = re.compile(r"\b(?:пришл\w*|дай|дайте|открой\w*|повтори\w*)\b.{0,100}\b(?:маршрут\w*|ссылк\w*\s+яндекс|яндекс\s+карт\w*)\b", re.I | re.S)
_DESTINATION_CLARIFICATION_RE = re.compile(r"\b(?:парк\w*|усадьб\w*|музе\w*|вднх|останкин\w*|точнее|около|рядом)\b", re.I)
_CAFE_OR_REVIEW_RE = re.compile(r"\b(?:кафе|ресторан|отзыв\w*|рейтинг\w*|ранжир\w*|покушать|поесть)\b", re.I)


def _city_travel_followup_intent(text: str) -> str | None:
    value = " ".join((text or "").strip().split())
    if not value or _CAFE_OR_REVIEW_RE.search(value):
        return None
    if _MORE_PARKING_RE.search(value):
        return "more_parking"
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


def _parking_status_note(candidate: dict[str, Any]) -> str:
    status = str(candidate.get("parking_status") or "unverified")
    if status == "likely_free":
        return "Статус: likely_free; это не официальная гарантия бесплатности."
    if status == "official" and candidate.get("is_free") is False:
        return "Статус: official; источник указывает платную парковку."
    if status in {"official", "user_confirmed"} and candidate.get("is_free") is True:
        return f"Статус: {status}; бесплатность подтверждена источником."
    return f"Статус: {status}; условия нужно проверить на месте."


def _format_parking_candidate(candidate: dict[str, Any], *, index: int | None = None) -> str:
    prefix = f"{index}. " if index is not None else ""
    coords = candidate.get("coordinates") or {}
    lines = [f"{prefix}{candidate.get('title') or 'парковка'} - {_format_distance(candidate.get('distance_m'))}.", _parking_status_note(candidate), f"Координаты: {float(coords.get('lat')):.6f}, {float(coords.get('lon')):.6f}", f"Яндекс Карты: {candidate.get('raw_yandex_maps_url') or 'ссылка недоступна'}"]
    evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}
    if evidence.get("reason"):
        lines.append("Основание: " + str(evidence.get("reason")))
    official = evidence.get("official") if isinstance(evidence.get("official"), dict) else None
    if official and official.get("tariffs"):
        lines.append("Официальный тариф: " + str(official.get("tariffs")))
    return "\n".join(lines)


def _save_city_travel_context(*, platform_key: str, chat_id: str, session_key: str, payload: dict[str, Any], start: str, destination: str) -> None:
    try:
        CityTravelContextStore().save(platform=platform_key, chat_id=str(chat_id), session_key=session_key, context=_travel_context_from_payload(payload, start=start, destination=destination))
    except Exception:
        pass


def _update_city_travel_context(*, platform_key: str, chat_id: str, session_key: str, context: dict[str, Any]) -> None:
    try:
        CityTravelContextStore().save(platform=platform_key, chat_id=str(chat_id), session_key=session_key, context=context)
    except Exception:
        pass


def _deterministic_city_travel_followup(*, text: str, route: TaskRoute, platform_key: str, chat_id: str, session_key: str) -> dict | None:
    if "city-travel-concierge" not in route.skill_names:
        return None
    intent = _city_travel_followup_intent(text)
    if intent is None:
        return None
    context = CityTravelContextStore().get(platform=platform_key, chat_id=str(chat_id), session_key=session_key)
    if context is None:
        return None
    if intent == "route_repeat":
        route_url = str(context.get("route_url") or "").strip()
        if not route_url:
            return early_response("Сохранённого URL маршрута нет. Пришлите полный запрос с точкой старта и назначением.", role=route.role, reason="deterministic city travel follow-up missing route url", diagnostics={"tool_call_count": 0, "html_report_created": False, "travel_followup_intent": intent})
        return early_response("Маршрут в Яндекс Картах:\n" + route_url, role=route.role, reason="deterministic city travel route url repeat", diagnostics={"tool_call_count": 0, "html_report_created": False, "travel_followup_intent": intent})
    if intent == "parking_link":
        candidates = sorted([item for item in context.get("parking_candidates") or [] if isinstance(item, dict)], key=_parking_rank_key)
        eligible = [item for item in candidates if item.get("eligible_for_recommendation")]
        if not eligible:
            return early_response("В сохранённом маршруте нет подходящих кандидатов парковки. Бесплатность или доступность не подтверждаю.", role=route.role, reason="deterministic city travel no eligible parking candidates", diagnostics={"tool_call_count": 0, "html_report_created": False, "travel_followup_intent": intent})
        selected = eligible[0]
        shown = set(str(x) for x in context.get("shown_parking_ids") or [])
        shown.add(str(selected.get("id") or _parking_candidate_id(selected)))
        context["shown_parking_ids"] = sorted(shown)
        _update_city_travel_context(platform_key=platform_key, chat_id=str(chat_id), session_key=session_key, context=context)
        return early_response("Ближайший подходящий кандидат парковки:\n" + _format_parking_candidate(selected), role=route.role, reason="deterministic city travel parking link follow-up", diagnostics={"tool_call_count": 0, "html_report_created": False, "travel_followup_intent": intent})
    if intent == "more_parking":
        shown = set(str(x) for x in context.get("shown_parking_ids") or [])
        candidates = sorted([item for item in context.get("parking_candidates") or [] if isinstance(item, dict)], key=_parking_rank_key)
        selected = [item for item in candidates if str(item.get("id") or _parking_candidate_id(item)) not in shown][:3]
        if not selected:
            return early_response("Других сохранённых кандидатов парковки нет. Не буду выдумывать бесплатные места без источников.", role=route.role, reason="deterministic city travel no more parking candidates", diagnostics={"tool_call_count": 0, "html_report_created": False, "travel_followup_intent": intent})
        for item in selected:
            shown.add(str(item.get("id") or _parking_candidate_id(item)))
        context["shown_parking_ids"] = sorted(shown)
        _update_city_travel_context(platform_key=platform_key, chat_id=str(chat_id), session_key=session_key, context=context)
        body = [f"Показываю {len(selected)} сохранённ(ых) кандидат(а/ов) парковки. likely_free не считаю официально бесплатной парковкой."]
        body.extend(_format_parking_candidate(item, index=index) for index, item in enumerate(selected, 1))
        return early_response("\n\n".join(body), role=route.role, reason="deterministic city travel more parking follow-up", diagnostics={"tool_call_count": 0, "html_report_created": False, "travel_followup_intent": intent})
    if intent == "destination_clarification":
        start = str(context.get("start_text") or "").strip()
        if not start:
            return None
        return _run_city_travel_trip_fast_path(f"сколько ехать до {text} от {start}", route, platform_key=platform_key, chat_id=str(chat_id), session_key=session_key)
    return None


def _run_city_travel_trip_fast_path(text: str, route: TaskRoute, *, platform_key: str = "", chat_id: str = "", session_key: str = "") -> dict | None:
    if "city-travel-concierge" not in route.skill_names:
        return None
    if not travel_is_route_or_parking(text) or travel_needs_web_or_browser(text):
        return None
    parsed = _parse_route_trip_request(text)
    if parsed is None:
        return None
    start, destination = parsed
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
            start=start,
            destination=destination,
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
                               user_config=user_config, platform_toolsets=platform_toolsets)
    travel_followup_response = _deterministic_city_travel_followup(
        text=current_text,
        route=initial_route,
        platform_key=platform_key,
        chat_id=str(chat_id),
        session_key=session_key,
    )
    if travel_followup_response is not None:
        return PreparedTaskTurn(original, initial_route, None, travel_followup_response, False)

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

    if task is None:
        travel_fast_response = _run_city_travel_trip_fast_path(
            base_text,
            route,
            platform_key=platform_key,
            chat_id=str(chat_id),
            session_key=session_key,
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
        task_metadata = None
        source_request_id = request_id
        if calendar_request and calendar_request_draft is not None:
            source_request_id = _calendar_source_request_id(platform_key, str(chat_id), msg_ctx, request_id)
            task_metadata = {
                "intent": "calendar_write",
                "sender_id": msg_ctx.sender_id,
                "current_message_id": msg_ctx.current_message_id,
                "platform_update_id": msg_ctx.update_id,
                "reply_message_id": msg_ctx.reply_message_id,
                "reply_sender_id": msg_ctx.reply_sender_id,
                "calendar_event_draft": calendar_request_draft,
                "execution_contract": {"type": "calendar_write"},
            }
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
