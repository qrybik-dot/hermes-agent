from gateway.task_continuation import TaskStateStore
from gateway.task_router import route_turn


def _route(text: str):
    return route_turn(
        text,
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=[
            "terminal",
            "skills",
            "web",
            "browser",
            "file",
            "clarify",
            "no_mcp",
        ],
    )


def test_live_travel_program_today_routes_to_research_with_browser():
    route = _route(
        "Найди программу развлечений сегодня в парке Сокольники и ВДНХ. "
        "И лучшие парковки около центрального входа Сокольников и входов на ВДНХ."
    )

    assert route.role == "research"
    assert "browser" in route.toolsets
    assert "web" in route.toolsets
    assert "no_mcp" not in route.toolsets
    assert "city-travel-concierge" in route.skill_names


def test_elliptical_parking_refinement_routes_to_live_travel_tools():
    route = _route(
        "около парка есть ближе бесплатно подготовка или самый дешевый вариант"
    )

    assert route.role == "research"
    assert "browser" in route.toolsets
    assert "web" in route.toolsets
    assert "no_mcp" not in route.toolsets


def test_one_result_resumes_recent_iteration_exhausted_task(tmp_path):
    store = TaskStateStore(tmp_path / "state.db")
    store.create(
        platform="telegram",
        chat_id="1",
        session_key="old",
        title="Older unfinished task",
        original_request="Проверь Git",
        role="server_debug",
        toolsets=["terminal"],
        requires_execution=True,
        status="paused",
    )
    task = store.create(
        platform="telegram",
        chat_id="1",
        session_key="s",
        title="Live travel search",
        original_request="Найди программу развлечений сегодня в Сокольниках и парковку",
        role="research",
        toolsets=["browser", "web", "skills"],
        required_toolsets=[],
        requires_execution=True,
        status="incomplete",
    )
    store.update(task.task_id, last_error="iteration_budget_exhausted")

    decision = store.resolve("Один результат", "telegram", "1")

    assert decision.kind == "selected"
    assert decision.task is not None
    assert decision.task.task_id == task.task_id


def test_one_result_does_not_hijack_unrelated_active_task(tmp_path):
    store = TaskStateStore(tmp_path / "state.db")
    store.create(
        platform="telegram",
        chat_id="1",
        session_key="s",
        title="Unrelated paused task",
        original_request="Проверь Git",
        role="server_debug",
        toolsets=["terminal"],
        required_toolsets=["terminal"],
        requires_execution=True,
        status="paused",
    )

    assert store.resolve("Один результат", "telegram", "1").kind == "none"
