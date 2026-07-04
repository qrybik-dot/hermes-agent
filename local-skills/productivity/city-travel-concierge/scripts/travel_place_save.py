#!/usr/bin/env python3
"""Save a verified travel place through Hermes Knowledge quick-save."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from typing import Any
from urllib.parse import urlparse

HELPER_PATH = "/opt/hermes-knowledge-mcp/quick_save_cli.py"
MAX_FIELD = 4000


def _clean(value: str | None, *, limit: int = MAX_FIELD) -> str:
    return " ".join(str(value or "").split())[:limit]


def _absolute_http_url(value: str, *, field: str) -> str:
    cleaned = _clean(value, limit=2000)
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field} must be an absolute http(s) URL")
    return cleaned


def build_request(args: argparse.Namespace) -> dict[str, Any]:
    name = _clean(args.name, limit=240)
    location = _clean(args.location, limit=500)
    description = _clean(args.description)
    source_url = _absolute_http_url(args.source_url, field="source URL")
    if not name or not location or not description:
        raise ValueError("name, location and description are required")

    verified_urls = [
        _absolute_http_url(value, field="verified source URL")
        for value in (args.verified_source_url or [])
    ]
    sources = list(dict.fromkeys([source_url, *verified_urls]))
    optional = {
        "price": _clean(args.price),
        "schedule": _clean(args.schedule),
        "hours": _clean(args.hours),
    }
    labels = {
        "price": "Цена",
        "schedule": "Расписание",
        "hours": "Время работы",
    }

    facts = [
        f"Название: {name}",
        f"Локация: {location}",
        f"Описание: {description}",
    ]
    summary_lines = [f"{name}. {location}", description]
    for key, value in optional.items():
        if value:
            facts.append(f"{labels[key]}: {value}")
            summary_lines.append(f"{labels[key]}: {value}")
    facts.append(f"Исходный URL: {source_url}")
    for value in verified_urls:
        facts.append(f"Проверочный источник: {value}")
    summary_lines.append("Источники: " + ", ".join(sources))

    payload = {
        "knowledge_project": "travel",
        "type": "research",
        "title": f"Место для путешествий: {name}"[:240],
        "summary": "\n".join(summary_lines)[:8000],
        "accepted_facts": facts,
        "sources": sources,
        "sensitivity": "internal",
        "gpt_access": "allowed",
        "source_system": "hermes",
        "source_workspace": "telegram",
    }
    key_material = json.dumps(
        {
            "name": name.casefold(),
            "location": location.casefold(),
            "source_url": source_url,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return {
        "payload": payload,
        "idempotency_key": "travel-place:"
        + hashlib.sha256(key_material.encode("utf-8")).hexdigest(),
        "wait_seconds": 60,
    }


def save_request(request: dict[str, Any]) -> dict[str, Any]:
    proc = subprocess.run(
        [
            "sudo",
            "-n",
            "-u",
            "hermes-knowledge",
            "/usr/bin/python3",
            HELPER_PATH,
        ],
        input=json.dumps(request, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=80,
        check=False,
    )
    lines = (proc.stdout or "").strip().splitlines()
    if not lines:
        raise RuntimeError("knowledge quick-save returned no JSON")
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError("knowledge quick-save returned invalid JSON") from exc
    if proc.returncode != 0:
        result.setdefault("status", "error")
        result.setdefault("saved", False)
        if not result.get("message"):
            result["message"] = _clean(proc.stderr or f"quick-save exited with code {proc.returncode}", limit=1000)
    return result


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--name", required=True)
    value.add_argument("--location", required=True)
    value.add_argument("--description", required=True)
    value.add_argument("--price", default="")
    value.add_argument("--schedule", default="")
    value.add_argument("--hours", default="")
    value.add_argument("--source-url", required=True)
    value.add_argument("--verified-source-url", action="append", default=[])
    value.add_argument("--dry-run", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        request = build_request(args)
        result = request if args.dry_run else save_request(request)
    except (ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(
            json.dumps(
                {"status": "error", "saved": False, "message": str(exc)},
                ensure_ascii=False,
            )
        )
        return 2

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if args.dry_run:
        return 0
    confirmed = bool(result.get("saved") or result.get("already_exists")) and int(
        result.get("readback_count") or 0
    ) > 0
    return 0 if confirmed else 1


if __name__ == "__main__":
    sys.exit(main())
