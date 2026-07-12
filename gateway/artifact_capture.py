"""Best-effort outbound artifact capture shared by gateway delivery paths."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from hermes_cli.artifact_store import capture_outbound_artifact

logger = logging.getLogger(__name__)


async def archive_outbound_document(
    file_path: str,
    *,
    platform: str,
    chat_id: str | None,
    source_workspace: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    knowledge_project: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Archive a user-facing document without blocking delivery semantics.

    Hashing and copying run in a worker thread. The original file path remains
    the delivery source; the archive is an independent durable copy.
    """
    result = await asyncio.to_thread(
        capture_outbound_artifact,
        file_path,
        source_system=f"hermes-{platform}",
        source_workspace=source_workspace or platform,
        source_chat=str(chat_id or ""),
        title=title or Path(file_path).stem,
        summary=summary,
        knowledge_project=knowledge_project,
        metadata=metadata or {},
    )
    if result.get("status") == "error":
        logger.warning("Artifact capture failed for %s: %s", file_path, result.get("error"))
    elif result.get("saved"):
        logger.info(
            "Artifact captured before delivery: %s -> %s",
            file_path,
            result.get("artifact_id"),
        )
    return result
