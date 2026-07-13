"""Read-only Google Calendar source for the Telegram daily card."""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from hermes_constants import get_hermes_home


def _parse_start(value: str, timezone: ZoneInfo) -> tuple[int, dt.date, bool]:
    value = str(value or "").strip()
    if not value:
        raise ValueError("calendar event has no start")
    if "T" not in value:
        day = dt.date.fromisoformat(value)
        local = dt.datetime.combine(day, dt.time.min, tzinfo=timezone)
        return int(local.timestamp()), day, True
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    local = parsed.astimezone(timezone)
    return int(parsed.timestamp()), local.date(), False


def _preparation_hints(description: str) -> list[str]:
    text = str(description or "").casefold()
    hints: list[str] = []
    if "полис" in text:
        hints.append("Взять полис")
    if "направлен" in text:
        hints.append("Взять направление")
    if "результат" in text or "анализ" in text:
        hints.append("Взять результаты обследований")
    return hints


def _is_online_location(value: str) -> bool:
    text = str(value or "").casefold()
    markers = (
        "zoom",
        "google meet",
        "meet.google",
        "teams",
        "telemost",
        "телемост",
        "онлайн",
        "online",
    )
    return any(marker in text for marker in markers)


def fetch_google_calendar_payloads(
    target_dates: Iterable[dt.date],
    timezone: ZoneInfo,
    *,
    hermes_home: Path | None = None,
    timeout: int = 25,
) -> list[dict[str, Any]]:
    """Return normalized events for requested local dates.

    The existing Google Workspace helper remains the credential boundary. This
    function never reads token files directly and intentionally does not copy
    event descriptions into the daily card cache.
    """
    home = hermes_home or get_hermes_home()
    script = home / "skills/productivity/google-workspace/scripts/google_api.py"
    candidates = [
        home.parent / "hermes-runtime/shared/venv/bin/python",
        home / "hermes-agent/venv/bin/python",
        Path(sys.executable),
    ]
    python = next((path for path in candidates if path.exists()), None)
    if not script.exists() or python is None:
        raise RuntimeError("Google Calendar helper is unavailable")

    result = subprocess.run(
        [str(python), str(script), "calendar", "list", "--max", "100"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Google Calendar helper failed")
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Google Calendar returned invalid JSON") from exc
    if not isinstance(raw, list):
        raise RuntimeError("Google Calendar returned an unexpected payload")

    wanted = set(target_dates)
    payloads: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or item.get("status") == "cancelled":
            continue
        try:
            event_at, local_date, all_day = _parse_start(
                item.get("start", ""), timezone
            )
        except (TypeError, ValueError):
            continue
        if local_date not in wanted:
            continue
        event_id = str(item.get("id") or "").strip()
        title = " ".join(str(item.get("summary") or "Без названия").split())[:180]
        location = " ".join(str(item.get("location") or "").split())[:300]
        online = _is_online_location(location)
        online_url = (
            str(item.get("hangoutLink") or item.get("htmlLink") or "").strip()
            if online
            else ""
        )
        if not event_id or not title:
            continue
        payloads.append({
            "title": title,
            "event_at": event_at,
            "timezone": str(timezone.key),
            "location": location,
            "address": "" if online else location,
            "online_url": online_url,
            "requires_travel": bool(location) and not online,
            "preparation": _preparation_hints(item.get("description", "")),
            "importance": 1,
            "source_kind": "google_calendar",
            "source_id": event_id,
            "event_id": "gcal-" + event_id,
            "all_day": all_day,
        })
    return payloads
