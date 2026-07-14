"""Move selected Granola Knowledge candidates into Meetings/Granola.

Dry-run is the default.  The script never deletes the source artifact; apply
moves it into the supplied rollback directory, then records cancellation in
the candidate audit log so active Knowledge no longer exposes a second copy.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sqlite3

from gateway.granola_archive import GranolaMeeting, set_pending, upsert_meeting


def _meeting_date(payload: dict, title: str) -> str:
    for value in (payload.get("date"), title):
        match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", str(value or ""))
        if match:
            return match.group(1)
    return datetime.now(timezone.utc).date().isoformat()


def _meeting(candidate_id: str, payload: dict, meeting_id_override: str = "") -> GranolaMeeting:
    title = re.sub(r"^Встреча Granola:\s*", "", str(payload.get("title") or "Встреча Granola"), flags=re.I)
    meeting_id = str(meeting_id_override or payload.get("granola_document_id") or payload.get("artifact_id") or ("manual-" + candidate_id))
    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    source_url = next((str(item) for item in sources if "notes.granola.ai" in str(item)), "")
    return GranolaMeeting(
        meeting_id=meeting_id,
        title=title,
        meeting_date=_meeting_date(payload, title),
        summary=str(payload.get("summary") or "Самари пока недоступно."),
        source_url=source_url,
        content_hash=str(payload.get("granola_content_hash") or ""),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate_ids", nargs="+")
    parser.add_argument("--db", default="/srv/hermes-memory/staging/knowledge-candidates/candidates.sqlite3")
    parser.add_argument("--rollback-dir", required=True)
    parser.add_argument("--meeting-id", action="append", default=[], metavar="CANDIDATE_ID=GRANOLA_ID")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    overrides = dict(item.split("=", 1) for item in args.meeting_id)
    rollback = Path(args.rollback_dir)
    if args.apply:
        rollback.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    report = []
    retire_after_commit: list[Path] = []
    try:
        for candidate_id in args.candidate_ids:
            row = connection.execute(
                "select c.id,c.payload_json,a.canonical_path from candidates c left join artifacts a on a.candidate_id=c.id where c.id=?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                report.append({"candidate_id": candidate_id, "status": "missing"})
                continue
            payload = json.loads(row["payload_json"])
            meeting = _meeting(candidate_id, payload, overrides.get(candidate_id, ""))
            result = upsert_meeting(meeting, dry_run=not args.apply)
            item = {
                "candidate_id": candidate_id,
                "archive_status": result.status,
                "created": result.created,
                "duplicate": result.duplicate,
                "source_present": bool(row["canonical_path"] and Path(row["canonical_path"]).exists()),
            }
            if args.apply and result.status in {"success", "duplicate"}:
                set_pending(meeting)
                source = Path(row["canonical_path"]) if row["canonical_path"] else None
                if source and source.exists():
                    destination = rollback / f"{candidate_id}--{source.name}"
                    shutil.copy2(str(source), str(destination))
                    retire_after_commit.append(source)
                now = datetime.now(timezone.utc).isoformat()
                connection.execute("delete from artifacts where candidate_id=?", (candidate_id,))
                connection.execute(
                    "update candidates set status='cancelled',cancelled_at=?,updated_at=? where id=?",
                    (now, now, candidate_id),
                )
                connection.execute(
                    "insert into events(candidate_id,ts,event,data_json) values(?,?,?,?)",
                    (candidate_id, now, "migrated_to_meetings_granola", json.dumps({"canonical_path": result.path}, ensure_ascii=False)),
                )
                item["status"] = "applied"
            else:
                item["status"] = "dry-run"
            report.append(item)
        if args.apply:
            connection.commit()
            for source in retire_after_commit:
                source.unlink(missing_ok=True)
        else:
            connection.rollback()
    finally:
        connection.close()
    print(json.dumps({"apply": args.apply, "items": report}, ensure_ascii=False, indent=2))
    return 0 if all(item.get("status") != "missing" for item in report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
