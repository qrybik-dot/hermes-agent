"""Deterministic public Granola share ingestion."""
from __future__ import annotations

from dataclasses import dataclass
import re

from gateway.granola_share import (
    extract_granola_share_url, fetch_granola_share, is_granola_share_url,
)
from gateway.quick_note_capture import QuickNote, run_quick_save


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


def _quick_save_ok(value: dict) -> bool:
    saved = bool(value.get("saved") or value.get("already_exists"))
    try:
        readback = int(value.get("readback_count") or 0) > 0
    except (TypeError, ValueError):
        readback = False
    return saved and readback


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

    safe_summary = share.knowledge_summary()
    facts = [
        line.lstrip("• ").strip()
        for line in safe_summary.splitlines()
        if line.strip() and not line.startswith(("Дата Granola:", "Granola document ID:", "Источник:"))
    ]
    note = QuickNote(
        payload={
            "knowledge_project": "career",
            "type": "evidence",
            "title": ("Встреча Granola: " + share.title)[:240],
            "summary": safe_summary,
            "accepted_facts": facts[:12],
            "evidence": [f"Granola document ID: {share.document_id}", share.source_url],
            "sources": [share.source_url],
            "sensitivity": "internal",
            "gpt_access": "allowed",
            "source_system": "granola",
            "source_workspace": "telegram",
            "granola_document_id": share.document_id,
            "granola_content_hash": share.content_hash,
        },
        idempotency_key="granola-share:" + share.content_hash,
    )
    try:
        saved = run_quick_save(note)
    except Exception as exc:
        saved = {"status": "error", "saved": False, "message": str(exc), "readback_count": 0}
    if not _quick_save_ok(saved):
        return GranolaIngestResult(
            status="failed",
            text=(
                "INCOMPLETE\nGranola прочитана, но сохранение в Knowledge не подтверждено: "
                + str(saved.get("message") or saved.get("status") or "unknown")[:300]
            ),
            evidence=evidence,
            knowledge_readback=False,
            tool_call_count=2,
        )
    return GranolaIngestResult(
        status="success",
        text=(
            "READY\nGranola прочитана и сохранена в Hermes Knowledge.\n"
            f"Встреча: {share.title}\n"
            f"Документ: {share.document_id or 'public share'}\n"
            f"Источник: {share.source_url}"
        ),
        evidence=evidence,
        knowledge_readback=True,
        tool_call_count=2,
    )


__all__ = ["GranolaIngestResult", "ingest_public_granola", "should_ingest_public_granola"]
