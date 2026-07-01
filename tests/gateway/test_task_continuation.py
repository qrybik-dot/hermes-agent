import json
import time

from gateway.task_continuation import (
    TaskStateStore,
    format_choice,
    infer_execution_contract,
    is_pause_request,
    is_progress_only,
)
from gateway.task_router import route_turn
from gateway.task_runtime import (
    calendar_completion_evidence_missing,
    calendar_evidence_complete,
    extract_calendar_evidence_from_result,
    extract_calendar_evidence_from_tool_result,
    persist_calendar_evidence_from_tool_result,
    prepare_task_turn,
    task_reported_non_success,
)
from tools.session_search_tool import (
    _consume_search_budget,
    reset_turn_search_budget,
    set_turn_search_budget,
)


def _store(tmp_path):
    return TaskStateStore(tmp_path / "state.db")


def test_resume_one_task(tmp_path):
    store = _store(tmp_path)
    task = store.create(
        platform="telegram", chat_id="1", session_key="s", title="Git audit",
        original_request="Проверь Git", role="server_debug", toolsets=["terminal", "file"],
        required_toolsets=["terminal"], requires_execution=True, status="paused",
    )
    decision = store.resolve("Готов продолжить", "telegram", "1")
    assert decision.kind == "selected"
    assert decision.task.task_id == task.task_id


def test_explicit_task_prefix_with_followup_text_resumes_task(tmp_path):
    store = _store(tmp_path)
    task = store.create(
        task_id="deadbeef1234", platform="telegram", chat_id="1", session_key="s",
        title="Provider test", original_request="Run provider smoke tests",
        role="server_debug", toolsets=["terminal", "file"],
        required_toolsets=["terminal"], requires_execution=True, status="incomplete",
    )
    message = "\u041f\u0440\u043e\u0434\u043e\u043b\u0436\u0438\u0442\u044c deadbeef1234\n\nRun the full safe smoke test"
    decision = store.resolve(message, "telegram", "1")
    assert decision.kind == "selected"
    assert decision.task.task_id == task.task_id


def test_resume_choice_and_number(tmp_path):
    store = _store(tmp_path)
    store.create(platform="telegram", chat_id="1", session_key="s", title="First",
                 original_request="one", role="planning", toolsets=["file"], status="paused")
    store.create(platform="telegram", chat_id="1", session_key="s", title="Second",
                 original_request="two", role="planning", toolsets=["file"], status="paused")
    decision = store.resolve("Продолжай", "telegram", "1")
    assert decision.kind == "choice"
    assert "Продолжить 1" in format_choice(decision.candidates)
    chosen = store.resolve("Продолжить 1", "telegram", "1")
    assert chosen.kind == "selected"


def test_pause_progress_and_contract():
    assert is_pause_request("Повторим утром")
    assert is_progress_only("⏳ В работе: аудит")
    execution, required = infer_execution_contract(
        "Проверь Git-синхронизацию, ничего не меняй", "server_debug", ["terminal"]
    )
    assert execution is True
    assert required == ("terminal",)
    plan, required_plan = infer_execution_contract(
        "Составь план проверки Git, ничего не меняй", "planning", ["file"]
    )
    assert plan is False
    assert required_plan == ()


def test_status_message_persists(tmp_path):
    store = _store(tmp_path)
    task = store.create(platform="telegram", chat_id="1", session_key="s", title="Task",
                        original_request="x", role="planning", toolsets=["file"])
    store.set_status_message_id(task.task_id, 42)
    assert store.get(task.task_id).status_message_id == "42"


def test_git_audit_routes_to_terminal_server_debug():
    route = route_turn(
        "Проведи read-only аудит Git-синхронизации, ничего не меняй",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "memory", "no_mcp"],
    )
    assert route.role == "server_debug"
    assert "terminal" in route.toolsets


def test_session_search_budget_is_two_calls():
    token = set_turn_search_budget(2)
    try:
        assert _consume_search_budget() is True
        assert _consume_search_budget() is True
        assert _consume_search_budget() is False
    finally:
        reset_turn_search_budget(token)


def test_prepare_task_turn_restores_role_and_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    state_dir.mkdir()
    store = TaskStateStore(state_dir / "state.db")
    task = store.create(
        platform="telegram", chat_id="1", session_key="old", title="Git audit",
        original_request="Проведи read-only аудит Git-синхронизации",
        role="server_debug", toolsets=["terminal", "file", "no_mcp"],
        required_toolsets=["terminal"], requires_execution=True, status="paused",
    )
    prepared = prepare_task_turn(
        message="Готов продолжить", platform_key="telegram", chat_id="1",
        session_key="new", session_id="session-new", request_id="req-1",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "memory", "no_mcp"],
    )
    assert prepared.continued is True
    assert prepared.task.task_id == task.task_id
    assert prepared.route.role == "server_debug"
    assert "terminal" in prepared.route.toolsets
    assert "Saved task" in prepared.message



def test_reply_context_continuation_command(tmp_path):
    store = _store(tmp_path)
    task = store.create(
        platform="telegram", chat_id="1", session_key="s", title="Git audit",
        original_request="Проверь Git", role="server_debug", toolsets=["terminal"],
        required_toolsets=["terminal"], requires_execution=True, status="paused",
    )
    decision = store.resolve('[Replying to: "old report"]\nГотов продолжить', "telegram", "1")
    assert decision.kind == "selected"
    assert decision.task.task_id == task.task_id


def test_continuation_without_active_tasks_is_deterministic(tmp_path):
    store = _store(tmp_path)
    decision = store.resolve('[Replying to: "old report"]\nГотов продолжить', "telegram", "1")
    assert decision.kind == "empty"


def test_google_workspace_continuation_recomputes_skill_names(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    state_dir.mkdir()
    store = TaskStateStore(state_dir / "state.db")
    task = store.create(
        platform="telegram", chat_id="1", session_key="old", title="Calendar task",
        original_request="Какие у меня встречи сегодня в календаре?",
        role="simple", toolsets=["terminal", "skills", "file"],
        required_toolsets=[], requires_execution=False, status="paused",
    )
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: ("GOOGLE WORKSPACE SKILL", ["google-workspace"], []),
    )
    prepared = prepare_task_turn(
        message="Готов продолжить", platform_key="telegram", chat_id="1",
        session_key="new", session_id="session-new", request_id="req-1",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "memory", "no_mcp"],
    )
    assert prepared.continued is True
    assert prepared.task.task_id == task.task_id
    assert prepared.route.skill_names == ("google-workspace",)
    assert "GOOGLE WORKSPACE SKILL" in prepared.route.operational_context


def test_calendar_missing_image_and_datetime_blocks_before_model(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    state_dir.mkdir()
    store = TaskStateStore(state_dir / "state.db")
    original_request = (
        "Продолжи утреннюю задачу: по присланному изображению определить место или клинику "
        "для чистки зубов Веры и создать напоминание или событие в календаре. Если изображение "
        "или ключевые данные недоступны, не угадывай: верни BLOCKED и попроси переслать "
        "изображение или уточнить место и дату."
    )
    task = store.create(
        task_id="b27e2d8b2026",
        platform="telegram",
        chat_id="1",
        session_key="old",
        title="Calendar from image",
        original_request=original_request,
        role="simple",
        toolsets=["file", "memory", "no_mcp", "session_search", "skills", "terminal", "vision"],
        required_toolsets=["terminal"],
        requires_execution=True,
        status="incomplete",
    )

    def fail_if_preload_runs(*args, **kwargs):
        raise AssertionError("skill preload must not run before deterministic input preflight")

    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        fail_if_preload_runs,
    )
    prepared = prepare_task_turn(
        message="Продолжить b27e2d8b2026", platform_key="telegram", chat_id="1",
        session_key="new", session_id="session-new", request_id="req-1",
        user_config={"agent": {}},
        platform_toolsets=["file", "memory", "no_mcp", "session_search", "skills", "terminal", "vision"],
    )
    assert prepared.continued is True
    assert prepared.task.task_id == task.task_id
    assert prepared.early_response is not None
    assert prepared.early_response["model"] == "deterministic"
    assert prepared.early_response["api_calls"] == 0
    assert prepared.early_response["tools"] == []
    assert prepared.early_response["final_response"].startswith("BLOCKED")
    assert "доступное изображение" in prepared.early_response["final_response"]
    assert "конкретная дата" in prepared.early_response["final_response"]
    assert "конкретное время" in prepared.early_response["final_response"]
    assert store.get(task.task_id).status == "blocked"


def test_output_screenshot_artifacts_do_not_require_vision_context(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    state_dir.mkdir()
    store = TaskStateStore(state_dir / "state.db")
    task = store.create(
        task_id="facefeed2026",
        platform="telegram",
        chat_id="1",
        session_key="old",
        title="HTML reporting smoke",
        original_request=(
            "Измени production Report Finalizer renderer. После рендеринга HTML создай "
            "desktop screenshot и mobile screenshot как выходные PNG артефакты через браузер. "
            "Входное изображение и vision-контекст не требуются."
        ),
        role="server_debug",
        toolsets=["terminal", "file", "skills"],
        required_toolsets=["terminal"],
        requires_execution=True,
        status="incomplete",
    )

    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: ("PLAN SKILL", list(names), []),
    )
    prepared = prepare_task_turn(
        message="Продолжить facefeed2026",
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="req-html-reporting",
        user_config={"agent": {}},
        platform_toolsets=["file", "skills", "terminal", "no_mcp"],
    )

    assert prepared.continued is True
    assert prepared.task is not None
    assert prepared.task.task_id == task.task_id
    assert prepared.early_response is None
    assert prepared.route is not None
    assert "terminal" in prepared.route.toolsets


def test_completed_task_replays_saved_result_without_model(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    state_dir.mkdir()
    store = TaskStateStore(state_dir / "state.db")
    task = store.create(
        task_id="deadbeef1234",
        platform="telegram",
        chat_id="1",
        session_key="old",
        title="Completed calendar task",
        original_request="Создать событие в календаре завтра в 18:30",
        role="simple",
        toolsets=["file", "skills", "terminal"],
        requires_execution=True,
        status="completed",
        metadata={"final_response": "Название: Проверенное событие\nEvent ID: event-123"},
    )

    def fail_if_preload_runs(*args, **kwargs):
        raise AssertionError("completed task must not preload skills or enter the model path")

    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        fail_if_preload_runs,
    )
    prepared = prepare_task_turn(
        message="Продолжить deadbeef1234", platform_key="telegram", chat_id="1",
        session_key="new", session_id="session-new", request_id="req-done",
        user_config={"agent": {}},
        platform_toolsets=["file", "skills", "terminal"],
    )
    assert prepared.continued is True
    assert prepared.task.task_id == task.task_id
    assert prepared.early_response is not None
    assert prepared.early_response["model"] == "deterministic"
    assert prepared.early_response["api_calls"] == 0
    assert prepared.early_response["tools"] == []
    assert "Задача уже выполнена" in prepared.early_response["final_response"]
    assert "event-123" in prepared.early_response["final_response"]
    assert store.get(task.task_id).status == "completed"


def test_missing_google_workspace_skill_blocks_before_model(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes").mkdir()
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: ("", [], ["google-workspace"]),
    )
    prepared = prepare_task_turn(
        message="Какие у меня встречи сегодня в календаре?", platform_key="telegram", chat_id="1",
        session_key="new", session_id="session-new", request_id="req-1",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "memory", "no_mcp"],
    )
    assert prepared.early_response is not None
    assert prepared.early_response["model"] == "deterministic"
    assert prepared.early_response["api_calls"] == 0
    assert "google-workspace" in prepared.early_response["final_response"]


def test_execution_without_working_toolsets_explains_missing_capability(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes").mkdir()
    prepared = prepare_task_turn(
        message="Самостоятельно найди источники по теме и верни результат",
        platform_key="telegram", chat_id="1",
        session_key="new", session_id="session-new", request_id="req-no-tools",
        user_config={"agent": {}},
        platform_toolsets=["no_mcp"],
    )
    assert prepared.early_response is not None
    assert prepared.early_response["model"] == "deterministic"
    assert prepared.early_response["api_calls"] == 0
    assert prepared.early_response["tools"] == []
    response = prepared.early_response["final_response"]
    assert not response.startswith("BLOCKED")
    assert "нет доступного инструмента" in response
    assert "Пришли ссылку или файл" in response


def test_travel_turn_is_tracked_and_requires_terminal_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes").mkdir()
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: (
            "Travel skill loaded",
            ["city-travel-concierge"],
            [],
        ),
    )
    prepared = prepare_task_turn(
        message="сколько ехать до парка останкино от королева ул лесная и где там бесплатные парковки?",
        platform_key="telegram",
        chat_id="1",
        session_key="travel",
        session_id="session-travel",
        request_id="req-travel",
        user_config={"agent": {}},
        platform_toolsets=["browser", "file", "skills", "terminal", "web"],
    )
    assert prepared.early_response is None
    assert prepared.route is not None
    assert prepared.route.skill_names == ("city-travel-concierge",)
    assert prepared.task is not None
    assert prepared.task.requires_execution is True
    assert prepared.task.required_toolsets == ("terminal",)
    assert "terminal" in prepared.task.toolsets



def test_bare_numeric_choice_requires_recent_choice_prompt(tmp_path):
    store = _store(tmp_path)
    first = store.create(platform="telegram", chat_id="1", session_key="s", title="First", original_request="one", role="planning", toolsets=["file"], status="paused")
    second = store.create(platform="telegram", chat_id="1", session_key="s", title="Second", original_request="two", role="planning", toolsets=["file"], status="paused")
    assert store.resolve("1", "telegram", "1").kind == "none"
    choice = store.resolve("\u041f\u0440\u043e\u0434\u043e\u043b\u0436\u0430\u0439", "telegram", "1")
    assert choice.kind == "choice"
    chosen = store.resolve("1", "telegram", "1")
    assert chosen.kind == "selected"
    assert chosen.task.task_id == second.task_id
    assert chosen.task.task_id != first.task_id


def test_contextual_confirmation_opens_choice_instead_of_new_task(tmp_path):
    store = _store(tmp_path)
    store.create(platform="telegram", chat_id="1", session_key="s", title="Creative smoke test", original_request="Run smoke-test", role="simple", toolsets=["terminal"], status="incomplete")
    store.create(platform="telegram", chat_id="1", session_key="s", title="Notebook task", original_request="NotebookLM task", role="research", toolsets=["web"], status="incomplete")
    decision = store.resolve("\u0414\u0430, \u0437\u0430\u043f\u0443\u0441\u0442\u0438 \u0442\u0435\u0441\u0442", "telegram", "1")
    assert decision.kind == "choice"


def test_task_reported_partial_or_blocked_is_non_success():
    assert task_reported_non_success("\u0412\u0435\u0440\u0434\u0438\u043a\u0442: PARTIAL\nSmoke-test was not run") == ("incomplete", "reported verdict PARTIAL")
    assert task_reported_non_success("BLOCKED\nMissing approval") == ("blocked", "reported verdict BLOCKED")
    assert task_reported_non_success("READY\nAll checks passed") is None



def test_skill_inventory_question_is_execution_contract():
    execution, required = infer_execution_contract(
        "Какие навыки еще будут полезны для меня? Сделай подборку",
        "simple",
        ["skills", "terminal", "file"],
    )
    assert execution is True
    assert required == ()


def test_execution_contract_does_not_match_log_inside_unrelated_words():
    execution, required = infer_execution_contract(
        "Создай аналог методологического skill и сохрани результат",
        "simple",
        ["file", "skills"],
    )
    assert execution is True
    assert required == ()


def test_prepare_task_turn_restores_required_terminal_from_platform_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes").mkdir()

    from gateway.task_router import TaskRoute

    monkeypatch.setattr(
        "gateway.task_runtime.route_turn",
        lambda *args, **kwargs: TaskRoute(
            role="server_debug",
            reason="synthetic classifier miss",
            toolsets=["file", "no_mcp"],
            max_iterations=24,
        ),
    )
    prepared = prepare_task_turn(
        message="Проверь Git на VPS",
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="req-restore-terminal",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "no_mcp"],
    )
    assert prepared.early_response is None
    assert prepared.route is not None
    assert "terminal" in prepared.route.toolsets
    assert "restored_required=terminal" in prepared.route.reason


def test_opaque_followup_resumes_single_task(tmp_path):
    store = _store(tmp_path)
    task = store.create(
        platform="telegram", chat_id="1", session_key="s", title="Provider setup",
        original_request="Configure provider and wait for input",
        role="server_debug", toolsets=["terminal", "file"],
        required_toolsets=["terminal"], requires_execution=True, status="incomplete",
    )
    opaque = "a" * 32 + "." + "b" * 20
    decision = store.resolve("data " + opaque + " test", "telegram", "1")
    assert decision.kind == "selected"
    assert decision.task.task_id == task.task_id


def test_opaque_followup_does_not_hijack_new_question(tmp_path):
    store = _store(tmp_path)
    store.create(
        platform="telegram", chat_id="1", session_key="s", title="Provider setup",
        original_request="Configure provider", role="server_debug",
        toolsets=["terminal"], required_toolsets=["terminal"],
        requires_execution=True, status="incomplete",
    )
    assert store.resolve("What is the weather tomorrow?", "telegram", "1").kind == "none"



def test_technical_events_do_not_activate_calendar_verifier():
    assert calendar_completion_evidence_missing(
        "Исправь progress events и status events для live-status, события не календарные",
        "READY\nСобытия runtime обработаны, календарь не изменялся",
        route_toolsets=["terminal", "file"],
        route_skills=[],
    ) == ()


def test_real_calendar_write_still_requires_evidence():
    missing = calendar_completion_evidence_missing(
        "Создай событие в календаре завтра в 18:30: созвон с Иваном",
        "READY\nСобытие создано",
        route_toolsets=["google-calendar"],
        route_skills=["google-workspace"],
        metadata={"execution_contract": {"type": "calendar_write"}},
    )
    assert "event ID или штатная ссылка" in missing
    assert "подтверждение read-back" in missing


def test_continuation_uses_checkpoint_and_does_not_repeat_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    state_dir.mkdir()
    store = TaskStateStore(state_dir / "state.db")
    task = store.create(
        task_id="feedcafe2026",
        platform="telegram",
        chat_id="1",
        session_key="old",
        title="Gateway regression fix",
        original_request="Проверь git status, затем исправь runtime и проверь DoD",
        role="server_debug",
        toolsets=["terminal", "file", "no_mcp"],
        required_toolsets=["terminal"],
        requires_execution=True,
        status="incomplete",
        metadata={"checkpoint": "audit done; changed gateway/run.py; tests pending"},
    )

    prepared = prepare_task_turn(
        message="Продолжить feedcafe2026",
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="req-cont",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "no_mcp"],
    )

    assert prepared.continued is True
    assert prepared.task.task_id == task.task_id
    assert "Saved checkpoint" in prepared.message
    assert "audit done" in prepared.message
    assert "Do not repeat completed audit" in prepared.message
    assert "Reserve the last 5 iterations" in prepared.route.operational_context


def test_after_two_budget_exhaustions_continuation_escalates_without_model(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    state_dir.mkdir()
    store = TaskStateStore(state_dir / "state.db")
    store.create(
        task_id="deadbeef2026",
        platform="telegram",
        chat_id="1",
        session_key="old",
        title="Budget exhausted task",
        original_request="Исправь gateway regression",
        role="server_debug",
        toolsets=["terminal", "file", "no_mcp"],
        required_toolsets=["terminal"],
        requires_execution=True,
        status="incomplete",
        metadata={"budget_exhaustions": 2, "checkpoint": "tests still failing"},
    )

    prepared = prepare_task_turn(
        message="Продолжить deadbeef2026",
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="req-limit",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "no_mcp"],
    )

    assert prepared.early_response is not None
    assert prepared.early_response["model"] == "deterministic"
    assert prepared.early_response["final_response"].startswith("BLOCKED")
    assert "двух исчерпаний" in prepared.early_response["final_response"]



def test_calendar_reply_context_supplies_structured_event(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes").mkdir()
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: ("GOOGLE WORKSPACE SKILL", list(names), []),
    )

    prepared = prepare_task_turn(
        message='[Replying to: "1 июля 2026, 20:00 — консультация по Hermes в Zoom"]\n\nдобавь в календарь мне',
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="req-calendar-reply",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "memory", "no_mcp"],
        message_context={
            "current_text": "добавь в календарь мне",
            "current_message_id": 101,
            "chat_id": "1",
            "sender_id": "u1",
            "update_id": 5001,
            "reply_text": "1 июля 2026, 20:00 — консультация по Hermes в Zoom",
            "reply_message_id": 99,
            "reply_sender_id": "bot",
        },
    )

    assert prepared.early_response is None
    assert prepared.task is not None
    assert prepared.task.metadata["intent"] == "calendar_write"
    assert prepared.task.metadata["calendar_event_draft"] == {
        "date": "2026-07-01",
        "date_text": "1 июля 2026",
        "time": "20:00",
        "summary": "консультация по Hermes в Zoom",
    }
    assert "Structured calendar event from reply_text" in prepared.message
    assert "Summary: консультация по Hermes в Zoom" in prepared.message
    assert "google-workspace" in prepared.route.skill_names


def test_calendar_reply_caption_used_when_text_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes").mkdir()
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: ("GOOGLE WORKSPACE SKILL", list(names), []),
    )

    prepared = prepare_task_turn(
        message="поставь в календарь",
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="req-calendar-caption",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "memory", "no_mcp"],
        message_context={
            "current_text": "поставь в календарь",
            "sender_id": "u1",
            "update_id": 5002,
            "reply_caption": "1 июля 2026, 20:00 — консультация по Hermes в Zoom",
        },
    )

    assert prepared.early_response is None
    assert prepared.task.metadata["calendar_event_draft"]["summary"] == "консультация по Hermes в Zoom"


def test_calendar_wrapper_lines_not_used_as_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes").mkdir()
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: ("GOOGLE WORKSPACE SKILL", list(names), []),
    )

    prepared = prepare_task_turn(
        message="создай событие",
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="req-wrapper",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "memory", "no_mcp"],
        message_context={
            "current_text": "создай событие",
            "sender_id": "u1",
            "update_id": 5003,
            "reply_text": "Записал в формате события\n1 июля 2026, 20:00 — консультация по Hermes в Zoom\nЧасовой пояс не указан",
        },
    )

    summary = prepared.task.metadata["calendar_event_draft"]["summary"]
    assert summary == "консультация по Hermes в Zoom"
    assert "Записал" not in prepared.message
    assert "Часовой пояс" not in summary


def test_calendar_missing_reply_data_blocks_before_model(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes").mkdir()

    def fail_if_preload_runs(*args, **kwargs):
        raise AssertionError("calendar input preflight must block before skill preload")

    monkeypatch.setattr("agent.skill_commands.build_preloaded_skills_prompt", fail_if_preload_runs)
    prepared = prepare_task_turn(
        message="добавь в календарь мне",
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="req-calendar-missing",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "memory", "no_mcp"],
        message_context={"current_text": "добавь в календарь мне", "sender_id": "u1"},
    )

    assert prepared.early_response is not None
    assert prepared.early_response["final_response"].startswith("BLOCKED")
    assert "конкретная дата" in prepared.early_response["final_response"]
    assert "конкретное время" in prepared.early_response["final_response"]
    assert "назначение события" in prepared.early_response["final_response"]


def test_calendar_pending_continuation_requires_calendar_action_and_same_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    state_dir.mkdir()
    store = TaskStateStore(state_dir / "state.db")
    task = store.create(
        platform="telegram",
        chat_id="1",
        session_key="old",
        title="Calendar draft",
        original_request="добавь в календарь\n\nStructured calendar event from reply_text:\nDate: 2026-07-01\nTime: 20:00\nSummary: консультация по Hermes в Zoom",
        role="simple",
        toolsets=["file", "skills", "terminal"],
        status="blocked",
        metadata={
            "intent": "calendar_write",
            "sender_id": "u1",
            "calendar_event_draft": {"date": "2026-07-01", "time": "20:00", "summary": "консультация по Hermes в Zoom"},
        },
    )
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: ("GOOGLE WORKSPACE SKILL", list(names), []),
    )

    ordinary = prepare_task_turn(
        message="если я отвечаю тебе так на сообщение — ты видишь инфо по нему?",
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="req-ordinary",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "memory", "no_mcp"],
        message_context={"current_text": "если я отвечаю тебе так на сообщение — ты видишь инфо по нему?", "sender_id": "u1"},
    )
    assert ordinary.continued is False
    assert ordinary.task is None

    other_user = prepare_task_turn(
        message="и поставь в календарь",
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="req-other-user",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "memory", "no_mcp"],
        message_context={"current_text": "и поставь в календарь", "sender_id": "u2"},
    )
    assert other_user.continued is False

    same_user = prepare_task_turn(
        message="и поставь в календарь",
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="req-same-user",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "memory", "no_mcp"],
        message_context={"current_text": "и поставь в календарь", "sender_id": "u1"},
    )
    assert same_user.continued is False
    assert same_user.task.task_id != task.task_id
    assert same_user.task.metadata["calendar_draft_source_task_id"] == task.task_id
    assert "Structured calendar event from pending_task" in same_user.message


def test_calendar_duplicate_update_reuses_task(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes").mkdir()
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: ("GOOGLE WORKSPACE SKILL", list(names), []),
    )
    kwargs = dict(
        message="добавь в календарь мне",
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="fallback-req",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "memory", "no_mcp"],
        message_context={
            "current_text": "добавь в календарь мне",
            "sender_id": "u1",
            "update_id": 777,
            "reply_text": "1 июля 2026, 20:00 — консультация по Hermes в Zoom",
        },
    )

    first = prepare_task_turn(**kwargs)
    second = prepare_task_turn(**kwargs)

    assert first.task.task_id == second.task.task_id
    assert second.task.source_request_id == "telegram:1:u1:777:calendar_write"


def test_new_calendar_message_after_incomplete_task_creates_new_task_id(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    state_dir.mkdir()
    store = TaskStateStore(state_dir / "state.db")
    old = store.create(
        task_id="8ac73f70df8a",
        platform="telegram",
        chat_id="1",
        session_key="old",
        title="old calendar",
        original_request="добавь в календарь мне",
        role="simple",
        toolsets=["terminal", "file", "skills"],
        status="incomplete",
        metadata={
            "intent": "calendar_write",
            "sender_id": "u1",
            "current_message_id": "101",
            "platform_update_id": "5001",
            "calendar_event_draft": {
                "date": "2026-07-01",
                "date_text": "1 июля 2026",
                "time": "20:00",
                "summary": "консультация по Hermes в Zoom",
            },
            "calendar_evidence": {
                "calendar_id": "primary",
                "event_id": "stale-deleted-event",
                "summary": "консультация по Hermes в Zoom",
                "start": "2026-07-01T20:00:00+03:00",
                "end": "2026-07-01T21:00:00+03:00",
                "event_link": "https://calendar.google.com/event?stale",
                "read_back": True,
                "status": "created",
            },
        },
    )
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: ("GOOGLE WORKSPACE SKILL", list(names), []),
    )

    prepared = prepare_task_turn(
        message="добавь в календарь мне",
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="req-new-update",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "memory", "no_mcp"],
        message_context={
            "current_text": "добавь в календарь мне",
            "sender_id": "u1",
            "current_message_id": 202,
            "update_id": 5002,
        },
    )

    assert prepared.continued is False
    assert prepared.task.task_id != old.task_id
    assert prepared.task.metadata["calendar_draft_source_task_id"] == old.task_id
    assert prepared.task.metadata["calendar_event_draft"]["summary"] == "консультация по Hermes в Zoom"
    assert "calendar_evidence" not in prepared.task.metadata
    assert "Structured calendar event from pending_task" in prepared.message


def test_new_calendar_update_with_same_text_is_not_duplicate(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes").mkdir()
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: ("GOOGLE WORKSPACE SKILL", list(names), []),
    )
    base = dict(
        message="добавь в календарь мне",
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "memory", "no_mcp"],
    )
    first = prepare_task_turn(
        **base,
        request_id="req-1",
        message_context={
            "current_text": "добавь в календарь мне",
            "sender_id": "u1",
            "update_id": 777,
            "reply_text": "1 июля 2026, 20:00 — консультация по Hermes в Zoom",
        },
    )
    second = prepare_task_turn(
        **base,
        request_id="req-2",
        message_context={
            "current_text": "добавь в календарь мне",
            "sender_id": "u1",
            "update_id": 778,
            "reply_text": "1 июля 2026, 20:00 — консультация по Hermes в Zoom",
        },
    )

    assert first.task.task_id != second.task.task_id
    assert first.task.source_request_id == "telegram:1:u1:777:calendar_write"
    assert second.task.source_request_id == "telegram:1:u1:778:calendar_write"


def test_stale_event_id_without_readback_does_not_complete_calendar_task():
    evidence = _calendar_evidence()
    evidence.update({"read_back": False, "status": "not_found", "read_back_error": "404 notFound"})

    missing = calendar_completion_evidence_missing(
        "добавь в календарь мне",
        "Событие уже создано. ID: stale-deleted-event",
        route_skills=["google-workspace"],
        metadata={"execution_contract": {"type": "calendar_write"}, "calendar_evidence": evidence},
    )

    assert "подтверждение read-back" in missing
    assert "status=created" in missing


def test_after_stale_invalidation_new_update_can_create_again(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    state_dir.mkdir()
    store = TaskStateStore(state_dir / "state.db")
    store.create(
        task_id="oldcalendar01",
        platform="telegram",
        chat_id="1",
        session_key="old",
        title="old calendar",
        original_request="добавь в календарь мне",
        role="simple",
        toolsets=["terminal", "file", "skills"],
        status="incomplete",
        metadata={
            "intent": "calendar_write",
            "sender_id": "u1",
            "calendar_event_draft": {"date": "2026-07-01", "time": "20:00", "summary": "консультация по Hermes в Zoom"},
            "calendar_evidence": {"event_id": "stale", "read_back": False, "status": "not_found"},
        },
    )
    store.update("oldcalendar01", last_error="missing calendar evidence: calendar ID,подтверждение read-back")
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: ("GOOGLE WORKSPACE SKILL", list(names), []),
    )

    prepared = prepare_task_turn(
        message="добавь в календарь мне",
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="req-fresh-create",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "memory", "no_mcp"],
        message_context={"current_text": "добавь в календарь мне", "sender_id": "u1", "update_id": 9002},
    )

    assert prepared.continued is False
    assert prepared.early_response is None
    assert prepared.task.task_id != "oldcalendar01"
    assert "calendar_evidence" not in prepared.task.metadata
    assert "terminal" in prepared.route.toolsets


def test_calendar_summary_preserves_full_zoom_text_from_reply(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes").mkdir()
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: ("GOOGLE WORKSPACE SKILL", list(names), []),
    )

    prepared = prepare_task_turn(
        message="добавь в календарь мне",
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="req-full-summary",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "memory", "no_mcp"],
        message_context={
            "current_text": "добавь в календарь мне",
            "sender_id": "u1",
            "update_id": 9010,
            "reply_text": "1 июля 2026, 20:00 — консультация по Hermes в Zoom",
        },
    )

    assert prepared.task.metadata["calendar_event_draft"]["summary"] == "консультация по Hermes в Zoom"
    assert "Summary: консультация по Hermes в Zoom" in prepared.message


def test_new_calendar_create_uses_runtime_callback_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes").mkdir()
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: ("GOOGLE WORKSPACE SKILL", list(names), []),
    )
    prepared = prepare_task_turn(
        message="добавь в календарь мне",
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="req-callback",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "memory", "no_mcp"],
        message_context={
            "current_text": "добавь в календарь мне",
            "sender_id": "u1",
            "update_id": 9020,
            "reply_text": "1 июля 2026, 20:00 — консультация по Hermes в Zoom",
        },
    )
    evidence = _calendar_evidence()

    saved = persist_calendar_evidence_from_tool_result(TaskStateStore(tmp_path / ".hermes" / "state.db"), prepared.task.task_id, json.dumps(evidence))

    assert saved == evidence
    metadata = TaskStateStore(tmp_path / ".hermes" / "state.db").get(prepared.task.task_id).metadata
    assert metadata["calendar_evidence"] == evidence
    assert calendar_completion_evidence_missing(
        prepared.task.original_request,
        "Готово",
        route_skills=["google-workspace"],
        metadata=metadata,
    ) == ()


def test_calendar_tool_error_does_not_count_as_successful_write():
    missing = calendar_completion_evidence_missing(
        "Создай событие в календаре завтра в 18:30: созвон с Иваном",
        "READY\nСобытие создано",
        route_toolsets=["google-calendar"],
        route_skills=["google-workspace"],
        tool_calls=[{"name": "google_calendar_create_event", "success": False, "error": "quota"}],
    )
    assert "event ID или штатная ссылка" in missing


def test_calendar_successful_tool_still_needs_readback_evidence():
    missing = calendar_completion_evidence_missing(
        "Создай событие в календаре завтра в 18:30: созвон с Иваном",
        "READY\nEvent ID: abc\nCalendar ID: primary\nSummary: созвон с Иваном\nStart: 2026-07-01T18:30\nEnd: 2026-07-01T19:30",
        route_toolsets=[],
        route_skills=[],
        tool_calls=[{"name": "google_calendar_create_event", "success": True}],
    )
    assert missing == ("подтверждение read-back",)


def test_calendar_old_numeric_date_still_passes_preflight(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes").mkdir()
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: ("GOOGLE WORKSPACE SKILL", list(names), []),
    )
    prepared = prepare_task_turn(
        message="создай событие в календаре 01.07.2026 в 20:00 консультация по Hermes",
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="req-old-date",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "memory", "no_mcp"],
    )
    assert prepared.early_response is None



def test_calendar_readback_evidence_allows_completion():
    assert calendar_completion_evidence_missing(
        "Создай событие в календаре завтра в 18:30: созвон с Иваном",
        '{"status":"created","calendar_id":"primary","event_id":"evt-1","summary":"созвон с Иваном","start":"2026-07-01T18:30:00+03:00","end":"2026-07-01T19:00:00+03:00","event_link":"https://calendar.google.com/event?evt-1","read_back":true}',
        route_skills=["google-workspace"],
        metadata={"execution_contract": {"type": "calendar_write"}},
    ) == ()


def test_calendar_readback_false_does_not_allow_completion():
    missing = calendar_completion_evidence_missing(
        "Создай событие в календаре завтра в 18:30: созвон с Иваном",
        '{"status":"incomplete","calendar_id":"primary","event_id":"evt-1","summary":"созвон с Иваном","event_link":"https://calendar.google.com/event?evt-1","read_back":false}',
        route_skills=["google-workspace"],
        metadata={"execution_contract": {"type": "calendar_write"}},
    )
    assert "start/начало" in missing
    assert "end/окончание" in missing
    assert "подтверждение read-back" in missing



def _calendar_evidence():
    return {
        "calendar_id": "primary",
        "event_id": "evt-1",
        "summary": "консультация по Hermes в Zoom",
        "start": "2026-07-01T20:00:00+03:00",
        "end": "2026-07-01T21:00:00+03:00",
        "event_link": "https://calendar.google.com/event?evt-1",
        "read_back": True,
        "status": "created",
    }


def test_calendar_tool_result_extracts_structured_evidence():
    evidence = _calendar_evidence()
    result = {
        "messages": [
            {"role": "assistant", "content": None, "tool_calls": [{"id": "tc1", "function": {"name": "terminal"}}]},
            {"role": "tool", "tool_call_id": "tc1", "content": json.dumps(evidence)},
        ]
    }
    assert extract_calendar_evidence_from_result(result) == evidence


def test_calendar_metadata_evidence_allows_ready_without_final_text():
    evidence = _calendar_evidence()
    assert calendar_evidence_complete(evidence) is True
    assert calendar_completion_evidence_missing(
        "добавь в календарь мне",
        "Готово, создал событие.",
        route_skills=["google-workspace"],
        metadata={"execution_contract": {"type": "calendar_write"}, "calendar_evidence": evidence},
    ) == ()


def test_calendar_metadata_missing_readback_blocks_ready():
    evidence = _calendar_evidence()
    evidence["read_back"] = False
    evidence["status"] = "incomplete"
    missing = calendar_completion_evidence_missing(
        "добавь в календарь мне",
        "READY\nСоздано",
        route_skills=["google-workspace"],
        metadata={"execution_contract": {"type": "calendar_write"}, "calendar_evidence": evidence},
    )
    assert "подтверждение read-back" in missing
    assert "status=created" in missing


def test_calendar_existing_event_id_result_uses_readback_not_create():
    evidence = _calendar_evidence()
    result = {"messages": [{"role": "tool", "content": json.dumps(evidence)}]}
    extracted = extract_calendar_evidence_from_result(result)
    assert extracted["event_id"] == "evt-1"
    assert extracted["read_back"] is True
    assert calendar_evidence_complete(extracted) is True


def test_non_calendar_tool_result_not_calendar_evidence():
    result = {
        "messages": [
            {"role": "tool", "content": json.dumps({"status": "created", "id": "file-1", "webViewLink": "https://drive"})},
        ]
    }
    assert extract_calendar_evidence_from_result(result) is None


def test_runtime_tool_result_persists_calendar_evidence_for_finalizer(tmp_path):
    store = _store(tmp_path)
    task = store.create(
        platform="telegram",
        chat_id="1",
        session_key="s",
        title="добавь в календарь мне",
        original_request="добавь в календарь мне",
        role="productivity",
        toolsets=["terminal", "skills"],
        required_toolsets=["skills"],
        requires_execution=True,
        metadata={"execution_contract": {"type": "calendar_write"}},
    )
    evidence = _calendar_evidence()
    raw_result = "calendar tool stdout\n" + json.dumps(evidence, ensure_ascii=False)

    assert extract_calendar_evidence_from_tool_result(raw_result) == evidence
    assert persist_calendar_evidence_from_tool_result(store, task.task_id, raw_result) == evidence

    saved = store.get(task.task_id).metadata
    assert saved["calendar_evidence"] == evidence
    assert calendar_completion_evidence_missing(
        task.original_request,
        "Готово, событие создано.",
        route_skills=["google-workspace"],
        metadata=saved,
    ) == ()


def test_runtime_terminal_wrapper_persists_calendar_evidence_for_finalizer(tmp_path):
    store = _store(tmp_path)
    task = store.create(
        platform="telegram",
        chat_id="1",
        session_key="s",
        title="добавь в календарь мне",
        original_request="добавь в календарь мне",
        role="productivity",
        toolsets=["terminal", "skills"],
        required_toolsets=["skills"],
        requires_execution=True,
        metadata={"execution_contract": {"type": "calendar_write"}},
    )
    evidence = _calendar_evidence()
    terminal_result = json.dumps(
        {
            "output": "google_api calendar create output\n" + json.dumps(evidence, ensure_ascii=False, indent=2),
            "exit_code": 0,
            "error": None,
        },
        ensure_ascii=False,
    )

    assert extract_calendar_evidence_from_tool_result(terminal_result) == evidence
    assert persist_calendar_evidence_from_tool_result(store, task.task_id, terminal_result) == evidence
    saved = store.get(task.task_id).metadata
    assert saved["calendar_evidence"] == evidence
    assert calendar_completion_evidence_missing(
        task.original_request,
        "Готово, событие создано.",
        route_skills=["google-workspace"],
        metadata=saved,
    ) == ()


def test_runtime_tool_result_ignores_non_calendar_payload(tmp_path):
    store = _store(tmp_path)
    task = store.create(
        platform="telegram",
        chat_id="1",
        session_key="s",
        title="drive upload",
        original_request="загрузи файл",
        role="productivity",
        toolsets=["drive"],
        metadata={},
    )
    payload = json.dumps({"status": "created", "id": "file-1", "webViewLink": "https://drive"})

    assert extract_calendar_evidence_from_tool_result(payload) is None
    assert persist_calendar_evidence_from_tool_result(store, task.task_id, payload) is None
    assert "calendar_evidence" not in store.get(task.task_id).metadata



def test_execution_classifier_miss_uses_universal_safe_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes").mkdir()

    from gateway.task_router import TaskRoute

    monkeypatch.setattr(
        "gateway.task_runtime.route_turn",
        lambda *args, **kwargs: TaskRoute(
            role="simple",
            reason="synthetic unknown action",
            toolsets=["no_mcp"],
            max_iterations=12,
        ),
    )
    prepared = prepare_task_turn(
        message="Скачай ролик",
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="req-universal-safe-fallback",
        user_config={"agent": {}},
        platform_toolsets=["clarify", "skills", "file", "web", "terminal", "no_mcp"],
    )
    assert prepared.early_response is None
    assert prepared.route is not None
    assert "universal_safe_action_fallback" in prepared.route.reason
    assert prepared.route.toolsets == ["clarify", "file", "skills", "terminal", "web"]
