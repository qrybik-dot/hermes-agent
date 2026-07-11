#!/usr/bin/env python3
"""Disk threshold and seven-day growth monitor with deduplicated Telegram alerts."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

STATE = Path("/var/lib/hermes-disk-guard/state.json")
HERMES = "/home/hermes/hermes-runtime/shared/venv/bin/hermes"


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"history": [], "last_level": "ok"}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    total, used, free = shutil.disk_usage("/")
    percent = round(used * 100 / total)
    free_gib = round(free / 1024**3, 1)
    level = "critical" if percent >= 80 else "warning" if percent >= 70 else "ok"
    state = load_state()
    history = [h for h in state.get("history", []) if h.get("date") != date.today().isoformat()]
    history.append({"date": date.today().isoformat(), "used": used, "percent": percent})
    history = history[-14:]
    week = history[-8] if len(history) >= 8 else None
    growth_gib = round((used - week["used"]) / 1024**3, 1) if week else 0.0
    growth_alert = bool(week and growth_gib >= 3.0)
    changed = level != state.get("last_level")
    should_notify = args.notify and ((level != "ok" and changed) or growth_alert)
    message_id = None
    if should_notify:
        if level == "critical":
            text = f"🔴 Диск Hermes заполнен на {percent}%. Свободно {free_gib} ГБ. Нужна очистка."
        elif level == "warning":
            text = f"🟡 Диск Hermes заполнен на {percent}%. Свободно {free_gib} ГБ."
        else:
            text = f"🟡 Диск Hermes вырос на {growth_gib} ГБ за 7 дней. Сейчас занято {percent}%."
        proc = subprocess.run([HERMES, "send", "--to", "telegram", "--json", text], text=True, capture_output=True, timeout=30, cwd="/home/hermes")
        if proc.returncode == 0:
            try:
                message_id = json.loads(proc.stdout).get("message_id")
            except ValueError:
                message_id = "sent"
        else:
            raise SystemExit("telegram delivery failed")
    state.update({"history": history, "last_level": level, "updated_at": datetime.now(timezone.utc).isoformat()})
    save_state(state)
    result = {"status": "PASS", "used_percent": percent, "free_gib": free_gib, "level": level, "seven_day_growth_gib": growth_gib, "notified": bool(message_id), "message_id": message_id}
    print(json.dumps(result, ensure_ascii=False) if args.json else result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
