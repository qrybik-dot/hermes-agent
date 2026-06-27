from __future__ import annotations
import json, re, shutil, time
from pathlib import Path

ROOT = Path('/srv/hermes-memory/indexes/graphify/personal-anton-pilot')
SOURCE = ROOT / 'source'
OUT = ROOT / 'output' / 'graphify-out'
VAULT = Path('/srv/hermes-memory/vault/Personal Anton')
ALLOW = [
    'README.md', 'Systems/Memory.md', 'Systems/Hermes.md',
    'Projects/Hermes Agent.md',
    'Projects/Hermes Continuation Milestone 2026-06-27.md',
    'Projects/AI Agents Learning.md', 'Decisions/Decisions.md',
    'Anton/Preferences.md',
]
WIKILINK = re.compile(r'\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]')
COMMIT = re.compile(r'\b[0-9a-f]{7,40}\b', re.I)
SKILL = re.compile(r'\b(?:skill|навык)\s+`?([a-z0-9][a-z0-9_-]*-[a-z0-9_-]+)`?', re.I)
CONCEPTS = {
    'continuation': [r'\bcontinuation\b', r'продолжени\w*\s+задач'],
    'Google Calendar': [r'Google Calendar', r'календарн\w*\s+событ'],
    'evidence contract': [r'evidence[- ]contract', r'подтвержд[её]нн\w*\s+результат'],
    'Hermes VPS Admin': [r'Hermes VPS Admin'],
    'SSH': [r'\bSSH\b'],
    'Syncthing': [r'\bSyncthing\b'],
    'Restic': [r'\bRestic\b'],
    'FTS': [r'\bFTS\b'],
    'Graphify': [r'\bGraphify\b'],
    'milestone-memory-writer': [r'milestone-memory-writer'],
    'task-workflow': [r'task-workflow'],
}

def frontmatter(text: str) -> dict[str, object]:
    if not text.startswith('---\n'):
        return {}
    end = text.find('\n---\n', 4)
    if end < 0:
        return {}
    out = {}
    for line in text[4:end].splitlines():
        if ':' not in line or line.startswith(' '):
            continue
        key, value = line.split(':', 1)
        value = value.strip()
        out[key.strip()] = [x.strip() for x in value[1:-1].split(',') if x.strip()] if value.startswith('[') and value.endswith(']') else value
    return out

def heading(text: str, fallback: str) -> str:
    return next((line[2:].strip() for line in text.splitlines() if line.startswith('# ')), fallback)

def nid(kind: str, value: str) -> str:
    return f'{kind}:{value}'

start = time.time()
SOURCE.mkdir(parents=True, exist_ok=True)
for rel in ALLOW:
    src = VAULT / rel
    dst = SOURCE / 'Personal Anton' / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
OUT.mkdir(parents=True, exist_ok=True)

nodes = {}
edges = []
seen = set()
def add_node(i, label, file_type, source_file, **extra):
    nodes.setdefault(i, {'id': i, 'label': label, 'file_type': file_type, 'source_file': source_file, **extra})
def add_edge(a, b, relation, source_file, confidence='EXTRACTED'):
    key = (a, b, relation)
    if a != b and key not in seen:
        seen.add(key)
        edges.append({'source': a, 'target': b, 'relation': relation, 'confidence': confidence, 'source_file': source_file})

doc_lookup = {}
texts = {}
for rel in ALLOW:
    p = SOURCE / 'Personal Anton' / rel
    text = p.read_text(encoding='utf-8')
    source_file = str(p.relative_to(SOURCE))
    d = nid('document', rel)
    add_node(d, heading(text, Path(rel).stem), 'document', source_file, path=rel)
    doc_lookup[Path(rel).stem.lower()] = d
    doc_lookup[rel.removesuffix('.md').lower()] = d
    texts[rel] = text

for rel, text in texts.items():
    sf = f'Personal Anton/{rel}'
    d = nid('document', rel)
    fm = frontmatter(text)
    project = str(fm.get('project', '')).strip()
    if project:
        p = nid('project', project)
        add_node(p, project, 'concept', sf, entity_type='Project')
        add_edge(d, p, 'belongs_to', sf)
    tags = fm.get('tags', [])
    if isinstance(tags, list):
        for tag in tags:
            t = nid('tag', str(tag))
            add_node(t, str(tag), 'concept', sf, entity_type='Tag')
            add_edge(d, t, 'tagged_with', sf)
    for raw in WIKILINK.findall(text):
        target = raw.strip().replace('\\', '/')
        target_id = doc_lookup.get(target.lower()) or doc_lookup.get(Path(target).name.lower())
        if target_id:
            add_edge(d, target_id, 'references', sf)
    for commit in sorted(set(COMMIT.findall(text))):
        c = nid('commit', commit.lower())
        add_node(c, commit.lower(), 'concept', sf, entity_type='Commit')
        add_edge(d, c, 'implemented_by', sf)
    for skill in sorted(set(SKILL.findall(text))):
        s = nid('skill', skill.lower())
        add_node(s, skill.lower(), 'concept', sf, entity_type='Skill')
        add_edge(d, s, 'mentions_skill', sf)
    for label, patterns in CONCEPTS.items():
        if any(re.search(pattern, text, re.I) for pattern in patterns):
            c = nid('concept', label.lower())
            add_node(c, label, 'concept', sf, entity_type='Concept')
            add_edge(d, c, 'mentions', sf)

D = lambda rel: nid('document', rel)
add_edge(D('Projects/Hermes Continuation Milestone 2026-06-27.md'), D('Projects/Hermes Agent.md'), 'milestone_of', 'Personal Anton/Projects/Hermes Continuation Milestone 2026-06-27.md')
add_edge(D('Projects/Hermes Continuation Milestone 2026-06-27.md'), D('Systems/Hermes.md'), 'changed_system', 'Personal Anton/Projects/Hermes Continuation Milestone 2026-06-27.md')
add_edge(D('Decisions/Decisions.md'), D('Systems/Hermes.md'), 'governs', 'Personal Anton/Decisions/Decisions.md')
add_edge(D('Systems/Memory.md'), D('Systems/Hermes.md'), 'memory_for', 'Personal Anton/Systems/Memory.md')
add_edge(D('Anton/Preferences.md'), D('Systems/Hermes.md'), 'configures', 'Personal Anton/Anton/Preferences.md')
add_edge(D('Projects/AI Agents Learning.md'), D('Projects/Hermes Agent.md'), 'informs', 'Personal Anton/Projects/AI Agents Learning.md')

graph = {
    'directed': False, 'nodes': list(nodes.values()), 'edges': edges,
    'hyperedges': [], 'input_tokens': 0, 'output_tokens': 0,
    'metadata': {'generator': 'hermes deterministic markdown pilot', 'graphify_version': '0.8.50', 'allowlist_count': len(ALLOW), 'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'llm_used': False},
}
(OUT / 'graph.json').write_text(json.dumps(graph, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'status': 'ok', 'files': len(ALLOW), 'nodes': len(nodes), 'edges': len(edges), 'elapsed_ms': int((time.time()-start)*1000), 'graph': str(OUT/'graph.json')}, ensure_ascii=False))
