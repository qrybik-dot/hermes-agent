#!/usr/bin/env python3
"""Render a daily card from explicit test databases without Telegram delivery."""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_cli import daily_card as dc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("morning", "evening"))
    parser.add_argument("--kanban-db", type=Path, required=True)
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--timezone", default="Europe/Moscow")
    parser.add_argument("--sync-calendar", action="store_true")
    args = parser.parse_args()

    target = dt.date.fromisoformat(args.date)
    timezone = ZoneInfo(args.timezone)
    conn = dc.connect_state(args.state_db)
    try:
        if args.sync_calendar:
            dates = [target]
            if args.kind == "evening":
                dates.append(target + dt.timedelta(days=1))
            dc.sync_google_calendar_events(conn, dates, timezone)
        tasks = dc.select_tasks(args.kanban_db, target, timezone)
        if args.kind == "morning":
            render = dc.render_morning(
                target,
                tasks,
                dc.select_events(conn, target, timezone),
                max_buttons=6,
            )
        else:
            render = dc.render_evening(
                target,
                tasks,
                dc.select_events(conn, target + dt.timedelta(days=1), timezone),
                max_buttons=6,
            )
    finally:
        conn.close()

    if render is None:
        print("NO_CONTENT")
    else:
        print(render.text)
        print(f"\nBUTTONS={len(render.buttons)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
