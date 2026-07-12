from gateway.task_runtime import prepare_task_turn


AVITO_PLATFORM_TOOLSETS = [
    "avito",
    "browser",
    "clarify",
    "image_gen",
    "memory",
    "no_mcp",
    "skills",
    "terminal",
    "vision",
    "web",
]


def _prepare(tmp_path, monkeypatch, message, *, request_id, current_text=None, sender="u1", toolsets=None):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes").mkdir(exist_ok=True)
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: ("AVITO SELLER SKILL", list(names), []),
    )
    return prepare_task_turn(
        message=message,
        platform_key="telegram",
        chat_id="1",
        session_key="same-session",
        session_id="session-1",
        request_id=request_id,
        user_config={"agent": {}},
        platform_toolsets=toolsets or AVITO_PLATFORM_TOOLSETS,
        message_context={
            "current_text": current_text if current_text is not None else message,
            "sender_id": sender,
            "update_id": request_id,
        },
    )


def test_explicit_avito_routes_to_skill_and_exact_tool_boundary(tmp_path, monkeypatch):
    prepared = _prepare(
        tmp_path,
        monkeypatch,
        "@avito\n[The user sent an image]\nЧетыре бутыли 18,9 л, б/у",
        current_text="@avito\nЧетыре бутыли 18,9 л, б/у",
        request_id="avito-1",
    )

    assert prepared.early_response is None
    assert prepared.task is not None
    assert prepared.task.metadata["intent"] == "avito_sale"
    assert prepared.task.metadata["mode_label"] == "Продажа на Avito"
    assert prepared.route.skill_names == ("avito-seller",)
    assert set(prepared.route.toolsets) == {"avito", "clarify", "image_gen", "no_mcp", "vision"}
    assert {"web", "browser", "memory", "terminal", "skills"}.isdisjoint(prepared.route.toolsets)


def test_photo_without_intent_clarifies_before_tools(tmp_path, monkeypatch):
    prepared = _prepare(
        tmp_path,
        monkeypatch,
        "[The user sent an image]",
        current_text="",
        request_id="avito-clarify",
    )

    assert prepared.task is None
    assert prepared.early_response is not None
    assert prepared.early_response["diagnostics"]["tool_call_count"] == 0
    assert prepared.early_response["diagnostics"]["clarification_kind"] == "avito_intent"
    assert "Что сделать с фотографиями" in prepared.early_response["final_response"]


def test_active_avito_task_accepts_relevant_followup(tmp_path, monkeypatch):
    first = _prepare(
        tmp_path,
        monkeypatch,
        "@avito\n[The user sent an image]\nБутыль 18,9 л, б/у",
        current_text="@avito\nБутыль 18,9 л, б/у",
        request_id="avito-first",
    )
    second = _prepare(
        tmp_path,
        monkeypatch,
        "ещё фото\n[The user sent an image]",
        current_text="ещё фото",
        request_id="avito-followup",
    )

    assert second.continued is True
    assert second.task.task_id == first.task.task_id
    assert second.route.skill_names == ("avito-seller",)
    assert "web" not in second.route.toolsets
    assert "browser" not in second.route.toolsets


def test_active_avito_task_does_not_capture_topic_switch(tmp_path, monkeypatch):
    _prepare(
        tmp_path,
        monkeypatch,
        "@avito\n[The user sent an image]\nБутыль 18,9 л, б/у",
        current_text="@avito\nБутыль 18,9 л, б/у",
        request_id="avito-topic-first",
    )
    weather = _prepare(
        tmp_path,
        monkeypatch,
        "Какая завтра погода?",
        request_id="avito-topic-weather",
    )

    assert weather.continued is False
    assert weather.task is None
    assert weather.route is not None
    assert "avito-seller" not in weather.route.skill_names
    assert "avito" not in weather.route.toolsets


def test_missing_platform_avito_capability_blocks_without_fallback(tmp_path, monkeypatch):
    prepared = _prepare(
        tmp_path,
        monkeypatch,
        "@avito\n[The user sent an image]\nБутыль 18,9 л, б/у",
        current_text="@avito\nБутыль 18,9 л, б/у",
        request_id="avito-no-capability",
        toolsets=["clarify", "image_gen", "no_mcp", "vision", "web", "browser"],
    )

    assert prepared.early_response is not None
    assert prepared.early_response["error"] == "blocked"
    assert prepared.early_response["completed"] is False
    assert "avito" in prepared.early_response["final_response"]
    assert prepared.route is not None
    assert "web" not in prepared.route.toolsets
    assert "browser" not in prepared.route.toolsets
