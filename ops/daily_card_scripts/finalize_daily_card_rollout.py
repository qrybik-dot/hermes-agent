#!/usr/bin/env python3
"""Finalize the daily-card rollout only after a pinned live card exists."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cron.jobs import pause_job, resolve_job_ref, update_job
from hermes_constants import get_hermes_home


def main() -> int:
    home = get_hermes_home()
    today = dt.datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
    state_db = home / "daily_cards.db"
    if not state_db.is_file():
        raise SystemExit("daily_cards.db is missing")

    with sqlite3.connect(state_db) as conn:
        row = conn.execute(
            """
            SELECT message_id, pinned FROM daily_cards
             WHERE card_date=? AND card_kind='morning' AND platform='telegram'
             ORDER BY updated_at DESC LIMIT 1
            """,
            (today,),
        ).fetchone()
    if row is None or int(row[1] or 0) != 1:
        raise SystemExit("refusing rollout: today's pinned Telegram card is not confirmed")

    briefing = pause_job(
        "daily-family-briefing",
        reason="Replaced by structured pinned daily card",
    )
    if briefing is None:
        raise SystemExit("daily-family-briefing job not found")

    watchdog = resolve_job_ref("git-status-watchdog")
    if watchdog is None:
        raise SystemExit("git-status-watchdog job not found")
    watchdog = update_job(watchdog["id"], {"deliver": "local"})
    if watchdog is None:
        raise SystemExit("could not update git-status-watchdog")

    config_path = home / "config.yaml"
    config_text = config_path.read_text(encoding="utf-8")
    if "  wrap_response: true" in config_text:
        config_text = config_text.replace(
            "  wrap_response: true",
            "  wrap_response: false",
            1,
        )
        config_path.write_text(config_text, encoding="utf-8")
    elif "  wrap_response: false" not in config_text:
        raise SystemExit("cron.wrap_response setting not found")

    print(
        json.dumps(
            {
                "status": "ready",
                "card_message_id": str(row[0]),
                "daily_family_briefing_enabled": bool(briefing.get("enabled")),
                "git_watchdog_delivery": watchdog.get("deliver"),
                "cron_wrap_response": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
