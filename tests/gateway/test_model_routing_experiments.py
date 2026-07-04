from gateway.task_router import route_turn

ALL_ALLOWED = [
    "browser", "clarify", "code_execution", "context7", "delegation", "file",
    "granola", "memory", "notebooklm", "session_search", "skills", "terminal",
    "todo", "tutu", "web",
]


def _route(text: str, **experiments):
    return route_turn(
        text,
        command=None,
        platform_key="telegram",
        user_config={"agent": {}, "routing_experiments": experiments},
        platform_toolsets=ALL_ALLOWED,
    )


def test_experiments_are_off_by_default():
    route = _route("Найди два файла, прочитай их, затем собери итог и сохрани результат")
    assert route.role == "simple"
    text = "Извлеки ключевые факты и даты из большого документа\n" + ("данные " * 9000)
    assert _route(text).role == "long_context"


def test_safe_multi_step_request_routes_to_agentic_when_enabled():
    route = _route(
        "Найди два файла, прочитай их, затем сравни данные и подготовь итог",
        agentic_enabled=True,
    )
    assert route.role == "agentic"
    assert "file" in route.toolsets
    assert "web" in route.toolsets
    assert "terminal" not in route.toolsets
    assert route.max_iterations == 16
    assert "только чтение" in route.operational_context


def test_agentic_does_not_steal_existing_specialized_flows():
    cases = [
        ("Проверь systemd на VPS, затем исправь сервис", "server_debug"),
        ("Исправь Python-код и затем добавь тест", "coding"),
        ("Найди свежие источники и сравни факты", "research"),
        ("Сделай экспертную прожарку и выбери лучший вариант", "expert_analysis"),
        ("Составь план внедрения и затем подготовь rollback", "planning"),
    ]
    for text, expected in cases:
        assert _route(text, agentic_enabled=True).role == expected


def test_agentic_does_not_steal_travel_or_calendar():
    travel = _route(
        "Найди парковку около ВДНХ, затем пришли ссылку на Яндекс Карты",
        agentic_enabled=True,
    )
    assert travel.role == "simple"
    assert travel.skill_names == ("city-travel-concierge",)

    calendar = _route(
        "Проверь календарь на завтра, затем подготовь краткий список встреч",
        agentic_enabled=True,
    )
    assert calendar.role == "simple"
    assert calendar.skill_names == ("google-workspace",)


def test_long_context_extract_split_is_narrow_and_optional():
    text = "Извлеки ключевые факты, даты и версии из большого документа\n" + ("строка данных " * 5000)
    route = _route(text, long_context_split=True)
    assert route.role == "long_context_extract"
    assert route.max_iterations == 12
    assert "terminal" not in route.toolsets
    assert "delegation" not in route.toolsets
    assert "plan" not in route.skill_names
    assert "adaptive_plan" not in route.reason
    assert "точные факты" in route.operational_context

    deep = "Сделай глубокий анализ большого документа, оцени риски и альтернативы\n" + ("строка данных " * 5000)
    assert _route(deep, long_context_split=True).role == "long_context"


def test_explicit_experimental_roles_are_available_for_manual_pilot():
    assert _route("/role agentic Найди и прочитай файлы").role == "agentic"
    assert _route("/role long_context_extract Извлеки факты").role == "long_context_extract"
