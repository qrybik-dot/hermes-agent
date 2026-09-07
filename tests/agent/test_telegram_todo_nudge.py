from types import SimpleNamespace

from agent.conversation_loop import _should_nudge_telegram_todo
from tools.todo_tool import TodoStore


def tc(name):
    return SimpleNamespace(function=SimpleNamespace(name=name))


def agent(platform="telegram", started=100.0):
    return SimpleNamespace(
        platform=platform,
        valid_tool_names={"todo", "terminal", "read_file", "web_search", "skill", "kanban_create"},
        _todo_store=TodoStore(),
        _telegram_todo_substantive_calls=0,
        _telegram_todo_nudge_attempts=0,
        _inflight_turn_started=started,
    )


def test_nudge_after_third_substantive_call_not_before():
    a = agent()
    assert _should_nudge_telegram_todo(a, [tc("terminal")], now=110.0) is False
    assert _should_nudge_telegram_todo(a, [tc("read_file")], now=120.0) is False
    assert _should_nudge_telegram_todo(a, [tc("web_search")], now=130.0) is True
    assert a._telegram_todo_substantive_calls == 3
    assert a._telegram_todo_nudge_attempts == 1


def test_nudge_after_sixty_seconds_even_before_third_call():
    a = agent()
    assert _should_nudge_telegram_todo(a, [tc("terminal")], now=160.1) is True


def test_housekeeping_does_not_count_and_kanban_is_exempt():
    a = agent()
    assert _should_nudge_telegram_todo(a, [tc("skill")], now=170.0) is False
    assert a._telegram_todo_substantive_calls == 0
    assert _should_nudge_telegram_todo(a, [tc("kanban_create")], now=170.0) is False
    assert a._telegram_todo_substantive_calls == 0


def test_existing_or_in_batch_todo_disables_nudge():
    a = agent()
    assert _should_nudge_telegram_todo(a, [tc("todo"), tc("terminal"), tc("read_file")], now=200.0) is False
    a._todo_store.write([{"id": "x", "content": "Проверить данные", "status": "in_progress"}])
    assert _should_nudge_telegram_todo(a, [tc("terminal"), tc("read_file"), tc("web_search")], now=200.0) is False


def test_completed_old_todo_does_not_disable_new_turn_nudge():
    a = agent()
    a._todo_store.write([{"id": "old", "content": "Старая задача", "status": "completed"}])
    assert _should_nudge_telegram_todo(
        a, [tc("terminal"), tc("read_file"), tc("web_search")], now=110.0
    ) is True


def test_only_telegram_and_max_two_nudges():
    a = agent(platform="cli")
    assert _should_nudge_telegram_todo(a, [tc("terminal"), tc("read_file"), tc("web_search")], now=200.0) is False

    a = agent()
    assert _should_nudge_telegram_todo(a, [tc("terminal"), tc("read_file"), tc("web_search")], now=110.0) is True
    assert _should_nudge_telegram_todo(a, [tc("terminal")], now=111.0) is True
    assert _should_nudge_telegram_todo(a, [tc("terminal")], now=112.0) is False
    assert a._telegram_todo_nudge_attempts == 2
