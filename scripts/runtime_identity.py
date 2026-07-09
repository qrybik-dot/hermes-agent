#!/usr/bin/env python3
"""Emit a redacted, machine-readable Hermes runtime identity snapshot.

The output intentionally contains only executable paths, git metadata, and
systemd unit references. It never loads Hermes configuration or environment
files, so it is safe to attach to operational reports.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _git(root: Path, *args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def git_identity(root: Path) -> dict[str, Any]:
    return {
        "root": str(root),
        "branch": _git(root, "branch", "--show-current"),
        "head": _git(root, "rev-parse", "HEAD"),
        "dirty": bool(_git(root, "status", "--porcelain")),
    }


def parse_unit_text(text: str) -> dict[str, str | None]:
    values: dict[str, str | None] = {"working_directory": None, "exec_start": None}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("WorkingDirectory="):
            values["working_directory"] = line.partition("=")[2] or None
        elif line.startswith("ExecStart="):
            values["exec_start"] = line.partition("=")[2] or None
    return values


def unit_identity(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "present": path.is_file()}
    if path.is_file():
        result.update(parse_unit_text(path.read_text(encoding="utf-8", errors="replace")))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--primary-root", type=Path, default=Path("/home/hermes/.hermes/hermes-agent"))
    parser.add_argument("--fts-unit", type=Path, default=Path("/etc/systemd/system/hermes-memory-fts-index.service"))
    args = parser.parse_args()
    payload = {
        "schema": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "runtime": git_identity(args.runtime_root),
        "primary_checkout": git_identity(args.primary_root),
        "fts_indexer": unit_identity(args.fts_unit),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
