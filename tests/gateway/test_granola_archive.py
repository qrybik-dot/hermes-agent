from pathlib import Path

from gateway.granola_archive import (
    GranolaMeeting, find_match, handle_transcript_reply, load_state,
    pending_prompt, refresh_index, set_pending, upsert_meeting,
)
from gateway.granola_sync import parse_meeting_list, parse_summary


def _configure(monkeypatch, tmp_path):
    archive = tmp_path / "Meetings" / "Granola"
    state = tmp_path / "state.json"
    monkeypatch.setenv("HERMES_GRANOLA_ARCHIVE_ROOT", str(archive))
    monkeypatch.setenv("HERMES_GRANOLA_STATE_PATH", str(state))
    monkeypatch.setenv("HERMES_GRANOLA_ATTACHMENT_ROOTS", str(tmp_path / "attachments"))
    return archive, state


def _meeting(meeting_id="m-1", title="Созвон", day="2026-07-14", summary="Краткое самари", source_url="https://notes.granola.ai/t/00000000-0000-0000-0000-000000000001"):
    return GranolaMeeting(meeting_id, title, day, summary, source_url)


def _cards(archive):
    return [path for path in archive.glob("*.md") if not path.name.casefold().startswith("индекс")]


def test_upsert_is_idempotent_by_granola_id(monkeypatch, tmp_path):
    archive, _ = _configure(monkeypatch, tmp_path)
    first = upsert_meeting(_meeting())
    second = upsert_meeting(_meeting(summary="Изменившееся самари не плодит карточку"))
    assert first.created is True
    assert second.duplicate is True
    assert len(_cards(archive)) == 1


def test_dedupe_falls_back_to_title_and_date(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    upsert_meeting(_meeting(meeting_id="old"))
    match, status = find_match(_meeting(meeting_id="new", title="  СОЗВОН "))
    assert status == "matched"
    assert match["meeting_id"] == "old"


def test_index_keeps_header_and_lists_each_card_once(monkeypatch, tmp_path):
    archive, _ = _configure(monkeypatch, tmp_path)
    archive.mkdir(parents=True)
    index = archive / "Индекс встреч Granola.md"
    index.write_text("# Индекс встреч Granola\n\nСохранённая шапка\n\n- [[old]]\n", encoding="utf-8")
    upsert_meeting(_meeting(meeting_id="a", title="Первая", source_url=""))
    upsert_meeting(_meeting(meeting_id="b", title="Вторая", day="2026-07-15", source_url=""))
    refresh_index()
    text = index.read_text(encoding="utf-8")
    assert "Сохранённая шапка" in text
    assert text.count("- [[") == 2
    assert text.count("Первая") == 1
    assert text.count("Вторая") == 1


def test_ambiguous_candidates_block_write(monkeypatch, tmp_path):
    archive, _ = _configure(monkeypatch, tmp_path)
    archive.mkdir(parents=True)
    base = "---\ntype: meeting\nsource: granola\ngranola_id: {mid}\ndate: 2026-07-14\ntranscript_available: false\ntags: [meeting, granola]\n---\n\n# Созвон\n"
    (archive / "a.md").write_text(base.format(mid="a"), encoding="utf-8")
    (archive / "b.md").write_text(base.format(mid="b"), encoding="utf-8")
    result = upsert_meeting(_meeting(meeting_id="new"))
    assert result.status == "blocked"
    assert len(_cards(archive)) == 2


def test_transcript_reply_updates_same_card_without_second_markdown(monkeypatch, tmp_path):
    archive, _ = _configure(monkeypatch, tmp_path)
    meeting = _meeting()
    upsert_meeting(meeting)
    set_pending(meeting)
    transcript = ("Спикер: полный текст встречи.\n" * 40).strip()
    result = handle_transcript_reply(
        current_text=transcript,
        reply_text=pending_prompt(meeting),
    )
    assert result.status == "success"
    assert result.transcript_attached is True
    assert len(_cards(archive)) == 1
    assert len(list((archive / "Transcripts").glob("*.txt"))) == 1
    assert "m-1" not in load_state().get("pending", {})
    note = _cards(archive)[0].read_text(encoding="utf-8")
    assert "transcript_available: true" in note
    assert "## Резюме Granola\n\nКраткое самари" in note


def test_single_pending_accepts_text_file_but_rejects_path_escape(monkeypatch, tmp_path):
    archive, _ = _configure(monkeypatch, tmp_path)
    meeting = _meeting()
    upsert_meeting(meeting)
    set_pending(meeting)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret" * 200, encoding="utf-8")
    result = handle_transcript_reply(
        current_text="полная запись",
        processed_text=f"[The user sent a document. It is saved at: {outside}]",
    )
    assert result is None
    assert not (archive / "Transcripts").exists()


def test_reply_uses_trusted_attachment_path(monkeypatch, tmp_path):
    archive, _ = _configure(monkeypatch, tmp_path)
    meeting = _meeting()
    upsert_meeting(meeting)
    set_pending(meeting)
    attachments = tmp_path / "attachments"
    attachments.mkdir()
    document = attachments / "meeting.txt"
    document.write_text("Полная запись\n" + ("реплика\n" * 100), encoding="utf-8")
    result = handle_transcript_reply(
        current_text="держи файл",
        reply_text=pending_prompt(meeting),
        attachment_paths=(str(document),),
    )
    assert result.status == "success"
    assert len(list((archive / "Transcripts").glob("*.txt"))) == 1


def test_marker_blocks_unsupported_attachment_instead_of_falling_to_llm(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    meeting = _meeting()
    upsert_meeting(meeting)
    set_pending(meeting)
    attachments = tmp_path / "attachments"
    attachments.mkdir()
    document = attachments / "meeting.pdf"
    document.write_bytes(b"%PDF-1.4")
    result = handle_transcript_reply(
        current_text="держи файл",
        reply_text=pending_prompt(meeting),
        attachment_paths=(str(document),),
    )
    assert result.status == "blocked"


def test_explicit_unmatched_granola_transcript_creates_manual_card(monkeypatch, tmp_path):
    archive, _ = _configure(monkeypatch, tmp_path)
    transcript = "Granola, полный транскрипт новой встречи\n" + ("диалог\n" * 120)
    result = handle_transcript_reply(current_text=transcript)
    assert result.created is True
    assert len(_cards(archive)) == 1


def test_sync_parsers_do_not_need_llm():
    listed = '<meeting id="11111111-1111-1111-1111-111111111111" title="A &amp; B" date="Jul 14, 2026 03:30 PM">'
    meetings = parse_meeting_list(listed)
    assert meetings == [{"id": "11111111-1111-1111-1111-111111111111", "title": "A & B", "date": "2026-07-14"}]
    assert parse_summary("<summary><b>Итог</b><br>Следующий шаг</summary>") == "Итог\nСледующий шаг"
