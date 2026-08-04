"""Persistent continuation state for gateway tasks."""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from gateway.intent_uncertainty import is_reminder_request

_ACTIVE = ("running", "paused", "blocked", "incomplete", "awaiting_delivery", "delivery_failed")
_CONTINUE_RE = re.compile(
    r"(?:^|\n)\s*(?:готов\s+продолжить|продолж(?:ай|ить|им)|дальше|возобнов(?:и|ить)|"
    r"можно\s+продолжить|давай\s+продолжим)(?:\s+(\d+|[0-9a-f]{6,32}))?[\s.!?]*$", re.I)
_CONTEXTUAL_CONTINUE_RE = re.compile(
    r"^\s*(?:\u0434\u0430[\s,]+)?(?:\u0437\u0430\u043f\u0443\u0441\u0442\u0438|\u0432\u044b\u043f\u043e\u043b\u043d\u0438|\u043f\u0440\u043e\u0432\u0435\u0434\u0438|\u043f\u0440\u043e\u0434\u043e\u043b\u0436\u0438|\u0441\u0434\u0435\u043b\u0430\u0439)\s+"
    r"(?:\u044d\u0442\u043e|\u0435\u0433\u043e|\u0435[\u0435\u0451]|\u0442\u0435\u0441\u0442|smoke[- ]?test)\s*[.!?]*$",
    re.I,
)
_OPTION_CONTINUE_RE = re.compile(
    r"(?:^|\n)\s*(?:да[\s,]+)?(?:"
    r"(?:попробуй\s+)?друг(?:ой|им)\s+(?:источник|способ)(?:ом)?|"
    r"один\s+(?:подтвержд[её]нный\s+)?результат|"
    r"сузь\s+(?:задачу|поиск)|сузить\s+(?:задачу|поиск)"
    r")\s*[.!?]*$",
    re.I,
)
_NUMERIC_CHOICE_RE = re.compile(r"^\s*(\d{1,2})[\s.!?]*$")
_OPAQUE_INPUT_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z0-9][A-Za-z0-9_.-]{23,}(?![A-Za-z0-9])"
)
_CHOICE_CACHE: dict[tuple[str, str], tuple[float, tuple[str, ...]]] = {}
_CHOICE_TTL_SECONDS = 600

_PAUSE_RE = re.compile(
    r"\b(?:повторим\s+(?:утром|позже)|продолжим\s+(?:утром|позже)|"
    r"поставь\s+на\s+паузу|приостанови|отложи\s+(?:до|на))\b", re.I)
_PROGRESS_ONLY_RE = re.compile(r"^\s*(?:⏳|🟡|🔄)?\s*(?:в\s+работе|этап\s+\d+|шаг\s+\d+)", re.I)
_EXECUTION_RE = re.compile(
    r"\b(?:проверь|аудит|найди|создай|добавь|исправь|почини|выполни|запусти|"
    r"обнови|настрой|установи|расширь|подключи|внедри|доработай|реализуй|"
    r"синхрониз|отправь|запиши|сохрани|скачай|проанализируй\s+на\s+vps)\w*\b|"
    r"(?:какие|какой|список|подборк)\w*\s+(?:ещ[её]\s+)?(?:навык|skill)\w*|"
    r"(?:есть|установлен|подключен|доступен)\w*.*(?:навык|skill)\w*", re.I)
_PLAN_ONLY_RE = re.compile(r"\b(?:составь|подготовь)\s+план\b", re.I)
_NON_PLAN_ACTION_RE = re.compile(
    r"\b(?:проверь|найди|создай|добавь|исправь|почини|выполни|запусти|"
    r"обнови|настрой|установи|расширь|подключи|внедри|доработай|реализуй|"
    r"синхрониз\w*|отправь|запиши|сохрани|скачай|разверни|примени|удали)\b",
    re.I,
)

_READBACK_LOOKUP_RE = re.compile(
    r"\b(?:покажи|прочитай|выведи|открой)\w*\b.{0,120}\b(?:текст|содержим|звонок|запис|файл|лог|статус|сервис|таймер|карточк|knowledge|памят)\w*\b|"
    r"\b(?:текст|содержим|звонок|запис|файл|лог|статус|сервис|таймер|карточк|knowledge|памят)\w*\b.{0,120}\b(?:покажи|прочитай|выведи|открой)\w*\b",
    re.I,
)

_STATE_LOOKUP_RE = re.compile(
    r"\b(?:сохран[её]н|записан|создан|существует|доступен|работает|активен)\w*\b.{0,100}\b(?:звонок|запись|файл|сервис|таймер|баз[аыу]|knowledge|granola)\w*\b|"
    r"\b(?:звонок|запись|файл|сервис|таймер|баз[аыу]|knowledge|granola)\w*\b.{0,100}\b(?:сохран[её]н|записан|создан|существует|доступен|работает|активен)\w*\b",
    re.I,
)

_TERMINAL_EXECUTION_RE = re.compile(
    r"\b(?:git|vps|systemd|journalctl|sudo|root|ssh|gateway)\b|"
    r"\bсервис(?:а|ы|ов|е|у|ом|ами|ах)?\b|"
    r"\bлог(?:и|ов|е|ах|ами)?\b|права\s+доступа|authorized_keys|\.env\b",
    re.I,
)


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    platform: str
    chat_id: str
    session_key: str
    title: str
    original_request: str
    role: str
    toolsets: tuple[str, ...]
    required_toolsets: tuple[str, ...]
    requires_execution: bool
    status: str
    source_request_id: str
    source_session_id: str
    status_message_id: Optional[str]
    created_at: float
    updated_at: float
    expires_at: float
    last_error: Optional[str]
    metadata: dict


@dataclass(frozen=True)
class ContinuationDecision:
    kind: str
    task: Optional[TaskRecord] = None
    candidates: tuple[TaskRecord, ...] = ()


class TaskStateStore:
    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path or Path.home() / ".hermes" / "state.db")
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS gateway_tasks (
                    task_id TEXT PRIMARY KEY, platform TEXT NOT NULL, chat_id TEXT NOT NULL,
                    session_key TEXT NOT NULL DEFAULT '', title TEXT NOT NULL,
                    original_request TEXT NOT NULL, role TEXT NOT NULL,
                    toolsets_json TEXT NOT NULL DEFAULT '[]',
                    required_toolsets_json TEXT NOT NULL DEFAULT '[]',
                    requires_execution INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
                    source_request_id TEXT NOT NULL DEFAULT '', source_session_id TEXT NOT NULL DEFAULT '',
                    status_message_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL, last_error TEXT, metadata_json TEXT NOT NULL DEFAULT '{}');
                CREATE INDEX IF NOT EXISTS idx_gateway_tasks_chat_status
                    ON gateway_tasks(platform, chat_id, status, updated_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_gateway_tasks_source_request
                    ON gateway_tasks(source_request_id) WHERE source_request_id <> '';
            """)

    @staticmethod
    def _record(row: sqlite3.Row) -> TaskRecord:
        def _list(name: str) -> tuple[str, ...]:
            try:
                return tuple(str(x) for x in json.loads(row[name] or "[]") if x)
            except Exception:
                return ()

        try:
            metadata = json.loads(row["metadata_json"] or "{}")
            if not isinstance(metadata, dict):
                metadata = {}
        except Exception:
            metadata = {}

        return TaskRecord(
            row["task_id"], row["platform"], row["chat_id"], row["session_key"] or "",
            row["title"], row["original_request"], row["role"], _list("toolsets_json"),
            _list("required_toolsets_json"), bool(row["requires_execution"]), row["status"],
            row["source_request_id"] or "", row["source_session_id"] or "", row["status_message_id"],
            float(row["created_at"]), float(row["updated_at"]), float(row["expires_at"]),
            row["last_error"], metadata,
        )

    def get(self, task_id: str) -> Optional[TaskRecord]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM gateway_tasks WHERE task_id=?", (task_id,)).fetchone()
        return self._record(row) if row else None

    def active(self, platform: str, chat_id: str, limit: int = 10) -> list[TaskRecord]:
        placeholders = ",".join("?" for _ in _ACTIVE)
        params = [platform, str(chat_id), *_ACTIVE, time.time(), int(limit)]
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM gateway_tasks WHERE platform=? AND chat_id=? AND status IN ({placeholders}) "
                f"AND expires_at>? ORDER BY updated_at DESC LIMIT ?", params,
            ).fetchall()
        records = [self._record(row) for row in rows]
        return [
            task for task in records
            if not (
                task.status == "incomplete"
                and task.last_error in {
                    "execution task completed without tool calls",
                    "iteration_budget_exhausted",
                }
                and _CONTINUE_RE.search(task.original_request or "")
            )
        ]

    def latest_awaiting_delivery(self, platform: str, chat_id: str) -> Optional[TaskRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM gateway_tasks WHERE platform=? AND chat_id=? AND status='awaiting_delivery' "
                "ORDER BY updated_at DESC LIMIT 1", (platform, str(chat_id)),
            ).fetchone()
        return self._record(row) if row else None

    def create(self, *, platform: str, chat_id: str, session_key: str, title: str,
               original_request: str, role: str, toolsets: Iterable[str],
               required_toolsets: Iterable[str] = (), requires_execution: bool = False,
               source_request_id: str = "", source_session_id: str = "", status: str = "running",
               metadata: Optional[dict] = None, ttl_days: int = 14,
               task_id: Optional[str] = None) -> TaskRecord:
        now = time.time()
        task_id = task_id or uuid.uuid4().hex[:12]
        with self._connect() as conn:
            if source_request_id:
                existing = conn.execute(
                    "SELECT task_id FROM gateway_tasks WHERE source_request_id=?", (source_request_id,),
                ).fetchone()
                if existing:
                    self.update(existing["task_id"], status=status, original_request=original_request)
                    return self.get(existing["task_id"])
            conn.execute("""INSERT INTO gateway_tasks (
                task_id,platform,chat_id,session_key,title,original_request,role,toolsets_json,
                required_toolsets_json,requires_execution,status,source_request_id,source_session_id,
                created_at,updated_at,expires_at,metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (task_id, platform, str(chat_id), session_key or "", (title or "Задача Hermes")[:120],
                 original_request, role, json.dumps(sorted(set(toolsets)), ensure_ascii=False),
                 json.dumps(sorted(set(required_toolsets)), ensure_ascii=False), 1 if requires_execution else 0,
                 status, source_request_id or "", source_session_id or "", now, now,
                 now + ttl_days * 86400, json.dumps(metadata or {}, ensure_ascii=False)))
        return self.get(task_id)

    def update(self, task_id: str, **changes) -> None:
        allowed = {"status", "title", "original_request", "role", "session_key", "source_session_id",
                   "status_message_id", "last_error", "expires_at"}
        fields, values = [], []
        for key, value in changes.items():
            if key in allowed:
                fields.append(f"{key}=?")
                values.append(value)
        if not fields:
            return
        fields.append("updated_at=?")
        values.extend([time.time(), task_id])
        with self._connect() as conn:
            conn.execute(f"UPDATE gateway_tasks SET {', '.join(fields)} WHERE task_id=?", values)

    def merge_metadata(self, task_id: str, values: Optional[dict] = None, **changes) -> None:
        task = self.get(task_id)
        if task is None:
            return
        merged = dict(task.metadata)
        if values:
            merged.update(values)
        merged.update(changes)
        with self._connect() as conn:
            conn.execute(
                "UPDATE gateway_tasks SET metadata_json=?, updated_at=? WHERE task_id=?",
                (json.dumps(merged, ensure_ascii=False), time.time(), task_id),
            )

    def set_status_message_id(self, task_id: str, message_id: str | int | None) -> None:
        if message_id is not None:
            self.update(task_id, status_message_id=str(message_id))

    def resolve(self, text: str, platform: str, chat_id: str) -> ContinuationDecision:
        value = text or ""
        key = (platform, str(chat_id))
        match = _CONTINUE_RE.search(value)
        prefix_match = _CONTINUE_RE.search(value.splitlines()[0]) if value.splitlines() else None
        contextual = _CONTEXTUAL_CONTINUE_RE.search(value)
        option_match = _OPTION_CONTINUE_RE.search(value)
        numeric = _NUMERIC_CHOICE_RE.fullmatch(value)

        if numeric and not match and not prefix_match:
            cached = _CHOICE_CACHE.get(key)
            if cached is None:
                return ContinuationDecision("none")
            created_at, task_ids = cached
            if time.time() - created_at > _CHOICE_TTL_SECONDS:
                _CHOICE_CACHE.pop(key, None)
                return ContinuationDecision("none")
            index = int(numeric.group(1)) - 1
            if not 0 <= index < len(task_ids):
                tasks = tuple(
                    task for task_id in task_ids
                    if (task := self.get(task_id)) is not None
                    and task.platform == platform
                    and task.chat_id == str(chat_id)
                    and task.status in _ACTIVE
                    and task.expires_at > time.time()
                )
                return ContinuationDecision("choice", candidates=tasks)
            task = self.get(task_ids[index])
            _CHOICE_CACHE.pop(key, None)
            if (
                task is not None
                and task.platform == platform
                and task.chat_id == str(chat_id)
                and task.status in _ACTIVE
                and task.expires_at > time.time()
            ):
                return ContinuationDecision("selected", task=task)
            return ContinuationDecision("empty")

        if not match and not prefix_match and not contextual and not option_match:
            tasks = self.active(platform, str(chat_id))
            implicit_input = bool(_OPAQUE_INPUT_RE.search(value))
            if (
                implicit_input
                and len(tasks) == 1
                and len(value) <= 600
                and tasks[0].status in {"paused", "blocked", "incomplete"}
                and time.time() - tasks[0].updated_at <= 6 * 3600
            ):
                _CHOICE_CACHE.pop(key, None)
                return ContinuationDecision("selected", task=tasks[0])
            if value.strip():
                _CHOICE_CACHE.pop(key, None)
            return ContinuationDecision("none")

        tasks = self.active(platform, str(chat_id))
        selector_match = match or prefix_match
        selector = selector_match.group(1) if selector_match else None
        if selector:
            if selector.isdigit() and 0 <= int(selector) - 1 < len(tasks):
                _CHOICE_CACHE.pop(key, None)
                return ContinuationDecision("selected", task=tasks[int(selector) - 1])
            selected = [task for task in tasks if task.task_id.lower().startswith(selector.lower())]
            if len(selected) == 1:
                _CHOICE_CACHE.pop(key, None)
                return ContinuationDecision("selected", task=selected[0])
            stored = self.get(selector)
            if stored is not None and stored.platform == platform and stored.chat_id == str(chat_id):
                _CHOICE_CACHE.pop(key, None)
                return ContinuationDecision("selected", task=stored)
        if len(tasks) == 1:
            _CHOICE_CACHE.pop(key, None)
            return ContinuationDecision("selected", task=tasks[0])
        if tasks:
            _CHOICE_CACHE[key] = (time.time(), tuple(task.task_id for task in tasks))
            return ContinuationDecision("choice", candidates=tuple(tasks))
        _CHOICE_CACHE.pop(key, None)
        return ContinuationDecision("empty")



def is_pause_request(text: str) -> bool:
    return bool(_PAUSE_RE.search(text or ""))


def is_progress_only(text: str) -> bool:
    return bool(_PROGRESS_ONLY_RE.match(text or ""))


def infer_execution_contract(text: str, role: str, toolsets: Iterable[str]) -> tuple[bool, tuple[str, ...]]:
    value = text or ""
    reminder_request = is_reminder_request(value)
    plan_only = bool(_PLAN_ONLY_RE.search(value)) and not bool(_NON_PLAN_ACTION_RE.search(value))
    execution = (
        bool(_EXECUTION_RE.search(value))
        or bool(_READBACK_LOOKUP_RE.search(value))
        or bool(_STATE_LOOKUP_RE.search(value))
        or reminder_request
    ) and not plan_only
    required: set[str] = set()
    lowered = (text or "").lower()
    if execution and _TERMINAL_EXECUTION_RE.search(text or ""):
        required.add("terminal")
    explicit_calendar = "календар" in lowered or "google calendar" in lowered
    if execution and reminder_request and not explicit_calendar:
        required.add("cronjob")
    if execution and (explicit_calendar or any(term in lowered for term in ("письм", "почт"))):
        required.update({"skills", "terminal"})
    if execution and role in {"coding", "server_debug"}:
        required.add("terminal")
    return execution, tuple(sorted(required))


def should_track_task(text: str, role: str, toolsets: Iterable[str]) -> bool:
    execution, _ = infer_execution_contract(text, role, toolsets)
    return execution or role in {"planning", "coding", "server_debug", "long_context"}


def format_choice(tasks: Iterable[TaskRecord]) -> str:
    rows = ["Есть несколько незавершённых задач. Выберите, какую продолжить:"]
    for index, task in enumerate(tasks, 1):
        rows.append(f"{index}. {task.title} [{task.status}]")
    rows.append("\nОтветьте: «Продолжить 1» или «Продолжить 2»")
    return "\n".join(rows)
