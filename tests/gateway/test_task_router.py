from pathlib import Path

from gateway.task_router import classify_task, route_turn, select_toolsets


ALL_ALLOWED = [
    "browser",
    "clarify",
    "code_execution",
    "context7",
    "file",
    "granola",
    "memory",
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
    assert "⏳ В работе:" in debug.operational_context
    assert "[░░░░░░░░░░] 0%" in debug.operational_context
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
    assert "_emit_task_status(0, \"подготовка плана\")" in source
    assert '"key": f"task:{session_key}:{run_generation}"' in source
