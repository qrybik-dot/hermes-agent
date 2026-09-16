from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
import re
import time
from urllib.parse import urlparse

MAX_RESULTS = 100
MAX_MESSAGES_SCANNED = 4000
MAX_DIALOGS_PER_REQUEST = 20
MAX_PREVIEW_CHARS = 1200
MAX_REQUEST_SECONDS = 40.0
MAX_PEER_RESOLVE_DIALOGS = 100
MAX_PEER_RESOLVE_SECONDS = 8.0


class ReadApiError(Exception):
    pass


def parse_chat_ref(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ReadApiError("chat_required")
    if raw.lower() in {"me", "saved", "saved_messages", "saved messages", "избранное"}:
        return "me"
    if re.fullmatch(r"-?\d+", raw):
        return raw
    if raw.startswith("@"):
        raw = raw[1:]
    if re.fullmatch(r"[A-Za-z0-9_]{5,}", raw):
        return raw
    parsed = urlparse(raw)
    if parsed.scheme == "https" and parsed.hostname in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "c" and parts[1].isdigit():
            return f"-100{int(parts[1])}"
        if len(parts) >= 2 and parts[0] == "s" and re.fullmatch(r"[A-Za-z0-9_]{5,}", parts[1]):
            return parts[1]
        if parts and re.fullmatch(r"[A-Za-z0-9_]{5,}", parts[0]):
            return parts[0]
    raise ReadApiError("unsupported_chat_reference")


def _coerce_limit(raw: object, default: int = 50) -> int:
    try:
        value = int(raw) if raw is not None else default
    except Exception as exc:
        raise ReadApiError("invalid_limit") from exc
    if value < 1:
        raise ReadApiError("invalid_limit")
    return min(value, MAX_RESULTS)


def _optional_bool(raw: object) -> bool | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    raise ReadApiError("invalid_boolean_filter")


def _bound(raw: object, *, until: bool = False) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return dt + timedelta(days=1) if until else dt
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception as exc:
        raise ReadApiError("invalid_date") from exc


def _utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _cursor_encode(peer: str, offset_id: int) -> str:
    raw = json.dumps({"v": 1, "peer": peer, "offset_id": int(offset_id)}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _cursor_decode(raw: object) -> dict | None:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        value += "=" * (-len(value) % 4)
        data = json.loads(base64.urlsafe_b64decode(value.encode()).decode())
        if data.get("v") != 1:
            raise ValueError
        return {"peer": str(data["peer"]), "offset_id": max(0, int(data.get("offset_id") or 0))}
    except Exception as exc:
        raise ReadApiError("invalid_cursor") from exc


def _all_cursor_encode(*, page_peer: str | None = None, page_offset_id: int = 0, page_offset_date: datetime | None = None, page_index: int = 0, message_offset_id: int = 0) -> str:
    raw = json.dumps({
        "v": 2,
        "kind": "all",
        "page_peer": page_peer,
        "page_offset_id": int(page_offset_id),
        "page_offset_date": page_offset_date.isoformat() if page_offset_date else None,
        "page_index": int(page_index),
        "message_offset_id": int(message_offset_id),
    }, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _all_cursor_decode(raw: object) -> dict:
    value = str(raw or "").strip()
    if not value:
        return {"page_peer": None, "page_offset_id": 0, "page_offset_date": None, "page_index": 0, "message_offset_id": 0}
    try:
        value += "=" * (-len(value) % 4)
        data = json.loads(base64.urlsafe_b64decode(value.encode()).decode())
        if data.get("v") != 2 or data.get("kind") != "all":
            raise ValueError
        date_raw = data.get("page_offset_date")
        date = datetime.fromisoformat(date_raw) if date_raw else None
        if date is not None and date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return {
            "page_peer": str(data["page_peer"]) if data.get("page_peer") is not None else None,
            "page_offset_id": max(0, int(data.get("page_offset_id") or 0)),
            "page_offset_date": date,
            "page_index": max(0, int(data.get("page_index") or 0)),
            "message_offset_id": max(0, int(data.get("message_offset_id") or 0)),
        }
    except Exception as exc:
        raise ReadApiError("invalid_cursor") from exc


def _peer_id(peer) -> int | None:
    if peer is None:
        return None
    try:
        from telethon import utils
        return int(utils.get_peer_id(peer))
    except Exception:
        return None


def _entity(entity) -> dict | None:
    if entity is None:
        return None
    title = getattr(entity, "title", None)
    if not title:
        title = " ".join(x for x in [str(getattr(entity, "first_name", "") or "").strip(), str(getattr(entity, "last_name", "") or "").strip()] if x) or None
    return {"id": getattr(entity, "id", None), "name": title, "username": getattr(entity, "username", None), "type": type(entity).__name__}


def _entities(message) -> list[dict]:
    out = []
    for ent in getattr(message, "entities", None) or []:
        row = {"type": type(ent).__name__.removeprefix("MessageEntity"), "offset": getattr(ent, "offset", None), "length": getattr(ent, "length", None)}
        for attr in ("url", "language", "user_id", "document_id", "collapsed"):
            value = getattr(ent, attr, None)
            if value is not None:
                row[attr] = value
        out.append(row)
    return out


def _reaction_name(reaction) -> str:
    if reaction is None:
        return "unknown"
    if getattr(reaction, "emoticon", None):
        return str(reaction.emoticon)
    if getattr(reaction, "document_id", None) is not None:
        return f"custom_emoji:{reaction.document_id}"
    return "paid" if type(reaction).__name__ == "ReactionPaid" else type(reaction).__name__


def _reactions(message) -> dict | None:
    obj = getattr(message, "reactions", None)
    if obj is None:
        return None
    return {
        "counts": [{"reaction": _reaction_name(getattr(x, "reaction", None)), "count": int(getattr(x, "count", 0) or 0), "chosen_order": getattr(x, "chosen_order", None)} for x in (getattr(obj, "results", None) or [])],
        "recent": [{"peer_id": _peer_id(getattr(x, "peer_id", None)), "reaction": _reaction_name(getattr(x, "reaction", None)), "date": getattr(x, "date", None).isoformat() if getattr(x, "date", None) else None} for x in (getattr(obj, "recent_reactions", None) or [])],
        "can_see_list": getattr(obj, "can_see_list", None),
    }


def _media_type(message) -> str | None:
    for attr, label in (("voice", "voice"), ("video_note", "video_note"), ("gif", "gif"), ("sticker", "sticker"), ("video", "video"), ("audio", "audio"), ("photo", "photo"), ("document", "document"), ("poll", "poll"), ("geo", "geo"), ("contact", "contact"), ("web_preview", "webpage")):
        if getattr(message, attr, None) is not None:
            return label
    return type(getattr(message, "media", None)).__name__ if getattr(message, "media", None) is not None else None


def _media(message) -> dict | None:
    if getattr(message, "media", None) is None:
        return None
    file_obj = getattr(message, "file", None)
    ext = getattr(file_obj, "ext", None) if file_obj else None
    name = getattr(file_obj, "name", None) if file_obj else None
    if not name and ext:
        name = f"telegram-{message.id}{ext}"
    out = {"type": _media_type(message), "filename": name, "mime_type": getattr(file_obj, "mime_type", None) if file_obj else None, "size": getattr(file_obj, "size", None) if file_obj else None, "ext": ext, "ttl_seconds": getattr(getattr(message, "media", None), "ttl_seconds", None)}
    obj = next((getattr(message, a, None) for a in ("voice", "video_note", "video", "audio", "gif", "sticker", "document") if getattr(message, a, None) is not None), None)
    for attr in ("duration", "width", "height"):
        value = getattr(obj, attr, None) if obj is not None else None
        if value is not None:
            out[attr] = value
    poll = getattr(message, "poll", None)
    if poll is not None:
        q = getattr(poll, "question", None)
        out["poll_question"] = getattr(q, "text", None) or (str(q) if q else None)
        out["poll_closed"] = getattr(poll, "closed", None)
    contact = getattr(message, "contact", None)
    if contact is not None:
        out["contact"] = {"first_name": getattr(contact, "first_name", None), "last_name": getattr(contact, "last_name", None), "phone_number": getattr(contact, "phone_number", None), "user_id": getattr(contact, "user_id", None)}
    geo = getattr(message, "geo", None)
    if geo is not None:
        out["geo"] = {"lat": getattr(geo, "lat", None), "long": getattr(geo, "long", None)}
    return out


def _source_url(message) -> str | None:
    chat = getattr(message, "chat", None)
    username = getattr(chat, "username", None) if chat else None
    if username:
        return f"https://t.me/{username}/{int(message.id)}"
    marked = str(getattr(message, "chat_id", None) or "")
    if marked.startswith("-100") and marked[4:].isdigit():
        return f"https://t.me/c/{marked[4:]}/{int(message.id)}"
    return None


def message_payload(message, *, preview: bool = False, dialog=None) -> dict:
    text = getattr(message, "message", None) or ""
    ents = _entities(message)
    truncated = preview and len(text) > MAX_PREVIEW_CHARS
    reply = getattr(message, "reply_to", None)
    payload = {
        "id": int(message.id), "peer_ref": str(getattr(message, "chat_id", None) or _peer_id(getattr(message, "peer_id", None)) or ""),
        "date": _utc(getattr(message, "date", None)).isoformat() if getattr(message, "date", None) else None,
        "edit_date": _utc(getattr(message, "edit_date", None)).isoformat() if getattr(message, "edit_date", None) else None,
        "sender_id": getattr(message, "sender_id", None), "sender": _entity(getattr(message, "sender", None)), "chat": _entity(getattr(message, "chat", None)),
        "text": text[:MAX_PREVIEW_CHARS] + "…" if truncated else text, "text_truncated": truncated,
        "formatted": bool(ents), "entity_types": sorted({x["type"] for x in ents}),
        "has_media": bool(getattr(message, "media", None)), "media": _media(message), "reactions": _reactions(message),
        "views": getattr(message, "views", None), "forwards": getattr(message, "forwards", None),
        "reply_to_msg_id": getattr(message, "reply_to_msg_id", None), "topic_id": getattr(reply, "reply_to_top_id", None) if reply else None,
        "grouped_id": getattr(message, "grouped_id", None), "post_author": getattr(message, "post_author", None), "noforwards": getattr(message, "noforwards", None), "source_url": _source_url(message), "read_only": True,
    }
    if dialog is not None:
        payload["dialog"] = {"title": getattr(dialog, "name", None), "folder_id": getattr(dialog, "folder_id", None), "archived": bool(getattr(dialog, "archived", False)), "pinned": bool(getattr(dialog, "pinned", False)), "unread_count": getattr(dialog, "unread_count", None), "unread_mentions_count": getattr(dialog, "unread_mentions_count", None), "unread_reactions_count": getattr(dialog, "unread_reactions_count", None)}
    if not preview:
        payload["entities"] = ents
        payload["entity_offset_encoding"] = "utf-16"
    return payload


async def resolve_peer(client, ref: str):
    from telethon import utils
    if ref == "me":
        return await client.get_entity("me")
    if re.fullmatch(r"-?\d+", ref):
        wanted = int(ref)
        try:
            return await client.get_entity(wanted)
        except Exception:
            started = time.monotonic()
            async for dialog in client.iter_dialogs(limit=MAX_PEER_RESOLVE_DIALOGS):
                if int(utils.get_peer_id(dialog.entity)) == wanted:
                    return dialog.entity
                if time.monotonic() - started >= MAX_PEER_RESOLVE_SECONDS:
                    break
            raise ReadApiError("chat_not_cached_use_search")
    try:
        return await client.get_entity(ref)
    except Exception as exc:
        raise ReadApiError("chat_not_accessible") from exc


async def dialog_page(client, cursor: dict) -> tuple[list[dict], bool]:
    kwargs = {"limit": MAX_DIALOGS_PER_REQUEST + 1, "ignore_pinned": cursor.get("page_peer") is not None}
    if cursor.get("page_peer") is not None:
        try:
            kwargs["offset_peer"] = await client.get_input_entity(int(cursor["page_peer"]))
        except Exception as exc:
            raise ReadApiError("cursor_peer_not_found") from exc
        kwargs["offset_id"] = int(cursor.get("page_offset_id") or 0)
        if cursor.get("page_offset_date") is not None:
            kwargs["offset_date"] = cursor["page_offset_date"]
    page = []
    async for dialog in client.iter_dialogs(**kwargs):
        page.append({
            "peer_ref": str(int(dialog.id)),
            "entity": dialog.entity,
            "dialog": dialog,
            "input_entity": dialog.input_entity,
            "date": _utc(dialog.date),
            "top_message_id": int(getattr(dialog.message, "id", 0) or 0),
        })
    has_more = len(page) > MAX_DIALOGS_PER_REQUEST
    return page[:MAX_DIALOGS_PER_REQUEST], has_more


def _matches(message, has_media, media_type, formatted_only) -> bool:
    if has_media is not None and bool(getattr(message, "media", None)) is not has_media:
        return False
    if media_type and _media_type(message) != media_type:
        return False
    if formatted_only and not bool(getattr(message, "entities", None)):
        return False
    return True


async def _collect(client, entity, dialog, query, since, until, offset_id, result_budget, filters, counters, started):
    from telethon.errors import FloodWaitError
    results, last_id = [], offset_id
    kwargs = {"entity": entity, "limit": None}
    if query:
        kwargs["search"] = query
    if offset_id:
        kwargs["offset_id"] = offset_id
    elif until is not None:
        kwargs["offset_date"] = until
    try:
        async for msg in client.iter_messages(**kwargs):
            if time.monotonic() - started >= MAX_REQUEST_SECONDS:
                return results, False, last_id, "time_budget", None
            counters["messages_scanned"] += 1
            last_id = int(msg.id)
            msg_date = _utc(getattr(msg, "date", None))
            if until is not None and msg_date is not None and msg_date >= until:
                continue
            if since is not None and msg_date is not None and msg_date < since:
                return results, True, last_id, None, None
            if _matches(msg, **filters):
                results.append(message_payload(msg, preview=True, dialog=dialog))
                if len(results) >= result_budget:
                    return results, False, last_id, "result_limit", None
            if counters["messages_scanned"] >= MAX_MESSAGES_SCANNED:
                return results, False, last_id, "scan_budget", None
        return results, True, last_id, None, None
    except FloodWaitError as exc:
        return results, False, last_id, "flood_wait", int(getattr(exc, "seconds", 0) or 0)


async def run_search(client, request: dict, *, history: bool = False) -> dict:
    scope = "chat" if history else str(request.get("scope") or "all").strip().lower()
    if scope not in {"all", "saved", "chat"}:
        raise ReadApiError("invalid_scope")
    query = "" if history else str(request.get("query") or "").strip()
    since = _bound(request.get("since"))
    until = _bound(request.get("until"), until=True) or (datetime.now(timezone.utc) + timedelta(seconds=1))
    if since is not None and since >= until:
        raise ReadApiError("invalid_date_range")
    if scope == "all" and not query and since is None:
        raise ReadApiError("since_required_for_all_history_scan")
    from telethon.errors import FloodWaitError
    limit = _coerce_limit(request.get("limit"))
    filters = {"has_media": _optional_bool(request.get("has_media")), "media_type": str(request.get("media_type") or "").strip().lower() or None, "formatted_only": bool(request.get("formatted_only", False))}
    counters = {"messages_scanned": 0, "dialogs_scanned": 0}
    started, results = time.monotonic(), []
    reason = flood = next_cursor = None

    if scope in {"saved", "chat"}:
        cursor = _cursor_decode(request.get("cursor"))
        target = "me" if scope == "saved" else parse_chat_ref(request.get("chat"))
        try:
            entity = await resolve_peer(client, target)
        except FloodWaitError as exc:
            return {"results": [], "coverage": {"scope": scope, "query": query, "since": since.isoformat() if since else None, "until_exclusive": until.isoformat(), "messages_scanned": 0, "dialogs_scanned": 0, "results_returned": 0, "complete": False, "partial_reason": "flood_wait", "next_cursor": request.get("cursor"), "flood_wait_seconds": int(getattr(exc, "seconds", 0) or 0), "retry_same_request": True}, "read_only": True, "telegram_side_effects": "none_intended"}
        from telethon import utils
        peer_ref = str(int(utils.get_peer_id(entity)))
        offset = 0
        if cursor:
            if cursor["peer"] != peer_ref:
                raise ReadApiError("cursor_peer_mismatch")
            offset = cursor["offset_id"]
        chunk, finished, last_id, reason, flood = await _collect(client, entity, None, query, since, until, offset, limit, filters, counters, started)
        counters["dialogs_scanned"] = 1
        results.extend(chunk)
        if not finished:
            next_cursor = _cursor_encode(peer_ref, last_id)
    else:
        page_cursor = _all_cursor_decode(request.get("cursor"))
        try:
            records, has_more_dialogs = await dialog_page(client, page_cursor)
        except FloodWaitError as exc:
            return {"results": [], "coverage": {"scope": scope, "query": query, "since": since.isoformat() if since else None, "until_exclusive": until.isoformat(), "messages_scanned": 0, "dialogs_scanned": 0, "results_returned": 0, "complete": False, "partial_reason": "flood_wait", "next_cursor": request.get("cursor"), "flood_wait_seconds": int(getattr(exc, "seconds", 0) or 0), "retry_same_request": True}, "read_only": True, "telegram_side_effects": "none_intended"}
        start_idx = page_cursor["page_index"]
        if start_idx > len(records):
            raise ReadApiError("cursor_page_index_invalid")
        for i in range(start_idx, len(records)):
            rec = records[i]
            offset = page_cursor["message_offset_id"] if i == start_idx else 0
            chunk, finished, last_id, reason, flood = await _collect(client, rec["entity"], rec["dialog"], query, since, until, offset, max(1, limit - len(results)), filters, counters, started)
            counters["dialogs_scanned"] += 1
            results.extend(chunk)
            if not finished:
                next_cursor = _all_cursor_encode(
                    page_peer=page_cursor["page_peer"],
                    page_offset_id=page_cursor["page_offset_id"],
                    page_offset_date=page_cursor["page_offset_date"],
                    page_index=i,
                    message_offset_id=last_id,
                )
                break
            if time.monotonic() - started >= MAX_REQUEST_SECONDS:
                reason = "time_budget"
                next_cursor = _all_cursor_encode(
                    page_peer=page_cursor["page_peer"],
                    page_offset_id=page_cursor["page_offset_id"],
                    page_offset_date=page_cursor["page_offset_date"],
                    page_index=i + 1,
                    message_offset_id=0,
                )
                break
        else:
            if has_more_dialogs and records:
                last = records[-1]
                reason = "dialog_page"
                next_cursor = _all_cursor_encode(
                    page_peer=last["peer_ref"],
                    page_offset_id=last["top_message_id"],
                    page_offset_date=last["date"],
                    page_index=0,
                    message_offset_id=0,
                )

    return {"results": results, "coverage": {"scope": scope, "query": query, "since": since.isoformat() if since else None, "until_exclusive": until.isoformat(), "messages_scanned": counters["messages_scanned"], "dialogs_scanned": counters["dialogs_scanned"], "results_returned": len(results), "complete": next_cursor is None and reason is None, "partial_reason": reason, "next_cursor": next_cursor, "flood_wait_seconds": flood, "budgets": {"max_messages_per_request": MAX_MESSAGES_SCANNED, "max_dialogs_per_request": MAX_DIALOGS_PER_REQUEST, "max_seconds": MAX_REQUEST_SECONDS, "max_results": MAX_RESULTS}}, "read_only": True, "telegram_side_effects": "none_intended"}

