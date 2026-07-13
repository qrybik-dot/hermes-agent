import json
from pathlib import Path

from gateway.granola_evidence import granola_tool_result_successful
from gateway.granola_ingest import (
    GranolaIngestResult, ingest_public_granola, should_ingest_public_granola,
)
from gateway.granola_share import GranolaShare, parse_granola_share_html
from gateway.task_continuation import TaskStateStore
from gateway.task_router import route_turn
from gateway.task_runtime import (
    MessageContext,
    _is_calendar_followup_payload,
    _place_intent_from_text,
    prepare_task_turn,
)
from gateway.transcript_ingest import (
    StagedTranscript, is_staged_prompt, looks_like_transcript, stage_transcript, staged_prompt,
)

GRANOLA_URL = "https://notes.granola.ai/t/34dd2eb8-467b-4644-ac5c-dd1245751343"


def _share():
    return GranolaShare(
        source_url=GRANOLA_URL,
        title="Собеседование: АО Спецхимия (Ростех)",
        description="Краткое описание",
        summary="Опыт и задачи\n• 4 рекрутера\n• 40 вакансий",
        document_id="61b71bf0-5790-408a-96da-223a362f39c7",
        created_at="2026-07-13T08:01:32.036Z",
        conferencing_url="https://us04web.zoom.us/j/123",
        content_hash="a" * 64,
    )


def _transcript():
    block = """- 11:02am -
Them: Расскажите про ваш опыт и места работы
Me: Работаю в HR и сохраняю информацию о проектах, включая Москву и Турцию
- 11:03am -
Them: Какие задачи были в последнем месте работы?
Me: Подбор, аналитика, автоматизация и управление командой
"""
    return (block * 14) + "\nВот полная запись, добавь в базу"


def test_public_granola_parser_reads_server_rendered_summary():
    flight = json.dumps(
        '<h3>Итоги</h3><ul><li>4 рекрутера</li><li>40 вакансий</li></ul>'
        ',"created_at":"2026-07-13T08:01:32.036Z"'
        ',"documentId":"61b71bf0-5790-408a-96da-223a362f39c7"'
    )
    page = (
        '<meta property="og:title" content="Собеседование: Ростех"/>'
        '<meta property="og:description" content="Краткое резюме"/>'
        f'<script>self.__next_f.push([1,{flight}])</script>'
    )
    parsed = parse_granola_share_html(GRANOLA_URL, page)
    assert parsed.title == "Собеседование: Ростех"
    assert "4 рекрутера" in parsed.summary
    assert parsed.document_id == "61b71bf0-5790-408a-96da-223a362f39c7"


def test_granola_url_is_strong_route_not_calendar():
    route = route_turn(
        GRANOLA_URL,
        command=None,
        platform_key="telegram",
        user_config={"agent": {}},
        platform_toolsets=["granola", "file", "skills", "terminal", "no_mcp"],
    )
    assert "granola" in route.toolsets
    assert "google-workspace" not in route.skill_names
    assert _is_calendar_followup_payload(GRANOLA_URL) is False


def test_granola_url_bypasses_stale_calendar_task(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes").mkdir()
    store = TaskStateStore(tmp_path / ".hermes" / "state.db")
    stale = store.create(
        platform="telegram",
        chat_id="1",
        session_key="old-session",
        title="Добавить Zoom в календарь",
        original_request="добавь ссылку Zoom в существующую встречу",
        role="simple",
        toolsets=["file", "skills", "terminal"],
        required_toolsets=["terminal"],
        requires_execution=True,
        status="incomplete",
    )
    store.update(stale.task_id, last_error="iteration_budget_exhausted")
    monkeypatch.setattr(
        "gateway.task_runtime.ingest_public_granola",
        lambda text: GranolaIngestResult(
            status="success",
            text="READY\nGranola read",
            evidence={"read_back": True, "source_url": GRANOLA_URL},
            knowledge_readback=True,
            tool_call_count=2,
        ),
    )
    prepared = prepare_task_turn(
        message=GRANOLA_URL,
        platform_key="telegram",
        chat_id="1",
        session_key="new-session",
        session_id="new-session",
        request_id="granola-1",
        user_config={"agent": {}},
        platform_toolsets=["granola", "file", "skills", "terminal", "no_mcp"],
        message_context={"current_text": GRANOLA_URL, "sender_id": "u1"},
    )
    assert prepared.early_response is not None
    assert prepared.early_response["final_response"].startswith("READY")
    assert prepared.continued is False
    assert prepared.task is None


def test_cross_session_opaque_input_does_not_resume_implicitly(tmp_path):
    store = TaskStateStore(tmp_path / "state.db")
    task = store.create(
        platform="telegram",
        chat_id="1",
        session_key="old-session",
        title="Old task",
        original_request="old task",
        role="simple",
        toolsets=["file"],
        status="incomplete",
    )
    opaque = "https://example.com/" + "a" * 30
    assert store.resolve(opaque, "telegram", "1", session_key="new-session").kind == "none"
    assert store.resolve(opaque, "telegram", "1", session_key="old-session").task.task_id == task.task_id
    assert store.resolve("Продолжай", "telegram", "1", session_key="new-session").task.task_id == task.task_id


def test_large_transcript_is_not_location_and_is_staged(tmp_path, monkeypatch):
    text = _transcript()
    assert looks_like_transcript(text)
    assert _place_intent_from_text(text) is None
    artifact = tmp_path / "transcript.txt"
    artifact.write_text(text)
    staged = StagedTranscript(
        path=str(artifact),
        metadata_path=str(artifact) + ".json",
        sha256="b" * 64,
        char_count=len(text),
        explicit_save=True,
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes").mkdir(exist_ok=True)
    monkeypatch.setattr("gateway.task_runtime.stage_transcript", lambda *a, **k: staged)
    prepared = prepare_task_turn(
        message=text,
        platform_key="telegram",
        chat_id="1",
        session_key="s1",
        session_id="s1",
        request_id="transcript-1",
        user_config={"agent": {}},
        platform_toolsets=["file", "memory", "skills", "session_search", "clarify", "no_mcp"],
        message_context={"current_text": text, "sender_id": "u1"},
    )
    assert prepared.early_response is None
    assert prepared.route.role == "long_context_extract"
    assert str(artifact) in prepared.message
    assert "Пришли геолокацию" not in prepared.message


def test_public_granola_ingest_requires_knowledge_readback(monkeypatch):
    monkeypatch.setattr("gateway.granola_ingest.fetch_granola_share", lambda url: _share())
    monkeypatch.setattr(
        "gateway.granola_ingest.run_quick_save",
        lambda note: {"saved": True, "readback_count": 1, "status": "saved"},
    )
    result = ingest_public_granola(GRANOLA_URL)
    assert result.status == "success"
    assert result.knowledge_readback is True
    assert result.evidence["document_id"] == _share().document_id

    monkeypatch.setattr(
        "gateway.granola_ingest.run_quick_save",
        lambda note: {"saved": True, "readback_count": 0, "status": "saved"},
    )
    failed = ingest_public_granola(GRANOLA_URL)
    assert failed.status == "failed"
    assert failed.knowledge_readback is False


def test_public_granola_explicit_target_priority():
    assert should_ingest_public_granola(GRANOLA_URL) is True
    assert should_ingest_public_granola("прочитай и сохрани в базу " + GRANOLA_URL) is True
    assert should_ingest_public_granola("посмотри информацию по ссылке " + GRANOLA_URL) is True
    assert should_ingest_public_granola("добавь эту ссылку в календарь " + GRANOLA_URL) is False
    assert should_ingest_public_granola("отправь ссылку письмом " + GRANOLA_URL) is False


def test_granola_tool_evidence_rejects_failed_results():
    assert granola_tool_result_successful("granola_get_meeting", {"success": True, "data": {"id": "m1"}})
    assert granola_tool_result_successful("granola_get_transcript", "meeting transcript")
    assert not granola_tool_result_successful("granola_get_meeting", None)
    assert not granola_tool_result_successful("granola_get_meeting", {"isError": True, "error": "oauth"})
    assert not granola_tool_result_successful("granola_get_meeting", "ERROR: auth failed")
    assert not granola_tool_result_successful("terminal", "meeting transcript")


def test_granola_semantic_hash_ignores_unrelated_next_payload():
    common = (
        '<meta property="og:title" content="Собеседование: Ростех"/>'
        '<meta property="og:description" content="Краткое резюме"/>'
    )
    flight = json.dumps(
        '<h3>Итоги</h3><ul><li>4 рекрутера</li><li>40 вакансий</li></ul>'
        ',"created_at":"2026-07-13T08:01:32.036Z"'
        ',"documentId":"61b71bf0-5790-408a-96da-223a362f39c7"'
    )
    first = parse_granola_share_html(
        GRANOLA_URL, common + f'<script>self.__next_f.push([1,{flight}])</script><i>nonce-a</i>'
    )
    second = parse_granola_share_html(
        GRANOLA_URL, common + f'<script>self.__next_f.push([1,{flight}])</script><i>nonce-b</i>'
    )
    assert first.content_hash == second.content_hash
    assert "zoom.us" not in first.knowledge_summary()


def test_transcript_artifact_is_idempotent_and_prompt_is_compact(tmp_path):
    text = _transcript()
    first = stage_transcript(text, artifact_root=tmp_path, chat_id="1", sender_id="u1")
    second = stage_transcript(text, artifact_root=tmp_path, chat_id="1", sender_id="u1")
    assert first.path == second.path
    assert first.sha256 == second.sha256
    assert len(list(tmp_path.rglob("*.txt"))) == 1
    prompt = staged_prompt(first)
    assert is_staged_prompt(prompt.replace(str(tmp_path), "/srv/hermes-artifacts/transcripts/test"))
    assert len(prompt) < 1200
    assert text[:200] not in prompt


def test_granola_knowledge_summary_cleans_empty_transcript_link_and_bank_wording():
    share = GranolaShare(
        source_url=GRANOLA_URL,
        title="Interview",
        description="",
        summary=(
            "Рекрутер не сравнивает с банковской СБ: структура другая\n"
            "Chat with meeting transcript:"
        ),
        document_id="61b71bf0-5790-408a-96da-223a362f39c7",
        created_at="2026-07-13T08:01:32.036Z",
        conferencing_url="",
        content_hash="a" * 64,
    )
    value = share.knowledge_summary()
    assert "СБ банка и структура Ростеха — принципиально разные контуры" in value
    assert "корпоративной СБ" not in value
    assert "Chat with meeting transcript" not in value
