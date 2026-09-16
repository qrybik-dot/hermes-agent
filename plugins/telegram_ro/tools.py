"""Hermes-facing client for the isolated Telegram read-only service."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import stat
import time
from typing import Any

from tools.registry import tool_error, tool_result

SOCKET_PATH = "/run/hermes-telegram-ro/telegram-ro.sock"
DOWNLOAD_ROOT = Path.home() / ".hermes" / "downloads" / "telegram-ro"
MAX_HEADER = 2 * 1024 * 1024
CACHE_TTL_SECONDS = 6 * 3600
CACHE_QUOTA_BYTES = 2 * 1024 * 1024 * 1024
MIN_FREE_BYTES = 4 * 1024 * 1024 * 1024

SAFETY_CONTRACT = (
    "STRICTLY READ-ONLY with zero intended Telegram-side effects. The isolated service may read/search "
    "messages, metadata, formatting, reactions and available media, but must never send/reply/forward/edit/delete, "
    "change reactions, join/leave/manage chats or contacts, mark messages/media/reactions as read, increment views, "
    "change online/typing/story state, make payments, or expose arbitrary/raw Telegram API access. Never bypass this "
    "boundary through terminal, another Telegram client, or the private session file."
)


def stat_is_socket(path: str) -> bool:
    try:
        return stat.S_ISSOCK(os.stat(path).st_mode)
    except OSError:
        return False


def _check_available() -> bool:
    return os.path.exists(SOCKET_PATH) and stat_is_socket(SOCKET_PATH)


def _readline(sock: socket.socket) -> bytes:
    buf = bytearray()
    while len(buf) < MAX_HEADER:
        chunk = sock.recv(1)
        if not chunk:
            break
        if chunk == b"\n":
            return bytes(buf)
        buf.extend(chunk)
    if len(buf) >= MAX_HEADER:
        raise RuntimeError("telegram-ro response header too large")
    return bytes(buf)


def _request(payload: dict[str, Any], *, timeout: float = 50.0) -> tuple[dict[str, Any], socket.socket]:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(SOCKET_PATH)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(encoded) > 32 * 1024:
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


def _selector(args: dict) -> dict:
    url = str(args.get("url") or "").strip()
    if url:
        return {"url": url}
    peer = str(args.get("peer") or "").strip()
    if not peer or args.get("message_id") is None:
        raise ValueError("provide url or peer + message_id")
    message_id = int(args["message_id"])
    if message_id <= 0:
        raise ValueError("message_id must be positive")
    return {"peer": peer, "message_id": message_id}


def _handle_read(args: dict, **kw) -> str:
    try:
        header, sock = _request({"action": "message", **_selector(args)})
        sock.close()
        return tool_result(header.get("message") or {})
    except Exception as exc:
        return tool_error(f"Telegram read-only read failed: {type(exc).__name__}: {exc}")


def _search_payload(args: dict, *, history: bool = False) -> dict:
    payload = {"action": "history" if history else "search"}
    for key in ("scope", "query", "chat", "since", "until", "limit", "cursor", "has_media", "media_type", "formatted_only"):
        if key in args and args.get(key) is not None:
            payload[key] = args[key]
    return payload


def _handle_search(args: dict, **kw) -> str:
    try:
        header, sock = _request(_search_payload(args), timeout=70.0)
        sock.close()
        return tool_result({k: v for k, v in header.items() if k != "ok"})
    except Exception as exc:
        return tool_error(f"Telegram read-only search failed: {type(exc).__name__}: {exc}")


def _handle_history(args: dict, **kw) -> str:
    chat = str(args.get("chat") or "").strip()
    if not chat:
        return tool_error("chat is required")
    try:
        header, sock = _request(_search_payload(args, history=True), timeout=70.0)
        sock.close()
        return tool_result({k: v for k, v in header.items() if k != "ok"})
    except Exception as exc:
        return tool_error(f"Telegram read-only history failed: {type(exc).__name__}: {exc}")


def _safe_filename(name: str, message_id: int) -> str:
    candidate = Path(name or "").name.strip().replace("\x00", "")
    if not candidate or candidate in {".", ".."}:
        candidate = f"telegram-{message_id}.bin"
    cleaned = "".join(ch if (ch.isalnum() or ch in "._-() ") else "_" for ch in candidate).strip()
    return (cleaned or f"telegram-{message_id}.bin")[:180]



def _download_target(peer_ref: str, message_id: int, filename: str) -> Path:
    peer_tag = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(peer_ref or "unknown"))[:48]
    return DOWNLOAD_ROOT / f"{peer_tag}-{int(message_id)}-{_safe_filename(filename, int(message_id))}"

def _prepare_cache(expected: int | None) -> None:
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(DOWNLOAD_ROOT, 0o700)
    now = time.time()
    total = 0
    for item in DOWNLOAD_ROOT.iterdir():
        if not item.is_file():
            continue
        try:
            st = item.stat()
        except OSError:
            continue
        if now - st.st_mtime > CACHE_TTL_SECONDS:
            item.unlink(missing_ok=True)
            continue
        total += st.st_size
    if expected is not None:
        if expected > CACHE_QUOTA_BYTES:
            raise RuntimeError("media exceeds telegram-ro local cache quota")
        if total + expected > CACHE_QUOTA_BYTES:
            raise RuntimeError("telegram-ro local cache quota would be exceeded; old files expire automatically")
        free = shutil.disk_usage(DOWNLOAD_ROOT).free
        if free < expected + MIN_FREE_BYTES:
            raise RuntimeError("insufficient VPS free disk while preserving the safety reserve")
    elif shutil.disk_usage(DOWNLOAD_ROOT).free < MIN_FREE_BYTES:
        raise RuntimeError("VPS free disk is below the telegram-ro safety reserve")


def _handle_download(args: dict, **kw) -> str:
    try:
        header, sock = _request({"action": "download", **_selector(args)}, timeout=60.0)
        meta = header.get("media") or {}
        message_id = int(meta.get("message_id") or 0)
        filename = _safe_filename(str(meta.get("filename") or ""), message_id)
        expected = int(meta["size"]) if meta.get("size") is not None else None
        _prepare_cache(expected)
        target = _download_target(str(meta.get("peer_ref") or "unknown"), message_id, filename)
        target.unlink(missing_ok=True)
        part = target.with_name(target.name + ".part")
        total = 0
        try:
            with part.open("wb") as fh:
                os.chmod(part, 0o600)
                while True:
                    chunk = sock.recv(1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    total += len(chunk)
                    if total > CACHE_QUOTA_BYTES:
                        raise RuntimeError("download exceeded telegram-ro local cache quota")
            if expected is not None and total != expected:
                raise RuntimeError(f"incomplete media stream: expected {expected} bytes, got {total}")
            os.replace(part, target)
        except Exception:
            part.unlink(missing_ok=True)
            raise
        finally:
            sock.close()
        return tool_result({"success": True, "path": str(target), "bytes": total, "message_id": message_id, "peer_ref": meta.get("peer_ref"), "filename": filename, "mime_type": meta.get("mime_type"), "read_only": True, "cache_ttl_hours": CACHE_TTL_SECONDS // 3600})
    except Exception as exc:
        return tool_error(f"Telegram read-only download failed: {type(exc).__name__}: {exc}")


MESSAGE_SELECTOR_PROPERTIES = {
    "url": {"type": "string", "description": "Direct Telegram message URL, including private t.me/c/... links."},
    "peer": {"type": "string", "description": "Peer reference returned by search/history, @username, numeric peer id, t.me chat URL, or me/saved."},
    "message_id": {"type": "integer", "minimum": 1},
}

COMMON_SEARCH_PROPERTIES = {
    "since": {"type": "string", "description": "Inclusive ISO date/time. For broad all-history scans this is required when query is empty."},
    "until": {"type": "string", "description": "Inclusive ISO date or upper timestamp."},
    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Results per bounded request."},
    "cursor": {"type": "string", "description": "Opaque continuation cursor from coverage.next_cursor. Continue until coverage.complete=true when exhaustive coverage is needed."},
    "has_media": {"type": "boolean"},
    "media_type": {"type": "string", "enum": ["photo", "video", "voice", "video_note", "audio", "document", "gif", "sticker", "poll", "geo", "contact", "webpage"]},
    "formatted_only": {"type": "boolean", "description": "Only messages with Telegram text entities such as bold, links, spoilers, code or blockquotes."},
}

TELEGRAM_RO_READ_SCHEMA = {
    "name": "telegram_ro_read",
    "description": "Read one Telegram message in full, including text entities, reactions, counters and media metadata. " + SAFETY_CONTRACT,
    "parameters": {"type": "object", "properties": MESSAGE_SELECTOR_PROPERTIES, "additionalProperties": False, "anyOf": [{"required": ["url"]}, {"required": ["peer", "message_id"]}]},
}

TELEGRAM_RO_SEARCH_SCHEMA = {
    "name": "telegram_ro_search",
    "description": "Bounded read-only search across all accessible Telegram dialogs, Saved Messages, or one chat. Results include coverage and a continuation cursor; paginate instead of bulk-exporting raw history. Short Telegram flood waits are passively waited once; longer waits are returned in coverage.flood_wait_seconds. " + SAFETY_CONTRACT,
    "parameters": {
        "type": "object",
        "properties": {"scope": {"type": "string", "enum": ["all", "saved", "chat"]}, "query": {"type": "string"}, "chat": {"type": "string", "description": "Required when scope=chat."}, **COMMON_SEARCH_PROPERTIES},
        "additionalProperties": False,
    },
}

TELEGRAM_RO_HISTORY_SCHEMA = {
    "name": "telegram_ro_history",
    "description": "Read a bounded page of message history from one accessible chat or Saved Messages without changing unread state. Use cursor pagination for more. " + SAFETY_CONTRACT,
    "parameters": {"type": "object", "properties": {"chat": {"type": "string", "description": "@username, peer_ref, t.me chat URL, me, saved or Избранное."}, **COMMON_SEARCH_PROPERTIES}, "required": ["chat"], "additionalProperties": False},
}

TELEGRAM_RO_DOWNLOAD_SCHEMA = {
    "name": "telegram_ro_download",
    "description": "Download available media for one selected Telegram message into a bounded temporary Hermes cache. Search/history never bulk-download media. " + SAFETY_CONTRACT,
    "parameters": {"type": "object", "properties": MESSAGE_SELECTOR_PROPERTIES, "additionalProperties": False, "anyOf": [{"required": ["url"]}, {"required": ["peer", "message_id"]}]},
}
