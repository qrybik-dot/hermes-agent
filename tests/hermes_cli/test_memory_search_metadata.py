import sqlite3
from pathlib import Path

from hermes_cli.memory_search import reindex, search


def test_reindex_persists_schema_v2_metadata(tmp_path: Path):
    vault = tmp_path / 'vault'
    vault.mkdir()
    (vault / 'place.md').write_text(
        '''---
type: "research"
entity_type: "travel_place"
knowledge_project: "travel"
entity_key: "yandex:123456"
---

# Example Park

Family place near water.
''',
        encoding='utf-8',
    )
    index = tmp_path / 'idx'
    stats = reindex(vault, index)
    assert stats['total'] == 1

    conn = sqlite3.connect(index / 'memory_fts.sqlite3')
    row = conn.execute(
        'select entity_type,knowledge_project,entity_key from docs where path=?',
        ('place.md',),
    ).fetchone()
    conn.close()
    assert row == ('travel_place', 'travel', 'yandex:123456')

    results = search('Example Park', limit=3, vault=vault, index_dir=index)
    assert results[0]['entity_type'] == 'travel_place'
