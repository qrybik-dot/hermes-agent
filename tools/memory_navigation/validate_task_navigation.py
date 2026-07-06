#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

DEFAULT_VAULT = Path('/srv/hermes-memory/vault')
PERSONAL_ROOT = 'Personal Anton'
INDEX_REL = Path(PERSONAL_ROOT) / '00-start' / 'INDEX.md'
VIEW_NAMES = (
    'hermes-runtime.md',
    'model-routing.md',
    'knowledge-memory.md',
    'web-research.md',
    'reporting-artifacts.md',
    'security-access.md',
)
VIEW_RELS = tuple(Path(PERSONAL_ROOT) / 'views' / name for name in VIEW_NAMES)
WIKILINK_RE = re.compile(r'\[\[([^\]]+)\]\]')
CANONICAL_RE = re.compile(r'canonical:([a-z0-9._-]+)', re.I)
ARCHIVE_MARKERS = ('/Archive/', '/.stversions/', '/backups/', '/Backup/')
VIEW_SECTIONS = (
    'Purpose',
    'Start here',
    'Canonical files',
    'Common task routes',
    'Related components',
    'Archived or non-canonical materials',
    'Last verified',
)
INDEX_SECTIONS = ('Current work', 'Start here', 'Canonical files', 'Common task routes', 'History and archive', 'Last verified')


@dataclass
class Finding:
    level: str
    code: str
    file: str
    detail: str


class NavigationPermissionError(RuntimeError):
    pass


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith('---\n'):
        return {}
    end = text.find('\n---\n', 4)
    if end < 0:
        return {}
    result: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ':' not in line:
            continue
        key, value = line.split(':', 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def section_text(text: str, heading: str) -> str:
    marker = f'## {heading}'
    if marker not in text:
        return ''
    block = text.split(marker, 1)[1]
    if '\n## ' in block:
        block = block.split('\n## ', 1)[0]
    return block


def link_target(raw: str) -> str:
    value = raw.split('|', 1)[0].split('#', 1)[0].strip()
    return value[:-3] if value.lower().endswith('.md') else value


def resolve_link(vault: Path, source: Path, raw: str) -> Path | None:
    target = link_target(raw)
    if not target:
        return source
    rel = Path(target + '.md')
    candidates = []
    if target.startswith(PERSONAL_ROOT + '/'):
        candidates.append(vault / rel)
    else:
        candidates.extend((source.parent / rel, vault / rel, vault / PERSONAL_ROOT / rel))
    for candidate in candidates:
        try:
            candidate.resolve().relative_to(vault.resolve())
        except ValueError:
            continue
        try:
            if candidate.is_file():
                return candidate
        except PermissionError as exc:
            raise NavigationPermissionError(str(candidate)) from exc
    return None


def validate(vault: Path = DEFAULT_VAULT) -> dict:
    nav_rels = (INDEX_REL, *VIEW_RELS)
    findings: list[Finding] = []
    canonical: dict[str, str] = {}
    checked_links = 0

    for rel in nav_rels:
        path = vault / rel
        if not path.is_file():
            findings.append(Finding('error', 'missing_navigation_file', rel.as_posix(), 'required navigation file is missing'))
            continue
        text = path.read_text(encoding='utf-8', errors='strict')
        required = INDEX_SECTIONS if rel == INDEX_REL else VIEW_SECTIONS
        for heading in required:
            if f'## {heading}' not in text:
                findings.append(Finding('error', 'missing_section', rel.as_posix(), heading))

        fm = parse_frontmatter(text)
        verified = fm.get('last_verified', '')
        try:
            age = (date.today() - datetime.strptime(verified, '%Y-%m-%d').date()).days
            if age > 90:
                findings.append(Finding('warning', 'stale_navigation', rel.as_posix(), f'last_verified is {age} days old'))
        except ValueError:
            findings.append(Finding('error', 'invalid_last_verified', rel.as_posix(), verified or 'missing'))

        for raw in WIKILINK_RE.findall(text):
            checked_links += 1
            try:
                target = resolve_link(vault, path, raw)
            except NavigationPermissionError as exc:
                findings.append(Finding('error', 'permission_denied', rel.as_posix(), str(exc)))
                continue
            if target is None:
                findings.append(Finding('error', 'broken_link', rel.as_posix(), raw))

        for heading in ('Start here', 'Canonical files'):
            for raw in WIKILINK_RE.findall(section_text(text, heading)):
                normalized = '/' + link_target(raw).strip('/') + '/'
                if any(marker.casefold() in normalized.casefold() for marker in ARCHIVE_MARKERS):
                    findings.append(Finding('error', 'archive_default_route', rel.as_posix(), raw))

        for line in text.splitlines():
            key_match = CANONICAL_RE.search(line)
            link_match = WIKILINK_RE.search(line)
            if not key_match:
                continue
            if not link_match:
                findings.append(Finding('error', 'canonical_without_link', rel.as_posix(), key_match.group(1)))
                continue
            key = key_match.group(1).casefold()
            target = link_target(link_match.group(1))
            previous = canonical.get(key)
            if previous and previous != target:
                findings.append(Finding('error', 'canonical_conflict', rel.as_posix(), f'{key}: {previous} != {target}'))
            else:
                canonical[key] = target

    index_path = vault / INDEX_REL
    if index_path.is_file():
        index_text = index_path.read_text(encoding='utf-8')
        index_targets = {link_target(raw) for raw in WIKILINK_RE.findall(index_text)}
        for rel in VIEW_RELS:
            expected = rel.with_suffix('').as_posix()
            if expected not in index_targets:
                findings.append(Finding('error', 'view_not_routed', INDEX_REL.as_posix(), expected))

    errors = [item for item in findings if item.level == 'error']
    warnings = [item for item in findings if item.level == 'warning']
    return {
        'status': 'ok' if not errors else 'failed',
        'navigation_files': len(nav_rels),
        'checked_links': checked_links,
        'canonical_keys': len(canonical),
        'errors': [item.__dict__ for item in errors],
        'warnings': [item.__dict__ for item in warnings],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--vault', type=Path, default=DEFAULT_VAULT)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    result = validate(args.vault)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None, sort_keys=True))
    return 0 if result['status'] == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
