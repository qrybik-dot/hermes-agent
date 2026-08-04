#!/usr/bin/env python3
"""Send one explicit Telegram notice after a healthy Hermes gateway restart."""
from __future__ import annotations

import json
import os
import random
import socket
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml

PROFILE_DIR = Path(os.environ.get("HERMES_HOME", "/home/hermes/.hermes"))
PHRASES_FILE = PROFILE_DIR / "status_phrases.yaml"
STATE_FILE = PROFILE_DIR / "startup-notify-state.json"
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _run(*args: str, timeout: int = 12) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def gateway_state() -> tuple[bool, int]:
    active = _run("systemctl", "is-active", "--quiet", "hermes-gateway.service").returncode == 0
    result = _run("systemctl", "show", "hermes-gateway.service", "-p", "MainPID", "--value")
    pid = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
    return active and pid > 0, pid


def journal_errors() -> list[str]:
    patterns = (
        "telegram failed",
        "no connected platforms",
        "Traceback",
        "ERROR gateway",
        "failed with result",
    )
    result = _run(
        "journalctl", "-u", "hermes-gateway.service", "--since", STARTED_AT,
        "--no-pager", "-n", "250",
    )
    if result.returncode != 0:
        return [f"journalctl exit {result.returncode}"]
    output = (result.stdout + result.stderr).casefold()
    return [pattern for pattern in patterns if pattern.casefold() in output]


def telegram_config() -> tuple[str | None, str | None]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_HOME_CHANNEL") or os.environ.get("TELEGRAM_CHAT_ID")
    env_path = PROFILE_DIR / ".env"
    if env_path.exists() and (not token or not chat_id):
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip("\"'")
            if key == "TELEGRAM_BOT_TOKEN" and not token:
                token = value
            elif key in {"TELEGRAM_HOME_CHANNEL", "TELEGRAM_CHAT_ID"} and not chat_id:
                chat_id = value
    return token, chat_id


def already_notified(pid: int) -> bool:
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return False
    return int(state.get("gateway_pid") or 0) == pid


def record_notified(pid: int) -> None:
    STATE_FILE.write_text(
        json.dumps({"gateway_pid": pid, "sent_at": datetime.now().isoformat()}, ensure_ascii=False),
        encoding="utf-8",
    )


def choose_detail() -> str:
    fallback = [
        "Сервис прошёл проверку и готов принимать задачи.",
        "Gateway active, критических ошибок запуска не найдено.",
        "Перезапуск завершён, можно продолжать работу.",
    ]
    try:
        data = yaml.safe_load(PHRASES_FILE.read_text(encoding="utf-8")) or {}
        phrases = [str(item).strip() for item in data.get("phrases", []) if str(item).strip()]
    except Exception:
        phrases = []
    detail = random.choice(phrases or fallback)
    return detail.removeprefix("🟢").strip()


def send_message(token: str, chat_id: str, text: str) -> bool:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat_id, "text": text, "disable_notification": True}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout) as exc:
        print(f"telegram_send_failed:{type(exc).__name__}")
        return False


def main() -> int:
    time.sleep(5)
    token, chat_id = telegram_config()
    if not token or not chat_id:
        print("startup_notify_skipped:telegram_config_missing")
        return 0

    active, pid = gateway_state()
    if not active:
        send_message(token, chat_id, "🔴 Hermes перезапускался, но Gateway не вышел в active state.")
        return 1
    if already_notified(pid):
        print(f"startup_notify_skipped:already_sent pid={pid}")
        return 0

    errors = journal_errors()
    if errors:
        sent = send_message(
            token,
            chat_id,
            "🟠 Hermes перезапущен, но стартовая проверка обнаружила проблему: " + errors[0],
        )
    else:
        sent = send_message(
            token,
            chat_id,
            "🟢 Hermes перезапущен и снова онлайн. " + choose_detail(),
        )
    if sent:
        record_notified(pid)
        print(f"startup_notify_sent pid={pid} errors={len(errors)}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
