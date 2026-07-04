#!/usr/bin/env python3
"""Save a travel place from a validated fixed JSON staging file."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys

SCRIPT = Path(__file__).with_name("travel_place_save.py")
SPEC = importlib.util.spec_from_file_location("travel_place_save", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)

MAX_SIZE = 32 * 1024
ALLOWED_KEYS = {
    "name", "location", "description", "price", "schedule", "hours",
    "source_url", "verified_source_url", "verified_fields",
}


def payload_path() -> Path:
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return home / "tmp" / "travel-place-save.json"


def load_args(path: Path) -> argparse.Namespace:
    if path.is_symlink() or not path.is_file():
        raise ValueError("travel payload must be a regular non-symlink file")
    if path.stat().st_size > MAX_SIZE:
        raise ValueError("travel payload is too large")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("travel payload must be a JSON object")
    unknown = sorted(set(data) - ALLOWED_KEYS)
    if unknown:
        raise ValueError("unsupported fields: " + ", ".join(unknown))
    verified = data.get("verified_source_url") or []
    if isinstance(verified, str):
        verified = [verified]
    if not isinstance(verified, list) or not all(isinstance(item, str) for item in verified):
        raise ValueError("verified_source_url must be a string list")
    verified_fields = data.get("verified_fields") or []
    return argparse.Namespace(
        name=data.get("name", ""),
        location=data.get("location", ""),
        description=data.get("description", ""),
        price=data.get("price", ""),
        schedule=data.get("schedule", ""),
        hours=data.get("hours", ""),
        source_url=data.get("source_url", ""),
        verified_source_url=verified,
        verified_fields=verified_fields,
        dry_run=False,
    )


def main() -> int:
    try:
        request = MODULE.build_request(load_args(payload_path()))
        result = MODULE.save_request(request)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        result = {"status": "error", "saved": False, "message": str(exc)}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    confirmed = bool(result.get("saved") or result.get("already_exists")) and int(
        result.get("readback_count") or 0
    ) > 0
    return 0 if confirmed else 1


if __name__ == "__main__":
    sys.exit(main())
