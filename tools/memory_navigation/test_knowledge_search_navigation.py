from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path('/opt/hermes-knowledge-mcp')
sys.path.insert(0, str(BACKEND))
SPEC = importlib.util.spec_from_file_location(
    'hermes_knowledge_search_candidate',
    ROOT / 'tools' / 'memory_navigation' / 'hermes_knowledge_search.py',
)
SEARCH = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = SEARCH
SPEC.loader.exec_module(SEARCH)


class TaskNavigationSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'fts.sqlite3'
        conn = sqlite3.connect(self.db)
        conn.execute('create table docs(path text primary key,title text,mtime real,sha256 text,entity_type text,source text,knowledge_project text,entity_key text)')
        conn.execute("create virtual table docs_fts using fts5(path unindexed,title,body,entity_type unindexed,source unindexed,tokenize='unicode61')")
        rows = [
            (SEARCH.NAV_INDEX_PATH, 'Start index', '## Start here\n[[Personal Anton/views/model-routing]]', 'navigation_index', ''),
            (SEARCH.VIEW_PATHS['model-routing'], 'Model routing', '## Canonical files\n- [[Personal Anton/Systems/Model Policy]] <!-- canonical:models.policy -->', 'task_view', ''),
            ('Personal Anton/Systems/Model Policy.md', 'Model policy', 'Current model routing and fallback policy.', 'decision', 'hermes'),
            ('Personal Anton/Notes/Random.md', 'Random routing note', 'Old routing discussion without authority.', 'note', ''),
            ('Personal Anton/Archive/Old routing.md', 'Old routing', 'Archived routing history.', 'note', ''),
        ]
        for path, title, body, entity_type, project in rows:
            conn.execute('insert into docs values(?,?,?,?,?,?,?,?)', (path, title, 0, path, entity_type, 'vault', project, ''))
            conn.execute('insert into docs_fts values(?,?,?,?,?)', (path, title, body, entity_type, 'vault'))
        conn.commit()
        conn.close()
        self.old_db = SEARCH.FTS_DB
        SEARCH.FTS_DB = self.db

    def tearDown(self):
        SEARCH.FTS_DB = self.old_db
        self.tmp.cleanup()

    def test_context_loads_index_view_and_canonical_first(self):
        result = SEARCH.knowledge_context('current model routing fallback', limit=4)
        paths = [item['path'] for item in result['chunks']]
        self.assertEqual(result['navigation_view'], 'model-routing')
        self.assertEqual(paths[:3], [
            SEARCH.NAV_INDEX_PATH,
            SEARCH.VIEW_PATHS['model-routing'],
            'Personal Anton/Systems/Model Policy.md',
        ])
        self.assertTrue(result['chunks'][0]['navigation'])
        self.assertTrue(result['chunks'][2]['canonical'])

    def test_reel_route_prefers_web_research(self):
        self.assertEqual(SEARCH._select_view('разобрать рилс и сохранить место'), 'web-research')

    def test_search_hides_navigation_and_archive_by_default(self):
        rows = SEARCH.knowledge_search('routing', limit=10)
        paths = {item['path'] for item in rows}
        self.assertNotIn(SEARCH.NAV_INDEX_PATH, paths)
        self.assertNotIn(SEARCH.VIEW_PATHS['model-routing'], paths)
        self.assertNotIn('Personal Anton/Archive/Old routing.md', paths)
        self.assertIn('Personal Anton/Systems/Model Policy.md', paths)

    def test_archive_is_available_only_when_requested(self):
        rows = SEARCH.knowledge_search('archive routing', limit=10)
        paths = {item['path'] for item in rows}
        self.assertIn('Personal Anton/Archive/Old routing.md', paths)


if __name__ == '__main__':
    unittest.main(verbosity=2)
