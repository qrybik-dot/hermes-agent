"""No-LLM periodic synchronization of Granola summaries into Meetings/Granola."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import fcntl
import html
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

from gateway.granola_archive import (
    GranolaMeeting, clear_pending, find_match, load_state, pending_prompt, save_state, set_pending,
    upsert_meeting,
)


MEETING_RE = re.compile(r'<meeting\s+id="([^"]+)"\s+title="([^"]*)"\s+date="([^"]*)">')
SUMMARY_RE = re.compile(r"<summary>\s*(.*?)\s*</summary>", re.S | re.I)
UNAVAILABLE_TRANSCRIPT_RE = re.compile(r"paid\s+granola\s+tiers|transcript\s+is\s+not\s+available|not\s+available", re.I)
RATE_LIMIT_RE = re.compile(r"rate\s+limit|too\s+many\s+requests", re.I)


def _text(result: Any) -> str:
    return "\n".join(
        str(getattr(block, "text", "") or "")
        for block in (getattr(result, "content", None) or [])
        if getattr(block, "text", None)
    ).strip()


def _meeting_date(value: str) -> str:
    raw = html.unescape(str(value or "")).replace(" GMT+3", "").strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", raw):
        return raw[:10]
    for pattern in ("%b %d, %Y %I:%M %p", "%B %d, %Y %I:%M %p", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, pattern).date().isoformat()
        except ValueError:
            pass
    return raw


def parse_meeting_list(value: str) -> list[dict[str, str]]:
    return [
        {"id": match.group(1), "title": html.unescape(match.group(2)), "date": _meeting_date(match.group(3))}
        for match in MEETING_RE.finditer(value or "")
    ]


def parse_summary(value: str) -> str:
    match = SUMMARY_RE.search(value or "")
    if not match:
        return ""
    summary = html.unescape(match.group(1)).strip()
    summary = re.sub(r"<br\s*/?>", "\n", summary, flags=re.I)
    summary = re.sub(r"<[^>]+>", "", summary)
    return summary.strip()


def _notify(prompt: str) -> bool:
    command = [
        "/home/hermes/hermes-runtime/shared/venv/bin/python",
        "/home/hermes/hermes-runtime/current/hermes",
        "send", "--to", os.environ.get("HERMES_GRANOLA_NOTIFY_TARGET", "telegram"),
        "--quiet", prompt,
    ]
    try:
        completed = subprocess.run(command, timeout=30, check=False, capture_output=True, text=True)
        return completed.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


async def synchronize(*, dry_run: bool = False, notify: bool = True, limit: int = 50) -> dict[str, Any]:
    from tools.mcp_tool import _connect_server, _load_mcp_config

    config = _load_mcp_config().get("granola")
    if not isinstance(config, dict):
        raise RuntimeError("Granola MCP is not configured")
    server = await _connect_server("granola", config)
    counters = {"listed": 0, "created": 0, "updated": 0, "duplicate": 0, "transcripts": 0, "pending": 0, "blocked": 0, "notified": 0, "rate_limited": 0}
    pace = max(0.0, float(os.environ.get("HERMES_GRANOLA_MCP_PACE_SECONDS", "8")))
    try:
        async with server._rpc_lock:
            listed = await server.session.call_tool("list_meetings", arguments={"time_range": "last_30_days"})
        if bool(getattr(listed, "isError", False)):
            raise RuntimeError("Granola list_meetings failed")
        meetings = parse_meeting_list(_text(listed))[: max(1, limit)]
        counters["listed"] = len(meetings)
        for item in meetings:
            stub = GranolaMeeting(item["id"], item["title"], item["date"], "")
            existing, match_status = find_match(stub)
            if existing is not None:
                counters["duplicate"] += 1
                if item["id"] in (load_state().get("pending") or {}):
                    counters["pending"] += 1
                continue
            if match_status == "ambiguous":
                counters["blocked"] += 1
                continue
            if pace:
                await asyncio.sleep(pace)
            async with server._rpc_lock:
                details = await server.session.call_tool("get_meetings", arguments={"meeting_ids": [item["id"]]})
            details_text = _text(details)
            if RATE_LIMIT_RE.search(details_text):
                counters["rate_limited"] = 1
                break
            summary = parse_summary(details_text)
            if bool(getattr(details, "isError", False)) or not summary:
                counters["blocked"] += 1
                continue
            if pace:
                await asyncio.sleep(pace)
            async with server._rpc_lock:
                transcript_result = await server.session.call_tool(
                    "get_meeting_transcript", arguments={"meeting_id": item["id"]}
                )
            transcript_text = _text(transcript_result)
            if RATE_LIMIT_RE.search(transcript_text):
                counters["rate_limited"] = 1
                break
            transcript = None
            if (
                transcript_text
                and not bool(getattr(transcript_result, "isError", False))
                and not UNAVAILABLE_TRANSCRIPT_RE.search(transcript_text)
            ):
                transcript = transcript_text
            meeting = GranolaMeeting(item["id"], item["title"], item["date"], summary)
            result = upsert_meeting(meeting, transcript=transcript, dry_run=dry_run)
            if result.status == "blocked":
                counters["blocked"] += 1
                continue
            if result.created:
                counters["created"] += 1
            elif result.updated:
                counters["updated"] += 1
            elif result.duplicate:
                counters["duplicate"] += 1
            if transcript:
                counters["transcripts"] += 1
                if not dry_run:
                    clear_pending(meeting.meeting_id)
                continue
            counters["pending"] += 1
            if dry_run:
                continue
            if result.created or meeting.meeting_id in (load_state().get("pending") or {}):
                set_pending(meeting)
        if notify and not dry_run:
            state = load_state()
            pending = state.get("pending") if isinstance(state.get("pending"), dict) else {}
            for meeting_id, record in list(pending.items()):
                if counters["notified"] >= 3 or not isinstance(record, dict) or bool(record.get("notified")):
                    continue
                meeting = GranolaMeeting(
                    meeting_id,
                    str(record.get("title") or "Встреча Granola"),
                    str(record.get("date") or ""),
                    "",
                    str(record.get("source_url") or ""),
                )
                if _notify(pending_prompt(meeting)):
                    record["notified"] = True
                    pending[meeting_id] = record
                    counters["notified"] += 1
            state["pending"] = pending
            save_state(state)
        return counters
    finally:
        await server.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    lock_path = Path(os.environ.get("HERMES_GRANOLA_LOCK_PATH", "/run/hermes-granola-sync/sync.lock"))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "skipped", "reason": "already running"}))
            return 0
        try:
            result = asyncio.run(synchronize(dry_run=args.dry_run, notify=not args.no_notify, limit=args.limit))
        except Exception as exc:
            print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
            return 1
        print(json.dumps({"status": "ok", **result}, ensure_ascii=False, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
