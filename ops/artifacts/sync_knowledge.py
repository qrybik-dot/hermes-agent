#!/usr/bin/env python3
"""Publish pending artifact index cards through Hermes Knowledge's safe pipeline."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_cli.artifact_store import ArtifactStore


def _tags(item: dict[str, Any]) -> list[str]:
    metadata = item.get("metadata") or {}
    values = ["artifact", item.get("extension", "").lstrip("."), item.get("status", "")]
    values.extend(metadata.get("tags") or [])
    return sorted({str(value).strip().lower() for value in values if str(value).strip()})[:12]


def _wiki_links(metadata: dict[str, Any]) -> list[str]:
    links: list[str] = []
    for key in ("people", "companies", "projects", "meetings", "related_artifacts"):
        values = metadata.get(key) or []
        if isinstance(values, str):
            values = [values]
        for value in values:
            text = str(value).strip()
            if not text:
                continue
            links.append(text if text.startswith("[[") else f"[[{text}]]")
    return list(dict.fromkeys(links))[:30]


def build_payload(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") or {}
    title = item.get("title") or Path(item["original_name"]).stem
    links = _wiki_links(metadata)
    tags = _tags(item)
    facts = [
        f"Artifact ID: `{item['artifact_id']}`",
        f"Формат: `{item['extension'].lstrip('.')}`",
        f"Размер: {item['size_bytes']} bytes",
        f"SHA-256: `{item['sha256']}`",
        f"Статус: `{item['status']}`",
        f"Версия: {item['version']} (group `{item['version_group']}`)",
        f"Локальный путь: `{item['stored_path']}`",
        f"Теги: {' '.join('#' + tag.replace(' ', '-') for tag in tags)}",
    ]
    if links:
        facts.append("Связи: " + ", ".join(links))
    if item.get("supersedes"):
        facts.append(f"Заменяет артефакт: `{item['supersedes']}`")
    sources = [
        f"Файл Hermes Artifact Store: `{item['stored_path']}`",
        f"Источник: {item.get('source_system') or 'unknown'} / {item.get('source_workspace') or 'unknown'}",
    ]
    constraints = [
        f"Хранение: {item.get('retention_days') or 'без автоматического срока'} дней",
        f"Закреплён: {'да' if item.get('pinned') else 'нет'}",
        "Физический файл управляется Artifact Store; карточка Knowledge является смысловым индексом.",
    ]
    return {
        "knowledge_project": item.get("knowledge_project") or metadata.get("knowledge_project") or "general",
        "project": item.get("knowledge_project") or metadata.get("knowledge_project") or "general",
        "type": "note",
        "title": f"Артефакт: {title}",
        "summary": item.get("summary") or f"Пользовательский файл {item['original_name']}, сохранённый для поиска и повторной отправки между сессиями.",
        "accepted_facts": facts,
        "constraints": constraints,
        "sources": sources,
        "sensitivity": metadata.get("sensitivity") or "internal",
        "gpt_access": metadata.get("gpt_access") or "allowed",
        "entity_key": f"artifact:{item['artifact_id']}",
        "artifact_id": item["artifact_id"],
        "tags": tags,
        "relations": links,
    }


def post_json(url: str, key: str, payload: dict[str, Any], timeout: float = 90.0) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {key}",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def sync(limit: int = 50, dry_run: bool = False) -> dict[str, Any]:
    store = ArtifactStore()
    items = store.pending_knowledge(limit=limit)
    result: dict[str, Any] = {
        "pending": len(items),
        "synced": [],
        "review_required": [],
        "failed": [],
        "dry_run": dry_run,
    }
    if dry_run:
        result["items"] = [
            {"artifact_id": item["artifact_id"], "payload": build_payload(item)} for item in items
        ]
        return result

    key = os.environ.get("HERMES_KNOWLEDGE_SHARED_KEY", "").strip()
    if not key:
        raise RuntimeError("HERMES_KNOWLEDGE_SHARED_KEY is not configured")
    base = os.environ.get("HERMES_KNOWLEDGE_LOCAL_URL", "http://127.0.0.1:2092").rstrip("/")

    for item in items:
        artifact_id = item["artifact_id"]
        body = {
            "payload": build_payload(item),
            "confirmation": True,
            "approval_note": "Пользователь заранее согласовал автоматическое индексирование финальных файлов в Hermes Knowledge.",
            "idempotency_key": f"artifact-card:{artifact_id}",
            "source_project": item.get("knowledge_project") or "general",
            "source_workspace": item.get("source_workspace") or "artifact-store",
            "wait_seconds": 75,
        }
        try:
            response = post_json(f"{base}/api/candidates/save", key, body)
            status = str(response.get("status") or "")
            candidate = response.get("candidate") or {}
            candidate_id = candidate.get("id") or response.get("candidate_id")
            if status in {"accepted", "rendered", "approved", "idempotent-replay"} or response.get("saved"):
                store.mark_knowledge_sync(
                    artifact_id,
                    "synced",
                    candidate_id=candidate_id,
                    card_path=response.get("path") or response.get("canonical_path"),
                )
                result["synced"].append({"artifact_id": artifact_id, "candidate_id": candidate_id, "status": status})
            elif status == "duplicate" or response.get("message", "").startswith("Не сохранено: такой документ уже есть"):
                store.mark_knowledge_sync(artifact_id, "synced", candidate_id=candidate_id)
                result["synced"].append({"artifact_id": artifact_id, "candidate_id": candidate_id, "status": "duplicate"})
            elif status in {"needs_review", "blocked", "rejected"}:
                error = response.get("message") or response.get("error") or status
                store.mark_knowledge_sync(
                    artifact_id,
                    "review_required",
                    candidate_id=candidate_id,
                    error=str(error),
                )
                result["review_required"].append(
                    {"artifact_id": artifact_id, "error": error, "response_status": status}
                )
            else:
                error = response.get("message") or response.get("error") or f"unexpected status: {status}"
                store.mark_knowledge_sync(artifact_id, "failed", candidate_id=candidate_id, error=str(error))
                result["failed"].append({"artifact_id": artifact_id, "error": error, "response_status": status})
        except (urllib.error.URLError, TimeoutError, ValueError, RuntimeError) as exc:
            store.mark_knowledge_sync(artifact_id, "failed", error=f"{type(exc).__name__}: {exc}")
            result["failed"].append({"artifact_id": artifact_id, "error": f"{type(exc).__name__}: {exc}"})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(sync(limit=args.limit, dry_run=args.dry_run), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
