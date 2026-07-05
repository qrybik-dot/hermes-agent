import json
import time
from pathlib import Path
from types import SimpleNamespace

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
from gateway.quick_note_capture import detect_quick_note
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

def test_simple_travel_route_parking_uses_deterministic_trip_helper(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(state_dir))
    state_dir.mkdir()
    helper = state_dir / "skills" / "productivity" / "city-travel-concierge" / "scripts" / "city_travel_trip.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("# helper", encoding="utf-8")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "answer_ready": True,
                    "approximate_start": True,
                    "traffic_status": "not_available",
                    "answer_text": (
                        "Маршрут: примерно 35 мин, 28.1 км.\n"
                        "Старт без номера дома, точка приблизительная.\n"
                        "Geoapify не учитывает live traffic; проверьте пробки в Яндекс Картах перед выездом.\n"
                        "Яндекс Карты: https://yandex.ru/maps/?rtext=55.920000,37.820000~55.824000,37.614000\n"
                        "Есть кандидат со статусом likely_free, но бесплатность не подтверждена официально: Parking.\n"
                        "Перед парковкой проверьте знаки, разметку, шлагбаум и платную зону на месте."
                    ),
                    "checked_at": "2026-07-02T10:00:00Z",
                    "start_query": "Королёва, ул. Лесная",
                    "destination_query": "парк Останкино",
                    "resolved_start": {"title": "Королёв, Лесная", "coordinates": {"lat": 55.92, "lon": 37.82}},
                    "resolved_destination": {"title": "парк Останкино", "coordinates": {"lat": 55.824, "lon": 37.614}},
                    "coordinates": {"destination": {"lat": 55.824, "lon": 37.614}},
                    "route": {"deep_links": {"yandex_maps": "https://yandex.ru/maps/?rtext=55.920000,37.820000~55.824000,37.614000"}},
                    "parking_candidates": [
                        {"title": "Parking A", "coordinates": {"lat": 55.8241, "lon": 37.6141}, "distance_m": 120, "parking_status": "likely_free", "is_free": None, "eligible_for_recommendation": True, "deep_links": {"yandex_maps": "https://yandex.ru/maps/?pt=37.614100,55.824100&z=18&l=map"}, "parking_evidence": {"source": "osm", "fee": "no", "reason": "В OSM стоит fee=no, но знаки не проверены."}},
                        {"title": "Parking B", "coordinates": {"lat": 55.825, "lon": 37.615}, "distance_m": 260, "parking_status": "unverified", "eligible_for_recommendation": True, "deep_links": {"yandex_maps": "https://yandex.ru/maps/?pt=37.615000,55.825000&z=18&l=map"}, "parking_evidence": {"source": "osm", "reason": "Источник не подтверждает оплату."}},
                    ],
                    "parking_statuses": ["likely_free", "unverified"],
                    "degraded_sections": ["data.mos.ru"],
                    "provider_status": [{"provider": "geoapify", "success": True}],
                }
            ),
        )

    monkeypatch.setattr("gateway.task_runtime.subprocess.run", fake_run)
    prepared = prepare_task_turn(
        message="сколько ехать до парка Останкино от Королёва, ул. Лесная и где там бесплатные парковки?",
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="req-travel",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "skills", "web", "browser", "file", "clarify"],
    )
    assert prepared.early_response is not None
    assert prepared.early_response["api_calls"] == 0
    assert prepared.early_response["completed"] is True
    assert prepared.early_response["diagnostics"]["tool_call_count"] == 1
    assert prepared.early_response["diagnostics"]["aggregate_helper"] == "city_travel_trip.py"
    assert prepared.early_response["diagnostics"]["html_report_created"] is False
    assert "html_report_path" not in prepared.early_response
    assert "document_delivery_generation" not in prepared.early_response
    assert "INCOMPLETE" not in prepared.early_response["final_response"]
    assert "BLOCKED" not in prepared.early_response["final_response"]
    assert "live traffic" in prepared.early_response["final_response"]
    assert "Яндекс Карты" in prepared.early_response["final_response"]
    assert "не подтверждена официально" in prepared.early_response["final_response"]
    assert calls and "city_travel_trip.py" in calls[0][1]
    assert "/.hermes/.env" not in " ".join(calls[0])

def test_city_travel_multiturn_followups_use_saved_context(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(state_dir))
    state_dir.mkdir()
    helper = state_dir / "skills" / "productivity" / "city-travel-concierge" / "scripts" / "city_travel_trip.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("# helper", encoding="utf-8")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "answer_ready": True,
            "checked_at": "2026-07-02T10:00:00Z",
            "approximate_start": True,
            "traffic_status": "not_available",
            "answer_text": (
                "Маршрут: примерно 35 мин.\n"
                "Яндекс Карты: https://yandex.ru/route\n"
                "Есть кандидат со статусом likely_free, но бесплатность не подтверждена официально: Parking A."
            ),
            "resolved_start": {"title": "Королёв", "coordinates": {"lat": 55.92, "lon": 37.82}},
            "resolved_destination": {"title": "Останкино", "coordinates": {"lat": 55.824, "lon": 37.614}},
            "coordinates": {"destination": {"lat": 55.824, "lon": 37.614}},
            "route": {"deep_links": {"yandex_maps": "https://yandex.ru/route"}},
            "parking_candidates": [
                {"title": "Parking A", "coordinates": {"lat": 55.8241, "lon": 37.6141}, "distance_m": 120, "parking_status": "likely_free", "eligible_for_recommendation": True, "deep_links": {"yandex_maps": "https://yandex.ru/a"}, "parking_evidence": {"source": "osm", "fee": "no", "reason": "fee=no, не гарантия"}},
                {"title": "Parking B", "coordinates": {"lat": 55.8242, "lon": 37.6142}, "distance_m": 220, "parking_status": "unverified", "eligible_for_recommendation": True, "deep_links": {"yandex_maps": "https://yandex.ru/b"}},
                {"title": "Parking C", "coordinates": {"lat": 55.8243, "lon": 37.6143}, "distance_m": 320, "parking_status": "unverified", "eligible_for_recommendation": True, "deep_links": {"yandex_maps": "https://yandex.ru/c"}},
                {"title": "Parking D", "coordinates": {"lat": 55.8244, "lon": 37.6144}, "distance_m": 420, "parking_status": "unverified", "eligible_for_recommendation": True, "deep_links": {"yandex_maps": "https://yandex.ru/d"}},
            ],
            "parking_statuses": ["likely_free", "unverified"],
            "degraded_sections": [],
            "provider_status": [],
        }))

    monkeypatch.setattr("gateway.task_runtime.subprocess.run", fake_run)
    common = dict(platform_key="telegram", chat_id="1", session_key="session-a", session_id="session-a-id", user_config={"agent": {}}, platform_toolsets=["terminal", "skills", "web", "browser", "file", "clarify"])
    first = prepare_task_turn(message="сколько ехать до парка Останкино от Королёва, ул. Лесная и где там бесплатные парковки?", request_id="req-1", **common)
    assert first.early_response["api_calls"] == 0
    assert first.early_response["diagnostics"]["tool_call_count"] == 1
    assert first.task is None

    second = prepare_task_turn(message="пришли ссылкой на Яндекс Карты самый лучший вариант парковки для меня", request_id="req-2", **common)
    assert second.task is None
    assert second.early_response["api_calls"] == 0
    assert second.early_response["tools"] == []
    assert second.early_response["diagnostics"]["tool_call_count"] == 0
    assert "кандидат" in second.early_response["final_response"].lower()
    assert "likely_free" in second.early_response["final_response"]
    assert "pt=37.614100,55.824100" in second.early_response["final_response"]
    assert "лучший" not in second.early_response["final_response"].lower()
    assert "html_report_path" not in second.early_response

    third = prepare_task_turn(message="дай еще три варианта бесплатной или самой дешевой парковки в шаговой доступности", request_id="req-3", **common)
    assert third.task is None
    assert third.early_response["api_calls"] == 0
    assert third.early_response["diagnostics"]["tool_call_count"] == 0
    assert "Parking A" not in third.early_response["final_response"]
    assert "Parking B" in third.early_response["final_response"]
    assert "Parking C" in third.early_response["final_response"]
    assert "Parking D" in third.early_response["final_response"]
    assert "INCOMPLETE" not in third.early_response["final_response"]
    assert len(calls) == 1

def test_city_travel_context_is_session_chat_scoped_and_ttl_bound(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(state_dir))
    state_dir.mkdir()
    from gateway.task_runtime import CityTravelContextStore
    store = CityTravelContextStore(state_dir / "state.db")
    context = {"route_url": "https://yandex.ru/route", "parking_candidates": [{"id": "a", "title": "Parking A", "coordinates": {"lat": 55.0, "lon": 37.0}, "distance_m": 100, "parking_status": "likely_free", "eligible_for_recommendation": True, "raw_yandex_maps_url": "https://yandex.ru/a"}], "shown_parking_ids": []}
    store.save(platform="telegram", chat_id="1", session_key="s1", context=context)
    assert store.get(platform="telegram", chat_id="1", session_key="s1") is not None
    assert store.get(platform="telegram", chat_id="2", session_key="s1") is None
    assert store.get(platform="telegram", chat_id="1", session_key="s2") is None
    import sqlite3
    with sqlite3.connect(state_dir / "state.db") as conn:
        conn.execute("UPDATE city_travel_contexts SET expires_at=?", (time.time() - 1,))
    assert store.get(platform="telegram", chat_id="1", session_key="s1") is None

def test_city_travel_route_repeat_and_no_eligible_are_deterministic(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(state_dir))
    state_dir.mkdir()
    from gateway.task_runtime import CityTravelContextStore

    store = CityTravelContextStore(state_dir / "state.db")
    store.save(
        platform="telegram",
        chat_id="1",
        session_key="s1",
        context={"route_url": "https://yandex.ru/route", "parking_candidates": [], "shown_parking_ids": []},
    )
    common = dict(
        platform_key="telegram",
        chat_id="1",
        session_key="s1",
        session_id="session",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "skills", "web", "browser", "file", "clarify"],
    )
    route = prepare_task_turn(message="пришли ещё раз маршрут", request_id="req-route", **common)
    assert route.task is None
    assert route.early_response["api_calls"] == 0
    assert route.early_response["diagnostics"]["travel_followup_intent"] == "route_repeat"
    assert "https://yandex.ru/route" in route.early_response["final_response"]

    parking = prepare_task_turn(message="дай координаты парковки", request_id="req-parking", **common)
    assert parking.task is None
    assert parking.early_response["api_calls"] == 0
    assert "нет подходящих кандидатов" in parking.early_response["final_response"]

def test_city_travel_more_parking_one_candidate_and_official_tariff(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(state_dir))
    state_dir.mkdir()
    from gateway.task_runtime import CityTravelContextStore

    CityTravelContextStore(state_dir / "state.db").save(
        platform="telegram",
        chat_id="1",
        session_key="s1",
        context={
            "route_url": "https://yandex.ru/route",
            "shown_parking_ids": [],
            "parking_candidates": [
                {
                    "id": "official-a",
                    "title": "Official Parking",
                    "coordinates": {"lat": 55.1, "lon": 37.1},
                    "distance_m": 80,
                    "parking_status": "official",
                    "eligible_for_recommendation": True,
                    "is_free": False,
                    "raw_yandex_maps_url": "https://yandex.ru/official",
                    "evidence": {"official": {"tariffs": "100 руб/час"}},
                }
            ],
        },
    )
    prepared = prepare_task_turn(
        message="покажи другие парковки рядом",
        platform_key="telegram",
        chat_id="1",
        session_key="s1",
        session_id="session",
        request_id="req-more-one",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "skills", "web", "browser", "file", "clarify"],
    )
    assert prepared.task is None
    assert prepared.early_response["api_calls"] == 0
    assert prepared.early_response["diagnostics"]["travel_followup_intent"] == "more_parking"
    assert "Official Parking" in prepared.early_response["final_response"]
    assert "Официальный тариф: 100 руб/час" in prepared.early_response["final_response"]

def test_city_travel_destination_clarification_uses_saved_start(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(state_dir))
    state_dir.mkdir()
    helper = state_dir / "skills" / "productivity" / "city-travel-concierge" / "scripts" / "city_travel_trip.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("# helper", encoding="utf-8")
    from gateway.task_runtime import CityTravelContextStore

    CityTravelContextStore(state_dir / "state.db").save(
        platform="telegram",
        chat_id="1",
        session_key="s1",
        context={"start_text": "Королёв, ул. Лесная", "route_url": "", "parking_candidates": [], "shown_parking_ids": []},
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "answer_ready": True,
            "checked_at": "2026-07-02T10:00:00Z",
            "traffic_status": "not_available",
            "answer_text": "Маршрут уточнён.\nЯндекс Карты: https://yandex.ru/new-route",
            "resolved_start": {"title": "Королёв", "coordinates": {"lat": 55.92, "lon": 37.82}},
            "resolved_destination": {"title": "Усадьба Останкино", "coordinates": {"lat": 55.824, "lon": 37.614}},
            "coordinates": {"destination": {"lat": 55.824, "lon": 37.614}},
            "route": {"deep_links": {"yandex_maps": "https://yandex.ru/new-route"}},
            "parking_candidates": [],
            "parking_statuses": [],
            "degraded_sections": [],
            "provider_status": [],
        }))

    monkeypatch.setattr("gateway.task_runtime.subprocess.run", fake_run)
    prepared = prepare_task_turn(
        message="парк усадьба останкино около вднх, точнее сказать не могу",
        platform_key="telegram",
        chat_id="1",
        session_key="s1",
        session_id="session",
        request_id="req-clarify",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "skills", "web", "browser", "file", "clarify"],
    )
    assert prepared.task is None
    assert prepared.early_response["api_calls"] == 0
    assert prepared.early_response["diagnostics"]["tool_call_count"] == 1
    assert len(calls) == 1
    assert "Королёв, ул. Лесная" in calls[0]
    assert "парк усадьба останкино около вднх, точнее сказать не могу" in calls[0]

def test_city_travel_followup_does_not_catch_cafe_rating(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(state_dir))
    state_dir.mkdir()
    from gateway.task_runtime import CityTravelContextStore
    CityTravelContextStore(state_dir / "state.db").save(platform="telegram", chat_id="1", session_key="s1", context={"route_url": "https://yandex.ru/route", "parking_candidates": [], "shown_parking_ids": [], "start_text": "Королёв"})
    prepared = prepare_task_turn(
        message="найди 3 кафе и ранжируй их по отзывам, покушать после прогулки в парке с детьми",
        platform_key="telegram", chat_id="1", session_key="s1", session_id="session", request_id="req-cafe",
        user_config={"agent": {}}, platform_toolsets=["terminal", "skills", "web", "browser", "file", "clarify"],
    )
    assert prepared.route is not None
    assert "web" in prepared.route.toolsets
    assert "browser" in prepared.route.toolsets
    assert not (
        prepared.early_response
        and prepared.early_response.get("diagnostics", {}).get("travel_followup_intent")
    )

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
    assert "не буду продолжать перебор" in prepared.early_response["final_response"]
    assert prepared.early_response["diagnostics"]["clarification_required"] is True
    assert prepared.early_response["diagnostics"]["clarification_kind"] == "budget_exhaustion"

def test_reminder_clarification_choices_are_compact():
    from gateway.intent_uncertainty import clarification_choices

    assert clarification_choices("", "missing_reminder_date_time") == [
        "Сегодня в 19:00", "Завтра в 09:00", "Завтра в 19:00"
    ]
    assert clarification_choices("", "missing_reminder_date") == [
        "Сегодня", "Завтра", "Послезавтра"
    ]
    assert clarification_choices("", "missing_reminder_time") == [
        "В 09:00", "В 15:00", "В 19:00"
    ]
    assert clarification_choices("", "bare_url") is None


def test_uncertainty_gate_asks_before_model_or_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes").mkdir()
    common = dict(
        platform_key="telegram",
        chat_id="1",
        session_key="session-a",
        session_id="session-a-id",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "skills", "web", "browser", "file", "clarify"],
    )

    for index, message in enumerate((
        "https://www.instagram.com/reel/example/",
        "сделай это",
        "сохрани это для меня",
        "сохрани локацию",
        "скачай ролик",
        "Запиши напоминание - разобраться с штрафами, сделать заявления",
    )):
        prepared = prepare_task_turn(message=message, request_id=f"req-uncertain-{index}", **common)
        assert prepared.task is None
        assert prepared.early_response is not None
        assert prepared.early_response["api_calls"] == 0
        assert prepared.early_response["tools"] == []
        assert prepared.early_response["diagnostics"]["clarification_required"] is True
        assert prepared.early_response["diagnostics"]["uncertainty_gate"] == "pre_model"
        assert prepared.early_response["diagnostics"]["tool_call_count"] == 0


def test_uncertainty_gate_keeps_real_active_task_continuation(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    state_dir.mkdir()
    store = TaskStateStore(state_dir / "state.db")
    task = store.create(
        platform="telegram",
        chat_id="1",
        session_key="old",
        title="Active fix",
        original_request="Исправь gateway regression",
        role="server_debug",
        toolsets=["terminal", "file"],
        required_toolsets=["terminal"],
        requires_execution=True,
        status="incomplete",
        metadata={"checkpoint": "audit done"},
    )

    prepared = prepare_task_turn(
        message="сделай это",
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="req-active-ambiguous",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "file", "skills", "clarify"],
    )

    assert prepared.continued is True
    assert prepared.task.task_id == task.task_id
    assert prepared.early_response is None
    assert "Saved checkpoint" in prepared.message


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
        message="Скачай ролик https://youtu.be/example",
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

def test_quick_note_detects_location_address_without_enrichment():
    note = detect_quick_note(
        "Г. О. Мытищи, дер. Пирогово, ул. Береговая, 1, стр. 1\n\n"
        "запиши локацию пляжа в пирогово"
    )
    assert note is not None
    assert note.payload["knowledge_project"] == "travel"
    assert note.payload["title"] == "Локация: Пляж в Пирогово"
    assert note.payload["summary"] == (
        "Пляж в Пирогово. Адрес: Г. О. Мытищи, дер. Пирогово, ул. Береговая, 1, стр. 1"
    )
    assert "Флагман" not in json.dumps(note.payload, ensure_ascii=False)

def test_quick_note_rejects_calendar_mail_vps_links_and_unclear():
    blocked = [
        "запиши заметку создать напоминание завтра",
        "сохрани адрес https://example.com",
        "зафиксируй место настройка VPS",
        "сохрани адрес это",
    ]
    for text in blocked:
        assert detect_quick_note(text) is None

def test_prepare_task_turn_quick_note_returns_no_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes").mkdir()
    monkeypatch.setattr(
        "gateway.task_runtime.run_quick_save",
        lambda note: {
            "status": "saved",
            "saved": True,
            "already_exists": False,
            "title": note.payload["title"],
            "summary": note.payload["summary"],
            "readback_count": 1,
        },
    )
    prepared = prepare_task_turn(
        message=(
            "Г. О. Мытищи, дер. Пирогово, ул. Береговая, 1, стр. 1\n\n"
            "запиши локацию пляжа в пирогово"
        ),
        platform_key="telegram",
        chat_id="1",
        session_key="new",
        session_id="session-new",
        request_id="req-quick-note",
        user_config={"agent": {}},
        platform_toolsets=["clarify", "skills", "file", "web", "terminal", "no_mcp"],
    )
    assert prepared.task is None
    assert prepared.early_response["api_calls"] == 0
    assert prepared.early_response["task_level"] == "no_llm"
    assert prepared.early_response["final_response"] == (
        "Записал: Пляж в Пирогово\n"
        "Г. О. Мытищи, дер. Пирогово, ул. Береговая, 1, стр. 1"
    )
    store = TaskStateStore(tmp_path / ".hermes" / "state.db")
    assert store.active("telegram", "1") == []

def test_quick_note_uses_general_for_non_travel_note():
    note = detect_quick_note("\u041a\u0443\u043f\u0438\u0442\u044c \u043c\u043e\u043b\u043e\u043a\u043e\n\n\u0437\u0430\u043f\u0438\u0448\u0438 \u0437\u0430\u043c\u0435\u0442\u043a\u0443 \u043f\u043e\u043a\u0443\u043f\u043a\u0438")
    assert note is not None
    assert note.payload["knowledge_project"] == "general"

def test_quick_note_rejects_continuation_and_unsafe_actions():
    sample = "\u0413. \u041e. \u041c\u044b\u0442\u0438\u0449\u0438, \u0443\u043b. \u0411\u0435\u0440\u0435\u0433\u043e\u0432\u0430\u044f, 1\n\n\u0437\u0430\u043f\u0438\u0448\u0438 \u043b\u043e\u043a\u0430\u0446\u0438\u044e \u043f\u043b\u044f\u0436\u0430"
    assert detect_quick_note(sample, continued=True) is None
    for text in (
        "\u0437\u0430\u043f\u0438\u0448\u0438 \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435 \u0437\u0430\u0432\u0442\u0440\u0430 \u0432 10",
        "\u0441\u043e\u0445\u0440\u0430\u043d\u0438 \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f \u0432 Python-\u0444\u0430\u0439\u043b\u0435",
        "\u043f\u0435\u0440\u0435\u0437\u0430\u043f\u0443\u0441\u0442\u0438 gateway \u0438 \u0437\u0430\u043f\u0438\u0448\u0438 \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442",
        "\u0441\u043e\u0445\u0440\u0430\u043d\u0438 \u044d\u0442\u043e",
    ):
        assert detect_quick_note(text) is None

def test_quick_note_location_without_address_stays_in_travel():
    note = detect_quick_note("пляж в Пирогово\n\nзапиши локацию любимое место")
    assert note is not None
    assert note.payload["knowledge_project"] == "travel"

def test_quick_note_duplicate_and_failure_responses():
    from gateway.quick_note_capture import format_quick_save_response

    duplicate, duplicate_status = format_quick_save_response({
        "saved": False,
        "already_exists": True,
        "readback_count": 1,
        "title": "Локация: Пляж в Пирогово",
        "summary": "Пляж в Пирогово. Адрес: тестовый адрес",
    })
    assert duplicate_status == "success"
    assert duplicate == "Эта локация уже сохранена"

    failure, failure_status = format_quick_save_response({
        "saved": False,
        "already_exists": False,
        "readback_count": 0,
        "message": "publication read-back was not confirmed",
    })
    assert failure_status == "failed"
    assert failure.startswith("Не удалось подтвердить сохранение:")
    assert "Сохранено" not in failure

def _location_context(lat=55.8241, lon=37.6141, *, message_id="loc-1", sender_id="42", chat_id="1"):
    return {
        "latitude": lat,
        "longitude": lon,
        "message_id": message_id,
        "chat_id": chat_id,
        "sender_id": sender_id,
        "received_at": "2026-07-02T10:00:00+03:00",
        "live_location": False,
    }

def _location_common(tmp_path, monkeypatch, *, chat_id="1", sender_id="42", session_key="s1"):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(state_dir))
    state_dir.mkdir(exist_ok=True)
    return dict(
        platform_key="telegram",
        chat_id=chat_id,
        session_key=session_key,
        session_id=session_key + "-id",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "skills", "web", "browser", "file", "clarify"],
        message_context={"chat_id": chat_id, "sender_id": sender_id, "session_key": session_key},
    )

def test_travel_source_save_bypasses_personal_location_interceptor(tmp_path, monkeypatch):
    common = _location_common(tmp_path, monkeypatch)
    monkeypatch.setattr("gateway.task_runtime._with_preloaded_skills", lambda route, task_id: (route, ()))
    message = ("https://www.instagram.com/reel/example/\n\n"
               "Сохрани это место для будущих путешествий. Сам вытащи из рилс название, локацию, описание, цены, расписание и время работы.")
    result = prepare_task_turn(message=message, request_id="travel-source-1", **common)
    assert result.early_response is None
    assert result.route is not None
    assert "city-travel-concierge" in result.route.skill_names
    assert {"terminal", "web", "browser"}.issubset(set(result.route.toolsets))
    assert "не проси геолокацию" in result.route.operational_context


def test_telegram_location_then_save_home_is_deterministic(tmp_path, monkeypatch):
    common = _location_common(tmp_path, monkeypatch)
    saved_notes = []

    def fake_save(note):
        saved_notes.append(note)
        return {"status": "saved", "saved": True, "readback_count": 1}

    monkeypatch.setattr("gateway.task_runtime.run_quick_save", fake_save)
    first_context = dict(common["message_context"], location=_location_context())
    first = prepare_task_turn(message="[Telegram location received]", request_id="loc-1", **{**common, "message_context": first_context})
    assert first.task is None
    assert first.early_response["api_calls"] == 0
    assert first.early_response["diagnostics"]["tool_call_count"] == 0
    assert first.early_response["diagnostics"]["html_report_created"] is False
    assert "Геолокацию получил" in first.early_response["final_response"]
    assert not saved_notes

    second = prepare_task_turn(message="Запомни мой дом", request_id="txt-1", **common)
    assert second.task is None
    assert second.early_response["api_calls"] == 0
    assert second.early_response["diagnostics"]["tool_call_count"] == 1
    assert second.early_response["diagnostics"]["location_readback_ok"] is True
    assert second.early_response["diagnostics"]["html_report_created"] is False
    assert second.early_response["final_response"] == "Дом сохранён в памяти."
    assert "кноп" not in second.early_response["final_response"].lower()
    assert saved_notes[0].payload["title"] == "Личное место: Дом"
    assert saved_notes[0].payload["accepted_facts"][0] == {"kind": "personal_place_label", "value": "Дом"}
    assert saved_notes[0].idempotency_key.startswith("personal-place:")

def test_telegram_place_text_then_location_saves_without_confirmation(tmp_path, monkeypatch):
    common = _location_common(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr("gateway.task_runtime.run_quick_save", lambda note: calls.append(note) or {"status": "saved", "saved": True, "readback_count": 1})

    first = prepare_task_turn(message="Запомни мой дом", request_id="txt-1", **common)
    assert first.task is None
    assert first.early_response["api_calls"] == 0
    assert first.early_response["diagnostics"]["tool_call_count"] == 0
    assert first.early_response["diagnostics"]["location_intent"] == "waiting_for_location"
    assert "Пришли геолокацию" in first.early_response["final_response"]

    second_context = dict(common["message_context"], location=_location_context(lat=55.1, lon=37.1, message_id="loc-2"))
    second = prepare_task_turn(message="[Telegram location received]", request_id="loc-2", **{**common, "message_context": second_context})
    assert second.task is None
    assert second.early_response["api_calls"] == 0
    assert second.early_response["diagnostics"]["location_intent"] == "pending_text_then_location"
    assert second.early_response["final_response"] == "Дом сохранён в памяти."
    assert len(calls) == 1

def test_telegram_reply_location_has_priority_over_last_location(tmp_path, monkeypatch):
    common = _location_common(tmp_path, monkeypatch)
    saved = []
    monkeypatch.setattr("gateway.task_runtime.run_quick_save", lambda note: saved.append(note) or {"saved": True, "readback_count": 1})
    last_context = dict(common["message_context"], location=_location_context(lat=55.0, lon=37.0, message_id="last"))
    prepare_task_turn(message="[Telegram location received]", request_id="last", **{**common, "message_context": last_context})

    reply = _location_context(lat=56.0, lon=38.0, message_id="reply")
    command_context = dict(common["message_context"], reply_location=reply)
    result = prepare_task_turn(message="Запомни мой дом", request_id="txt", **{**common, "message_context": command_context})
    assert result.early_response["diagnostics"]["location_intent"] == "reply_location"
    facts = saved[0].payload["accepted_facts"]
    assert {"kind": "latitude", "value": "56.000000"} in facts
    assert {"kind": "longitude", "value": "38.000000"} in facts

def test_telegram_home_update_uses_same_canonical_note(tmp_path, monkeypatch):
    common = _location_common(tmp_path, monkeypatch)
    keys = []

    def fake_save(note):
        keys.append(note.idempotency_key)
        return {"status": "saved" if len(keys) == 1 else "updated", "saved": True, "updated": len(keys) > 1, "readback_count": 1}

    monkeypatch.setattr("gateway.task_runtime.run_quick_save", fake_save)
    first_context = dict(common["message_context"], location=_location_context(lat=55.0, lon=37.0, message_id="a"))
    prepare_task_turn(message="[Telegram location received]", request_id="a", **{**common, "message_context": first_context})
    first = prepare_task_turn(message="Запомни мой дом", request_id="a-txt", **common)
    second_context = dict(common["message_context"], location=_location_context(lat=56.0, lon=38.0, message_id="b"))
    prepare_task_turn(message="[Telegram location received]", request_id="b", **{**common, "message_context": second_context})
    second = prepare_task_turn(message="Теперь мой дом здесь", request_id="b-txt", **common)
    assert first.early_response["final_response"] == "Дом сохранён в памяти."
    assert second.early_response["final_response"] == "Дом обновлён в памяти."
    assert len(keys) == 2
    assert keys[0] == keys[1]

def test_telegram_location_context_is_sender_chat_session_ttl_and_used_bound(tmp_path, monkeypatch):
    common = _location_common(tmp_path, monkeypatch, chat_id="1", sender_id="42", session_key="s1")
    calls = []
    monkeypatch.setattr("gateway.task_runtime.run_quick_save", lambda note: calls.append(note) or {"saved": True, "readback_count": 1})
    context = dict(common["message_context"], location=_location_context(lat=55.0, lon=37.0, sender_id="42", chat_id="1"))
    prepare_task_turn(message="[Telegram location received]", request_id="loc", **{**common, "message_context": context})

    other_chat = prepare_task_turn(message="Запомни мой дом", request_id="other-chat", **{**common, "chat_id": "2", "message_context": {"chat_id": "2", "sender_id": "42", "session_key": "s1"}})
    other_sender = prepare_task_turn(message="Запомни мой дом", request_id="other-sender", **{**common, "message_context": {"chat_id": "1", "sender_id": "99", "session_key": "s1"}})
    other_session = prepare_task_turn(message="Запомни мой дом", request_id="other-session", **{**common, "session_key": "s2", "session_id": "s2-id", "message_context": {"chat_id": "1", "sender_id": "42", "session_key": "s2"}})
    assert other_chat.early_response["diagnostics"]["location_intent"] == "waiting_for_location"
    assert other_sender.early_response["diagnostics"]["location_intent"] == "waiting_for_location"
    assert other_session.early_response["diagnostics"]["location_intent"] == "waiting_for_location"
    assert not calls

    import sqlite3
    with sqlite3.connect(tmp_path / ".hermes" / "state.db") as conn:
        conn.execute("UPDATE telegram_location_contexts SET expires_at=? WHERE kind='location'", (time.time() - 1,))
    expired = prepare_task_turn(message="Запомни мой дом", request_id="expired", **common)
    assert expired.early_response["diagnostics"]["location_intent"] == "waiting_for_location"

    fresh_context = dict(common["message_context"], location=_location_context(lat=55.2, lon=37.2, message_id="fresh"))
    saved = prepare_task_turn(message="[Telegram location received]", request_id="fresh", **{**common, "message_context": fresh_context})
    reused = prepare_task_turn(message="Запомни мой дом", request_id="reuse", **common)
    assert saved.early_response["final_response"] == "Дом сохранён в памяти."
    assert saved.early_response["diagnostics"]["location_intent"] == "pending_text_then_location"
    assert reused.early_response["diagnostics"]["location_intent"] == "waiting_for_location"
    assert len(calls) == 1

def test_ambiguous_location_save_does_not_promise_phantom_button(tmp_path, monkeypatch):
    common = _location_common(tmp_path, monkeypatch)
    location_context = dict(common["message_context"], location=_location_context())
    prepare_task_turn(message="[Telegram location received]", request_id="loc", **{**common, "message_context": location_context})
    result = prepare_task_turn(message="Сохрани это", request_id="ambiguous", **common)
    text = result.early_response["final_response"]
    assert "Как назвать место" in text
    assert "кноп" not in text.lower()
    assert "выберите ниже" not in text.lower()
    assert "Ответьте сообщением" in text

def test_city_travel_parking_ux_plural_fallback_and_selection_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(state_dir))
    state_dir.mkdir()
    from gateway.task_runtime import CityTravelContextStore
    candidates = [
        {"id": "u1", "title": "parking", "coordinates": {"lat": 55.1, "lon": 37.1}, "distance_m": 401, "parking_status": "unverified", "eligible_for_recommendation": True, "raw_yandex_maps_url": "https://yandex.ru/u1"},
        {"id": "u2", "title": "parking", "coordinates": {"lat": 55.2, "lon": 37.2}, "distance_m": 480, "parking_status": "unverified", "eligible_for_recommendation": True, "raw_yandex_maps_url": "https://yandex.ru/u2"},
        {"id": "lf", "title": "parking", "coordinates": {"lat": 55.3, "lon": 37.3}, "distance_m": 571, "parking_status": "likely_free", "eligible_for_recommendation": True, "raw_yandex_maps_url": "https://yandex.ru/lf", "evidence": {"reason": "fee=no, знаки не проверены"}},
        {"id": "u3", "title": "parking", "coordinates": {"lat": 55.4, "lon": 37.4}, "distance_m": 650, "parking_status": "unverified", "eligible_for_recommendation": True, "raw_yandex_maps_url": "https://yandex.ru/u3"},
    ]
    CityTravelContextStore(state_dir / "state.db").save(platform="telegram", chat_id="1", session_key="s1", context={"route_url": "https://yandex.ru/route", "parking_candidates": candidates, "shown_parking_ids": []})
    common = dict(platform_key="telegram", chat_id="1", session_key="s1", session_id="session", user_config={"agent": {}}, platform_toolsets=["terminal", "skills", "web", "browser", "file", "clarify"])
    best = prepare_task_turn(message="пришли ссылкой на Яндекс Карты самый лучший вариант парковки", request_id="best", **common)
    text = best.early_response["final_response"]
    assert "Вероятно бесплатный кандидат" in text
    assert "не как самый близкий" in text
    assert "Самый близкий кандидат" in text
    assert "Парковка-кандидат №1" in text
    assert "parking" not in text
    assert "pt=37.300000,55.300000" in text

    more = prepare_task_turn(message="дай еще три варианта бесплатной или самой дешевой парковки", request_id="more", **common)
    more_text = more.early_response["final_response"]
    assert "Показываю ещё 2 сохранённых варианта парковки" in more_text
    assert "сохранённ(ых)" not in more_text
    assert "кандидат(а/ов)" not in more_text
    assert "Официальных тарифов" in more_text
    assert "likely_free не считаю доказанной нулевой ценой" in more_text

def test_city_travel_parking_closest_and_plural_one_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(state_dir))
    state_dir.mkdir()
    from gateway.task_runtime import CityTravelContextStore
    CityTravelContextStore(state_dir / "state.db").save(platform="telegram", chat_id="1", session_key="s1", context={"route_url": "https://yandex.ru/route", "shown_parking_ids": [], "parking_candidates": [
        {"id": "near", "title": "parking", "coordinates": {"lat": 55.1, "lon": 37.1}, "distance_m": 100, "parking_status": "unverified", "eligible_for_recommendation": True, "raw_yandex_maps_url": "https://yandex.ru/near"},
        {"id": "far", "title": "parking", "coordinates": {"lat": 55.2, "lon": 37.2}, "distance_m": 500, "parking_status": "likely_free", "eligible_for_recommendation": True, "raw_yandex_maps_url": "https://yandex.ru/far"},
    ]})
    common = dict(platform_key="telegram", chat_id="1", session_key="s1", session_id="session", user_config={"agent": {}}, platform_toolsets=["terminal", "skills", "web", "browser", "file", "clarify"])
    closest = prepare_task_turn(message="дай самую близкую парковку", request_id="closest", **common)
    assert "Самый близкий кандидат" in closest.early_response["final_response"]
    assert "pt=37.100000,55.100000" in closest.early_response["final_response"]

    more = prepare_task_turn(message="покажи другие парковки рядом", request_id="more-one", **common)
    assert "Показываю ещё 1 сохранённый вариант парковки" in more.early_response["final_response"]
    assert "pt=37.200000,55.200000" in more.early_response["final_response"]


def test_personal_place_lookup_preempts_stale_parking_context(tmp_path, monkeypatch):
    common = _location_common(tmp_path, monkeypatch)
    monkeypatch.setattr("gateway.task_runtime.run_quick_save", lambda note: {"status": "error", "saved": False, "readback_count": 0})
    from gateway.task_runtime import CityTravelContextStore
    state_db = tmp_path / ".hermes" / "state.db"
    CityTravelContextStore(state_db).save(platform="telegram", chat_id="1", session_key="s1", context={"route_url": "https://yandex.ru/route", "shown_parking_ids": [], "parking_candidates": [{"id": "p", "title": "parking", "coordinates": {"lat": 55.3, "lon": 37.3}, "distance_m": 571, "parking_status": "likely_free", "eligible_for_recommendation": True, "raw_yandex_maps_url": "https://yandex.ru/parking"}]})
    loc = dict(common["message_context"], location=_location_context(lat=55.92, lon=37.82, message_id="home"))
    prepare_task_turn(message="[Telegram location received]", request_id="loc-home", **{**common, "message_context": loc})
    saved = prepare_task_turn(message="Запомни мой дом", request_id="save-home", **common)
    assert saved.early_response["final_response"] == "Дом сохранён в памяти."
    assert saved.early_response["diagnostics"]["location_readback_ok"] is True

    lookup = prepare_task_turn(message="Где мой дом? Пришли ссылку на Яндекс Карты", request_id="lookup-home", **common)
    text = lookup.early_response["final_response"]
    assert lookup.task is None
    assert lookup.early_response["api_calls"] == 0
    assert lookup.early_response["diagnostics"]["tool_call_count"] == 0
    assert "Дом сохранён здесь" in text
    assert "pt=37.820000,55.920000" in text
    assert "parking" not in text.lower()
    assert "Парковка" not in text


def test_personal_place_missing_does_not_fall_back_to_parking(tmp_path, monkeypatch):
    common = _location_common(tmp_path, monkeypatch)
    from gateway.task_runtime import CityTravelContextStore
    CityTravelContextStore(tmp_path / ".hermes" / "state.db").save(platform="telegram", chat_id="1", session_key="s1", context={"route_url": "https://yandex.ru/route", "shown_parking_ids": [], "parking_candidates": [{"id": "p", "title": "parking", "coordinates": {"lat": 55.3, "lon": 37.3}, "distance_m": 571, "parking_status": "likely_free", "eligible_for_recommendation": True, "raw_yandex_maps_url": "https://yandex.ru/parking"}]})
    for message in ("Где мой дом? Пришли ссылку на Яндекс Карты", "Дай координаты моей работы", "Покажи дачу на карте"):
        result = prepare_task_turn(message=message, request_id="missing-" + message[:8], **common)
        assert result.task is None
        assert result.early_response["api_calls"] == 0
        assert "пока не сохран" in result.early_response["final_response"]
        assert "Парков" not in result.early_response["final_response"]


def test_new_cleanup_removes_ephemeral_context_but_keeps_personal_place(tmp_path, monkeypatch):
    common = _location_common(tmp_path, monkeypatch)
    monkeypatch.setattr("gateway.task_runtime.run_quick_save", lambda note: {"status": "saved", "saved": True, "readback_count": 1})
    from gateway.task_runtime import CityTravelContextStore, PendingLocationStore, clear_ephemeral_contexts_for_session
    db = tmp_path / ".hermes" / "state.db"
    CityTravelContextStore(db).save(platform="telegram", chat_id="1", session_key="s1", context={"route_url": "https://yandex.ru/route", "shown_parking_ids": [], "parking_candidates": [{"id":"p","title":"parking","coordinates":{"lat":55.3,"lon":37.3},"distance_m":571,"parking_status":"likely_free","eligible_for_recommendation":True,"raw_yandex_maps_url":"https://yandex.ru/parking"}]})
    PendingLocationStore(db).save(platform="telegram", chat_id="1", session_key="s1", sender_id="42", kind="intent", payload={"label":"Дом"})
    loc = dict(common["message_context"], location=_location_context(lat=55.92, lon=37.82))
    prepare_task_turn(message="[Telegram location received]", request_id="loc", **{**common, "message_context": loc})
    prepare_task_turn(message="Запомни мой дом", request_id="save", **common)
    cleared = clear_ephemeral_contexts_for_session(platform="telegram", chat_id="1", session_key="s1", sender_id="42")
    assert cleared["city_travel_contexts"] >= 1
    assert CityTravelContextStore(db).get(platform="telegram", chat_id="1", session_key="s1") is None
    lookup = prepare_task_turn(message="Где мой дом?", request_id="lookup-after-new", **common)
    assert "Дом сохранён здесь" in lookup.early_response["final_response"]
    assert "pt=37.820000,55.920000" in lookup.early_response["final_response"]


def test_personal_place_update_changes_canonical_coordinates(tmp_path, monkeypatch):
    common = _location_common(tmp_path, monkeypatch)
    monkeypatch.setattr("gateway.task_runtime.run_quick_save", lambda note: {"status": "saved", "saved": True, "readback_count": 1})
    loc_a = dict(common["message_context"], location=_location_context(lat=55.0, lon=37.0, message_id="a"))
    prepare_task_turn(message="[Telegram location received]", request_id="a", **{**common, "message_context": loc_a})
    prepare_task_turn(message="Запомни мой дом", request_id="save-a", **common)
    loc_b = dict(common["message_context"], location=_location_context(lat=56.0, lon=38.0, message_id="b"))
    prepare_task_turn(message="[Telegram location received]", request_id="b", **{**common, "message_context": loc_b})
    updated = prepare_task_turn(message="Теперь мой дом здесь", request_id="save-b", **common)
    assert updated.early_response["final_response"] == "Дом обновлён в памяти."
    lookup = prepare_task_turn(message="Где мой дом?", request_id="lookup-b", **common)
    assert "pt=38.000000,56.000000" in lookup.early_response["final_response"]
    assert "pt=37.000000,55.000000" not in lookup.early_response["final_response"]


def test_parking_followup_requires_explicit_parking_word_and_uses_point_url(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(state_dir))
    state_dir.mkdir()
    from gateway.task_runtime import CityTravelContextStore
    CityTravelContextStore(state_dir / "state.db").save(platform="telegram", chat_id="1", session_key="s1", context={"route_url": "https://yandex.ru/route", "shown_parking_ids": [], "parking_candidates": [{"id": "p", "title": "parking", "address": {"city": None, "housenumber": None, "street": None}, "coordinates": {"lat": 55.8241, "lon": 37.6141}, "distance_m": 120, "parking_status": "likely_free", "eligible_for_recommendation": True, "deep_links": {"yandex_maps": "https://yandex.ru/maps/?ll=37.6141,55.8241&text=parking"}}]})
    common = dict(platform_key="telegram", chat_id="1", session_key="s1", session_id="session", user_config={"agent": {}}, platform_toolsets=["terminal", "skills", "web", "browser", "file", "clarify"])
    non_parking = prepare_task_turn(message="Где мой дом? Пришли ссылку на Яндекс Карты", request_id="not-parking", message_context={"chat_id":"1","sender_id":"42","session_key":"s1"}, **common)
    assert "Парков" not in non_parking.early_response["final_response"]
    parking = prepare_task_turn(message="Пришли ссылку на выбранную парковку", request_id="parking", **common)
    text = parking.early_response["final_response"]
    assert "Парковка-кандидат №1" in text
    assert "{'city'" not in text
    assert "None" not in text
    assert "text=parking" not in text
    assert "pt=37.614100,55.824100" in text


def test_bare_telegram_location_sentinel_has_no_old_prompt_in_adapter():
    source = Path("plugins/platforms/telegram/adapter.py").read_text(encoding="utf-8")
    handler = source[source.index("async def _handle_location_message"):source.index("# ------------------------------------------------------------------", source.index("async def _handle_location_message"))]
    assert "[Telegram location received]" in handler
    assert "Ask what they'd like to find nearby" not in handler
    assert "[The user shared a location pin.]" not in handler


def test_city_travel_exact_parking_points_and_selected_route_from_saved_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state_dir = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(state_dir))
    state_dir.mkdir()
    helper = state_dir / "skills" / "productivity" / "city-travel-concierge" / "scripts" / "city_travel_trip.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("# helper", encoding="utf-8")

    from gateway.task_runtime import PersonalPlaceStore

    PersonalPlaceStore(state_dir / "state.db").upsert(
        platform="telegram",
        chat_id="1",
        sender_id="42",
        label="Дом",
        latitude=55.92,
        longitude=37.82,
        payload={"source": "test"},
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "answer_ready": True,
            "checked_at": "2026-07-02T10:00:00Z",
            "approximate_start": False,
            "traffic_status": "not_available",
            "answer_text": "Маршрут: примерно 23 мин.\nЯндекс Карты: https://yandex.ru/route",
            "resolved_start": {"title": "Сохранённая точка", "coordinates": {"lat": 55.92, "lon": 37.82}},
            "resolved_destination": {"title": "Парк Останкино", "coordinates": {"lat": 55.824, "lon": 37.614}},
            "coordinates": {"start": {"lat": 55.92, "lon": 37.82}, "destination": {"lat": 55.824, "lon": 37.614}},
            "route": {"deep_links": {"yandex_maps": "https://yandex.ru/route"}},
            "parking_candidates": [
                {"title": "parking", "coordinates": {"lat": 55.8283386, "lon": 37.6013861}, "distance_m": 571, "parking_status": "likely_free", "eligible_for_recommendation": True, "deep_links": {"yandex_maps": "https://yandex.ru/maps/?text=parking"}, "parking_evidence": {"source": "osm", "fee": "no", "reason": "fee=no, не гарантия"}},
                {"title": "parking", "address": {"street": None, "housenumber": None, "city": None}, "coordinates": {"lat": 55.8258147, "lon": 37.6089213}, "distance_m": 401, "parking_status": "unverified", "eligible_for_recommendation": True},
                {"title": "parking", "coordinates": {"lat": 55.8323353, "lon": 37.6158610}, "distance_m": 480, "parking_status": "unverified", "eligible_for_recommendation": True},
                {"title": "parking", "coordinates": {"lat": 55.8295978, "lon": 37.6190863}, "distance_m": 547, "parking_status": "unverified", "eligible_for_recommendation": True},
            ],
            "parking_statuses": ["likely_free", "unverified"],
            "degraded_sections": [],
            "provider_status": [],
        }))

    monkeypatch.setattr("gateway.task_runtime.subprocess.run", fake_run)
    common = dict(
        platform_key="telegram",
        chat_id="1",
        session_key="session-a",
        session_id="session-a-id",
        user_config={"agent": {}},
        platform_toolsets=["terminal", "skills", "web", "browser", "file", "clarify"],
        message_context={"chat_id": "1", "sender_id": "42", "session_key": "session-a"},
    )

    route = prepare_task_turn(
        message="Сколько ехать от моего дома до парка Останкино?",
        request_id="parking-route-home",
        **common,
    )
    assert route.task is None
    assert route.early_response["api_calls"] == 0
    assert route.early_response["diagnostics"]["tool_call_count"] == 1
    assert len(calls) == 1
    assert "55.920000,37.820000" in calls[0]

    point = prepare_task_turn(
        message="пришли конкретную локацию бесплатной парковки, нужную точку на карте ближайшую к парку",
        request_id="parking-exact-point",
        **common,
    )
    point_text = point.early_response["final_response"]
    assert point.task is None
    assert point.early_response["api_calls"] == 0
    assert point.early_response["diagnostics"]["tool_call_count"] == 0
    assert "pt=37.601386,55.828339" in point_text
    assert "rtext=55.920000,37.820000~55.828339,37.601386" in point_text
    assert "text=parking" not in point_text
    assert "{'city'" not in point_text
    assert len(calls) == 1

    options = prepare_task_turn(
        message="дай ещё три варианта на выбор",
        request_id="parking-three-options",
        **common,
    )
    options_text = options.early_response["final_response"]
    assert options.task is None
    assert options.early_response["api_calls"] == 0
    assert options.early_response["diagnostics"]["tool_call_count"] == 0
    assert options.early_response["diagnostics"]["parking_options_count"] == 3
    assert options_text.count("Открыть точку в Яндекс Картах:") == 3
    assert options_text.count("Построить маршрут от дома:") == 3
    assert "55.828339,37.601386" not in options_text
    assert len(calls) == 1

    selected = prepare_task_turn(
        message="построй маршрут до второй парковки",
        request_id="parking-route-second",
        **common,
    )
    selected_text = selected.early_response["final_response"]
    assert selected.task is None
    assert selected.early_response["api_calls"] == 0
    assert selected.early_response["diagnostics"]["tool_call_count"] == 0
    assert selected.early_response["diagnostics"]["selected_parking_index"] == 2
    assert "rtext=55.920000,37.820000~55.832335,37.615861" in selected_text
    assert len(calls) == 1


def test_relative_reminder_requires_cronjob_evidence():
    execution, required = infer_execution_contract(
        "Напомни через два часа разобраться со штрафами",
        "simple",
        ["cronjob"],
    )
    assert execution is True
    assert required == ("cronjob",)


def test_english_google_calendar_requires_workspace_evidence():
    execution, required = infer_execution_contract(
        "Добавь напоминание в Google Calendar завтра в 10:00",
        "simple",
        ["skills", "terminal"],
    )
    assert execution is True
    assert required == ("skills", "terminal")
