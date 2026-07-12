#!/usr/bin/env python3
"""Idempotently extend Hermes Knowledge cards with artifact graph metadata."""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
from pathlib import Path

OLD_FRONTMATTER = """        f'type: {yaml_string(row[\"classified_type\"])}',
        'status: accepted',
"""
NEW_FRONTMATTER = """        f'type: {yaml_string(row[\"classified_type\"])}',
        f'entity_key: {yaml_string(payload.get(\"entity_key\") or \"\")}',
        f'artifact_id: {yaml_string(payload.get(\"artifact_id\") or \"\")}',
        f'tags: {json.dumps([str(item) for item in as_list(payload.get(\"tags\")) if str(item).strip()], ensure_ascii=False)}',
        'status: accepted',
"""
OLD_SECTIONS = """        ('Следующие действия', bullet_lines(as_list(payload.get('next_actions')))),
        ('Источники', bullet_lines(as_list(payload.get('sources')))),
    ]
"""
NEW_SECTIONS = """        ('Следующие действия', bullet_lines(as_list(payload.get('next_actions')))),
        ('Источники', bullet_lines(as_list(payload.get('sources')))),
        ('Связи', bullet_lines(as_list(payload.get('relations')))),
    ]
"""


def patch_text(text: str) -> tuple[str, bool]:
    changed = False
    if "f'entity_key: {yaml_string(payload.get" not in text:
        if OLD_FRONTMATTER not in text:
            raise RuntimeError("knowledge promoter frontmatter anchor not found")
        text = text.replace(OLD_FRONTMATTER, NEW_FRONTMATTER, 1)
        changed = True
    if "('Связи', bullet_lines(as_list(payload.get('relations'))))" not in text:
        if OLD_SECTIONS not in text:
            raise RuntimeError("knowledge promoter sections anchor not found")
        text = text.replace(OLD_SECTIONS, NEW_SECTIONS, 1)
        changed = True
    return text, changed


def patch_file(path: Path) -> dict:
    if not path.is_file():
        return {"path": str(path), "status": "missing"}
    original = path.read_text(encoding="utf-8")
    updated, changed = patch_text(original)
    if not changed:
        return {"path": str(path), "status": "already-patched"}
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(path.name + f".artifact-metadata-{stamp}.bak")
    shutil.copy2(path, backup)
    temporary = path.with_name("." + path.name + ".artifact-metadata.tmp")
    temporary.write_text(updated, encoding="utf-8")
    temporary.chmod(path.stat().st_mode)
    temporary.replace(path)
    return {"path": str(path), "status": "patched", "backup": str(backup)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    paths = [Path(value) for value in args.paths] or [
        Path("/opt/hermes-knowledge-mcp/promoter.py"),
        Path("/opt/hermes-knowledge-runtime/current/promoter.py"),
    ]
    seen: set[Path] = set()
    results = []
    for path in paths:
        resolved = path.resolve() if path.exists() else path
        if resolved in seen:
            continue
        seen.add(resolved)
        results.append(patch_file(path))
    for result in results:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
