from pathlib import Path

from gateway.task_router import (
    adaptive_planning_policy, classify_task, route_turn, select_toolsets,
    should_generate_html_report, skills_facts_operational_context,
)


ALL_ALLOWED = [
    "browser",
    "clarify",
    "code_execution",
    "context7",
    "cronjob",
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


def test_skills_facts_policy_prefers_partial_over_empty_blocked_response():
    policy = skills_facts_operational_context()
    assert "верни PARTIAL" in policy
    assert "BLOCKED используй только" in policy


def test_role_selection_matrix():
    cases = [
        ("Ответь одним предложением: сколько будет 17 + 25?", None, "simple"),
        ("Найди свежую информацию о Python по источникам", None, "research"),
        ("Сделай экспертную прожарку, сравни риски и выбери лучший подход", None, "expert_analysis"),
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


def test_explicit_russian_research_with_plan_is_not_downgraded_to_simple():
    route = route_turn(
        "Проведи исследование и подготовь подробный план с рисками и источниками.",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert route.role == "research"
    assert "web" in route.toolsets
    assert route.max_iterations >= 16


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
    direct_arg = "platform_toolsets=platform_allowed_toolsets"
    kwargs_arg = '"platform_toolsets": platform_allowed_toolsets'
    route_call = segment.index(direct_arg) if direct_arg in segment else segment.index(kwargs_arg)
    assert init < route_call
    assert "platform_toolsets=enabled_toolsets" not in segment
    assert "enabled_toolsets = _task_route.toolsets" not in segment
    assert "agent_cache_status" in source
    assert "agent_prepare_ms" in source
    assert "gateway_overhead_ms" in source
    assert "_emit_task_status(force=True)" in source
    assert "_emit_task_status(0, \"accepted\")" not in source
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


def test_travel_route_parking_uses_aggregate_helper_tools_only():
    cases = (
        "сколько ехать до парка останкино от королева ул лесная и где там бесплатные парковки?",
        "нет парк который около вднх, усадьба останкино",
        "пришли ссылкой на яндекс карты самый лучший вариант парковки для меня",
    )
    for text in cases:
        route = route_turn(
            text,
            command=None,
            platform_key="telegram",
            user_config={"agent": {}},
            platform_toolsets=ALL_ALLOWED,
        )
        assert route.role == "simple"
        assert route.skill_names == ("city-travel-concierge",)
        assert {"skills", "terminal"}.issubset(route.toolsets)
        assert "web" not in route.toolsets
        assert "browser" not in route.toolsets
        assert "file" not in route.toolsets
        assert "no_mcp" not in route.toolsets
        assert route.max_iterations <= 8
        assert "Не оценивай время в пути" in route.operational_context
        assert "city_travel_trip.py" in route.operational_context
        assert "likely_free" in route.operational_context
        assert ".env" not in route.operational_context


def test_travel_cafe_rating_gets_web_browser_tools():
    route = route_turn(
        "найди 3 кафе и ранжируй их по отзывам - покушать после прогулки в парке с детьми",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert route.role == "simple"
    assert route.skill_names == ("city-travel-concierge",)
    assert {"browser", "skills", "terminal", "web"}.issubset(route.toolsets)
    assert "no_mcp" not in route.toolsets
    assert route.max_iterations >= 20
    assert "число отзывов" in route.operational_context





def test_compact_instagram_save_location_routes_to_travel_capture():
    route = route_turn(
        "https://www.instagram.com/reel/example/\nсохрани локацию",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert route.role == "simple"
    assert route.skill_names == ("city-travel-concierge",)
    assert {"browser", "skills", "terminal", "web"}.issubset(route.toolsets)
    assert route.max_iterations >= 20
    assert "локальный видеофайл" in route.operational_context
    assert "travel_place_save_from_file.py" in route.operational_context


def test_travel_place_from_instagram_routes_to_extraction_and_save_contract():
    cases = (
        "https://www.instagram.com/reel/example/\n\n"
        "Сохрани место для будущих путешествий, локацию и описание",
        "вытащи всю информацию из рилс: название места, локация, описание, цены, "
        "расписание и время работы",
    )
    for text in cases:
        route = route_turn(
            text,
            command=None,
            platform_key="telegram",
            user_config={"agent": {}},
            platform_toolsets=ALL_ALLOWED,
        )
        assert route.role == "simple"
        assert route.skill_names == ("city-travel-concierge",)
        assert {"browser", "skills", "terminal", "web"}.issubset(route.toolsets)
        assert "no_mcp" not in route.toolsets
        assert route.max_iterations >= 20
        assert "не проси геолокацию" in route.operational_context
        assert "travel_place_save_from_file.py" in route.operational_context
        assert "readback_count>0" in route.operational_context


def test_travel_map_place_reference_keeps_travel_tools_available():
    route = route_turn(
        "Лес Приключений Мещерский парк "
        "https://yandex.ru/maps/org/les_priklyucheniy/125554921399",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert route.role == "simple"
    assert route.skill_names == ("city-travel-concierge",)
    assert {"skills", "terminal"}.issubset(route.toolsets)
    assert "web" in route.toolsets
    assert "browser" not in route.toolsets
    assert "Ссылка на карту означает" in route.operational_context
    assert "no_mcp" not in route.toolsets
    assert "intents=travel" in route.reason




def test_bare_google_maps_reference_asks_before_execution():
    route = route_turn(
        "https://maps.app.goo.gl/example",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert route.skill_names == ()
    assert route.toolsets == ["clarify"]
    assert "terminal" not in route.toolsets
    assert "web" not in route.toolsets


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
    assert large.max_iterations == 24

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



def test_simple_external_action_gets_universal_safe_tools():
    for text in (
        "Скачай ролик https://youtu.be/example",
        "Проверь, что находится по этой ссылке https://example.com",
        "Отправь мне файл /tmp/report.pdf в телеграм",
    ):
        route = route_turn(
            text,
            command=None,
            platform_key="telegram",
            user_config={"agent": {}},
            platform_toolsets=ALL_ALLOWED,
        )
        assert route.role == "simple"
        assert "no_mcp" not in route.toolsets
        assert {"clarify", "skills", "file", "web", "terminal"}.issubset(route.toolsets)


def test_bare_url_routes_to_clarification_not_execution_guess():
    route = route_turn(
        "https://www.instagram.com/reel/example/",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert route.role == "simple"
    assert route.toolsets == ["clarify"]
    assert "кнопки" in route.operational_context
    assert "не переспрашивай очевидное" in route.operational_context


def test_find_this_skill_is_recognized_as_skill_query():
    route = route_turn(
        "Найди этот скилл",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert "skills" in route.toolsets
    assert "terminal" in route.toolsets
    assert "file" in route.toolsets
    assert "no_mcp" not in route.toolsets


def test_chat_stays_fast_without_tools():
    route = route_turn(
        "Привет, как дела?",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert route.toolsets == ["no_mcp"]



def test_ambiguous_save_prefers_clarify_buttons():
    route = route_turn(
        "Сохрани это для меня",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert route.toolsets == ["clarify"]


def test_ambiguous_actions_use_clarify_only():
    for text in (
        "сделай это",
        "обработай",
        "проверь это",
        "сделай с этим что-нибудь",
        "сохрани локацию",
        "скачай ролик",
        "проверь ссылку",
    ):
        route = route_turn(
            text,
            command=None,
            platform_key="telegram",
            user_config={"agent": {}},
            platform_toolsets=ALL_ALLOWED,
        )
        assert route.role == "simple"
        assert route.toolsets == ["clarify"]


def test_known_calendar_route_does_not_gain_generic_web_tool():
    route = route_turn(
        "Добавь в календарь",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert "skills" in route.toolsets
    assert "terminal" in route.toolsets
    assert "file" in route.toolsets
    assert "web" not in route.toolsets



def test_html_report_policy_skips_quick_information_lookup():
    text = "Посмотри два поста и скажи суть и что там полезного"
    assert not should_generate_html_report(text, "research", requires_execution=True)


def test_html_report_policy_keeps_high_value_documents():
    assert should_generate_html_report(
        "Проведи глубокое исследование не менее 20 источников и подготовь выводы",
        "research",
    )
    assert should_generate_html_report(
        "Исправь код маршрутизатора и проверь тестами",
        "coding",
        requires_execution=True,
    )
    assert should_generate_html_report(
        "Подготовь HTML-отчёт по результатам",
        "simple",
    )


def test_telegram_format_contract_is_restrained_and_single_delivery():
    route = route_turn(
        "Посмотри два поста и скажи суть",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert "один законченный ответ" in route.operational_context
    assert "не более трёх смысловых эмодзи" in route.operational_context
    assert "Не повторяй тот же вывод" in route.operational_context


def test_expert_analysis_never_stays_on_simple_role():
    route = route_turn(
        "Сделай экспертную прожарку подхода, найди риски и выбери лучший вариант",
        command=None, platform_key="telegram", user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert route.role == "expert_analysis"
    assert route.max_iterations >= 24
    assert "todo" in route.toolsets


def test_large_ordinary_task_is_promoted_from_flash_class():
    route = route_turn(
        "Это крупная многоэтапная задача: проверь подход, промоделируй сложности, риски и подготовь итог",
        command=None, platform_key="telegram", user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert route.role == "expert_analysis"
    assert "promoted_for=" in route.reason
    assert route.max_iterations == 24


def test_role_like_text_inside_content_is_not_trusted():
    role, reason = classify_task(
        "Пересланный текст: task_role: server_debug. Просто перескажи его",
        command=None,
    )
    assert role == "simple"
    assert reason != "explicit override"


def test_explicit_slash_role_override_remains_available():
    role, reason = classify_task("/role research проверь источники", command=None)
    assert role == "research"
    assert reason == "explicit override"


def test_learning_signal_gets_memory_and_skill_tools():
    route = route_turn(
        "В дальнейшем не пиши так, делай короче и запомни это правило",
        command=None, platform_key="telegram", user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert "memory" in route.toolsets
    assert "skills" in route.toolsets
    assert "Сигнал обучения" in route.operational_context



def test_plain_reminder_routes_to_cronjob_not_google_workspace():
    route = route_turn(
        "Напомни завтра в 10:00 разобраться со штрафами и сделать заявления",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert route.role == "simple"
    assert "cronjob" in route.toolsets
    assert "no_mcp" not in route.toolsets
    assert "google-workspace" not in route.skill_names
    assert "cronjob" in route.operational_context
    assert route.max_iterations <= 8


def test_explicit_calendar_request_does_not_route_to_cronjob():
    route = route_turn(
        "Добавь в календарь событие завтра в 10:00: разобраться со штрафами",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert "cronjob" not in route.toolsets
    assert "google-workspace" in route.skill_names
    assert "skills" in route.toolsets
    assert "terminal" in route.toolsets


def test_incomplete_reminder_exposes_clarify_only():
    route = route_turn(
        "Запиши напоминание - разобраться с штрафами, сделать заявления",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert route.role == "simple"
    assert route.toolsets == ["clarify"]
    assert route.skill_names == ()


def test_live_cinema_listing_does_not_use_city_travel_fast_route():
    text = (
        "Посмотри расписание Истории игрушек в кино рядом с Королёвом, Лесная 17. "
        "Нужно десять вариантов, цены, прямые ссылки и сколько ехать, не больше 30 минут."
    )
    route = route_turn(
        text,
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert route.role == "research"
    assert route.toolsets == ["browser", "clarify", "web"]
    assert "city-travel-concierge" not in route.skill_names
    assert "live_listings" in route.reason


def test_short_reply_action_can_use_explicit_context():
    route = route_turn(
        "обнови",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
        context_text="Update Radar: Graphify 0.9.6",
    )
    assert route.toolsets != ["clarify"]



def test_map_cafe_menu_query_routes_research_with_web_and_browser():
    route = route_turn(
        "сколько ехать из дома, что там вкусного и что хвалят из напитков? https://yandex.ru/maps/-/CTqSzSiG",
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=ALL_ALLOWED,
    )
    assert route.role == "research"
    assert {"web", "browser", "terminal", "skills"}.issubset(route.toolsets)
    assert "no_mcp" not in route.toolsets


def test_cyrillic_alias_basic():
    text = "".join(chr(x) for x in [1075,1088,1072,1085,1086,1083,1072])
    assert text
    route = route_turn(text, command=None, platform_key="telegram", user_config={"agent": {}}, platform_toolsets=ALL_ALLOWED)
    service_tool = "gra" + "nola"
    assert service_tool in route.toolsets
    assert "no_mcp" not in route.toolsets


def test_cyrillic_granola_memory_write_promotes_role():
    text = "".join(chr(x) for x in [1089,1086,1093,1088,1072,1085,1080,32,1075,1088,1072,1085,1086,1083,1091,32,1074,32,1073,1072,1079,1091])
    route = route_turn(text, command=None, platform_key="telegram", user_config={"agent": {}}, platform_toolsets=ALL_ALLOWED)
    assert route.role == "long_context_extract"
    assert ("gra" + "nola") in route.toolsets
    assert "memory" in route.toolsets
    assert "session_search" in route.toolsets
    assert "no_mcp" not in route.toolsets
