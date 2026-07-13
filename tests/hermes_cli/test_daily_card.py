from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from gateway import config as gateway_config
from hermes_cli import kanban_db as kb
from hermes_cli import daily_card as dc
from hermes_cli import daily_card_calendar as calendar_source


TZ = ZoneInfo("Europe/Moscow")
TARGET = dt.date(2026, 7, 7)


def _epoch(local_iso: str) -> int:
    value = dt.datetime.fromisoformat(local_iso).replace(tzinfo=TZ)
    return int(value.timestamp())


@pytest.fixture()
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "kanban.db"
    kb.init_db(path)
    return path


def _insert_task(
    path: Path,
    task_id: str,
    title: str,
    *,
    status: str = "ready",
    planned_for: str | None = None,
    due_at: int | None = None,
    importance: int = 0,
    completed_at: int | None = None,
    assignee: str | None = "family",
    created_by: str = "test",
    created_at: int | None = None,
) -> None:
    with kb.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO tasks (
                id, title, body, assignee, status, priority, created_by,
                created_at, completed_at, workspace_kind, consecutive_failures,
                goal_mode, block_recurrences, planned_for, due_at, importance,
                carry_count
            ) VALUES (?, ?, '', ?, ?, 0, ?, ?, ?, 'scratch', 0, 0, 0, ?, ?, ?, 0)
            """,
            (
                task_id,
                title,
                assignee,
                status,
                created_by,
                created_at or _epoch("2026-07-01T10:00:00"),
                completed_at,
                planned_for,
                due_at,
                importance,
            ),
        )
        conn.commit()


def test_kanban_migration_adds_daily_planning_columns(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    assert {
        "planned_for",
        "due_at",
        "importance",
        "carry_count",
        "risk_ack_at",
        "canceled_at",
    }.issubset(columns)


def test_selector_includes_today_important_overdue_and_personal_undated(
    db_path: Path,
) -> None:
    _insert_task(db_path, "today", "Сегодня", planned_for="2026-07-07")
    _insert_task(db_path, "due", "Срок сегодня", due_at=_epoch("2026-07-07T18:00:00"))
    _insert_task(db_path, "tomorrow", "Завтра", planned_for="2026-07-08")
    _insert_task(db_path, "undated", "Без даты")
    _insert_task(db_path, "old-low", "Старое обычное", planned_for="2026-07-06")
    _insert_task(
        db_path,
        "old-important",
        "Старое важное",
        planned_for="2026-07-06",
        importance=2,
    )

    selected = dc.select_tasks(db_path, TARGET, TZ)
    assert [item.id for item in selected] == [
        "old-important",
        "due",
        "today",
        "undated",
    ]
    assert selected[0].overdue is True
    assert selected[-1].undated is True


def test_selector_caps_undated_personal_tasks_and_excludes_technical(
    db_path: Path,
) -> None:
    for index in range(5):
        _insert_task(
            db_path,
            f"family-{index}",
            f"Бытовая задача {index}",
            created_at=_epoch(f"2026-07-0{index + 1}T10:00:00"),
        )
    _insert_task(
        db_path,
        "technical",
        "Техническая задача",
        assignee=None,
        created_by="system",
    )

    selected = dc.select_tasks(db_path, TARGET, TZ)

    assert [item.id for item in selected] == ["family-0", "family-1", "family-2"]
    assert all(item.undated for item in selected)


def test_completed_today_stays_visible_without_action_button(
    db_path: Path, tmp_path: Path
) -> None:
    _insert_task(
        db_path,
        "done",
        "Уже сделано",
        status="done",
        planned_for="2026-07-07",
        completed_at=_epoch("2026-07-07T12:00:00"),
    )
    _insert_task(db_path, "open", "Открыто", planned_for="2026-07-07")

    state = dc.connect_state(tmp_path / "daily_cards.db")
    render = dc.render_morning(
        TARGET,
        dc.select_tasks(db_path, TARGET, TZ),
        [],
        max_buttons=6,
    )
    markup = dc.build_markup(state, render, TARGET, "morning")

    assert "✅ Уже сделано" in render.text
    assert "☐ Открыто" in render.text
    assert len(markup) == 1
    assert markup[0][0].label == "✓ Открыто"


def test_events_never_receive_completion_buttons(tmp_path: Path) -> None:
    event = dc.CardEvent(
        id="evt-1",
        title="Федя — детский хирург",
        event_at=_epoch("2026-07-07T10:45:00"),
        timezone="Europe/Moscow",
        person="Федя",
        address="Детская поликлиника №5",
        requires_travel=True,
        preparation=(),
        importance=2,
    )
    render = dc.render_morning(TARGET, [], [event], max_buttons=6)
    state = dc.connect_state(tmp_path / "daily_cards.db")
    markup = dc.build_markup(state, render, TARGET, "morning")

    assert "<b>10:45</b> · Федя — детский хирург" in render.text
    assert render.parse_mode == "HTML"
    assert markup == []


def test_mobile_event_layout_highlights_time_and_separates_events() -> None:
    first = dc.CardEvent(
        id="evt-1",
        title="Федя <хирург> & врач",
        event_at=_epoch("2026-07-07T10:45:00"),
        timezone="Europe/Moscow",
        address="Поликлиника №2 & корпус <А>",
    )
    second = dc.CardEvent(
        id="evt-2",
        title="Встречи в Skyeng",
        event_at=_epoch("2026-07-07T17:30:00"),
        timezone="Europe/Moscow",
    )

    render = dc.render_morning(TARGET, [], [first, second], max_buttons=6)

    assert (
        "<b>10:45</b> · Федя &lt;хирург&gt; &amp; врач\n"
        "📍 Поликлиника №2 &amp; корпус &lt;А&gt;\n\n"
        "<b>17:30</b> · Встречи в Skyeng"
    ) in render.text


def test_pinned_card_shows_three_tasks_and_more_button(
    db_path: Path,
    tmp_path: Path,
) -> None:
    for index in range(7):
        _insert_task(
            db_path,
            f"task-{index}",
            f"Дело {index}",
            planned_for="2026-07-07",
            importance=10 - index,
        )
    tasks = dc.select_tasks(db_path, TARGET, TZ)
    all_active = dc.select_all_active_personal_tasks(db_path, TARGET, TZ)
    render = dc.render_morning(
        TARGET,
        tasks,
        [],
        max_buttons=6,
        all_active_tasks=all_active,
    )
    state = dc.connect_state(tmp_path / "daily_cards.db")
    markup = dc.build_markup(state, render, TARGET, "morning")

    assert render.text.count("☐ ") == 3
    assert render.hidden_tasks_count == 4
    assert len(markup) == 4
    assert markup[-1][0].label == "Ещё задачи · 4"
    assert markup[-1][0].callback_data == "dc:l:20260707"


def test_full_task_list_groups_all_active_personal_tasks(db_path: Path) -> None:
    _insert_task(
        db_path,
        "overdue",
        "Просроченная",
        planned_for="2026-07-06",
        importance=2,
    )
    _insert_task(db_path, "today", "Сегодня", planned_for="2026-07-07")
    _insert_task(db_path, "future", "Будущая", planned_for="2026-07-09")
    _insert_task(db_path, "undated", "Без даты")
    _insert_task(
        db_path,
        "technical",
        "Техническая",
        assignee=None,
        created_by="system",
    )

    render = dc.render_full_task_list_from_db(db_path, TARGET, TZ)

    assert "Просрочено\n☐ Просроченная" in render.text
    assert "Сегодня\n☐ Сегодня" in render.text
    assert "Запланировано\n☐ 9 июля · Будущая" in render.text
    assert "Без даты\n☐ Без даты" in render.text
    assert "Техническая" not in render.text
    assert render.parse_mode == "HTML"


def test_mark_done_is_idempotent_and_audited(db_path: Path) -> None:
    _insert_task(db_path, "open", "Открыто", planned_for="2026-07-07")

    first = dc.mark_task_done(db_path, "open")
    second = dc.mark_task_done(db_path, "open")

    assert first == "completed"
    assert second == "already_done"
    with kb.connect(db_path) as conn:
        row = conn.execute(
            "SELECT status, completed_at FROM tasks WHERE id='open'"
        ).fetchone()
        events = conn.execute(
            "SELECT kind FROM task_events WHERE task_id='open' ORDER BY id"
        ).fetchall()
    assert row["status"] == "done"
    assert row["completed_at"] is not None
    assert [event["kind"] for event in events] == ["daily_card_completed"]


def test_state_upsert_keeps_one_card_per_day_kind_and_chat(tmp_path: Path) -> None:
    conn = dc.connect_state(tmp_path / "daily_cards.db")
    dc.save_card_state(
        conn, "2026-07-07", "morning", "telegram", "123", "100", True, "a"
    )
    dc.save_card_state(
        conn, "2026-07-07", "morning", "telegram", "123", "101", True, "b"
    )
    rows = conn.execute("SELECT * FROM daily_cards").fetchall()
    assert len(rows) == 1
    assert rows[0]["message_id"] == "101"
    assert rows[0]["text_hash"] == "b"


def test_telegram_destination_falls_back_to_existing_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=test-token\nTELEGRAM_ALLOWED_USERS=123,456\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        gateway_config,
        "load_gateway_config",
        lambda: SimpleNamespace(
            platforms={},
            get_home_channel=lambda platform: None,
        ),
    )

    token, chat_id, thread_id = dc._telegram_destination()

    assert token == "test-token"
    assert chat_id == "123"
    assert thread_id is None


def test_evening_is_silent_without_open_tasks_or_useful_tomorrow_events(
    db_path: Path,
) -> None:
    render = dc.render_evening(TARGET, [], [], max_buttons=6)
    assert render is None


def test_evening_includes_travel_preparation_but_not_ordinary_tomorrow_task(
    db_path: Path,
) -> None:
    _insert_task(db_path, "ordinary", "Позвонить", planned_for="2026-07-08")
    useful = dc.CardEvent(
        id="evt-2",
        title="Рентген",
        event_at=_epoch("2026-07-08T08:00:00"),
        timezone="Europe/Moscow",
        person="Антон",
        address="Поликлиника",
        requires_travel=True,
        preparation=("Взять направление",),
        importance=2,
    )
    render = dc.render_evening(TARGET, [], [useful], max_buttons=6)
    assert render is not None
    assert "На завтра" in render.text
    assert "Рентген" in render.text
    assert "Взять направление" in render.text
    assert "Позвонить" not in render.text


def test_callback_token_roundtrip(tmp_path: Path) -> None:
    conn = dc.connect_state(tmp_path / "daily_cards.db")
    token = dc.register_task_token(conn, "task-1", TARGET, "morning")
    data = f"dc:d:m:20260707:{token}"
    parsed = dc.resolve_callback(conn, data)
    assert parsed.task_id == "task-1"
    assert parsed.card_date == TARGET
    assert parsed.card_kind == "morning"


def test_task_payload_file_creates_dated_task_without_duplicates(
    tmp_path: Path,
) -> None:
    payload_path = tmp_path / "daily-task.json"
    payload = {
        "title": "Позвонить в страховую",
        "planned_for": "2026-07-07",
        "importance": 1,
        "assignee": "family",
    }
    db_path = tmp_path / "kanban.db"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    first = dc.import_task_from_file(payload_path, db_path=db_path)
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    second = dc.import_task_from_file(payload_path, db_path=db_path)

    assert first == second
    assert not payload_path.exists()
    with kb.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, planned_for, importance FROM tasks WHERE title=?",
            ("Позвонить в страховую",),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["planned_for"] == "2026-07-07"
    assert rows[0]["importance"] == 1


def test_event_payload_file_is_validated_and_upserted(tmp_path: Path) -> None:
    payload_path = tmp_path / "daily-event.json"
    payload_path.write_text(
        json.dumps(
            {
                "title": "Федя — детский хирург",
                "event_at": "2026-07-07T10:45:00+03:00",
                "timezone": "Europe/Moscow",
                "person": "Федя",
                "address": "Детская поликлиника №5",
                "requires_travel": True,
                "preparation": ["Взять полис"],
                "importance": 2,
                "source_kind": "medical",
                "source_id": "doctor-20260707",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state_path = tmp_path / "daily_cards.db"

    event_id = dc.upsert_event_from_file(payload_path, state_path=state_path)

    assert not payload_path.exists()
    conn = dc.connect_state(state_path)
    rows = conn.execute("SELECT * FROM daily_events").fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == event_id
    assert rows[0]["person"] == "Федя"
    assert rows[0]["requires_travel"] == 1


def test_event_payload_rejects_unknown_fields(tmp_path: Path) -> None:
    payload_path = tmp_path / "daily-event.json"
    payload_path.write_text(
        json.dumps({
            "title": "Событие",
            "event_at": "2026-07-07T10:45:00+03:00",
            "unexpected": "no",
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsupported fields"):
        dc.upsert_event_from_file(
            payload_path,
            state_path=tmp_path / "daily_cards.db",
        )


def test_google_calendar_source_filters_dates_and_redacts_description(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "skills/productivity/google-workspace/scripts/google_api.py"
    script.parent.mkdir(parents=True)
    script.write_text("# fake", encoding="utf-8")
    python = tmp_path / "hermes-agent/venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")

    payload = [
        {
            "id": "doctor-1",
            "summary": "Федя — хирург",
            "start": "2026-07-07T10:45:00+03:00",
            "location": "Детская поликлиника",
            "description": "Полис: 123456789. Взять результаты анализов.",
            "status": "confirmed",
        },
        {
            "id": "other-day",
            "summary": "Завтра",
            "start": "2026-07-08T10:00:00+03:00",
            "status": "confirmed",
        },
        {
            "id": "cancelled",
            "summary": "Отменено",
            "start": "2026-07-07T12:00:00+03:00",
            "status": "cancelled",
        },
    ]
    monkeypatch.setattr(
        calendar_source.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload, ensure_ascii=False),
            stderr="",
        ),
    )

    events = calendar_source.fetch_google_calendar_payloads(
        [TARGET],
        TZ,
        hermes_home=tmp_path,
    )

    assert len(events) == 1
    assert events[0]["source_id"] == "doctor-1"
    assert events[0]["requires_travel"] is True
    assert events[0]["preparation"] == [
        "Взять полис",
        "Взять результаты обследований",
    ]
    assert "123456789" not in json.dumps(events[0], ensure_ascii=False)


def test_google_calendar_sync_applies_configured_title_alias(
    tmp_path: Path, monkeypatch
) -> None:
    conn = dc.connect_state(tmp_path / "daily_cards.db")
    monkeypatch.setattr(
        calendar_source,
        "fetch_google_calendar_payloads",
        lambda *args, **kwargs: [
            {
                "title": "Младший: Хирург",
                "event_at": _epoch("2026-07-07T10:45:00"),
                "timezone": "Europe/Moscow",
                "source_kind": "google_calendar",
                "source_id": "doctor",
                "event_id": "gcal-doctor",
            }
        ],
    )
    dc.sync_google_calendar_events(
        conn,
        [TARGET],
        TZ,
        title_aliases={"Младший:": "Федя —"},
    )
    row = conn.execute(
        "SELECT title FROM daily_events WHERE id='gcal-doctor'"
    ).fetchone()
    assert row["title"] == "Федя — Хирург"


def test_google_calendar_sync_keeps_past_today_and_stales_missing_future(
    tmp_path: Path,
    monkeypatch,
) -> None:
    conn = dc.connect_state(tmp_path / "daily_cards.db")
    dc.upsert_event(
        conn,
        title="Утренний врач",
        event_at=_epoch("2026-07-07T09:00:00"),
        source_kind="google_calendar",
        source_id="past",
        event_id="gcal-past",
    )
    dc.upsert_event(
        conn,
        title="Будущая встреча",
        event_at=_epoch("2026-07-07T15:00:00"),
        source_kind="google_calendar",
        source_id="future",
        event_id="gcal-future",
    )
    monkeypatch.setattr(
        calendar_source,
        "fetch_google_calendar_payloads",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        dc.time,
        "time",
        lambda: dt.datetime(2026, 7, 7, 12, 0, tzinfo=TZ).timestamp(),
    )

    result = dc.sync_google_calendar_events(conn, [TARGET], TZ)

    assert result["available"] is True
    statuses = {
        row["id"]: row["status"]
        for row in conn.execute(
            "SELECT id, status FROM daily_events WHERE id IN ('gcal-past','gcal-future')"
        )
    }
    assert statuses == {"gcal-past": "active", "gcal-future": "stale"}


def test_action_reminder_uses_route_when_known() -> None:
    event = dc.CardEvent(
        id="evt-drive",
        title="Федя — детский хирург",
        event_at=_epoch("2026-07-07T10:45:00"),
        timezone="Europe/Moscow",
        address="Детская поликлиника №5",
        requires_travel=True,
        travel_mode="drive",
        route_minutes=25,
        route_buffer_minutes=15,
    )
    now = dt.datetime(2026, 7, 7, 10, 5, tzinfo=TZ)
    reminder = dc.build_action_reminder(event, int(now.timestamp()))
    assert reminder is not None
    assert "Пора выезжать" in reminder
    assert "На машине около 25 минут" in reminder
    assert "Адрес: Детская поликлиника №5" in reminder


def test_action_reminder_falls_back_to_one_hour_without_route() -> None:
    event = dc.CardEvent(
        id="evt-unknown",
        title="Федя — детский хирург",
        event_at=_epoch("2026-07-07T10:45:00"),
        timezone="Europe/Moscow",
        address="Детская поликлиника №5",
        requires_travel=True,
    )
    now = dt.datetime(2026, 7, 7, 9, 45, tzinfo=TZ)
    reminder = dc.build_action_reminder(event, int(now.timestamp()))
    assert reminder is not None
    assert reminder.startswith("Через час")
    assert "Пешком" not in reminder
    assert "На машине" not in reminder


def test_action_delivery_claim_is_idempotent(tmp_path: Path) -> None:
    conn = dc.connect_state(tmp_path / "daily_cards.db")
    assert dc.claim_delivery(conn, "evt-1", "action", "20260707T1045") is True
    assert dc.claim_delivery(conn, "evt-1", "action", "20260707T1045") is False
    dc.release_delivery(conn, "evt-1", "action", "20260707T1045")
    assert dc.claim_delivery(conn, "evt-1", "action", "20260707T1045") is True


@pytest.mark.asyncio
async def test_morning_send_is_silent_when_requested_and_pinned(tmp_path: Path) -> None:
    class Bot:
        def __init__(self):
            self.sent = []
            self.pinned = []

        async def send_message(self, **kwargs):
            self.sent.append(kwargs)
            return SimpleNamespace(message_id=321)

        async def pin_chat_message(self, **kwargs):
            self.pinned.append(kwargs)

        async def unpin_chat_message(self, **kwargs):
            return None

        async def edit_message_text(self, **kwargs):
            return None

    db_path = tmp_path / "kanban.db"
    kb.init_db(db_path)
    state_path = tmp_path / "daily_cards.db"
    bot = Bot()
    settings = dc.DailyCardSettings(
        enabled=True,
        shadow_mode=False,
        timezone="Europe/Moscow",
        google_calendar=False,
    )

    result = await dc.send_or_update_card(
        "morning",
        TARGET,
        db_path=db_path,
        state_path=state_path,
        settings=settings,
        force_send=True,
        silent=True,
        bot=bot,
        chat_id="123",
    )

    assert result["sent"] is True
    assert result["pinned"] is True
    assert bot.sent[0]["disable_notification"] is True
    assert bot.sent[0]["parse_mode"] == "HTML"
    assert bot.pinned[0]["disable_notification"] is True


@pytest.mark.asyncio
async def test_action_watcher_sends_once(tmp_path: Path) -> None:
    class Bot:
        def __init__(self):
            self.calls = []

        async def send_message(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(message_id=500 + len(self.calls))

    state_path = tmp_path / "daily_cards.db"
    conn = dc.connect_state(state_path)
    dc.upsert_event(
        conn,
        title="Федя — детский хирург",
        event_at=_epoch("2026-07-07T10:45:00"),
        timezone="Europe/Moscow",
        address="Детская поликлиника №5",
        requires_travel=True,
        source_kind="test",
        source_id="doctor",
    )
    conn.close()
    bot = Bot()
    now = int(dt.datetime(2026, 7, 7, 9, 45, tzinfo=TZ).timestamp())
    settings = dc.DailyCardSettings(
        enabled=True,
        shadow_mode=False,
        timezone="Europe/Moscow",
        google_calendar=False,
    )

    first = await dc.send_action_reminders(
        settings=settings,
        state_path=state_path,
        bot=bot,
        chat_id="123",
        now_epoch=now,
    )
    second = await dc.send_action_reminders(
        settings=settings,
        state_path=state_path,
        bot=bot,
        chat_id="123",
        now_epoch=now,
    )

    assert first["sent"] == 1
    assert second["sent"] == 0
    assert len(bot.calls) == 1
    assert bot.calls[0]["text"].startswith("Через час")


def test_google_calendar_source_prefers_shared_runtime_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / ".hermes"
    script = home / "skills/productivity/google-workspace/scripts/google_api.py"
    script.parent.mkdir(parents=True)
    script.write_text("# fake", encoding="utf-8")
    runtime_python = tmp_path / "hermes-runtime/shared/venv/bin/python"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_text("", encoding="utf-8")
    called: dict[str, object] = {}

    def fake_run(args, **kwargs):
        called["args"] = args
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(calendar_source.subprocess, "run", fake_run)
    calendar_source.fetch_google_calendar_payloads([TARGET], TZ, hermes_home=home)

    assert Path(called["args"][0]) == runtime_python


def test_google_calendar_source_marks_zoom_as_online(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "skills/productivity/google-workspace/scripts/google_api.py"
    script.parent.mkdir(parents=True)
    script.write_text("# fake", encoding="utf-8")
    python = tmp_path / "hermes-agent/venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    payload = [
        {
            "id": "interview-online",
            "summary": "Собеседование",
            "start": "2026-07-07T11:00:00+03:00",
            "location": "Zoom",
            "htmlLink": "https://calendar.example/event",
            "status": "confirmed",
        }
    ]
    monkeypatch.setattr(
        calendar_source.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload, ensure_ascii=False),
            stderr="",
        ),
    )

    events = calendar_source.fetch_google_calendar_payloads(
        [TARGET],
        TZ,
        hermes_home=tmp_path,
    )

    assert len(events) == 1
    assert events[0]["requires_travel"] is False
    assert events[0]["address"] == ""
    assert events[0]["online_url"] == "https://calendar.example/event"
