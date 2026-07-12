#!/usr/bin/env python3
"""Narrow JSON stdin API used by the global Hermes VPS Admin connector."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_cli.artifact_store import ArtifactStore
from ops.artifacts.import_base64 import import_payload


def public(item):
    if not item:
        return item
    hidden = {"metadata_json", "source_chat"}
    return {key: value for key, value in item.items() if key not in hidden}


def handle(request: dict) -> dict:
    action = str(request.get("action") or "health")
    store = ArtifactStore()
    if action == "health":
        return store.health()
    if action == "search":
        return {
            "items": [
                public(item)
                for item in store.search(
                    str(request.get("query") or ""),
                    limit=int(request.get("limit") or 20),
                    current_only=bool(request.get("current_only", False)),
                )
            ]
        }
    if action == "get":
        return {"artifact": public(store.get(str(request.get("artifact_id") or "")))}
    if action == "versions":
        return {"items": [public(item) for item in store.versions(str(request.get("artifact_id") or ""))]}
    if action == "upload":
        return {"artifact": public(import_payload(request))}
    if action == "send_telegram":
        artifact_id = str(request.get("artifact_id") or "")
        item = store.get(artifact_id)
        if not item or not item.get("local_available"):
            raise ValueError("artifact not found or unavailable")
        caption = str(request.get("caption") or "").strip()
        message = (caption + "\n" if caption else "") + f"[[as_document]] MEDIA:{item['stored_path']}"
        completed = subprocess.run(
            [sys.executable, "-m", "hermes_cli.main", "send", "--to", "telegram", message],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        return {
            "artifact": public(item),
            "sent": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-1000:],
            "stderr": completed.stderr[-1000:],
        }
    raise ValueError(f"unsupported action: {action}")


def main() -> int:
    request = json.loads(sys.stdin.read() or "{}")
    print(json.dumps(handle(request), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
