"""Validation for the fixed daily-event JSON staging file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MAX_BYTES = 64 * 1024
ALLOWED_KEYS = frozenset(
    {
        "title",
        "event_at",
        "timezone",
        "person",
        "address",
        "location",
        "online_url",
        "requires_travel",
        "preparation",
        "importance",
        "source_kind",
        "source_id",
        "event_id",
        "all_day",
        "travel_mode",
        "route_minutes",
        "route_buffer_minutes",
    }
)


def load_event_payload(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("daily event payload must be a regular non-symlink file")
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("daily event payload is too large")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("daily event payload is invalid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("daily event payload must be a JSON object")
    unknown = sorted(set(data) - ALLOWED_KEYS)
    if unknown:
        raise ValueError("unsupported fields: " + ", ".join(unknown))
    title = " ".join(str(data.get("title") or "").split())
    event_at = str(data.get("event_at") or "").strip()
    if not title or not event_at:
        raise ValueError("title and event_at are required")
    preparation = data.get("preparation") or []
    if isinstance(preparation, str):
        preparation = [preparation]
    if not isinstance(preparation, list) or not all(
        isinstance(item, str) for item in preparation
    ):
        raise ValueError("preparation must be a string list")
    data["title"] = title
    data["event_at"] = event_at
    data["preparation"] = preparation
    return data
