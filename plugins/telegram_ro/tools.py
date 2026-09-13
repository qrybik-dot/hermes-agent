"""Hermes-facing client for the isolated Telegram read-only service."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
from typing import Any

from tools.registry import tool_error, tool_result

SOCKET_PATH = "/run/hermes-telegram-ro/telegram-ro.sock"
DOWNLOAD_ROOT = Path.home() / ".hermes" / "downloads" / "telegram-ro"
_MAX_HEADER = 64 * 1024

SAFETY_CONTRACT = (
    "STRICTLY READ-ONLY. This tool may only read a message or download its media. "
    "It cannot send, reply, forward, edit, delete, react, join/leave chats, change "
    "account/chat settings, or invoke arbitrary/raw Telegram API methods. Never "
    "attempt to bypass this boundary via terminal or another Telegram client."
)


def _check_available() -> bool:
    return os.path.exists(SOCKET_PATH) and stat_is_socket(SOCKET_PATH)


def stat_is_socket(path: str) -> bool:
    try:
        import stat
        return stat.S_ISSOCK(os.stat(path).st_mode)
    except OSError:
        return False


def _readline(sock: socket.socket) -> bytes:
    buf = bytearray()
    while len(buf) < _MAX_HEADER:
        chunk = sock.recv(1)
        if not chunk:
            break
        if chunk == b"\n":
            return bytes(buf)
        buf.extend(chunk)
    if len(buf) >= _MAX_HEADER:
        raise RuntimeError("telegram-ro response header too large")
    return bytes(buf)


def _request(payload: dict[str, Any], *, timeout: float = 30.0) -> tuple[dict[str, Any], socket.socket]:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(SOCKET_PATH)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(encoded) > 16 * 1024:
            raise ValueError("telegram-ro request too large")
        sock.sendall(encoded)
        raw = _readline(sock)
        if not raw:
            raise RuntimeError("telegram-ro service returned an empty response")
        header = json.loads(raw.decode("utf-8"))
        if not isinstance(header, dict):
            raise RuntimeError("telegram-ro service returned an invalid response")
        if not header.get("ok"):
            raise RuntimeError(str(header.get("error") or "telegram-ro request failed"))
        return header, sock
    except Exception:
        sock.close()
        raise


def _handle_read(args: dict, **kw) -> str:
    url = str(args.get("url") or "").strip()
    if not url:
        return tool_error("url is required")
    try:
        header, sock = _request({"action": "message", "url": url})
        sock.close()
        return tool_result(header.get("message") or {})
    except Exception as exc:
        return tool_error(f"Telegram read-only read failed: {type(exc).__name__}: {exc}")


def _safe_filename(name: str, message_id: int) -> str:
    candidate = Path(name or "").name.strip().replace("\x00", "")
    if not candidate or candidate in {".", ".."}:
        candidate = f"telegram-{message_id}.bin"
    # Keep filenames portable and non-surprising.
    cleaned = "".join(ch if (ch.isalnum() or ch in "._-() ") else "_" for ch in candidate).strip()
    return (cleaned or f"telegram-{message_id}.bin")[:180]


def _handle_download(args: dict, **kw) -> str:
    url = str(args.get("url") or "").strip()
    if not url:
        return tool_error("url is required")
    try:
        header, sock = _request({"action": "download", "url": url}, timeout=60.0)
        meta = header.get("media") or {}
        message_id = int(meta.get("message_id") or 0)
        filename = _safe_filename(str(meta.get("filename") or ""), message_id)
        DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
        target = DOWNLOAD_ROOT / f"{message_id}-{filename}"
        expected = meta.get("size")
        total = 0
        with target.open("wb") as fh:
            while True:
                chunk = sock.recv(1024 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                total += len(chunk)
        sock.close()
        if expected is not None and int(expected) >= 0 and total != int(expected):
            target.unlink(missing_ok=True)
            raise RuntimeError(f"incomplete media stream: expected {expected} bytes, got {total}")
        return tool_result({
            "success": True,
            "path": str(target),
            "bytes": total,
            "message_id": message_id,
            "filename": filename,
            "mime_type": meta.get("mime_type"),
            "source_url": url,
            "read_only": True,
        })
    except Exception as exc:
        return tool_error(f"Telegram read-only download failed: {type(exc).__name__}: {exc}")


TELEGRAM_RO_READ_SCHEMA = {
    "name": "telegram_ro_read",
    "description": "Read one Telegram message by t.me message URL. " + SAFETY_CONTRACT,
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Telegram message URL, including private t.me/c/... links."},
        },
        "required": ["url"],
        "additionalProperties": False,
    },
}

TELEGRAM_RO_DOWNLOAD_SCHEMA = {
    "name": "telegram_ro_download",
    "description": "Download media attached to one Telegram message by t.me URL into the Hermes downloads directory. " + SAFETY_CONTRACT,
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Telegram message URL, including private t.me/c/... links."},
        },
        "required": ["url"],
        "additionalProperties": False,
    },
}
