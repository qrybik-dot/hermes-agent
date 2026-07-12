#!/usr/bin/env python3
"""Create a transactionally consistent SQLite backup for Restic."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_cli.artifact_store import ArtifactStore


def snapshot() -> Path:
    store = ArtifactStore()
    store.ensure_layout()
    destination_dir = store.root / "backup"
    destination_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(destination_dir, 0o2770)
    except PermissionError:
        pass
    destination = destination_dir / "artifact_registry.sqlite3"
    fd, temporary_name = tempfile.mkstemp(prefix=".artifact-registry.", suffix=".tmp", dir=destination_dir)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        source = store.connect()
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
            integrity = target.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise RuntimeError(f"artifact registry snapshot integrity failed: {integrity}")
            target.commit()
        finally:
            target.close()
            source.close()
        os.chmod(temporary, 0o660)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def main() -> int:
    print(snapshot())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
