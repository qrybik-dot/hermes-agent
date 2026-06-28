from pathlib import Path

from gateway.task_router import adaptive_planning_policy, classify_task, route_turn, select_toolsets


ALL_ALLOWED = [
    "browser",
    "clarify",
    "code_execution",
    "context7",
    "delegation",
    "file",
    "granola",
    "memory",
    "notebooklm",
    "session_search",
    "skills",
    "terminal",
    "todo",
    "tutu",
    "web",
]


def test_role_selection_matrix():
    cases = [
        ("Ответь одним предложением: сколько будет 17 + 25?", None, "simple"),
        ("Найди свежую информацию о Python по источникам", None, "research"),
        ("Исправь Python-код и добавь тест", None, "coding"),
        ("Покажи статус systemd hermes-gateway на VPS", None, "server_debug"),
        ("Сделай архитектурный план внедрения", None, "planning"),
        ("Делаем дальше как большую задачу, разложи на этапы через планирование и понятные шаги", None, "planning"),
        ("/memory_search поиск работы", "memory_search", "no_llm"),
        ("Сделай нормальный отчёт об изменениях на русском", None, "simple"),
    ]
    for text, command, expected in cases:
        role, _ = classify_task(text, command=command)
        assert role == expected


def test_memory_continuation_gets_memory_and_session_search():
    for text in (
        "Что ты помнишь о моём поиске работы?",
        "Что мы делали вчера?",
        "Что обсуждали в прошлый раз по моему проекту?",
    ):
        route = route_turn(
            text,
            command=None,
            platform_key="telegram",
            user_config={"agent": {}},
            platform_toolsets=ALL_ALLOWED,
        )
        assert route.role == "simple"
        assert "memory" in route.toolsets
        assert "session_search" in route.toolsets


def test_notebooklm_research_routes_to_notebooklm_tools_and_contract():
    route = route_turn(
        "В NotebookLM сделай презентацию, подкаст и инфографику. Перед этим самостоятельно найди "
        "не менее 30 источников, не старше, чем 2 месяца.",
        command=None,
        platform_key="telegram",
        user_config={"agent": {"role_max_turns": {"research": 24}}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert route.role == "research"
    assert "web" in route.toolsets
    assert "file" in route.toolsets
    assert "terminal" in route.toolsets
    assert "notebooklm" in route.toolsets
    assert "no_mcp" not in route.toolsets
    assert route.max_iterations >= 36
    assert "stat -c" in route.operational_context
    assert "helper-скрипты" in route.operational_context
    assert "URL и точную дату" in route.operational_context
    assert "READY допустим только" in route.operational_context


def test_email_calendar_and_granola_are_not_confused():
    email = route_turn(
        "Найди письмо от Granola и верни тему и дату",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert "terminal" in email.toolsets
    assert "skills" in email.toolsets
    assert "granola" not in email.toolsets

    calendar = route_turn(
        "Какие у меня встречи сегодня в календаре?",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert "terminal" in calendar.toolsets
    assert "skills" in calendar.toolsets
    assert "granola" not in calendar.toolsets
    assert calendar.role == "simple"

    granola = route_turn(
        "Какие встречи сохранены в Granola?",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert "granola" in granola.toolsets
    assert "terminal" not in granola.toolsets


def test_reporting_is_not_misclassified_as_code_change():
    route = route_turn(
        "Сделай нормальный подробный отчёт об изменениях на русском языке",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert route.role == "simple"
    assert "file" in route.toolsets
    assert "memory" in route.toolsets
    assert "terminal" not in route.toolsets


def test_platform_allowlist_bounds_tools_and_cannot_reenable_browser():
    route = route_turn(
        "Исправь Python-код и добавь тест",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=["clarify", "file", "terminal"],
    )
    assert route.toolsets == ["clarify", "file", "no_mcp", "terminal"]
    assert "browser" not in route.toolsets
    assert "code_execution" not in route.toolsets


def test_telegram_uses_compact_personality_and_role_safety_context():
    simple = route_turn(
        "Привет",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert simple.skip_context_files is True
    assert "Отвечай по-русски" in simple.operational_context
    assert "проверяй риски" in simple.operational_context
    assert "сохранённую память" in simple.operational_context
    assert "clarify" not in simple.toolsets

    debug = route_turn(
        "Проверь systemd на VPS",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert "backup-first" in debug.operational_context
    assert "git reset" in debug.operational_context
    assert "progress-only" in debug.operational_context
    assert "gateway" in debug.operational_context
    assert "git diff/status" in debug.operational_context
    assert "не проверено" in debug.operational_context


def test_gateway_run_sync_scoping_regression():
    source = Path("gateway/run.py").read_text(encoding="utf-8")
    marker = "platform_allowed_toolsets = list(enabled_toolsets or [])"
    run_sync = source.index("        def run_sync():", source.index("async def _run_agent("))
    assert source.rfind(marker, 0, run_sync) != -1
    segment = source[run_sync : source.index("            _executor_task = asyncio.ensure_future", run_sync)]
    init = segment.index("routed_toolsets = list(platform_allowed_toolsets)")
    route_call = segment.index("platform_toolsets=platform_allowed_toolsets")
    assert init < route_call
    assert "platform_toolsets=enabled_toolsets" not in segment
    assert "enabled_toolsets = _task_route.toolsets" not in segment
    assert "agent_cache_status" in source
    assert "agent_prepare_ms" in source
    assert "gateway_overhead_ms" in source
    assert "_emit_task_status(0, \"подготовка\")" in source
    assert "task:{_active_task.task_id}" in source


def test_google_workspace_intents_preload_google_workspace_skill():
    cases = [
        "Найди письмо в Gmail от Granola",
        "Какие у меня встречи сегодня в календаре?",
        "Найди файл в Google Drive про отчёт",
    ]
    for text in cases:
        route = route_turn(
            text,
            command=None,
            platform_key="telegram",
            user_config={"agent": {}},
            platform_toolsets=ALL_ALLOWED,
        )
        assert route.skill_names == ("google-workspace",)
        assert "skills" in route.toolsets
        assert "terminal" in route.toolsets


def test_google_drive_intent_does_not_enable_granola():
    route = route_turn(
        "Найди файл в Google Drive с заметкой Granola",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert route.skill_names == ("google-workspace",)
    assert "granola" not in route.toolsets


def test_simple_route_does_not_preload_skill():
    route = route_turn(
        "Привет, ответь коротко",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert route.skill_names == ()



def test_skill_recommendation_requires_factual_tools():
    route = route_turn(
        "Какие навыки еще будут полезны для меня? Сделай подборку",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert "skills" in route.toolsets
    assert "terminal" in route.toolsets
    assert "file" in route.toolsets
    assert "no_mcp" not in route.toolsets
    assert "обязательно проверь фактический список" in route.operational_context
    assert "Не выдумывай команды установки" in route.operational_context


def _adaptive_route(text: str):
    return route_turn(
        text,
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )


def test_adaptive_planning_six_scenarios():
    simple = _adaptive_route("Почему небо голубое?")
    assert adaptive_planning_policy("Почему небо голубое?", simple.role).mode == "none"
    assert "plan" not in simple.skill_names
    assert "delegation" not in simple.toolsets

    one_command = _adaptive_route("Исправь опечатку в одном Python-файле")
    assert one_command.role == "coding"
    assert "terminal" in one_command.toolsets
    assert "plan" not in one_command.skill_names
    assert "delegation" not in one_command.toolsets

    medium = _adaptive_route(
        "Исправь обработку ошибок в Python-модуле, добавь тест и проверь обратную совместимость"
    )
    assert medium.role == "coding"
    assert "plan" not in medium.skill_names
    assert "delegation" not in medium.toolsets
    assert "короткий внутренний план" in medium.operational_context

    large = _adaptive_route(
        "Реализуй крупную многоэтапную доработку task router: обнови классификацию, "
        "добавь тесты, сохрани rollback и проверь Telegram UX"
    )
    assert large.role in {"coding", "server_debug"}
    assert large.skill_names.count("plan") == 1
    assert "delegation" in large.toolsets
    assert "ровно одного reviewer" in large.operational_context
    assert large.max_iterations >= 36

    production_auth = _adaptive_route(
        "Настрой production-авторизацию gateway, обнови права доступа и подготовь rollback"
    )
    assert production_auth.skill_names.count("plan") == 1
    assert "delegation" in production_auth.toolsets
    assert "backup-first" in production_auth.operational_context

    approved = _adaptive_route(
        "Реализуй уже согласованный план изменения Python-модуля и добавь тест"
    )
    assert approved.role == "coding"
    assert "terminal" in approved.toolsets
    assert "plan" not in approved.skill_names
    assert "delegation" not in approved.toolsets


def test_explicit_jtbd_dod_uses_plan_without_reviewer_for_small_task():
    route = _adaptive_route("Составь JTBD и DoD для небольшой обратимой правки документации")
    assert route.skill_names.count("plan") == 1
    assert "delegation" not in route.toolsets
    assert "3–7 пунктов" in route.operational_context


def test_status_request_does_not_trigger_adaptive_planner():
    route = _adaptive_route("Покажи статус systemd hermes-gateway на VPS")
    assert route.role == "server_debug"
    assert "plan" not in route.skill_names
    assert "delegation" not in route.toolsets


def test_adaptive_planner_install_request_routes_to_execution_tools():
    route = _adaptive_route(
        "Настрой адаптивное планирование крупных задач. Найди task router, расширь существующий "
        "skill, сделай backup, проверь production/авторизацию и rollback. Проверь минимум 6 сценариев."
    )
    assert route.role in {"coding", "server_debug"}
    assert "terminal" in route.toolsets
    assert "file" in route.toolsets
    assert route.skill_names.count("plan") == 1
    assert "delegation" in route.toolsets
