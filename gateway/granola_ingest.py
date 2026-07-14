"""Deterministic public Granola share ingestion."""
from __future__ import annotations

from dataclasses import dataclass
import re

from gateway.granola_share import (
    extract_granola_share_url, fetch_granola_share, is_granola_share_url,
)
from gateway.granola_archive import GranolaMeeting, set_pending, upsert_meeting


_EXPLICIT_OTHER_TARGET_RE = re.compile(
    r"\b(?:в|во)\s+(?:google\s+)?(?:календар|calendar|встреч|событи|письм|gmail|"
    r"google\s+drive|гугл\s+диск|диск)\w*\b|"
    r"\b(?:отправ|перешл)\w*[^\n.!?]{0,80}\b(?:письм|gmail|email|e-mail)\w*\b",
    re.I,
)
_GRANOLA_ACTION_RE = re.compile(
    r"\b(?:прочит|посмотр|покаж|разбер|проанализ|сохран|добав|занес|запиш)\w*\b|"
    r"\b(?:granola|гранол|баз[уы]|knowledge|памят[ьи]|инфо|данн|заметк|транскрипт)\w*\b",
    re.I,
)


def should_ingest_public_granola(text: str) -> bool:
    value = str(text or "").strip()
    if not extract_granola_share_url(value):
        return False
    if is_granola_share_url(value):
        return True
    if _EXPLICIT_OTHER_TARGET_RE.search(value):
        return False
    return bool(_GRANOLA_ACTION_RE.search(value))

_NO_SAVE_RE = re.compile(
    r"\b(?:не\s+сохраняй|не\s+добавляй|только\s+(?:прочитай|посмотри|покажи)|без\s+сохранения)\b",
    re.I,
)


@dataclass(frozen=True)
class GranolaIngestResult:
    status: str
    text: str
    evidence: dict
    knowledge_readback: bool | None
    tool_call_count: int


def ingest_public_granola(text: str) -> GranolaIngestResult | None:
    if not should_ingest_public_granola(text):
        return None
    url = extract_granola_share_url(text)
    if not url:
        return None
    try:
        share = fetch_granola_share(url)
    except Exception as exc:
        return GranolaIngestResult(
            status="blocked",
            text="BLOCKED\nНе удалось прочитать публичную ссылку Granola: " + str(exc)[:300],
            evidence={},
            knowledge_readback=None,
            tool_call_count=1,
        )

    evidence = {
        "source_url": share.source_url,
        "title": share.title,
        "document_id": share.document_id,
        "created_at": share.created_at,
        "content_hash": share.content_hash,
        "read_back": True,
    }
    if _NO_SAVE_RE.search(text or ""):
        preview = (share.summary or share.description)[:3000]
        return GranolaIngestResult(
            status="success",
            text="READY\nGranola прочитана без сохранения.\n\n" + share.title + "\n\n" + preview,
            evidence=evidence,
            knowledge_readback=None,
            tool_call_count=1,
        )

    meeting = GranolaMeeting(
        meeting_id=share.document_id or share.content_hash[:24],
        title=share.title,
        meeting_date=share.created_at,
        summary=share.knowledge_summary(),
        source_url=share.source_url,
        content_hash=share.content_hash,
    )
    try:
        saved = upsert_meeting(meeting)
    except Exception as exc:
        saved = None
        save_error = str(exc)
    else:
        save_error = ""
    if saved is None or saved.status not in {"success", "duplicate"}:
        return GranolaIngestResult(
            status="failed",
            text=(
                "PARTIAL\nGranola прочитана, но сохранение в Meetings/Granola не подтверждено: "
                + (save_error or getattr(saved, "reason", "unknown"))[:300]
            ),
            evidence=evidence,
            knowledge_readback=False,
            tool_call_count=1,
        )
    set_pending(meeting)
    return GranolaIngestResult(
        status="success",
        text=(
            "Granola прочитана и самари сохранено в Meetings/Granola.\n"
            "Полная запись недоступна: пришли её ответом как текст или текстовый файл.\n"
            f"[GRANOLA_TRANSCRIPT:{meeting.meeting_id}]"
        ),
        evidence=evidence,
        knowledge_readback=True,
        tool_call_count=1,
    )


__all__ = ["GranolaIngestResult", "ingest_public_granola", "should_ingest_public_granola"]
