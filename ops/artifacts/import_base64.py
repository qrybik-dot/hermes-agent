#!/usr/bin/env python3
"""Import one ChatGPT-created artifact from a bounded base64 JSON payload."""
from __future__ import annotations

import base64
import binascii
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_cli.artifact_store import ArtifactStore, sanitize_filename

MAX_DECODED = int(os.environ.get("HERMES_ARTIFACT_CHATGPT_UPLOAD_MAX_BYTES", str(20 * 1024**2)))
MAX_ENCODED = ((MAX_DECODED + 2) // 3) * 4 + 16


def import_payload(payload: dict) -> dict:
    filename = sanitize_filename(str(payload.get("filename") or "artifact"))
    encoded = str(payload.get("content_base64") or "")
    if not encoded:
        raise ValueError("content_base64 is required")
    if len(encoded) > MAX_ENCODED:
        raise ValueError("encoded artifact exceeds upload limit")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("content_base64 is invalid") from exc
    if not data or len(data) > MAX_DECODED:
        raise ValueError("decoded artifact is empty or exceeds upload limit")

    store = ArtifactStore()
    store.ensure_layout()
    temporary_dir = Path(tempfile.mkdtemp(prefix="chatgpt-upload-", dir=store.staging_dir))
    temporary = temporary_dir / filename
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        result = store.capture(
            temporary,
            status=str(payload.get("status") or "final"),
            source_system="chatgpt",
            source_workspace=payload.get("source_workspace") or "chatgpt",
            source_chat=payload.get("source_chat"),
            title=payload.get("title") or Path(filename).stem,
            summary=payload.get("summary"),
            knowledge_project=payload.get("knowledge_project"),
            version_group=payload.get("version_group"),
            pinned=bool(payload.get("pinned", False)),
            metadata={
                "chatgpt_upload": True,
                "requested_filename": filename,
                "tags": payload.get("tags") or [],
                "people": payload.get("people") or [],
                "companies": payload.get("companies") or [],
                "projects": payload.get("projects") or [],
                "meetings": payload.get("meetings") or [],
                "related_artifacts": payload.get("related_artifacts") or [],
            },
        )
        return result
    finally:
        shutil.rmtree(temporary_dir, ignore_errors=True)


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    result = import_payload(payload)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
