import asyncio
import json
import queue
from types import SimpleNamespace

import pytest

from gateway.telegram_execution_progress import ExecutionProgress


def plan(total=3, done=1):
    return json.dumps({"todos": [
        {"content": "Проверить источники", "status": "completed"},
        {"content": "Сравнить результаты", "status": "in_progress"},
        {"content": "Проверить live-поведение", "status": "pending"},
    ], "summary": {"total": total, "completed": done}})


def test_plan_renders_truthful_bar_semantic_stage_next_and_elapsed():
    now = [1.0]
    p = ExecutionProgress(clock=lambda: now[0])
    assert p.render(final=True) is None
    text = p.update("tool.completed", "todo", result=plan())
    assert "🧭 Работаю · 33%" in text
    assert "███░░░░░░░ 1/3" in text
    assert "Сейчас: Сравнить результаты" in text
    assert "Дальше: Проверить live-поведение" in text
    assert "Обрабатываю результат" not in text
    now[0] = 103.0
    assert "⏱ 1:42" in p.render(final=True)


@pytest.mark.parametrize("total,done", [(3.0, 1), ("3", 1), (True, 1), (3, 4), (0, 0), (2, 1)])
def test_invalid_plan_has_no_percentage(total, done):
    assert "%" not in ExecutionProgress().update("tool.completed", "todo", result=plan(total, done))


def test_multiple_active_plan_items_have_no_percentage():
    result = json.dumps({
        "todos": [
            {"content": "Готово", "status": "completed"},
            {"content": "Первый активный", "status": "in_progress"},
            {"content": "Второй активный", "status": "in_progress"},
        ],
        "summary": {"total": 3, "completed": 1},
    })
    assert "%" not in ExecutionProgress().update("tool.completed", "todo", result=result)


def test_tool_error_is_quiet_and_todo_error_invalidates_percentage():
    p = ExecutionProgress()
    p.update("tool.completed", "todo", result=plan())
    assert p.update("tool.completed", "terminal", is_error=True) is None
    still_working = p.render()
    assert "33%" in still_working
    assert "ошиб" not in still_working.casefold()

    assert p.update("tool.completed", "todo", result=plan(), is_error=True) is None
    assert "%" not in p.render()
    assert "ошиб" not in p.render().casefold()
    assert p.update("tool.started", "clarify") is None

def test_successful_tool_completion_does_not_overwrite_useful_stage():
    p = ExecutionProgress()
    p.update("tool.completed", "todo", result=plan())
    started = p.update("tool.started", "terminal")
    assert "Сейчас: Сравнить результаты" in started
    assert p.update("tool.completed", "terminal", result="SECRET") is None
    assert "Обрабатываю результат" not in p.render()




def test_no_plan_tool_stage_is_human_and_hides_raw_command():
    p = ExecutionProgress()
    command = "find /srv/hermes-call-processing -name '*.md'"
    text = p.update(
        "tool.started", "terminal", preview=command, args={"command": command},
    )
    assert "Сейчас: Проверяю сохранённые звонки" in text
    assert command not in text
    assert "/srv/" not in text

    generic = ExecutionProgress().update(
        "tool.started", "terminal", args={"command": "python3 -c 'print(1)'"},
    )
    assert "Сейчас: Проверяю нужные данные" in generic
    assert "python3" not in generic

    search = ExecutionProgress().update(
        "tool.started", "web_search", args={"query": "музеи Москвы для детей"},
    )
    assert "Сейчас: Ищу информацию: музеи Москвы для детей" in search


def test_unknown_tool_never_falls_back_to_useless_action_or_raw_preview():
    text = ExecutionProgress().update(
        "tool.started", "some_plugin_tool", preview="/secret/technical/path --flag",
    )
    assert "Сейчас: Проверяю текущий этап" in text
    assert "Выполняю действие" not in text
    assert "/secret/" not in text

def test_safe_commentary_updates_same_semantic_snapshot():
    p = ExecutionProgress()
    p.update("tool.completed", "todo", result=plan())
    p.update("tool.completed", "web_extract", result="ignored")
    text = p.update_commentary("Найдено: Источники расходятся; проверяю первичный документ.")
    assert "Найдено: Источники расходятся; проверяю первичный документ." in text
    assert "Дальше: Проверить live-поведение" in text
    assert "reasoning" not in text.lower()


def test_commentary_is_bounded_and_explicit_labels_are_understood():
    p = ExecutionProgress()
    text = p.update_commentary(
        "Сейчас: проверяю конфигурацию\n"
        "Найдено: конфликт подтверждён\n"
        "Дальше: запускаю транспортный тест\n" + "x" * 500
    )
    assert "Сейчас: проверяю конфигурацию" in text
    assert "Найдено: конфликт подтверждён" in text
    assert "Дальше: запускаю транспортный тест" in text


def test_completion_commentary_cannot_run_ahead_of_todo_percentage():
    p = ExecutionProgress()
    p.update("tool.completed", "todo", result=plan())
    text = p.update_commentary(
        "Сейчас: завершён пункт 2\n"
        "Найдено: Linux\n"
        "Дальше: итог"
    )
    assert "🧭 Работаю · 33%" in text
    assert "Сейчас: Сравнить результаты" in text
    assert "Сейчас: завершён пункт 2" not in text
    assert "Найдено: Linux" in text
    assert "Дальше: итог" in text
    assert len(text) < 500
    text = p.update_commentary("Результат: конфликт подтверждён")
    assert "Результат: конфликт подтверждён" in text
    text = p.update_commentary("Дальше: запускаю транспортный тест")
    assert "Дальше: запускаю транспортный тест" in text


def test_pending_only_plan_uses_first_pending_step_as_current():
    result = json.dumps({
        "todos": [
            {"content": "Проверить конфиг", "status": "pending"},
            {"content": "Запустить smoke", "status": "pending"},
        ],
        "summary": {"total": 2, "completed": 0},
    })
    text = ExecutionProgress().update("tool.completed", "todo", result=result)
    assert "Сейчас: Проверить конфиг" in text
    assert "Дальше: Запустить smoke" in text
    assert "Завершаю ответ" not in text


def test_future_intent_after_tool_completion_is_current_not_finding():
    p = ExecutionProgress()
    p.update("tool.started", "web_extract")
    p.update("tool.completed", "web_extract")
    text = p.update_commentary("Сейчас проверю первичный источник")
    assert "Сейчас: Сейчас проверю первичный источник" in text
    assert "Найдено: Сейчас проверю" not in text


@pytest.mark.parametrize(
    "outcome, heading",
    [
        (None, "⏹ Статус завершения неизвестен"),
        ("partial", "⚠️ Частично"),
        ("failed", "⚠️ Не завершено"),
        ("interrupted", "⏹ Остановлено"),
        ("success", "✅ Ответ готов"),
    ],
)
def test_final_heading_reflects_real_outcome(outcome, heading):
    p = ExecutionProgress()
    p.update("tool.started", "terminal")
    assert heading in p.render(final=True, outcome=outcome)


def context():
    from gateway.config import Platform
    return SimpleNamespace(
        source=SimpleNamespace(platform=Platform.TELEGRAM, chat_id="test"),
        user_config={"display": {"platforms": {"telegram": {"execution_progress": True}}}},
        progress_grouping="accumulate", progress_mode="all", tool_progress_enabled=True,
        progress_queue=queue.Queue(), _run_still_current=lambda: True,
        agent_holder=[SimpleNamespace(is_interrupted=False)], _native_slack_task_cards=False,
        _progress_metadata={"thread_id": "topic"}, _progress_reply_to="anchor",
        _cleanup_progress=False, last_progress_msg=[None], repeat_count=[0],
        _thinking_enabled=False,
        result_holder=[{"final_response": "done", "failed": False}],
    )


def test_real_callback_routes_interim_commentary_into_snapshot_queue():
    from gateway.run import TurnRunner
    ctx = context()
    turn = TurnRunner(None, ctx)
    assert turn.progress_commentary("Проверяю настройки Gateway") is True
    marker, text = ctx.progress_queue.get_nowait()
    assert marker == "__snapshot__"
    assert "Сейчас: Проверяю настройки Gateway" in text


def test_real_commentary_callback_reuses_secret_redaction_rail():
    from gateway.run import TurnRunner
    ctx = context()
    turn = TurnRunner(None, ctx)
    secret = "sk-proj-" + "X" * 40
    assert turn.progress_commentary(f"Сейчас: проверяю {secret}") is True
    _, text = ctx.progress_queue.get_nowait()
    assert secret not in text


def test_real_callback_filters_clarify_interrupt_and_stale():
    from gateway.run import TurnRunner
    ctx = context()
    turn = TurnRunner(None, ctx)
    turn.progress_callback("tool.started", "clarify")
    assert ctx.progress_queue.empty()
    turn.progress_callback("_thinking", "raw reasoning must stay hidden")
    assert ctx.progress_queue.empty()
    ctx._thinking_enabled = True
    turn.progress_callback("reasoning.available", "_thinking", "raw reasoning")
    assert ctx.progress_queue.empty()
    turn.progress_callback("tool.completed", "todo", result=plan())
    marker, text = ctx.progress_queue.get_nowait()
    assert marker == "__snapshot__" and "33%" in text
    ctx.agent_holder[0].is_interrupted = True
    turn.progress_callback("tool.started", "terminal")
    assert ctx.progress_queue.empty()
    ctx.agent_holder[0].is_interrupted = False
    ctx._run_still_current = lambda: False
    turn.progress_callback("tool.started", "terminal")
    assert ctx.progress_queue.empty()


@pytest.mark.asyncio
async def test_real_sender_keeps_one_bubble_across_stream_reset():
    from gateway.config import Platform, PlatformConfig
    from gateway.platforms.base import BasePlatformAdapter, SendResult
    from gateway.run import TurnRunner

    class Adapter(BasePlatformAdapter):
        def __init__(self):
            super().__init__(PlatformConfig(enabled=True), Platform.TELEGRAM)
            self.sent, self.edits = [], []
            self.visible = asyncio.Event()
        async def connect(self): return True
        async def disconnect(self): pass
        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.sent.append((content, reply_to, metadata))
            self.visible.set()
            return SendResult(success=True, message_id="one")
        async def edit_message(self, chat_id, message_id, content, **kwargs):
            self.edits.append((message_id, content, kwargs))
            return SendResult(success=True, message_id=message_id)
        async def send_typing(self, chat_id, metadata=None): pass
        async def get_chat_info(self, chat_id): return {}

    ctx, adapter = context(), Adapter()
    turn = TurnRunner(SimpleNamespace(_adapter_for_source=lambda s: adapter), ctx)
    turn.progress_callback("tool.started", "terminal")
    task = asyncio.create_task(turn.send_progress_messages())
    await asyncio.wait_for(adapter.visible.wait(), timeout=5)
    assert turn.progress_commentary("Проверяю настройки Gateway") is True
    ctx.progress_queue.put(("__reset__",))
    turn.progress_callback("tool.completed", "todo", result=plan())
    task.cancel()
    await task
    assert len(adapter.sent) == 1
    assert adapter.sent[0][1:] == ("anchor", {"thread_id": "topic"})
    assert adapter.edits[-1][0] == "one"
    assert "33%" in adapter.edits[-1][1]
    assert "⏱" in adapter.edits[-1][1]
    assert adapter.edits[-1][2]["metadata"] == {"thread_id": "topic"}


@pytest.mark.asyncio
async def test_snapshot_timer_refreshes_without_new_tool_events():
    from gateway.config import Platform, PlatformConfig
    from gateway.platforms.base import BasePlatformAdapter, SendResult
    from gateway.run import TurnRunner

    class Adapter(BasePlatformAdapter):
        def __init__(self):
            super().__init__(PlatformConfig(enabled=True), Platform.TELEGRAM)
            self.sent, self.edits = [], []
            self.visible = asyncio.Event()
        async def connect(self): return True
        async def disconnect(self): pass
        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.sent.append(content)
            self.visible.set()
            return SendResult(success=True, message_id="timer")
        async def edit_message(self, chat_id, message_id, content, **kwargs):
            self.edits.append(content)
            return SendResult(success=True, message_id=message_id)
        async def send_typing(self, chat_id, metadata=None): pass
        async def get_chat_info(self, chat_id): return {}

    ctx, adapter = context(), Adapter()
    turn = TurnRunner(SimpleNamespace(_adapter_for_source=lambda s: adapter), ctx)
    base = turn._execution_progress.started
    clocks = [base]
    turn._execution_progress.clock = lambda: clocks[0]
    turn.progress_callback(
        "tool.started", "terminal", preview="sleep 30", args={"command": "sleep 30"}
    )
    task = asyncio.create_task(turn.send_progress_messages())
    await asyncio.wait_for(adapter.visible.wait(), timeout=5)
    # Advance only the render clock; wait just past the 5s timer cadence.
    clocks[0] = base + 6
    await asyncio.sleep(5.3)
    ctx._run_still_current = lambda: False
    await asyncio.wait_for(task, timeout=2)
    assert any("⏱ 0:06" in text for text in adapter.edits)
