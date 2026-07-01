"""Regression tests for Telegram long-task live-status and final delivery."""

import asyncio
from gateway.run import _final_delivery_task_id

from gateway.platforms.base import SendResult
from gateway.telegram_task_status import (
    FinalDeliveryDeduper,
    TelegramTaskStatusState,
    completed_stage_percent,
    should_surface_telegram_interim,
    verdict_from_text,
)


class DummyStatusAdapter:
    name = "dummy-telegram"

    def __init__(self, *, edit_failures=0):
        self.sent = []
        self.edits = []
        self._next_id = 100
        self._status_message_ids = {}
        self._status_fallback_sent = set()
        self.edit_failures = edit_failures

    async def send(self, chat_id, content, metadata=None, **kwargs):
        self._next_id += 1
        self.sent.append((chat_id, content, metadata))
        return SendResult(success=True, message_id=str(self._next_id))

    async def edit_message(self, chat_id, message_id, content, finalize=False, metadata=None):
        self.edits.append((chat_id, message_id, content, finalize, metadata))
        if self.edit_failures > 0:
            self.edit_failures -= 1
            return SendResult(success=False, error="temporary", retryable=True)
        return SendResult(success=True, message_id=str(message_id))

    # Keep this implementation equivalent to TelegramAdapter.send_or_update_status
    # for the unit tests without constructing a real bot adapter.
    async def send_or_update_status(self, chat_id, status_key, content, *, metadata=None):
        key = (str(chat_id), str(status_key))
        cached_id = self._status_message_ids.get(key)
        fallback_sent = self._status_fallback_sent
        if cached_id is None and key in fallback_sent:
            return SendResult(success=False, error="status_edit_fallback_already_used")
        if cached_id is not None:
            result = await self.edit_message(chat_id, cached_id, content, finalize=True, metadata=metadata)
            if not result.success and getattr(result, "retryable", False):
                result = await self.edit_message(chat_id, cached_id, content, finalize=True, metadata=metadata)
            if result.success:
                if result.message_id:
                    self._status_message_ids[key] = str(result.message_id)
                return result
            self._status_message_ids.pop(key, None)
            if key in fallback_sent:
                return result
            fallback_sent.add(key)
        result = await self.send(chat_id, content, metadata=metadata)
        if result.success and result.message_id:
            self._status_message_ids[key] = str(result.message_id)
        return result


def test_one_long_task_creates_one_status_message_and_edits_afterwards():
    adapter = DummyStatusAdapter()

    async def scenario():
        await adapter.send_or_update_status("chat", "task:t1", "0%")
        for idx in range(5):
            await adapter.send_or_update_status("chat", "task:t1", f"stage {idx}")

    asyncio.run(scenario())
    assert len(adapter.sent) == 1
    assert len(adapter.edits) == 5


def test_five_progress_events_are_edits_not_five_sends():
    adapter = DummyStatusAdapter()

    async def scenario():
        for idx in range(6):
            await adapter.send_or_update_status("chat", "task:t2", f"progress {idx}")

    asyncio.run(scenario())
    assert len(adapter.sent) == 1
    assert len(adapter.edits) == 5


def test_reviewer_event_does_not_surface_in_telegram():
    assert not should_surface_telegram_interim("reviewer: plan has gaps", task_status_enabled=True)


def test_tool_event_does_not_surface_in_telegram():
    assert not should_surface_telegram_interim("receiving stream response from terminal", task_status_enabled=True)


def test_iteration_budget_exhausted_maps_to_one_incomplete_final():
    deduper = FinalDeliveryDeduper()
    verdict = verdict_from_text("INCOMPLETE\niteration_budget_exhausted")
    assert verdict == "INCOMPLETE"
    assert deduper.mark_once(task_id="task", verdict=verdict, delivery_type="text", generation="gateway-runtime-v1")
    assert not deduper.mark_once(task_id="task", verdict=verdict, delivery_type="text", generation="gateway-runtime-v1")


def test_runtime_and_finalizer_do_not_create_two_identical_finals():
    deduper = FinalDeliveryDeduper()
    assert deduper.mark_once(task_id="task", verdict="INCOMPLETE", delivery_type="text", generation="gateway-runtime-v1")
    assert not deduper.mark_once(task_id="task", verdict="INCOMPLETE", delivery_type="text", generation="gateway-runtime-v1")


def test_repeated_finalizer_run_is_idempotent():
    deduper = FinalDeliveryDeduper()
    assert deduper.mark_once(task_id="task", verdict="READY", delivery_type="text", generation="gateway-runtime-v1")
    assert not deduper.mark_once(task_id="task", verdict="READY", delivery_type="text", generation="gateway-runtime-v1")


def test_html_document_not_sent_twice():
    deduper = FinalDeliveryDeduper()
    assert deduper.mark_once(task_id="task", verdict="READY", delivery_type="document", generation="task-report-html-v1")
    assert not deduper.mark_once(task_id="task", verdict="READY", delivery_type="document", generation="task-report-html-v1")


def test_edit_failure_creates_at_most_one_fallback_message():
    adapter = DummyStatusAdapter(edit_failures=3)

    async def scenario():
        await adapter.send_or_update_status("chat", "task:t3", "start")
        await adapter.send_or_update_status("chat", "task:t3", "fallback")
        await adapter.send_or_update_status("chat", "task:t3", "suppressed")

    asyncio.run(scenario())
    assert len(adapter.sent) == 2
    assert adapter.sent[0][1] == "start"
    assert adapter.sent[1][1] == "fallback"
    assert all(item[2] != "suppressed" for item in adapter.sent)
    assert len(adapter.edits) == 4


def test_progress_not_100_without_completed_dod():
    state = TelegramTaskStatusState("demo")
    assert state.percent_for_stage("delivery") < 100
    state.critical_tests_started = True
    assert completed_stage_percent(state.stages, state.stages[:-1], tests_started=True) < 100


def test_progress_not_80_90_before_critical_tests_start():
    stages = ["accepted", "audit", "prepared", "applied", "tests", "delivery"]
    assert completed_stage_percent(stages, stages[:5], tests_started=False) == 60



def test_waiting_for_non_streaming_status_does_not_surface():
    assert not should_surface_telegram_interim(
        "waiting for non-streaming API response",
        task_status_enabled=True,
    )
    assert not should_surface_telegram_interim(
        "waiting for non-streaming API response",
        task_status_enabled=False,
    )


def test_incomplete_final_status_never_reaches_100_percent():
    state = TelegramTaskStatusState("demo")
    rendered = state.render(stage="delivery", verdict="INCOMPLETE")
    assert "100%" not in rendered
    assert "90%" in rendered or "80%" in rendered or "60%" in rendered



def test_ordinary_turn_has_no_final_delivery_task_key():
    assert _final_delivery_task_id({"task_id": None, "session_id": "same-session"}) == ""

def test_managed_task_has_final_delivery_task_key():
    assert _final_delivery_task_id({"task_id": "task-123", "session_id": "same-session"}) == "task-123"


def test_adaptive_progress_theme_selection():
    assert TelegramTaskStatusState("исправить баг в Telegram").resolved_theme() == "developer"
    assert TelegramTaskStatusState("исследовать причину сбоя").resolved_theme() == "detective"
    assert TelegramTaskStatusState("подготовить документ").resolved_theme() == "runner"


def test_adaptive_progress_uses_telegram_safe_emoji_bar():
    state = TelegramTaskStatusState("исправить код")
    rendered = state.render(stage="applied")
    assert "🧑‍💻" in rendered
    assert "▰" in rendered and "▱" in rendered
    assert "60%" in rendered
    assert "Задача: исправить код" in rendered


def test_detective_and_runner_finish_with_semantic_icons():
    detective = TelegramTaskStatusState("провести исследование")
    runner = TelegramTaskStatusState("подготовить документ")
    assert "💡" in detective.render(stage="delivery", verdict="READY")
    assert "🏆" in runner.render(stage="delivery", verdict="READY")


def test_blocked_progress_uses_warning_and_never_claims_completion():
    state = TelegramTaskStatusState("исправить сервер")
    rendered = state.render(stage="delivery", verdict="BLOCKED", blocker="нет доступа")
    assert "⚠️" in rendered
    assert "100%" not in rendered
    assert "Блокер: нет доступа" in rendered
