#!/usr/bin/env python3
"""Read-only local backup inventory and retention preview."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def tree_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/home/hermes/.hermes/backups"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    now = time.time()
    entries = []
    for path in args.root.iterdir():
        stat = path.stat()
        age_days = int((now - stat.st_mtime) / 86400)
        entries.append({"name": path.name, "bytes": tree_size(path), "age_days": age_days, "candidate": age_days >= 30})
    entries.sort(key=lambda item: item["bytes"], reverse=True)
    result = {
        "status": "DRY_RUN",
        "policy": "keep all under 30 days; review older entries manually; never delete recovery markers automatically",
        "count": len(entries),
        "total_bytes": sum(e["bytes"] for e in entries),
        "candidate_count": sum(bool(e["candidate"]) for e in entries),
        "candidate_bytes": sum(e["bytes"] for e in entries if e["candidate"]),
        "largest": entries[:20],
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            "DRY_RUN"
            f" count={result['count']}"
            f" total_bytes={result['total_bytes']}"
            f" candidate_count={result['candidate_count']}"
            f" candidate_bytes={result['candidate_bytes']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
