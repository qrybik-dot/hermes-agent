"""Fast local SQLite FTS search for the Hermes memory vault."""
from __future__ import annotations

import argparse, contextlib, fcntl, hashlib, json, re, sqlite3, time
from pathlib import Path

DEFAULT_VAULT = Path('/srv/hermes-memory/vault')
DEFAULT_INDEX_DIR = Path('/srv/hermes-memory/indexes/fts')
EXCLUDE_PARTS = {'.obsidian', '.stversions', '.stfolder', 'backups', 'staging', 'tmp', 'secrets'}

def _is_text_md(path: Path, *, root: Path | None = None) -> bool:
    if path.suffix.lower() != '.md':
        return False
    parts = path.parts
    if root is not None:
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            parts = path.parts
    return not (set(parts) & EXCLUDE_PARTS)

def _connect(index_dir: Path) -> sqlite3.Connection:
    index_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(index_dir / 'memory_fts.sqlite3')
    con.execute('pragma journal_mode=WAL')
    con.execute('pragma synchronous=NORMAL')
    con.execute('create table if not exists docs(path text primary key, title text, mtime real, sha256 text, entity_type text, source text, knowledge_project text, entity_key text)')
    columns = {row[1] for row in con.execute('pragma table_info(docs)').fetchall()}
    for name in ('knowledge_project', 'entity_key'):
        if name not in columns:
            con.execute(f'alter table docs add column {name} text')
    con.execute("create virtual table if not exists docs_fts using fts5(path unindexed, title, body, entity_type unindexed, source unindexed, tokenize='unicode61')")
    return con

@contextlib.contextmanager
def _lock(index_dir: Path):
    index_dir.mkdir(parents=True, exist_ok=True)
    with open(index_dir / '.lock', 'w') as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield

def _title(text: str, fallback: str) -> str:
    for line in text.splitlines()[:40]:
        s = line.strip()
        if s.startswith('#'):
            return s.lstrip('#').strip()[:200] or fallback
    return fallback

def _frontmatter_value(text: str, key: str) -> str | None:
    if not text.startswith('---\n'):
        return None
    end = text.find('\n---\n', 4)
    if end < 0:
        return None
    prefix = key.lower() + ':'
    for line in text[4:end].splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(prefix):
            value = stripped.split(':', 1)[1].strip().strip('"').strip("'")
            return value or None
    return None


def _entity_type(rel: str, text: str = '') -> str:
    declared = _frontmatter_value(text, 'entity_type') or _frontmatter_value(text, 'type')
    if declared:
        normalized = re.sub(r'[^a-z0-9_-]+', '-', declared.lower()).strip('-')
        if normalized:
            return normalized[:64]
    low = rel.lower()
    if 'granola' in low or 'meeting' in low or 'встреч' in low: return 'meeting'
    if 'career' in low or 'job' in low or 'работ' in low: return 'career'
    if 'project' in low or 'проект' in low: return 'project'
    if 'family' in low or 'сем' in low: return 'family'
    return 'note'

def reindex(vault: Path = DEFAULT_VAULT, index_dir: Path = DEFAULT_INDEX_DIR) -> dict:
    start = time.time(); changed = skipped = scanned = 0
    with _lock(index_dir):
        con = _connect(index_dir); seen = set()
        for path in vault.rglob('*.md'):
            if not _is_text_md(path, root=vault): skipped += 1; continue
            try:
                rel = str(path.relative_to(vault)); data = path.read_bytes(); text = data.decode('utf-8', errors='ignore')
            except Exception:
                skipped += 1; continue
            scanned += 1; seen.add(rel); digest = hashlib.sha256(data).hexdigest(); st = path.stat()
            title = _title(text, path.stem); et = _entity_type(rel, text)
            kp = _frontmatter_value(text, 'knowledge_project') or _frontmatter_value(text, 'project') or ''
            ek = _frontmatter_value(text, 'entity_key') or ''
            row = con.execute('select sha256,entity_type,knowledge_project,entity_key from docs where path=?', (rel,)).fetchone()
            if row and row[0] == digest and row[1] == et and (row[2] or '') == kp and (row[3] or '') == ek: continue
            con.execute('insert or replace into docs(path,title,mtime,sha256,entity_type,source,knowledge_project,entity_key) values(?,?,?,?,?,?,?,?)', (rel, title, st.st_mtime, digest, et, 'vault', kp, ek))
            con.execute('delete from docs_fts where path=?', (rel,))
            con.execute('insert into docs_fts(path,title,body,entity_type,source) values(?,?,?,?,?)', (rel, title, text[:200000], et, 'vault'))
            changed += 1
        for (rel,) in con.execute('select path from docs').fetchall():
            if rel not in seen:
                con.execute('delete from docs where path=?', (rel,)); con.execute('delete from docs_fts where path=?', (rel,)); changed += 1
        con.commit(); total = con.execute('select count(*) from docs').fetchone()[0]; con.close()
    return {'status': 'ok', 'scanned': scanned, 'changed': changed, 'skipped': skipped, 'total': total, 'elapsed_ms': int((time.time()-start)*1000)}

def _snippet(text: str, term: str, size: int = 220) -> str:
    low = text.lower(); words = (term or '').lower().split(); idx = low.find(words[0]) if words else -1
    if idx < 0: idx = 0
    return re.sub(r'\s+', ' ', text[max(0, idx-size//3):max(0, idx-size//3)+size]).strip()

def search(query: str, *, limit: int = 5, vault: Path = DEFAULT_VAULT, index_dir: Path = DEFAULT_INDEX_DIR) -> list[dict]:
    if not (index_dir / 'memory_fts.sqlite3').exists(): reindex(vault, index_dir)
    con = _connect(index_dir)
    q = ' '.join(t for t in re.findall(r'[\w\-А-Яа-яЁё]+', query or '') if t)
    if not q: return []
    try:
        rows = con.execute('select path,title,body,entity_type,source,bm25(docs_fts) as score from docs_fts where docs_fts match ? order by score limit ?', (q, int(limit))).fetchall()
    except sqlite3.OperationalError:
        rows = con.execute('select path,title,body,entity_type,source,bm25(docs_fts) as score from docs_fts where docs_fts match ? order by score limit ?', (q.replace(' ', ' OR '), int(limit))).fetchall()
    out = [{'path': p, 'heading': t, 'snippet': _snippet(b or t, query), 'score': float(s), 'entity_type': et, 'source': src} for p,t,b,et,src,s in rows]
    con.close(); return out

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog='hermes-memory-search')
    p.add_argument('query', nargs='*'); p.add_argument('--limit', type=int, default=5); p.add_argument('--json', action='store_true'); p.add_argument('--reindex', action='store_true'); p.add_argument('--vault', default=str(DEFAULT_VAULT)); p.add_argument('--index-dir', default=str(DEFAULT_INDEX_DIR))
    ns = p.parse_args(argv); vault = Path(ns.vault); index_dir = Path(ns.index_dir)
    result = reindex(vault, index_dir) if ns.reindex else search(' '.join(ns.query), limit=ns.limit, vault=vault, index_dir=index_dir)
    if ns.json: print(json.dumps(result, ensure_ascii=False, indent=2))
    elif isinstance(result, dict): print(json.dumps(result, ensure_ascii=False))
    else:
        for item in result: print(f"{item['heading']} - {item['path']}\n{item['snippet']}\n")
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
