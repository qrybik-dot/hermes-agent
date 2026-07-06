#!/usr/bin/env python3
"""Idempotently install the no-LLM cron jobs for Telegram daily cards."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cron.jobs import create_job, list_jobs


SPECS = (
    {
        "name": "daily-card-morning",
        "schedule": "0 4 * * *",  # 07:00 Europe/Moscow
        "script": "daily_card_morning.py",
    },
    {
        "name": "daily-card-evening",
        "schedule": "30 17 * * *",  # 20:30 Europe/Moscow
        "script": "daily_card_evening.py",
    },
    {
        "name": "daily-card-action-watch",
        "schedule": "*/10 * * * *",
        "script": "daily_card_action.py",
    },
)


def main() -> int:
    existing = {job.get("name"): job for job in list_jobs(include_disabled=True)}
    results = []
    for spec in SPECS:
        current = existing.get(spec["name"])
        if current is not None:
            results.append(
                {
                    "name": spec["name"],
                    "status": "exists",
                    "id": current.get("id"),
                    "enabled": bool(current.get("enabled")),
                }
            )
            continue
        job = create_job(
            prompt=None,
            schedule=spec["schedule"],
            name=spec["name"],
            deliver="local",
            script=spec["script"],
            no_agent=True,
        )
        results.append(
            {
                "name": spec["name"],
                "status": "created",
                "id": job["id"],
                "enabled": bool(job.get("enabled")),
                "next_run_at": job.get("next_run_at"),
            }
        )
    print(json.dumps(results, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
