"""Bounded staging for large pasted meeting transcripts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

_TRANSCRIPT_SPEAKER_RE = re.compile(
    r"(?m)^\s*(?:Me|Them|Speaker\s*\d+|Я|Собеседник)\s*:", re.I
)
_TRANSCRIPT_TIME_RE = re.compile(
    r"(?m)^\s*-\s*(?:[01]?\d|2[0-3]):[0-5]\d(?:am|pm)?\s*-\s*$", re.I
)
_SAVE_TRAILER_RE = re.compile(
    r"(?is)(?:вот\s+)?(?:полная\s+)?(?:запись|транскрипт|транскрибаци[яю])?[^\n]{0,80}"
    r"\b(?:добавь|сохрани|запиши|занеси|зафиксируй)\w*\b[^\n]{0,80}"
    r"\b(?:в\s+)?(?:баз[уы]|knowledge|памят[ьи])\b\s*[.!?]*$"
)


@dataclass(frozen=True)
class StagedTranscript:
    path: str
    metadata_path: str
    sha256: str
    char_count: int
    explicit_save: bool


def looks_like_transcript(text: str) -> bool:
    value = str(text or "")
    return (
        len(value) >= 2500
        and len(_TRANSCRIPT_SPEAKER_RE.findall(value)) >= 4
        and len(_TRANSCRIPT_TIME_RE.findall(value)) >= 2
    )


def transcript_save_requested(text: str) -> bool:
    return bool(_SAVE_TRAILER_RE.search(str(text or "").strip()))


def stage_transcript(
    text: str,
    *,
    artifact_root: str | Path = "/srv/hermes-artifacts/transcripts",
    source: str = "telegram",
    chat_id: str = "",
    sender_id: str = "",
) -> StagedTranscript:
    value = str(text or "")
    if not looks_like_transcript(value):
        raise ValueError("text does not look like a meeting transcript")
    encoded = value.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    now = datetime.now(timezone.utc)
    root = Path(artifact_root) / now.strftime("%Y/%m")
    root.mkdir(parents=True, exist_ok=True)
    base = f"telegram-{digest[:20]}"
    path = root / f"{base}.txt"
    meta_path = root / f"{base}.json"
    if not path.exists():
        temp = path.with_suffix(".txt.tmp")
        temp.write_bytes(encoded)
        os.chmod(temp, 0o640)
        os.replace(temp, path)
    metadata = {
        "schema": 1,
        "source": source,
        "chat_id": str(chat_id or ""),
        "sender_id": str(sender_id or ""),
        "created_at": now.isoformat(),
        "sha256": digest,
        "char_count": len(value),
        "explicit_save": transcript_save_requested(value),
        "artifact_path": str(path),
    }
    temp_meta = meta_path.with_suffix(".json.tmp")
    temp_meta.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.chmod(temp_meta, 0o640)
    os.replace(temp_meta, meta_path)
    return StagedTranscript(
        path=str(path),
        metadata_path=str(meta_path),
        sha256=digest,
        char_count=len(value),
        explicit_save=bool(metadata["explicit_save"]),
    )


def is_staged_prompt(text: str) -> bool:
    value = str(text or "")
    return (
        value.startswith("[System: Telegram собрал многочастный транскрипт")
        and "/srv/hermes-artifacts/transcripts/" in value
        and "SHA-256:" in value
    )


def staged_prompt(staged: StagedTranscript) -> str:
    save_instruction = (
        "После извлечения сохрани чистую структурированную карточку в Hermes Knowledge "
        "и сделай read-back. Не помещай сырой полный транскрипт в Knowledge: "
        "он уже сохранён как артефакт."
        if staged.explicit_save
        else "Проанализируй транскрипт и ответь по текущему контексту; "
        "ничего не сохраняй без явной команды."
    )
    return (
        "[System: Telegram собрал многочастный транскрипт и безопасно вынес его "
        "из диалогового контекста.]\n"
        f"Артефакт: {staged.path}\nSHA-256: {staged.sha256}\n"
        f"Символов: {staged.char_count}\n\n"
        "Прочитай файл частями через read_file. Извлеки только подтверждённые факты, "
        "решения, риски и следующие шаги. "
        + save_instruction
    )


__all__ = [
    "StagedTranscript",
    "is_staged_prompt",
    "looks_like_transcript",
    "stage_transcript",
    "staged_prompt",
    "transcript_save_requested",
]
