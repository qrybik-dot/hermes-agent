import re
import sqlite3
from typing import Any

from config import FTS_DB
from project_registry import project_aliases, resolve_project
from utils import normalize, tokens

NAV_INDEX_PATH = 'Personal Anton/00-start/INDEX.md'
VIEW_PATHS = {
    'hermes-runtime': 'Personal Anton/views/hermes-runtime.md',
    'model-routing': 'Personal Anton/views/model-routing.md',
    'knowledge-memory': 'Personal Anton/views/knowledge-memory.md',
    'web-research': 'Personal Anton/views/web-research.md',
    'reporting-artifacts': 'Personal Anton/views/reporting-artifacts.md',
    'security-access': 'Personal Anton/views/security-access.md',
}
NAVIGATION_TYPES = {'navigation', 'navigation_index', 'navigation-index', 'task_view', 'task-view'}
WIKILINK_RE = re.compile(r'\[\[([^\]]+)\]\]')
ARCHIVE_WORDS = {'архив', 'история', 'исторический', 'старый', 'backup', 'archive', 'history', 'rollback', 'откат'}
VIEW_RULES = (
    ('model-routing', ('модел', 'routing', 'роутинг', 'маршрутизац', 'fallback', 'провайдер', 'provider', 'codex', 'gemini')),
    ('web-research', ('web', 'research', 'источник', 'браузер', 'browser', 'reel', 'рилс', 'карты', 'парков', 'travel', 'поезд')),
    ('knowledge-memory', ('памят', 'knowledge', 'знан', 'fts', 'vault', 'obsidian', 'syncthing', 'graph', 'graphify', 'индекс', 'сохран', 'read-back', 'readback')),
    ('reporting-artifacts', ('отчёт', 'отчет', 'html', 'артефакт', 'report', 'evidence', 'доказатель', 'презентац')),
    ('security-access', ('vps', 'ssh', 'безопас', 'доступ', 'секрет', 'acl', 'backup', 'rollback', 'откат', 'systemd', 'restart', 'перезапуск')),
)


def _project_from_path(path: str) -> str | None:
    for marker in ('/Knowledge/', 'Knowledge/'):
        if marker in path:
            tail = path.split(marker, 1)[1].lstrip('/')
            return tail.split('/', 1)[0].strip() or None
    return None


def _resolved_project(value: str | None) -> tuple[str, list[str]]:
    if not value:
        return '', []
    resolved = resolve_project(value)
    slug = normalize(resolved.get('knowledge_project') or value)
    aliases = [normalize(x) for x in project_aliases(slug)] if slug else []
    return slug, aliases


def _archive_requested(query: str) -> bool:
    words = set(tokens(query))
    normalized = normalize(query)
    return bool(words & ARCHIVE_WORDS) or any(word in normalized for word in ARCHIVE_WORDS)


def _is_archive_path(path: str) -> bool:
    normalized = '/' + path.replace('\\', '/').strip('/') + '/'
    low = normalized.casefold()
    return any(marker in low for marker in ('/archive/', '/.stversions/', '/backups/', '/backup/'))


def _navigation_type(value: str | None) -> bool:
    return normalize(value or '').replace('-', '_') in {item.replace('-', '_') for item in NAVIGATION_TYPES}


def _connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f'file:{FTS_DB}?mode=ro&immutable=1', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _select_view(query: str) -> str:
    value = normalize(query)
    scores: dict[str, int] = {}
    for name, needles in VIEW_RULES:
        score = sum(1 for needle in needles if normalize(needle) in value)
        if score:
            scores[name] = score
    if not scores:
        return 'hermes-runtime'
    rule_order = {name: index for index, (name, _) in enumerate(VIEW_RULES)}
    return sorted(scores.items(), key=lambda item: (-item[1], rule_order[item[0]]))[0][0]


def _link_target(raw: str) -> str:
    value = raw.split('|', 1)[0].split('#', 1)[0].strip()
    if not value:
        return ''
    return value if value.lower().endswith('.md') else value + '.md'


def _section(text: str, heading: str) -> str:
    marker = f'## {heading}'
    if marker not in text:
        return ''
    block = text.split(marker, 1)[1]
    if '\n## ' in block:
        block = block.split('\n## ', 1)[0]
    return block


def _canonical_targets(view_text: str) -> list[str]:
    result: list[str] = []
    for raw in WIKILINK_RE.findall(_section(view_text, 'Canonical files')):
        target = _link_target(raw)
        if target and target not in result and not _is_archive_path(target):
            result.append(target)
    return result


def _fetch_paths(paths: list[str]) -> list[dict[str, Any]]:
    if not paths or not FTS_DB.exists():
        return []
    conn = _connect_ro()
    columns = {row[1] for row in conn.execute('pragma table_info(docs)').fetchall()}
    has_metadata = {'knowledge_project', 'entity_key'} <= columns
    placeholders = ','.join('?' for _ in paths)
    if has_metadata:
        sql = (
            'SELECT f.path,f.title,f.body,f.entity_type,f.source,'
            'd.knowledge_project,d.entity_key FROM docs_fts f '
            f'LEFT JOIN docs d ON d.path=f.path WHERE f.path IN ({placeholders})'
        )
    else:
        sql = (
            'SELECT path,title,body,entity_type,source,'
            "'' knowledge_project,'' entity_key FROM docs_fts "
            f'WHERE path IN ({placeholders})'
        )
    rows = conn.execute(sql, paths).fetchall()
    conn.close()
    by_path = {row['path']: row for row in rows}
    result: list[dict[str, Any]] = []
    for path in paths:
        row = by_path.get(path)
        if row is None:
            continue
        project = row['knowledge_project'] or _project_from_path(path)
        result.append({
            'path': path,
            'title': row['title'],
            'body': row['body'] or '',
            'entity_type': row['entity_type'],
            'entity_key': row['entity_key'] or None,
            'source': row['source'],
            'knowledge_project': project,
            'project': project,
        })
    return result


def _relevance(query: str, item: dict[str, Any]) -> int:
    haystack = normalize(f"{item.get('title', '')} {item.get('body', '')}")
    return sum(1 for word in tokens(query) if word and word in haystack)


def knowledge_search(query: str, project: str | None = None, knowledge_project: str | None = None, limit: int = 8, include_body: bool = False) -> list[dict[str, Any]]:
    try:
        if not FTS_DB.exists():
            return []
    except PermissionError:
        return []
    words = list(tokens(query))[:12]
    if not words:
        return []
    match = ' OR '.join(f'"{word.replace(chr(34), "")}"' for word in words)
    conn = _connect_ro()
    columns = {row[1] for row in conn.execute('pragma table_info(docs)').fetchall()}
    has_metadata = {'knowledge_project', 'entity_key'} <= columns
    if has_metadata:
        sql = (
            'SELECT f.path,f.title,f.body,f.entity_type,f.source,bm25(docs_fts) rank,'
            'd.knowledge_project,d.entity_key FROM docs_fts f '
            'LEFT JOIN docs d ON d.path=f.path WHERE docs_fts MATCH ? ORDER BY rank LIMIT ?'
        )
    else:
        sql = (
            'SELECT path,title,body,entity_type,source,bm25(docs_fts) rank,'
            "'' knowledge_project,'' entity_key FROM docs_fts "
            'WHERE docs_fts MATCH ? ORDER BY rank LIMIT ?'
        )
    rows = conn.execute(sql, (match, max(40, limit * 8))).fetchall()
    conn.close()

    requested = knowledge_project or project
    project_norm, aliases = _resolved_project(requested)
    allow_archive = _archive_requested(query)
    results: list[dict[str, Any]] = []
    for row in rows:
        if _navigation_type(row['entity_type']):
            continue
        if not allow_archive and _is_archive_path(row['path']):
            continue
        metadata_project = normalize(row['knowledge_project'] or '')
        path_project = normalize(_project_from_path(row['path']) or '')
        if project_norm and project_norm not in {'general', 'global'}:
            candidate = metadata_project or path_project
            if candidate:
                if candidate != project_norm and candidate not in aliases:
                    continue
            else:
                path_title = normalize(f"{row['path']} {row['title']}")
                if project_norm not in path_title and not any(alias in path_title for alias in aliases):
                    continue
        item_project = row['knowledge_project'] or _project_from_path(row['path'])
        item = {
            'path': row['path'], 'title': row['title'],
            'entity_type': row['entity_type'], 'entity_key': row['entity_key'] or None,
            'source': row['source'], 'knowledge_project': item_project,
            'project': item_project, 'score': round(-float(row['rank']), 6),
            'snippet': (row['body'] or '')[:1200],
        }
        if include_body:
            item['body'] = row['body'] or ''
        results.append(item)
        if len(results) >= limit:
            break
    return results


def knowledge_context(query: str, project: str | None = None, knowledge_project: str | None = None, limit: int = 8, max_chars: int = 12000) -> dict[str, Any]:
    view_name = _select_view(query)
    nav_paths = [NAV_INDEX_PATH, VIEW_PATHS[view_name]]
    nav_docs = _fetch_paths(nav_paths)
    view_text = next((item['body'] for item in nav_docs if item['path'] == VIEW_PATHS[view_name]), '')
    canonical_paths = _canonical_targets(view_text)
    canonical_docs = _fetch_paths(canonical_paths)
    canonical_docs.sort(key=lambda item: (-_relevance(query, item), canonical_paths.index(item['path'])))
    canonical_docs = canonical_docs[:2]

    fallback = knowledge_search(
        query,
        project=project,
        knowledge_project=knowledge_project,
        limit=max(12, limit * 4),
        include_body=True,
    )
    used_paths = {item['path'] for item in nav_docs + canonical_docs}
    fallback = [item for item in fallback if item['path'] not in used_paths]

    selected: list[dict[str, Any]] = []
    used = 0

    def add(item: dict[str, Any], *, navigation: bool = False, canonical: bool = False) -> None:
        nonlocal used
        body = item.get('body') or item.get('snippet') or ''
        block = f"# {item.get('title') or item['path']}\nPath: {item['path']}\n{body}"
        remaining = max_chars - used
        if remaining <= 0 or (selected and len(block) > remaining):
            return
        block = block[:remaining]
        selected.append({
            **{key: value for key, value in item.items() if key != 'body'},
            'context': block,
            'navigation': navigation,
            'canonical': canonical,
        })
        used += len(block)

    for item in nav_docs:
        add(item, navigation=True)
    for item in canonical_docs:
        add(item, canonical=True)
    max_documents = min(max(1, int(limit)), 2)
    for item in fallback:
        if sum(1 for row in selected if not row.get('navigation')) >= max_documents:
            break
        add(item)

    return {
        'query': query,
        'project': project or knowledge_project,
        'knowledge_project': knowledge_project or project,
        'navigation_view': view_name,
        'navigation_paths': nav_paths,
        'canonical_targets': canonical_paths,
        'chunks': selected,
        'chars': used,
        'archive_included': _archive_requested(query),
        'note': 'Task-oriented navigation is loaded first; canonical files are prioritized; archive is excluded unless the query explicitly requests history or rollback.',
    }
