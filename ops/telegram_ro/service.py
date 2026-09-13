#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import pwd
import re
import socket
import struct
from urllib.parse import urlparse

SOCKET_PATH = Path("/run/hermes-telegram-ro/telegram-ro.sock")
STATE_DIR = Path("/var/lib/hermes-telegram-ro")
CONFIG_PATH = STATE_DIR / "config.json"
SESSION_BASE = STATE_DIR / "account"
MAX_REQUEST = 16 * 1024
MAX_MEDIA_BYTES = 2 * 1024 * 1024 * 1024
ALLOWED_ACTIONS = frozenset({"health", "message", "download"})


class RequestError(Exception):
    pass


def validate_action(action: object) -> str:
    value = str(action or "").strip().lower()
    if value not in ALLOWED_ACTIONS:
        raise RequestError("action_not_allowed")
    return value


def parse_message_url(url: str) -> tuple[str, int]:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.hostname not in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
        raise RequestError("unsupported_telegram_url")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) == 3 and parts[0] == "c" and parts[1].isdigit() and parts[2].isdigit():
        return f"-100{int(parts[1])}", int(parts[2])
    if len(parts) == 3 and parts[0] == "s" and re.fullmatch(r"[A-Za-z0-9_]{5,}", parts[1]) and parts[2].isdigit():
        return parts[1], int(parts[2])
    if len(parts) == 2 and re.fullmatch(r"[A-Za-z0-9_]{5,}", parts[0]) and parts[1].isdigit():
        return parts[0], int(parts[1])
    raise RequestError("unsupported_message_url")


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise RequestError("not_configured")
    mode = CONFIG_PATH.stat().st_mode & 0o777
    if mode & 0o077:
        raise RequestError("unsafe_config_permissions")
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        api_id = int(data["api_id"])
        api_hash = str(data["api_hash"]).strip()
    except Exception as exc:
        raise RequestError("invalid_config") from exc
    if api_id <= 0 or not api_hash:
        raise RequestError("invalid_config")
    return {"api_id": api_id, "api_hash": api_hash}


def _client_uid_allowed(writer: asyncio.StreamWriter) -> bool:
    sock = writer.get_extra_info("socket")
    if sock is None or not hasattr(socket, "SO_PEERCRED"):
        return False
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", raw)
        hermes_uid = pwd.getpwnam("hermes").pw_uid
        return uid in {0, hermes_uid}
    except Exception:
        return False


async def _open_client():
    from telethon import TelegramClient

    cfg = _load_config()
    client = TelegramClient(str(SESSION_BASE), cfg["api_id"], cfg["api_hash"])
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RequestError("not_authorized")
    return client


async def _resolve_peer(client, peer_ref: str):
    if peer_ref.startswith("-100") and peer_ref[4:].isdigit():
        target_id = int(peer_ref[4:])
        try:
            return await client.get_entity(int(peer_ref))
        except Exception:
            async for dialog in client.iter_dialogs():
                entity = dialog.entity
                if int(getattr(entity, "id", 0) or 0) == target_id:
                    return entity
            raise RequestError("chat_not_accessible")
    try:
        return await client.get_entity(peer_ref)
    except Exception as exc:
        raise RequestError("chat_not_accessible") from exc


def _message_payload(message, source_url: str) -> dict:
    file_obj = getattr(message, "file", None)
    filename = getattr(file_obj, "name", None) if file_obj else None
    ext = getattr(file_obj, "ext", None) if file_obj else None
    if not filename and ext:
            filename = f'telegram-{message.id}{ext or ".bin"}'
    return {
        "id": int(message.id),
        "date": message.date.isoformat() if getattr(message, "date", None) else None,
        "sender_id": getattr(message, "sender_id", None),
        "text": getattr(message, "message", None) or "",
        "has_media": bool(getattr(message, "media", None)),
        "media": {
            "filename": filename,
            "mime_type": getattr(file_obj, "mime_type", None) if file_obj else None,
            "size": getattr(file_obj, "size", None) if file_obj else None,
            "ext": ext,
        } if getattr(message, "media", None) else None,
        "source_url": source_url,
        "read_only": True,
    }


async def _fetch_message(client, url: str):
    peer_ref, message_id = parse_message_url(url)
    peer = await _resolve_peer(client, peer_ref)
    message = await client.get_messages(peer, ids=message_id)
    if message is None:
        raise RequestError("message_not_found")
    return message


async def _write_json(writer: asyncio.StreamWriter, payload: dict) -> None:
    writer.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
    await writer.drain()


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    client = None
    binary_started = False
    try:
        if not _client_uid_allowed(writer):
            raise RequestError("unauthorized_local_client")
        raw = await reader.readline()
        if not raw or len(raw) > MAX_REQUEST:
            raise RequestError("invalid_request")
        try:
            request = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise RequestError("invalid_json") from exc
        if not isinstance(request, dict):
            raise RequestError("invalid_request")
        action = validate_action(request.get("action"))

        if action == "health":
            await _write_json(writer, {
                "ok": True,
                "service": "telegram-ro",
                "configured": CONFIG_PATH.exists(),
                "session_present": (STATE_DIR / "account.session").exists(),
                "allowed_actions": sorted(ALLOWED_ACTIONS),
                "read_only": True,
            })
            return

        url = str(request.get("url") or "").strip()
        if not url:
            raise RequestError("url_required")
        parse_message_url(url)
        client = await _open_client()
        message = await _fetch_message(client, url)

        if action == "message":
            await _write_json(writer, {"ok": True, "message": _message_payload(message, url)})
            return

        media = getattr(message, "media", None)
        if media is None:
            raise RequestError("message_has_no_media")
        file_obj = getattr(message, "file", None)
        size = getattr(file_obj, "size", None) if file_obj else None
        if size is not None and int(size) > MAX_MEDIA_BYTES:
            raise RequestError("media_too_large")
        filename = getattr(file_obj, "name", None) if file_obj else None
        ext = getattr(file_obj, "ext", None) if file_obj else None
        if not filename:
            filename = f'telegram-{message.id}{ext or ".bin"}'
        await _write_json(writer, {
            "ok": True,
            "media": {
                "message_id": int(message.id),
                "filename": filename,
                "mime_type": getattr(file_obj, "mime_type", None) if file_obj else None,
                "size": size,
                "read_only": True,
            },
        })
        binary_started = True
        sent = 0
        async for chunk in client.iter_download(media, request_size=512 * 1024):
            sent += len(chunk)
            if sent > MAX_MEDIA_BYTES:
                raise RequestError("media_too_large")
            writer.write(chunk)
            await writer.drain()
    except RequestError as exc:
        if not binary_started:
            try:
                await _write_json(writer, {"ok": False, "error": str(exc), "read_only": True})
            except Exception:
                pass
    except Exception as exc:
        if not binary_started:
            try:
                await _write_json(writer, {"ok": False, "error": f"internal_error:{type(exc).__name__}", "read_only": True})
            except Exception:
                pass
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def main() -> None:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        SOCKET_PATH.unlink()
    except FileNotFoundError:
        pass
    server = await asyncio.start_unix_server(_handle, path=str(SOCKET_PATH), limit=MAX_REQUEST)
    os.chmod(SOCKET_PATH, 0o660)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
