"""Agent-facing durable artifact registry tool."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hermes_cli.artifact_store import ArtifactStore, ArtifactStoreError
from tools.registry import registry


def _public(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return item
    allowed = {
        "artifact_id", "original_name", "stored_path", "extension", "size_bytes",
        "sha256", "status", "created_at", "updated_at", "source_system",
        "source_workspace", "title", "summary", "knowledge_project",
        "version_group", "version", "is_current", "retention_days", "delete_after",
        "pinned", "local_available", "supersedes", "superseded_by",
        "knowledge_sync_status", "knowledge_candidate_id", "knowledge_card_path",
        "archived_to", "metadata", "saved", "deduplicated",
    }
    return {key: value for key, value in item.items() if key in allowed}


def artifact_store_tool(args: dict[str, Any], **kwargs) -> str:
    action = str(args.get("action") or "search").strip().lower()
    store = ArtifactStore()
    try:
        if action == "health":
            return json.dumps(store.health(), ensure_ascii=False)
        if action == "search":
            query = str(args.get("query") or "").strip()
            items = store.search(
                query,
                limit=int(args.get("limit") or 20),
                current_only=bool(args.get("current_only", False)),
            )
            return json.dumps({"items": [_public(item) for item in items]}, ensure_ascii=False)
        if action == "get":
            item = store.get(str(args.get("artifact_id") or ""))
            return json.dumps({"artifact": _public(item)}, ensure_ascii=False)
        if action == "versions":
            items = store.versions(str(args.get("artifact_id") or ""))
            return json.dumps({"items": [_public(item) for item in items]}, ensure_ascii=False)
        if action == "capture":
            file_path = str(args.get("file_path") or "").strip()
            result = store.capture(
                file_path,
                status=str(args.get("status") or "final"),
                source_system="hermes-manual",
                source_workspace=args.get("source_workspace"),
                source_chat=kwargs.get("task_id"),
                title=args.get("title"),
                summary=args.get("summary"),
                knowledge_project=args.get("knowledge_project"),
                version_group=args.get("version_group"),
                pinned=bool(args.get("pinned", False)),
                metadata={"manual_capture": True},
            )
            return json.dumps({"artifact": _public(result)}, ensure_ascii=False)
        if action in {"pin", "unpin"}:
            result = store.pin(str(args.get("artifact_id") or ""), pinned=action == "pin")
            return json.dumps({"artifact": _public(result)}, ensure_ascii=False)
        if action == "delete":
            result = store.soft_delete(
                str(args.get("artifact_id") or ""),
                reason=str(args.get("reason") or "user"),
            )
            return json.dumps({"artifact": _public(result)}, ensure_ascii=False)
        if action == "restore":
            result = store.restore(str(args.get("artifact_id") or ""))
            return json.dumps({"artifact": _public(result)}, ensure_ascii=False)
        if action == "cleanup":
            result = store.cleanup(dry_run=not bool(args.get("apply", False)), safe_only=True)
            return json.dumps(result, ensure_ascii=False)
        if action == "reconcile":
            return json.dumps(store.reconcile(), ensure_ascii=False)
        if action == "send_telegram":
            artifact_id = str(args.get("artifact_id") or "").strip()
            item = store.get(artifact_id)
            if not item:
                raise ArtifactStoreError("artifact not found")
            if not item.get("local_available") or not Path(item["stored_path"]).is_file():
                raise ArtifactStoreError("artifact bytes are not available locally")
            from tools.send_message_tool import send_message_tool

            caption = str(args.get("caption") or "").strip()
            prefix = caption + "\n" if caption else ""
            result = send_message_tool(
                {
                    "action": "send",
                    "target": "telegram",
                    "message": f"{prefix}[[as_document]] MEDIA:{item['stored_path']}",
                },
                **kwargs,
            )
            return json.dumps(
                {"artifact": _public(item), "delivery": result},
                ensure_ascii=False,
            )
        raise ArtifactStoreError(f"unsupported action: {action}")
    except Exception as exc:
        return json.dumps(
            {"success": False, "error": f"{type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )


ARTIFACT_STORE_SCHEMA = {
    "name": "artifact_store",
    "description": (
        "Search and manage durable user-facing files created by Hermes. Use it to find a prior "
        "DOCX/PDF/XLSX/Markdown artifact across sessions, inspect versions, resend it to Telegram, "
        "pin it, restore it, or check the 4 GB storage quota. Images are intentionally excluded."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "search", "get", "versions", "health", "capture", "send_telegram",
                    "pin", "unpin", "delete", "restore", "cleanup", "reconcile",
                ],
            },
            "query": {"type": "string"},
            "artifact_id": {"type": "string"},
            "file_path": {"type": "string"},
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "knowledge_project": {"type": "string"},
            "source_workspace": {"type": "string"},
            "version_group": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["final", "draft", "temporary"],
            },
            "pinned": {"type": "boolean"},
            "caption": {"type": "string"},
            "reason": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "current_only": {"type": "boolean"},
            "apply": {
                "type": "boolean",
                "description": "For cleanup only. False/default returns dry-run; true deletes only expired safe categories.",
            },
        },
        "required": ["action"],
    },
}


registry.register(
    name="artifact_store",
    toolset="artifact",
    schema=ARTIFACT_STORE_SCHEMA,
    handler=artifact_store_tool,
    emoji="🗃️",
)
