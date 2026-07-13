"""Deterministic capture of explicit Kanban task commands.

This path intentionally runs before session continuation and model routing.  It
only accepts an explicit command containing the word "task", so ordinary plans
or discussions about tasks remain available to the agent.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from hermes_cli import kanban_db


_QUICK_TASK_RE = re.compile(
    r"^\s*(?:пожалуйста[,\s]+)?"
    r"(?:запиши|создай|добавь|зафиксируй|поставь)\s+"
    r"(?:мне\s+)?задач(?:у|ку)\s*[:\-—]?\s*(?P<title>.+?)\s*[.!?]*\s*$",
    re.IGNORECASE | re.DOTALL,
)
_NEGATED_RE = re.compile(
    r"^\s*(?:не|не\s+надо|не\s+нужно)\s+"
    r"(?:записывай|создавай|добавляй|фиксируй|ставь)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QuickTaskCaptureResult:
    created: bool
    title: str
    task_id: str


def detect_quick_task(text: str) -> str | None:
    """Return a normalized title for an unambiguous task-capture command."""
    value = str(text or "").strip()
    if not value or _NEGATED_RE.search(value):
        return None
    match = _QUICK_TASK_RE.fullmatch(value)
    if match is None:
        return None
    title = re.sub(r"\s+", " ", match.group("title")).strip(" \t\r\n:;,.!?—-")
    if len(title) < 3:
        return None
    return title[:1].upper() + title[1:]


def capture_quick_task(
    text: str,
    *,
    platform: str,
    chat_id: str,
    request_id: str,
    session_id: str | None = None,
) -> QuickTaskCaptureResult | None:
    """Create exactly one idempotent Kanban card without invoking an LLM."""
    title = detect_quick_task(text)
    if title is None:
        return None
    identity = "\x1f".join((platform, str(chat_id), str(request_id)))
    idempotency_key = "gateway-quick-task:" + hashlib.sha256(identity.encode()).hexdigest()
    with kanban_db.connect_closing() as conn:
        existing = conn.execute(
            "SELECT id FROM tasks WHERE idempotency_key = ? AND status != 'archived' "
            "ORDER BY created_at DESC LIMIT 1",
            (idempotency_key,),
        ).fetchone()
        task_id = kanban_db.create_task(
            conn,
            title=title,
            body=text.strip(),
            created_by="gateway-quick-task",
            idempotency_key=idempotency_key,
            session_id=session_id or None,
        )
    return QuickTaskCaptureResult(existing is None, title, task_id)
