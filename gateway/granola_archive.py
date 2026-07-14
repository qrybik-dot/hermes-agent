"""Canonical, deterministic archive for Granola meetings.

Summary cards live only in ``Meetings/Granola``.  Full transcripts are linked
text artifacts in a child directory so the Markdown FTS index does not ingest
the raw conversation twice.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any


DEFAULT_ARCHIVE_ROOT = Path("/srv/hermes-memory/vault/Meetings/Granola")
DEFAULT_STATE_PATH = Path("/srv/hermes-memory/staging/granola-sync/state.json")
MARKER_RE = re.compile(r"\[GRANOLA_TRANSCRIPT:([^\]]+)\]", re.I)
DOCUMENT_PATH_RE = re.compile(r"\bIt is saved at:\s+([^\]\n]+)", re.I)
SAFE_TEXT_SUFFIXES = {".txt", ".md", ".log", ".json", ".csv", ".xml", ".yaml", ".yml"}


@dataclass(frozen=True)
class GranolaMeeting:
    meeting_id: str
    title: str
    meeting_date: str
    summary: str
    source_url: str = ""
    content_hash: str = ""


@dataclass(frozen=True)
class ArchiveResult:
    status: str
    path: str | None
    meeting_id: str
    created: bool = False
    updated: bool = False
    duplicate: bool = False
    transcript_attached: bool = False
    reason: str = ""


def archive_root() -> Path:
    return Path(os.environ.get("HERMES_GRANOLA_ARCHIVE_ROOT", str(DEFAULT_ARCHIVE_ROOT)))


def state_path() -> Path:
    return Path(os.environ.get("HERMES_GRANOLA_STATE_PATH", str(DEFAULT_STATE_PATH)))


def _atomic_write(path: Path, text: str, *, mode: int = 0o660) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _norm(value: str) -> str:
    return re.sub(r"[^\w]+", " ", str(value or "").casefold(), flags=re.UNICODE).strip()


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "-", str(value or "")).strip("-.")
    return cleaned[:96] or hashlib.sha256(str(value).encode()).hexdigest()[:16]


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " - ", str(value or "")).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned[:150] or "Встреча Granola") + ".md"


def _date_only(value: str) -> str:
    text = str(value or "").strip()
    match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else date.today().isoformat()


def semantic_hash(title: str, meeting_date: str, summary: str) -> str:
    payload = "\n".join((_norm(title), _date_only(meeting_date), _norm(summary)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    result: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip().strip('"')
    return result


def _title_from_note(text: str) -> str:
    match = re.search(r"^#\s+(.+)$", text, re.M)
    return match.group(1).strip() if match else ""


def scan_archive(root: Path | None = None) -> list[dict[str, Any]]:
    base = root or archive_root()
    records: list[dict[str, Any]] = []
    if not base.exists():
        return records
    for path in sorted(base.glob("*.md")):
        if path.name.casefold().startswith("индекс"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta = _frontmatter(text)
        records.append({
            "path": path,
            "meeting_id": meta.get("granola_id", ""),
            "source_url": meta.get("source_url", ""),
            "content_hash": meta.get("content_hash", ""),
            "date": meta.get("date", ""),
            "title": _title_from_note(text),
            "text": text,
        })
    return records


def refresh_index(root: Path | None = None) -> None:
    base = root or archive_root()
    index = base / "Индекс встреч Granola.md"
    try:
        current = index.read_text(encoding="utf-8")
    except OSError:
        current = "# Индекс встреч Granola\n\nИсточник: [[Granola]]\n"
    first_entry = re.search(r"(?m)^- \[\[", current)
    prefix = current[: first_entry.start()].rstrip() if first_entry else current.rstrip()
    if not prefix:
        prefix = "# Индекс встреч Granola\n\nИсточник: [[Granola]]"
    entries = [
        f"- [[{path.stem}]]"
        for path in sorted(base.glob("*.md"), key=lambda item: item.name.casefold())
        if path != index
    ]
    rendered = prefix + "\n\n" + "\n".join(entries) + "\n"
    if rendered != current:
        _atomic_write(index, rendered)


def find_match(meeting: GranolaMeeting, root: Path | None = None) -> tuple[dict[str, Any] | None, str]:
    records = scan_archive(root)
    exact: list[dict[str, Any]] = []
    if meeting.meeting_id:
        exact = [r for r in records if r["meeting_id"] == meeting.meeting_id]
    if not exact and meeting.source_url:
        exact = [r for r in records if r["source_url"] == meeting.source_url]
    content_hash = meeting.content_hash or semantic_hash(meeting.title, meeting.meeting_date, meeting.summary)
    if not exact and content_hash:
        exact = [r for r in records if r["content_hash"] == content_hash]
    if not exact:
        exact = [r for r in records if _date_only(r["date"]) == _date_only(meeting.meeting_date) and _norm(r["title"]) == _norm(meeting.title)]
    if len(exact) == 1:
        return exact[0], "matched"
    if len(exact) > 1:
        return None, "ambiguous"
    return None, "new"


def _render(meeting: GranolaMeeting, *, transcript_rel: str = "") -> str:
    digest = meeting.content_hash or semantic_hash(meeting.title, meeting.meeting_date, meeting.summary)
    transcript_available = "true" if transcript_rel else "false"
    source_url = meeting.source_url.strip()
    archive_lines = []
    if source_url:
        archive_lines.append(f"- Источник: {source_url}")
    archive_lines.append(
        f"- Полная запись: [{Path(transcript_rel).name}]({transcript_rel})"
        if transcript_rel else "- Полная запись: недоступна; ожидается ответным сообщением."
    )
    summary = meeting.summary.strip() or "Самари пока недоступно."
    return (
        "---\n"
        "type: meeting\n"
        "source: granola\n"
        f"granola_id: {meeting.meeting_id}\n"
        f"date: {_date_only(meeting.meeting_date)}\n"
        f"transcript_available: {transcript_available}\n"
        f"source_url: {source_url}\n"
        f"content_hash: {digest}\n"
        "tags: [granola, meeting]\n"
        "---\n\n"
        f"# {meeting.title.strip() or 'Встреча Granola'}\n\n"
        "## Резюме Granola\n\n"
        f"{summary}\n\n"
        "## Следующие шаги\n\n"
        "- Уточняются по итогам встречи.\n\n"
        "## Архив\n\n"
        + "\n".join(archive_lines)
        + "\n"
    )


def _attach_transcript_to_existing(text: str, transcript_rel: str) -> str:
    updated = re.sub(
        r"(?m)^transcript_available:\s*(?:false|true)\s*$",
        "transcript_available: true",
        text,
        count=1,
    )
    link = f"- Полная запись: [{Path(transcript_rel).name}]({transcript_rel})"
    patterns = (
        r"(?m)^- Полная запись:.*$",
        r"(?m)^- Полный транскрипт:.*$",
    )
    for pattern in patterns:
        if re.search(pattern, updated):
            return re.sub(pattern, link, updated, count=1)
    if "## Архив" in updated:
        return updated.rstrip() + "\n" + link + "\n"
    return updated.rstrip() + "\n\n## Архив\n\n" + link + "\n"


def upsert_meeting(meeting: GranolaMeeting, *, transcript: str | None = None, dry_run: bool = False) -> ArchiveResult:
    root = archive_root()
    match, state = find_match(meeting, root)
    if state == "ambiguous":
        return ArchiveResult("blocked", None, meeting.meeting_id, reason="ambiguous duplicate candidates")
    path = Path(match["path"]) if match else root / _safe_filename(f"{_date_only(meeting.meeting_date)} — {meeting.title}")
    transcript_rel = ""
    if match:
        old = _frontmatter(str(match["text"]))
        old_link = re.search(r"- Полная запись: \[[^\]]+\]\(([^)]+)\)", str(match["text"]))
        transcript_rel = old_link.group(1) if old_link else ""
        if old.get("transcript_available") == "true" and not transcript:
            transcript_rel = transcript_rel or f"Transcripts/{_safe_id(meeting.meeting_id)}.txt"
    attached = False
    if transcript:
        transcript_rel = f"Transcripts/{_safe_id(meeting.meeting_id)}.txt"
        transcript_body = transcript.strip() + "\n"
        transcript_path = root / transcript_rel
        try:
            unchanged_transcript = transcript_path.read_text(encoding="utf-8") == transcript_body
        except OSError:
            unchanged_transcript = False
        if not dry_run and not unchanged_transcript:
            _atomic_write(root / transcript_rel, transcript.strip() + "\n", mode=0o640)
        attached = not unchanged_transcript
    if match and transcript:
        rendered = _attach_transcript_to_existing(str(match["text"]), transcript_rel)
    elif match:
        rendered = str(match["text"])
    else:
        rendered = _render(meeting, transcript_rel=transcript_rel)
    duplicate = bool(match and str(match["text"]) == rendered and not attached)
    if not dry_run and not duplicate:
        _atomic_write(path, rendered)
    if not dry_run:
        refresh_index(root)
    return ArchiveResult(
        "duplicate" if duplicate else "success",
        str(path),
        meeting.meeting_id,
        created=match is None,
        updated=match is not None and not duplicate,
        duplicate=duplicate,
        transcript_attached=attached,
    )


def load_state(path: Path | None = None) -> dict[str, Any]:
    target = path or state_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(data: dict[str, Any], path: Path | None = None) -> None:
    target = path or state_path()
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_write(target, json.dumps(data, ensure_ascii=False, indent=2) + "\n", mode=0o600)


def set_pending(meeting: GranolaMeeting, *, notified: bool = False) -> None:
    data = load_state()
    pending = data.setdefault("pending", {})
    existing = pending.get(meeting.meeting_id, {}) if isinstance(pending, dict) else {}
    pending[meeting.meeting_id] = {
        "title": meeting.title,
        "date": _date_only(meeting.meeting_date),
        "source_url": meeting.source_url,
        "notified": bool(existing.get("notified") or notified),
    }
    save_state(data)


def clear_pending(meeting_id: str) -> None:
    data = load_state()
    pending = data.get("pending")
    if isinstance(pending, dict):
        pending.pop(meeting_id, None)
    save_state(data)


def pending_prompt(meeting: GranolaMeeting) -> str:
    return (
        f"Самари встречи «{meeting.title}» сохранено в Meetings/Granola. "
        "Granola не отдала полный транскрипт. Пришли его ответом на это сообщение "
        "как текст или текстовый файл — я добавлю к той же встрече.\n"
        f"[GRANOLA_TRANSCRIPT:{meeting.meeting_id}]"
    )


def _trusted_document_path(processed_text: str, attachment_paths: tuple[str, ...] = ()) -> Path | None:
    candidates = [str(value) for value in attachment_paths if value]
    match = DOCUMENT_PATH_RE.search(processed_text or "")
    if match:
        candidates.append(match.group(1).strip().strip("'\"` "))
    if not candidates:
        return None
    roots = os.environ.get(
        "HERMES_GRANOLA_ATTACHMENT_ROOTS",
        "/home/hermes/.hermes/cache:/srv/hermes-artifacts/transcripts",
    ).split(":")
    for value in candidates:
        candidate = Path(value).resolve()
        allowed = False
        for raw in roots:
            try:
                candidate.relative_to(Path(raw).resolve())
                allowed = True
                break
            except (ValueError, OSError):
                continue
        if not allowed or candidate.suffix.casefold() not in SAFE_TEXT_SUFFIXES:
            continue
        try:
            if candidate.is_file() and candidate.stat().st_size <= 5_000_000:
                return candidate
        except OSError:
            continue
    return None


def handle_transcript_reply(*, current_text: str, processed_text: str = "", reply_text: str = "", attachment_paths: tuple[str, ...] = ()) -> ArchiveResult | None:
    marker = MARKER_RE.search("\n".join((current_text or "", reply_text or "", processed_text or "")))
    state = load_state()
    pending = state.get("pending") if isinstance(state.get("pending"), dict) else {}
    meeting_id = marker.group(1).strip() if marker else ""
    if not meeting_id and len(pending) == 1:
        meeting_id = next(iter(pending))
    explicit = bool(marker or meeting_id or re.search(r"\b(?:granola|гранол)\b", current_text or "", re.I))
    document = _trusted_document_path(processed_text, attachment_paths)
    text = ""
    if document:
        try:
            text = document.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ArchiveResult("blocked", None, meeting_id, reason="attachment cannot be read")
    elif len((current_text or "").strip()) >= 600:
        text = (current_text or "").strip()
    if marker and not text and (attachment_paths or DOCUMENT_PATH_RE.search(processed_text or "")):
        return ArchiveResult("blocked", None, meeting_id, reason="нужен текстовый файл до 5 МБ")
    if marker and not text and re.search(r"https?://", current_text or ""):
        return ArchiveResult("blocked", None, meeting_id, reason="ссылка не раскрывает полный транскрипт; пришли текст или текстовый файл")
    if not text or (not explicit and not marker and not meeting_id):
        return None
    record = pending.get(meeting_id) if meeting_id else None
    if isinstance(record, dict):
        meeting = GranolaMeeting(
            meeting_id=meeting_id,
            title=str(record.get("title") or "Встреча Granola"),
            meeting_date=str(record.get("date") or date.today().isoformat()),
            summary=_summary_for_existing(meeting_id),
            source_url=str(record.get("source_url") or ""),
        )
    else:
        meeting_id = meeting_id or "manual-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        first = next((line.strip(" #-\t") for line in text.splitlines() if line.strip()), "Встреча Granola")
        meeting = GranolaMeeting(meeting_id, first[:120], date.today().isoformat(), "Самари пока недоступно; добавлена полная запись вручную.")
    result = upsert_meeting(meeting, transcript=text)
    if result.status == "success":
        clear_pending(meeting.meeting_id)
    return result


def _summary_for_existing(meeting_id: str) -> str:
    for record in scan_archive():
        if record["meeting_id"] != meeting_id:
            continue
        match = re.search(r"## Резюме Granola\n\n(.*?)(?:\n\n## |\Z)", record["text"], re.S)
        if match:
            return match.group(1).strip()
    return "Самари пока недоступно."


__all__ = [
    "ArchiveResult", "GranolaMeeting", "clear_pending", "find_match",
    "handle_transcript_reply", "load_state", "pending_prompt", "save_state",
    "refresh_index", "scan_archive", "semantic_hash", "set_pending", "upsert_meeting",
]
