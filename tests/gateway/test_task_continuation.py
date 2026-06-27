import time

from gateway.task_continuation import (
    TaskStateStore,
    format_choice,
    infer_execution_contract,
    is_pause_request,
    is_progress_only,
)
from gateway.task_router import route_turn
from gateway.task_runtime import prepare_task_turn
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
