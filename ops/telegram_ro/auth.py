#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import getpass
import json
import os
from pathlib import Path

STATE_DIR = Path("/var/lib/hermes-telegram-ro")
CONFIG_PATH = STATE_DIR / "config.json"
SESSION_BASE = STATE_DIR / "account"


def _write_config(api_id: int, api_hash: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_DIR / ".config.json.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"api_id": api_id, "api_hash": api_hash}, fh)
        fh.write("\n")
    os.replace(tmp, CONFIG_PATH)
    os.chmod(CONFIG_PATH, 0o600)


async def main() -> None:
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError

    print("Telegram read-only authorization. Credentials stay in /var/lib/hermes-telegram-ro (0600).")
    api_id = int(input("api_id: ").strip())
    api_hash = getpass.getpass("api_hash: ").strip()
    phone = input("phone (+...): ").strip()
    if api_id <= 0 or not api_hash or not phone:
        raise SystemExit("Missing api_id/api_hash/phone")
    _write_config(api_id, api_hash)

    client = TelegramClient(str(SESSION_BASE), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            await client.send_code_request(phone)
            code = getpass.getpass("Telegram code: ").strip()
            try:
                await client.sign_in(phone=phone, code=code)
            except SessionPasswordNeededError:
                password = getpass.getpass("Telegram 2FA password: ")
                await client.sign_in(password=password)
        me = await client.get_me()
        session_file = STATE_DIR / "account.session"
        if session_file.exists():
            os.chmod(session_file, 0o600)
        print(f"AUTHORIZED user_id={getattr(me, 'id', None)} username={getattr(me, 'username', None) or '-'}")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
