import sqlite3

import pytest

from gateway.quick_task_capture import capture_quick_task, detect_quick_task
from gateway.task_runtime import prepare_task_turn


def _common(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(home / "kanban.db"))
    return {
        "platform_key": "telegram",
        "chat_id": "42",
        "session_key": "telegram:42",
        "session_id": "session-1",
        "user_config": {"agent": {}},
        "platform_toolsets": ["file", "skills", "terminal"],
    }


def test_detect_quick_task_is_narrow_and_normalizes_title():
    assert detect_quick_task(
        "Запиши задачу сделать пост на LinkedIn с объявлением о наборе айтишников."
    ) == "Сделать пост на LinkedIn с объявлением о наборе айтишников"
    assert detect_quick_task("Обсудим задачу сделать пост") is None
    assert detect_quick_task("Не записывай задачу сделать пост") is None


@pytest.mark.parametrize(
    "text",
    [
        "Запиши задачу проверить договор",
        "Создай задачу проверить договор",
        "Добавь задачу проверить договор",
        "Зафиксируй задачу проверить договор",
        "Поставь задачу проверить договор",
        "Пожалуйста, запиши задачу проверить договор",
        "Запиши мне задачу проверить договор",
        "Создай мне задачу проверить договор",
        "Добавь задачку проверить договор",
        "ЗАПИШИ ЗАДАЧУ проверить договор",
        "Запиши задачу: проверить договор",
        "Запиши задачу — проверить договор",
        "Запиши задачу проверить договор.",
        "Поставь мне задачу позвонить завтра",
        "Зафиксируй задачку обновить резюме",
    ],
)
def test_trigger_scenarios(text):
    assert detect_quick_task(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Обсудим задачу проверить договор",
        "Не записывай задачу проверить договор",
        "Не надо записывать задачу проверить договор",
        "Запиши заметку проверить договор",
        "Создай событие проверить договор",
        "Какие задачи у меня есть?",
        "Я записал задачу проверить договор",
        "Можно ли записать задачу?",
        "Нужно придумать задачу для команды",
        "Задача проверить договор",
        "Проверь договор",
        "Составь план проверки договора",
        "Запиши задачу",
        "",
        "Покажи задачу проверить договор",
    ],
)
def test_no_trigger_scenarios(text):
    assert detect_quick_task(text) is None


def test_quick_task_capture_is_idempotent(tmp_path, monkeypatch):
    common = _common(tmp_path, monkeypatch)
    text = "Создай задачу: проверить отчёт"
    first = capture_quick_task(
        text, platform="telegram", chat_id="42", request_id="same", session_id="s1"
    )
    second = capture_quick_task(
        text, platform="telegram", chat_id="42", request_id="same", session_id="s1"
    )
    assert first.created is True
    assert second.created is False
    assert first.task_id == second.task_id
    with sqlite3.connect(tmp_path / ".hermes" / "kanban.db") as conn:
        assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1


def test_prepare_turn_captures_task_before_router_or_history(tmp_path, monkeypatch):
    common = _common(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "gateway.task_runtime.route_turn",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("router must not run")),
    )
    prepared = prepare_task_turn(
        message="Запиши задачу сделать пост на LinkedIn с объявлением о наборе айтишников.",
        request_id="voice-update-17",
        **common,
    )
    result = prepared.early_response
    assert prepared.task is None
    assert result["api_calls"] == 0
    assert result["tools"] == []
    assert result["diagnostics"]["skill_call_count"] == 0
    assert result["diagnostics"]["tool_call_count"] == 0
    assert result["final_response"] == (
        "Записал задачу «Сделать пост на LinkedIn с объявлением о наборе айтишников»."
    )
    assert "t_" not in result["final_response"]
    with sqlite3.connect(tmp_path / ".hermes" / "kanban.db") as conn:
        row = conn.execute("SELECT title FROM tasks").fetchone()
    assert row[0] == "Сделать пост на LinkedIn с объявлением о наборе айтишников"
