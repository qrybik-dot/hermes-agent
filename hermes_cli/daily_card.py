"""Plain Telegram daily card for personal tasks and timed events.

The module deliberately keeps data ownership separated:

* ``kanban.db`` owns actionable tasks;
* ``daily_cards.db`` owns structured events and Telegram message state;
* cron only invokes morning/evening rendering and is not a second event store.

All renderers are deterministic and require no LLM call.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import logging
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence
from zoneinfo import ZoneInfo

from hermes_constants import get_hermes_home
from hermes_cli import kanban_db as kb

logger = logging.getLogger(__name__)

ACTIVE_TASK_STATUSES = frozenset({"triage", "todo", "ready", "blocked", "scheduled"})
DONE_TASK_STATUSES = frozenset({"done", "completed"})
CARD_KINDS = frozenset({"morning", "evening"})
EVENT_PAYLOAD_MAX_BYTES = 64 * 1024
EVENT_PAYLOAD_KEYS = frozenset(
    {
        "title",
        "event_at",
        "timezone",
        "person",
        "address",
        "location",
        "online_url",
        "requires_travel",
        "preparation",
        "importance",
        "source_kind",
        "source_id",
        "event_id",
    }
)

_RU_MONTHS = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


@dataclass(frozen=True)
class DailyCardSettings:
    enabled: bool = False
    shadow_mode: bool = True
    timezone: str = "Europe/Moscow"
    max_buttons: int = 6
    google_calendar: bool = True


@dataclass(frozen=True)
class CardTask:
    id: str
    title: str
    status: str
    planned_for: Optional[str]
    due_at: Optional[int]
    importance: int
    completed_at: Optional[int]
    overdue: bool = False

    @property
    def done(self) -> bool:
        return self.status in DONE_TASK_STATUSES


@dataclass(frozen=True)
class CardEvent:
    id: str
    title: str
    event_at: int
    timezone: str
    person: str = ""
    address: str = ""
    location: str = ""
    online_url: str = ""
    requires_travel: bool = False
    preparation: tuple[str, ...] = ()
    importance: int = 0
    status: str = "active"
    source_kind: str = "manual"
    source_id: str = ""
    all_day: bool = False
    travel_mode: str = "unknown"
    route_minutes: Optional[int] = None
    route_buffer_minutes: int = 15


@dataclass(frozen=True)
class CardButton:
    label: str
    task_id: str


@dataclass(frozen=True)
class ButtonSpec:
    label: str
    callback_data: str


@dataclass(frozen=True)
class CardRender:
    text: str
    buttons: tuple[CardButton, ...] = ()


@dataclass(frozen=True)
class CallbackTarget:
    task_id: str
    card_date: dt.date
    card_kind: str


def _coerce_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def load_settings(config_path: Optional[Path] = None) -> DailyCardSettings:
    path = config_path or (get_hermes_home() / "config.yaml")
    raw: dict = {}
    if path.exists():
        try:
            import yaml

            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            raw = loaded.get("daily_card") or {}
        except Exception as exc:  # pragma: no cover - defensive config fallback
            logger.warning("Could not read daily_card config: %s", exc)
    max_buttons = int(raw.get("max_buttons") or 6)
    return DailyCardSettings(
        enabled=_coerce_bool(raw.get("enabled"), False),
        shadow_mode=_coerce_bool(raw.get("shadow_mode"), True),
        timezone=str(raw.get("timezone") or "Europe/Moscow"),
        max_buttons=max(1, min(max_buttons, 8)),
        google_calendar=_coerce_bool(raw.get("google_calendar"), True),
    )


def state_db_path() -> Path:
    return get_hermes_home() / "daily_cards.db"


def connect_state(path: Optional[Path] = None) -> sqlite3.Connection:
    db_path = path or state_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS daily_cards (
            card_date   TEXT NOT NULL,
            card_kind   TEXT NOT NULL,
            platform    TEXT NOT NULL,
            chat_id     TEXT NOT NULL,
            message_id  TEXT NOT NULL,
            pinned      INTEGER NOT NULL DEFAULT 0,
            text_hash   TEXT NOT NULL DEFAULT '',
            created_at  INTEGER NOT NULL,
            updated_at  INTEGER NOT NULL,
            PRIMARY KEY (card_date, card_kind, platform, chat_id)
        );

        CREATE TABLE IF NOT EXISTS daily_events (
            id               TEXT PRIMARY KEY,
            title            TEXT NOT NULL,
            event_at         INTEGER NOT NULL,
            timezone         TEXT NOT NULL DEFAULT 'Europe/Moscow',
            person           TEXT NOT NULL DEFAULT '',
            address          TEXT NOT NULL DEFAULT '',
            location         TEXT NOT NULL DEFAULT '',
            online_url       TEXT NOT NULL DEFAULT '',
            requires_travel  INTEGER NOT NULL DEFAULT 0,
            preparation_json TEXT NOT NULL DEFAULT '[]',
            importance       INTEGER NOT NULL DEFAULT 0,
            status           TEXT NOT NULL DEFAULT 'active',
            source_kind      TEXT NOT NULL DEFAULT 'manual',
            source_id        TEXT NOT NULL DEFAULT '',
            all_day          INTEGER NOT NULL DEFAULT 0,
            travel_mode      TEXT NOT NULL DEFAULT 'unknown',
            route_minutes    INTEGER,
            route_buffer_minutes INTEGER NOT NULL DEFAULT 15,
            created_at       INTEGER NOT NULL,
            updated_at       INTEGER NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_events_source
            ON daily_events(source_kind, source_id)
            WHERE source_id <> '';
        CREATE INDEX IF NOT EXISTS idx_daily_events_at
            ON daily_events(event_at, status);

        CREATE TABLE IF NOT EXISTS daily_task_tokens (
            token       TEXT PRIMARY KEY,
            task_id     TEXT NOT NULL,
            card_date   TEXT NOT NULL,
            card_kind   TEXT NOT NULL,
            created_at  INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_daily_task_tokens_date
            ON daily_task_tokens(card_date, card_kind);

        CREATE TABLE IF NOT EXISTS delivery_log (
            item_id     TEXT NOT NULL,
            purpose     TEXT NOT NULL,
            window_key  TEXT NOT NULL,
            message_id  TEXT,
            sent_at     INTEGER NOT NULL,
            PRIMARY KEY (item_id, purpose, window_key)
        );

        CREATE TABLE IF NOT EXISTS source_sync (
            source      TEXT NOT NULL,
            window_key  TEXT NOT NULL,
            synced_at   INTEGER NOT NULL,
            item_count  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (source, window_key)
        );
        """
    )
    event_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(daily_events)")
    }
    for name, definition in (
        ("all_day", "all_day INTEGER NOT NULL DEFAULT 0"),
        ("travel_mode", "travel_mode TEXT NOT NULL DEFAULT 'unknown'"),
        ("route_minutes", "route_minutes INTEGER"),
        (
            "route_buffer_minutes",
            "route_buffer_minutes INTEGER NOT NULL DEFAULT 15",
        ),
    ):
        if name not in event_columns:
            conn.execute(f"ALTER TABLE daily_events ADD COLUMN {definition}")
    conn.commit()
    return conn


def _day_bounds(target_date: dt.date, timezone: ZoneInfo) -> tuple[int, int]:
    start = dt.datetime.combine(target_date, dt.time.min, tzinfo=timezone)
    end = start + dt.timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def _safe_title(value: object, limit: int = 180) -> str:
    return " ".join(str(value or "").split())[:limit]


def select_tasks(
    db_path: Path,
    target_date: dt.date,
    timezone: ZoneInfo,
) -> list[CardTask]:
    """Return tasks relevant to one day, excluding undated backlog noise."""
    kb.init_db(db_path)
    start, end = _day_bounds(target_date, timezone)
    target_iso = target_date.isoformat()
    selected: list[CardTask] = []
    with kb.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, title, status, planned_for, due_at, importance,
                   completed_at, canceled_at
              FROM tasks
             WHERE canceled_at IS NULL
            """
        ).fetchall()

    for row in rows:
        status = str(row["status"] or "")
        if status not in ACTIVE_TASK_STATUSES and status not in DONE_TASK_STATUSES:
            continue
        planned_for = row["planned_for"]
        due_at = int(row["due_at"]) if row["due_at"] is not None else None
        importance = int(row["importance"] or 0)
        today = planned_for == target_iso or (
            due_at is not None and start <= due_at < end
        )
        overdue = (
            status in ACTIVE_TASK_STATUSES
            and importance > 0
            and (
                (bool(planned_for) and str(planned_for) < target_iso)
                or (due_at is not None and due_at < start)
            )
        )
        if not today and not overdue:
            continue
        selected.append(
            CardTask(
                id=str(row["id"]),
                title=_safe_title(row["title"]),
                status=status,
                planned_for=str(planned_for) if planned_for else None,
                due_at=due_at,
                importance=importance,
                completed_at=(
                    int(row["completed_at"])
                    if row["completed_at"] is not None
                    else None
                ),
                overdue=overdue,
            )
        )

    selected.sort(
        key=lambda item: (
            0 if item.overdue else 1,
            1 if item.done else 0,
            -item.importance,
            item.due_at if item.due_at is not None else 2**62,
            item.title.casefold(),
        )
    )
    return selected


def _event_from_row(row: sqlite3.Row) -> CardEvent:
    try:
        preparation_raw = json.loads(row["preparation_json"] or "[]")
    except Exception:
        preparation_raw = []
    preparation = tuple(
        _safe_title(item, 240)
        for item in preparation_raw
        if _safe_title(item, 240)
    )
    return CardEvent(
        id=str(row["id"]),
        title=_safe_title(row["title"]),
        event_at=int(row["event_at"]),
        timezone=str(row["timezone"] or "Europe/Moscow"),
        person=_safe_title(row["person"], 100),
        address=_safe_title(row["address"], 300),
        location=_safe_title(row["location"], 300),
        online_url=str(row["online_url"] or "")[:1000],
        requires_travel=bool(row["requires_travel"]),
        preparation=preparation,
        importance=int(row["importance"] or 0),
        status=str(row["status"] or "active"),
        source_kind=str(row["source_kind"] or "manual"),
        source_id=str(row["source_id"] or ""),
        all_day=bool(row["all_day"]) if "all_day" in row.keys() else False,
        travel_mode=(
            str(row["travel_mode"] or "unknown")
            if "travel_mode" in row.keys()
            else "unknown"
        ),
        route_minutes=(
            int(row["route_minutes"])
            if "route_minutes" in row.keys() and row["route_minutes"] is not None
            else None
        ),
        route_buffer_minutes=(
            int(row["route_buffer_minutes"] or 15)
            if "route_buffer_minutes" in row.keys()
            else 15
        ),
    )


def select_events(
    conn: sqlite3.Connection,
    target_date: dt.date,
    timezone: ZoneInfo,
) -> list[CardEvent]:
    start, end = _day_bounds(target_date, timezone)
    rows = conn.execute(
        """
        SELECT * FROM daily_events
         WHERE status = 'active' AND event_at >= ? AND event_at < ?
         ORDER BY event_at, title
        """,
        (start, end),
    ).fetchall()
    return [_event_from_row(row) for row in rows]


def upsert_event(
    conn: sqlite3.Connection,
    *,
    title: str,
    event_at: int,
    timezone: str = "Europe/Moscow",
    person: str = "",
    address: str = "",
    location: str = "",
    online_url: str = "",
    requires_travel: bool = False,
    preparation: Sequence[str] = (),
    importance: int = 0,
    source_kind: str = "manual",
    source_id: str = "",
    event_id: str = "",
    all_day: bool = False,
    travel_mode: str = "unknown",
    route_minutes: Optional[int] = None,
    route_buffer_minutes: int = 15,
) -> str:
    title = _safe_title(title)
    if not title:
        raise ValueError("event title is required")
    if not event_id:
        material = "|".join(
            [source_kind, source_id, title.casefold(), str(event_at), person.casefold()]
        )
        event_id = "evt-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    now = int(time.time())
    payload = json.dumps(
        [_safe_title(item, 240) for item in preparation if _safe_title(item, 240)],
        ensure_ascii=False,
    )
    conn.execute(
        """
        INSERT INTO daily_events (
            id, title, event_at, timezone, person, address, location,
            online_url, requires_travel, preparation_json, importance,
            status, source_kind, source_id, all_day, travel_mode,
            route_minutes, route_buffer_minutes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title,
            event_at=excluded.event_at,
            timezone=excluded.timezone,
            person=excluded.person,
            address=excluded.address,
            location=excluded.location,
            online_url=excluded.online_url,
            requires_travel=excluded.requires_travel,
            preparation_json=excluded.preparation_json,
            importance=excluded.importance,
            status='active',
            source_kind=excluded.source_kind,
            source_id=excluded.source_id,
            all_day=excluded.all_day,
            travel_mode=excluded.travel_mode,
            route_minutes=excluded.route_minutes,
            route_buffer_minutes=excluded.route_buffer_minutes,
            updated_at=excluded.updated_at
        """,
        (
            event_id,
            title,
            int(event_at),
            timezone,
            _safe_title(person, 100),
            _safe_title(address, 300),
            _safe_title(location, 300),
            str(online_url or "")[:1000],
            1 if requires_travel else 0,
            payload,
            int(importance),
            source_kind,
            source_id,
            1 if all_day else 0,
            str(travel_mode or "unknown"),
            int(route_minutes) if route_minutes is not None else None,
            max(0, int(route_buffer_minutes)),
            now,
            now,
        ),
    )
    conn.commit()
    return event_id


def event_payload_path() -> Path:
    return get_hermes_home() / "tmp" / "daily-event.json"


def task_payload_path() -> Path:
    return get_hermes_home() / "tmp" / "daily-task.json"


def import_task_from_file(
    path: Optional[Path] = None,
    *,
    db_path: Optional[Path] = None,
) -> str:
    """Validate a fixed JSON staging file and create/update one planned task."""
    from hermes_cli.daily_task_payload import load_task_payload

    payload_path = path or task_payload_path()
    data = load_task_payload(payload_path)
    timezone = ZoneInfo(str(data.get("timezone") or "Europe/Moscow"))
    due_at_raw = str(data.get("due_at") or "")
    due_at = _parse_event_at(due_at_raw, str(timezone.key)) if due_at_raw else None
    planned_for = str(data.get("planned_for") or "")
    if planned_for:
        planned_for = dt.date.fromisoformat(planned_for).isoformat()
    elif due_at is not None:
        planned_for = (
            dt.datetime.fromtimestamp(due_at, tz=dt.timezone.utc)
            .astimezone(timezone)
            .date()
            .isoformat()
        )

    title = str(data["title"])
    importance = int(data.get("importance") or 0)
    key = str(data.get("idempotency_key") or "").strip()
    if not key:
        material = f"{title.casefold()}|{planned_for}|{due_at or ''}"
        key = "daily-task:" + hashlib.sha256(material.encode("utf-8")).hexdigest()

    target_db = db_path or kb.kanban_db_path(board="default")
    kb.init_db(target_db)
    with kb.connect(target_db) as conn:
        task_id = kb.create_task(
            conn,
            title=title,
            body=str(data.get("body") or ""),
            assignee=str(data.get("assignee") or "family"),
            created_by="daily_card",
            priority=importance,
            idempotency_key=key,
            initial_status="blocked",
        )
        conn.execute(
            """
            UPDATE tasks
               SET status='ready', planned_for=?, due_at=?, importance=?,
                   canceled_at=NULL, block_kind=NULL, block_recurrences=0
             WHERE id=?
            """,
            (planned_for, due_at, importance, task_id),
        )
        existing_event = conn.execute(
            """
            SELECT 1 FROM task_events
             WHERE task_id=? AND kind='daily_card_planned'
             LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if existing_event is None:
            conn.execute(
                """
                INSERT INTO task_events(task_id, run_id, kind, payload, created_at)
                VALUES (?, NULL, 'daily_card_planned', ?, ?)
                """,
                (
                    task_id,
                    json.dumps(
                        {"planned_for": planned_for, "due_at": due_at},
                        ensure_ascii=False,
                    ),
                    int(time.time()),
                ),
            )
        conn.commit()
    payload_path.unlink(missing_ok=True)
    return task_id


def upsert_event_from_file(
    path: Optional[Path] = None,
    *,
    state_path: Optional[Path] = None,
) -> str:
    """Validate the fixed JSON staging file and upsert one event."""
    from hermes_cli.daily_card_payload import load_event_payload

    payload_path = path or event_payload_path()
    data = load_event_payload(payload_path)
    timezone = str(data.get("timezone") or "Europe/Moscow")
    conn = connect_state(state_path)
    try:
        event_id = upsert_event(
            conn,
            title=str(data["title"]),
            event_at=_parse_event_at(str(data["event_at"]), timezone),
            timezone=timezone,
            person=str(data.get("person") or ""),
            address=str(data.get("address") or ""),
            location=str(data.get("location") or ""),
            online_url=str(data.get("online_url") or ""),
            requires_travel=bool(data.get("requires_travel")),
            preparation=data.get("preparation") or (),
            importance=int(data.get("importance") or 0),
            source_kind=str(data.get("source_kind") or "manual"),
            source_id=str(data.get("source_id") or ""),
            event_id=str(data.get("event_id") or ""),
            all_day=bool(data.get("all_day")),
            travel_mode=str(data.get("travel_mode") or "unknown"),
            route_minutes=(
                int(data["route_minutes"])
                if data.get("route_minutes") is not None
                else None
            ),
            route_buffer_minutes=int(data.get("route_buffer_minutes") or 15),
        )
    finally:
        conn.close()
    payload_path.unlink(missing_ok=True)
    return event_id


def sync_google_calendar_events(
    conn: sqlite3.Connection,
    target_dates: Sequence[dt.date],
    timezone: ZoneInfo,
    *,
    min_interval_seconds: int = 0,
) -> dict:
    """Refresh cached Google Calendar events for the requested days."""
    from hermes_cli.daily_card_calendar import fetch_google_calendar_payloads

    window_key = ",".join(sorted(day.isoformat() for day in target_dates))
    if min_interval_seconds > 0:
        row = conn.execute(
            "SELECT synced_at, item_count FROM source_sync WHERE source=? AND window_key=?",
            ("google_calendar", window_key),
        ).fetchone()
        if row and int(time.time()) - int(row["synced_at"]) < min_interval_seconds:
            return {
                "available": True,
                "count": int(row["item_count"] or 0),
                "error": "",
                "cached": True,
            }

    try:
        payloads = fetch_google_calendar_payloads(target_dates, timezone)
    except Exception as exc:
        logger.warning("Google Calendar sync skipped: %s", exc)
        return {"available": False, "count": 0, "error": type(exc).__name__}

    for target_date in target_dates:
        start, end = _day_bounds(target_date, timezone)
        conn.execute(
            """
            UPDATE daily_events SET status='stale', updated_at=?
             WHERE source_kind='google_calendar'
               AND event_at>=? AND event_at<?
            """,
            (int(time.time()), start, end),
        )
    conn.commit()
    for payload in payloads:
        upsert_event(conn, **payload)
    conn.execute(
        """
        INSERT INTO source_sync(source, window_key, synced_at, item_count)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source, window_key) DO UPDATE SET
            synced_at=excluded.synced_at,
            item_count=excluded.item_count
        """,
        ("google_calendar", window_key, int(time.time()), len(payloads)),
    )
    conn.commit()
    return {"available": True, "count": len(payloads), "error": "", "cached": False}


def _date_label(value: dt.date) -> str:
    return f"{value.day} {_RU_MONTHS[value.month]}"


def _event_local_time(event: CardEvent) -> dt.datetime:
    try:
        timezone = ZoneInfo(event.timezone)
    except Exception:
        timezone = ZoneInfo("Europe/Moscow")
    return dt.datetime.fromtimestamp(event.event_at, tz=dt.timezone.utc).astimezone(timezone)


def _event_line(event: CardEvent) -> str:
    if event.all_day:
        return f"Весь день  {event.title}"
    local = _event_local_time(event)
    return f"{local:%H:%M}  {event.title}"


def _action_lead_minutes(event: CardEvent) -> Optional[int]:
    if event.all_day:
        return None
    if event.online_url:
        return 10
    if event.requires_travel:
        if event.route_minutes is not None:
            return max(5, event.route_minutes + max(0, event.route_buffer_minutes))
        return 60
    if event.importance >= 2:
        return 60
    return None


def build_action_reminder(event: CardEvent, now_epoch: int) -> Optional[str]:
    """Render an action signal once its useful trigger time has arrived."""
    lead = _action_lead_minutes(event)
    if lead is None:
        return None
    seconds_until = event.event_at - int(now_epoch)
    if seconds_until <= 0 or seconds_until > lead * 60:
        return None

    if event.online_url:
        header = "Через 10 минут"
    elif event.route_minutes is None:
        header = "Через час"
    elif event.travel_mode == "drive":
        header = "Пора выезжать"
    else:
        header = "Пора выходить"

    lines = [header, "", _event_line(event)]
    if event.address:
        lines.append(f"Адрес: {event.address}")
    if event.route_minutes is not None:
        mode_labels = {
            "walk": "Пешком",
            "drive": "На машине",
            "transit": "На общественном транспорте",
        }
        label = mode_labels.get(event.travel_mode, "Дорога")
        lines.append(f"{label} около {event.route_minutes} минут")
        if event.route_buffer_minutes:
            lines.append(f"Запас: {event.route_buffer_minutes} минут")
    if event.online_url:
        lines.append(f"Ссылка: {event.online_url}")
    return "\n".join(lines)


def _delivery_key(event: CardEvent) -> str:
    local = _event_local_time(event)
    return f"{local:%Y%m%dT%H%M}"


def claim_delivery(
    conn: sqlite3.Connection,
    item_id: str,
    purpose: str,
    window_key: str,
) -> bool:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO delivery_log(
            item_id, purpose, window_key, message_id, sent_at
        ) VALUES (?, ?, ?, 'pending', ?)
        """,
        (item_id, purpose, window_key, int(time.time())),
    )
    conn.commit()
    return cur.rowcount == 1


def complete_delivery(
    conn: sqlite3.Connection,
    item_id: str,
    purpose: str,
    window_key: str,
    message_id: str,
) -> None:
    conn.execute(
        """
        UPDATE delivery_log SET message_id=?, sent_at=?
         WHERE item_id=? AND purpose=? AND window_key=?
        """,
        (str(message_id), int(time.time()), item_id, purpose, window_key),
    )
    conn.commit()


def release_delivery(
    conn: sqlite3.Connection,
    item_id: str,
    purpose: str,
    window_key: str,
) -> None:
    conn.execute(
        "DELETE FROM delivery_log WHERE item_id=? AND purpose=? AND window_key=?",
        (item_id, purpose, window_key),
    )
    conn.commit()


def render_morning(
    target_date: dt.date,
    tasks: Sequence[CardTask],
    events: Sequence[CardEvent],
    *,
    max_buttons: int,
) -> CardRender:
    lines = [f"Сегодня, {_date_label(target_date)}"]
    if events:
        lines.extend(["", "По времени"])
        for event in events:
            lines.append(_event_line(event))
            if event.address:
                lines.append(f"  Адрес: {event.address}")
            elif event.online_url:
                lines.append("  Онлайн")

    overdue = [task for task in tasks if task.overdue]
    current = [task for task in tasks if not task.overdue]
    if overdue:
        lines.extend(["", "Со вчера"])
        for task in overdue:
            marker = "✅" if task.done else "☐"
            lines.append(f"{marker} {task.title}")
    if current:
        lines.extend(["", "Сделать"])
        for task in current:
            marker = "✅" if task.done else "☐"
            lines.append(f"{marker} {task.title}")

    if not events and not tasks:
        lines.extend(["", "Планов на сегодня нет."])

    if tasks:
        completed = sum(task.done for task in tasks)
        lines.extend(["", f"Отмечено: {completed} из {len(tasks)}"])

    open_tasks = [task for task in tasks if not task.done]
    buttons = tuple(
        CardButton(label=f"✓ {task.title}"[:48], task_id=task.id)
        for task in open_tasks[:max_buttons]
    )
    return CardRender(text="\n".join(lines).strip(), buttons=buttons)


def event_needs_evening_attention(event: CardEvent) -> bool:
    local = _event_local_time(event)
    return bool(
        event.preparation
        or event.requires_travel
        or event.importance >= 2
        or local.hour < 10
    )


def render_evening(
    target_date: dt.date,
    today_tasks: Sequence[CardTask],
    tomorrow_events: Sequence[CardEvent],
    *,
    max_buttons: int,
) -> Optional[CardRender]:
    open_tasks = [task for task in today_tasks if not task.done]
    useful_events = [event for event in tomorrow_events if event_needs_evening_attention(event)]
    if not open_tasks and not useful_events:
        return None

    lines = [f"Вечер, {_date_label(target_date)}"]
    if today_tasks:
        completed = sum(task.done for task in today_tasks)
        lines.extend(["", "Сегодня", f"✅ Отмечено {completed} из {len(today_tasks)} дел"])
    if open_tasks:
        lines.extend(["", "Не отмечено"])
        lines.extend(f"• {task.title}" for task in open_tasks)
    if useful_events:
        lines.extend(["", "На завтра"])
        for event in useful_events:
            lines.append(_event_line(event))
            if event.address:
                lines.append(f"  Адрес: {event.address}")
            for item in event.preparation:
                lines.append(f"  • {item}")
            if event.requires_travel and not event.address:
                lines.append("  • Проверить адрес и дорогу")

    buttons = tuple(
        CardButton(label=f"✓ {task.title}"[:48], task_id=task.id)
        for task in open_tasks[:max_buttons]
    )
    return CardRender(text="\n".join(lines).strip(), buttons=buttons)


def save_card_state(
    conn: sqlite3.Connection,
    card_date: str,
    card_kind: str,
    platform: str,
    chat_id: str,
    message_id: str,
    pinned: bool,
    text_hash: str,
) -> None:
    if card_kind not in CARD_KINDS:
        raise ValueError(f"unsupported card kind: {card_kind}")
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO daily_cards (
            card_date, card_kind, platform, chat_id, message_id,
            pinned, text_hash, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(card_date, card_kind, platform, chat_id) DO UPDATE SET
            message_id=excluded.message_id,
            pinned=excluded.pinned,
            text_hash=excluded.text_hash,
            updated_at=excluded.updated_at
        """,
        (
            card_date,
            card_kind,
            platform,
            str(chat_id),
            str(message_id),
            1 if pinned else 0,
            text_hash,
            now,
            now,
        ),
    )
    conn.commit()


def get_card_state(
    conn: sqlite3.Connection,
    card_date: str,
    card_kind: str,
    platform: str,
    chat_id: str,
) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM daily_cards
         WHERE card_date=? AND card_kind=? AND platform=? AND chat_id=?
        """,
        (card_date, card_kind, platform, str(chat_id)),
    ).fetchone()


def register_task_token(
    conn: sqlite3.Connection,
    task_id: str,
    card_date: dt.date,
    card_kind: str,
) -> str:
    material = f"{task_id}|{card_date.isoformat()}|{card_kind}"
    token = hashlib.sha256(material.encode("utf-8")).hexdigest()[:14]
    conn.execute(
        """
        INSERT INTO daily_task_tokens(token, task_id, card_date, card_kind, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(token) DO UPDATE SET
            task_id=excluded.task_id,
            card_date=excluded.card_date,
            card_kind=excluded.card_kind,
            created_at=excluded.created_at
        """,
        (token, task_id, card_date.isoformat(), card_kind, int(time.time())),
    )
    conn.commit()
    return token


def build_markup(
    conn: sqlite3.Connection,
    render: CardRender,
    card_date: dt.date,
    card_kind: str,
) -> list[list[ButtonSpec]]:
    kind_code = "m" if card_kind == "morning" else "e"
    result: list[list[ButtonSpec]] = []
    for button in render.buttons:
        token = register_task_token(conn, button.task_id, card_date, card_kind)
        result.append(
            [
                ButtonSpec(
                    label=button.label,
                    callback_data=f"dc:d:{kind_code}:{card_date:%Y%m%d}:{token}",
                )
            ]
        )
    return result


def resolve_callback(conn: sqlite3.Connection, data: str) -> CallbackTarget:
    parts = data.split(":")
    if len(parts) != 5 or parts[:2] != ["dc", "d"]:
        raise ValueError("invalid daily-card callback")
    kind_code, date_raw, token = parts[2], parts[3], parts[4]
    card_kind = {"m": "morning", "e": "evening"}.get(kind_code)
    if not card_kind:
        raise ValueError("invalid daily-card kind")
    card_date = dt.datetime.strptime(date_raw, "%Y%m%d").date()
    row = conn.execute(
        """
        SELECT task_id, card_date, card_kind FROM daily_task_tokens
         WHERE token=?
        """,
        (token,),
    ).fetchone()
    if row is None:
        raise ValueError("daily-card button expired")
    if row["card_date"] != card_date.isoformat() or row["card_kind"] != card_kind:
        raise ValueError("daily-card button mismatch")
    return CallbackTarget(str(row["task_id"]), card_date, card_kind)


def mark_task_done(db_path: Path, task_id: str) -> str:
    kb.init_db(db_path)
    with kb.connect(db_path) as conn:
        row = conn.execute(
            "SELECT status FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if row is None:
            return "not_found"
        status = str(row["status"] or "")
        if status in DONE_TASK_STATUSES:
            return "already_done"
        if status not in ACTIVE_TASK_STATUSES:
            return "invalid_state"
        now = int(time.time())
        with kb.write_txn(conn):
            cur = conn.execute(
                """
                UPDATE tasks
                   SET status='done', completed_at=?, claim_lock=NULL,
                       claim_expires=NULL, worker_pid=NULL, block_kind=NULL,
                       block_recurrences=0
                 WHERE id=? AND status IN ('triage','todo','ready','blocked','scheduled')
                """,
                (now, task_id),
            )
            if cur.rowcount != 1:
                return "invalid_state"
            conn.execute(
                """
                INSERT INTO task_events(task_id, run_id, kind, payload, created_at)
                VALUES (?, NULL, 'daily_card_completed', ?, ?)
                """,
                (task_id, json.dumps({"source": "telegram_daily_card"}), now),
            )
    return "completed"


def _telegram_markup(rows: Sequence[Sequence[ButtonSpec]]):
    if not rows:
        return None
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(item.label, callback_data=item.callback_data) for item in row]
            for row in rows
        ]
    )


def _build_render(
    kind: str,
    target_date: dt.date,
    *,
    db_path: Path,
    state_conn: sqlite3.Connection,
    settings: DailyCardSettings,
) -> Optional[CardRender]:
    timezone = ZoneInfo(settings.timezone)
    tasks = select_tasks(db_path, target_date, timezone)
    if kind == "morning":
        events = select_events(state_conn, target_date, timezone)
        return render_morning(
            target_date,
            tasks,
            events,
            max_buttons=settings.max_buttons,
        )
    if kind == "evening":
        tomorrow = target_date + dt.timedelta(days=1)
        events = select_events(state_conn, tomorrow, timezone)
        return render_evening(
            target_date,
            tasks,
            events,
            max_buttons=settings.max_buttons,
        )
    raise ValueError(f"unsupported card kind: {kind}")


def _shadow_path(kind: str, target_date: dt.date) -> Path:
    directory = get_hermes_home() / "logs" / "daily-card-shadow"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{target_date.isoformat()}-{kind}.txt"


def _telegram_destination() -> tuple[str, str, Optional[str]]:
    from gateway.config import Platform, load_gateway_config

    config = load_gateway_config()
    platform_config = config.platforms.get(Platform.TELEGRAM)
    home = config.get_home_channel(Platform.TELEGRAM)
    if platform_config is None or not platform_config.enabled or not platform_config.token:
        raise RuntimeError("Telegram platform is not configured")
    if home is None:
        raise RuntimeError("Telegram home channel is not configured")
    return str(platform_config.token), str(home.chat_id), home.thread_id


async def send_or_update_card(
    kind: str,
    target_date: dt.date,
    *,
    db_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
    settings: Optional[DailyCardSettings] = None,
    force_send: bool = False,
    bot=None,
    chat_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> dict:
    settings = settings or load_settings()
    db_path = db_path or kb.kanban_db_path(board="default")
    state_conn = connect_state(state_path)
    if settings.google_calendar:
        sync_dates = [target_date]
        if kind == "evening":
            sync_dates.append(target_date + dt.timedelta(days=1))
        sync_google_calendar_events(
            state_conn,
            sync_dates,
            ZoneInfo(settings.timezone),
        )
    render = _build_render(
        kind,
        target_date,
        db_path=db_path,
        state_conn=state_conn,
        settings=settings,
    )
    if render is None:
        state_conn.close()
        return {"status": "no_content", "sent": False, "kind": kind}

    if (not settings.enabled or settings.shadow_mode) and not force_send:
        path = _shadow_path(kind, target_date)
        path.write_text(render.text + "\n", encoding="utf-8")
        state_conn.close()
        return {
            "status": "shadow",
            "sent": False,
            "kind": kind,
            "path": str(path),
        }

    owns_bot = bot is None
    try:
        if owns_bot:
            token, resolved_chat_id, resolved_thread_id = _telegram_destination()
            chat_id = chat_id or resolved_chat_id
            thread_id = thread_id if thread_id is not None else resolved_thread_id
            from telegram import Bot

            bot = Bot(token=token)
            await bot.initialize()
        elif chat_id is None:
            raise ValueError("chat_id is required when a bot is injected")
    except Exception:
        state_conn.close()
        raise

    try:
        markup_rows = build_markup(state_conn, render, target_date, kind)
        reply_markup = _telegram_markup(markup_rows)
        text_hash = hashlib.sha256(render.text.encode("utf-8")).hexdigest()
        current = get_card_state(
            state_conn,
            target_date.isoformat(),
            kind,
            "telegram",
            str(chat_id),
        )
        message_id: Optional[str] = None
        if current is not None:
            try:
                await bot.edit_message_text(
                    chat_id=int(chat_id),
                    message_id=int(current["message_id"]),
                    text=render.text,
                    parse_mode=None,
                    reply_markup=reply_markup,
                )
                message_id = str(current["message_id"])
            except Exception as exc:
                if "message is not modified" in str(exc).lower():
                    message_id = str(current["message_id"])
                else:
                    logger.warning("Daily card edit failed; creating replacement: %s", exc)

        if message_id is None:
            kwargs = {}
            if thread_id:
                kwargs["message_thread_id"] = int(thread_id)
            message = await bot.send_message(
                chat_id=int(chat_id),
                text=render.text,
                parse_mode=None,
                reply_markup=reply_markup,
                **kwargs,
            )
            message_id = str(message.message_id)

        pinned = False
        if kind == "morning":
            old_rows = state_conn.execute(
                """
                SELECT message_id FROM daily_cards
                 WHERE platform='telegram' AND chat_id=? AND card_kind='morning'
                   AND pinned=1 AND card_date<>?
                """,
                (str(chat_id), target_date.isoformat()),
            ).fetchall()
            for row in old_rows:
                try:
                    await bot.unpin_chat_message(
                        chat_id=int(chat_id), message_id=int(row["message_id"])
                    )
                except Exception:
                    pass
            try:
                await bot.pin_chat_message(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    disable_notification=True,
                )
                pinned = True
            except Exception as exc:
                logger.warning("Could not pin daily card: %s", exc)
            state_conn.execute(
                "UPDATE daily_cards SET pinned=0 WHERE platform='telegram' AND chat_id=? AND card_kind='morning'",
                (str(chat_id),),
            )
            state_conn.commit()

        save_card_state(
            state_conn,
            target_date.isoformat(),
            kind,
            "telegram",
            str(chat_id),
            message_id,
            pinned,
            text_hash,
        )
        return {
            "status": "sent",
            "sent": True,
            "kind": kind,
            "message_id": message_id,
            "pinned": pinned,
        }
    finally:
        state_conn.close()
        if owns_bot:
            await bot.shutdown()


async def refresh_stored_card(
    bot,
    card_date: dt.date,
    card_kind: str,
    chat_id: str,
    *,
    db_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
    settings: Optional[DailyCardSettings] = None,
    message_id: Optional[str] = None,
) -> bool:
    settings = settings or load_settings()
    db_path = db_path or kb.kanban_db_path(board="default")
    conn = connect_state(state_path)
    try:
        render = _build_render(
            card_kind,
            card_date,
            db_path=db_path,
            state_conn=conn,
            settings=settings,
        )
        if render is None:
            return False
        row = get_card_state(
            conn, card_date.isoformat(), card_kind, "telegram", str(chat_id)
        )
        target_message_id = message_id or (str(row["message_id"]) if row else None)
        if target_message_id is None:
            return False
        markup = _telegram_markup(build_markup(conn, render, card_date, card_kind))
        try:
            await bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=int(target_message_id),
                text=render.text,
                parse_mode=None,
                reply_markup=markup,
            )
        except Exception as exc:
            if "message is not modified" not in str(exc).lower():
                raise
        save_card_state(
            conn,
            card_date.isoformat(),
            card_kind,
            "telegram",
            str(chat_id),
            str(target_message_id),
            bool(row["pinned"]) if row else card_kind == "morning",
            hashlib.sha256(render.text.encode("utf-8")).hexdigest(),
        )
        return True
    finally:
        conn.close()


async def send_action_reminders(
    *,
    settings: Optional[DailyCardSettings] = None,
    state_path: Optional[Path] = None,
    bot=None,
    chat_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    now_epoch: Optional[int] = None,
) -> dict:
    """Send due action signals once, with an atomic dedupe claim."""
    settings = settings or load_settings()
    now_epoch = int(now_epoch if now_epoch is not None else time.time())
    timezone = ZoneInfo(settings.timezone)
    now_local = dt.datetime.fromtimestamp(now_epoch, tz=dt.timezone.utc).astimezone(timezone)
    conn = connect_state(state_path)
    try:
        if settings.google_calendar:
            sync_google_calendar_events(
                conn,
                [now_local.date(), now_local.date() + dt.timedelta(days=1)],
                timezone,
                min_interval_seconds=1800,
            )
        rows = conn.execute(
            """
            SELECT * FROM daily_events
             WHERE status='active' AND event_at>? AND event_at<=?
             ORDER BY event_at, title
            """,
            (now_epoch, now_epoch + 2 * 60 * 60),
        ).fetchall()
        due: list[tuple[CardEvent, str, str]] = []
        for row in rows:
            event = _event_from_row(row)
            text = build_action_reminder(event, now_epoch)
            if text is None:
                continue
            key = _delivery_key(event)
            if claim_delivery(conn, event.id, "action", key):
                due.append((event, key, text))

        if not due:
            return {"status": "no_content", "sent": 0}

        if (not settings.enabled or settings.shadow_mode) and bot is None:
            path = _shadow_path("action", now_local.date())
            path.write_text(
                "\n\n---\n\n".join(text for _, _, text in due) + "\n",
                encoding="utf-8",
            )
            for event, key, _ in due:
                release_delivery(conn, event.id, "action", key)
            return {"status": "shadow", "sent": 0, "path": str(path)}

        owns_bot = bot is None
        if owns_bot:
            token, resolved_chat_id, resolved_thread_id = _telegram_destination()
            chat_id = chat_id or resolved_chat_id
            thread_id = thread_id if thread_id is not None else resolved_thread_id
            from telegram import Bot

            bot = Bot(token=token)
            await bot.initialize()
        elif chat_id is None:
            raise ValueError("chat_id is required when a bot is injected")

        sent = 0
        try:
            for event, key, text in due:
                try:
                    kwargs = {}
                    if thread_id:
                        kwargs["message_thread_id"] = int(thread_id)
                    message = await bot.send_message(
                        chat_id=int(chat_id),
                        text=text,
                        parse_mode=None,
                        **kwargs,
                    )
                    complete_delivery(
                        conn,
                        event.id,
                        "action",
                        key,
                        str(message.message_id),
                    )
                    sent += 1
                except Exception:
                    release_delivery(conn, event.id, "action", key)
                    raise
        finally:
            if owns_bot:
                await bot.shutdown()
        return {"status": "sent", "sent": sent}
    finally:
        conn.close()


def _parse_date(value: Optional[str], timezone: ZoneInfo) -> dt.date:
    if value:
        return dt.date.fromisoformat(value)
    return dt.datetime.now(timezone).date()


def _parse_event_at(value: str, timezone: str) -> int:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    return int(parsed.timestamp())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes Telegram daily card")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("morning", "evening"):
        item = sub.add_parser(name)
        item.add_argument("--date")
        item.add_argument("--force-send", action="store_true")
        item.add_argument("--cron", action="store_true")

    action = sub.add_parser("action")
    action.add_argument("--cron", action="store_true")

    sub.add_parser("task-import")
    sub.add_parser("event-import")

    event = sub.add_parser("event-add")
    event.add_argument("--title", required=True)
    event.add_argument("--event-at", required=True)
    event.add_argument("--timezone", default="Europe/Moscow")
    event.add_argument("--person", default="")
    event.add_argument("--address", default="")
    event.add_argument("--location", default="")
    event.add_argument("--online-url", default="")
    event.add_argument("--requires-travel", action="store_true")
    event.add_argument("--prepare", action="append", default=[])
    event.add_argument("--importance", type=int, default=0)
    event.add_argument("--source-kind", default="manual")
    event.add_argument("--source-id", default="")
    return parser


async def _run_async(args: argparse.Namespace) -> int:
    settings = load_settings()
    timezone = ZoneInfo(settings.timezone)
    if args.command in CARD_KINDS:
        result = await send_or_update_card(
            args.command,
            _parse_date(args.date, timezone),
            settings=settings,
            force_send=bool(args.force_send),
        )
        if args.cron and result.get("status") in {"sent", "shadow", "no_content"}:
            print("[SILENT]")
        else:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0

    if args.command == "action":
        result = await send_action_reminders(settings=settings)
        if args.cron and result.get("status") in {"sent", "shadow", "no_content"}:
            print("[SILENT]")
        else:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0

    if args.command == "task-import":
        task_id = import_task_from_file()
        print(json.dumps({"status": "saved", "task_id": task_id}, ensure_ascii=False))
        return 0

    if args.command == "event-import":
        event_id = upsert_event_from_file()
        print(json.dumps({"status": "saved", "event_id": event_id}, ensure_ascii=False))
        return 0

    if args.command == "event-add":
        conn = connect_state()
        try:
            event_id = upsert_event(
                conn,
                title=args.title,
                event_at=_parse_event_at(args.event_at, args.timezone),
                timezone=args.timezone,
                person=args.person,
                address=args.address,
                location=args.location,
                online_url=args.online_url,
                requires_travel=bool(args.requires_travel),
                preparation=args.prepare,
                importance=args.importance,
                source_kind=args.source_kind,
                source_id=args.source_id,
            )
        finally:
            conn.close()
        print(json.dumps({"status": "saved", "event_id": event_id}, ensure_ascii=False))
        return 0
    return 2


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(_run_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
