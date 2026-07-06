"""Validation for the fixed daily-task JSON staging file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MAX_BYTES = 32 * 1024
ALLOWED_KEYS = frozenset(
    {
        "title",
        "body",
        "planned_for",
        "due_at",
        "timezone",
        "importance",
        "assignee",
        "idempotency_key",
    }
)


def load_task_payload(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("daily task payload must be a regular non-symlink file")
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("daily task payload is too large")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("daily task payload is invalid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("daily task payload must be a JSON object")
    unknown = sorted(set(data) - ALLOWED_KEYS)
    if unknown:
        raise ValueError("unsupported fields: " + ", ".join(unknown))
    title = " ".join(str(data.get("title") or "").split())[:180]
    planned_for = str(data.get("planned_for") or "").strip()
    due_at = str(data.get("due_at") or "").strip()
    if not title:
        raise ValueError("title is required")
    if not planned_for and not due_at:
        raise ValueError("planned_for or due_at is required")
    data["title"] = title
    data["planned_for"] = planned_for
    data["due_at"] = due_at
    return data
