import asyncio
import json
import queue
from types import SimpleNamespace

import pytest

from gateway.telegram_execution_progress import ExecutionProgress


def plan(total=2, done=1):
    return json.dumps({"todos": [
        {"content": "Проверить источники", "status": "completed"},
        {"content": "Сравнить результаты", "status": "in_progress"},
    ], "summary": {"total": total, "completed": done}})


def test_plan_stage_and_elapsed():
    now = [1.0]
    p = ExecutionProgress(clock=lambda: now[0])
    assert p.render(final=True) is None
    text = p.update("tool.completed", "todo", result=plan())
    assert "1/2" in text and "50%" in text and "Сравнить результаты" in text
    now[0] = 13.5
    assert "12.5 с" in p.render(final=True)


@pytest.mark.parametrize("total,done", [(2.0, 1), ("2", 1), (True, 1), (2, 3), (0, 0), (3, 1)])
def test_invalid_plan_has_no_percentage(total, done):
    assert "%" not in ExecutionProgress().update("tool.completed", "todo", result=plan(total, done))


def test_error_invalidates_old_plan_and_never_claims_success():
    p = ExecutionProgress()
    p.update("tool.completed", "todo", result=plan())
    text = p.update("tool.completed", "todo", result=plan(), is_error=True)
    assert "%" not in text and "ошибка" in text
    assert p.update("tool.started", "clarify") is None
    assert "SECRET" not in p.update("tool.completed", "terminal", result="SECRET")
    p.update("tool.completed", "todo", result=plan())
    assert "%" not in p.update("tool.completed", "terminal", is_error=True)


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
    )


def test_real_callback_filters_clarify_interrupt_and_stale():
    from gateway.run import TurnRunner
    ctx = context()
    turn = TurnRunner(None, ctx)
    turn.progress_callback("tool.started", "clarify")
    assert ctx.progress_queue.empty()
    turn.progress_callback("tool.completed", "todo", result=plan())
    marker, text = ctx.progress_queue.get_nowait()
    assert marker == "__snapshot__" and "50%" in text
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
    ctx.progress_queue.put(("__reset__",))
    turn.progress_callback("tool.completed", "todo", result=plan())
    task.cancel()
    await task
    assert len(adapter.sent) == 1
    assert adapter.sent[0][1:] == ("anchor", {"thread_id": "topic"})
    assert adapter.edits[-1][0] == "one"
    assert "50%" in adapter.edits[-1][1]
    assert "Время обработки" in adapter.edits[-1][1]
    assert adapter.edits[-1][2]["metadata"] == {"thread_id": "topic"}
