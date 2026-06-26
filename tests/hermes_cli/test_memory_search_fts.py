from pathlib import Path

from hermes_cli.memory_search import reindex, search


def test_memory_search_indexes_markdown(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "project.md").write_text("# Job Search\nPython recruiter project notes", encoding="utf-8")
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "ignore.md").write_text("# Hidden\nsecret", encoding="utf-8")
    index = tmp_path / "idx"
    stats = reindex(vault, index)
    assert stats["total"] == 1
    results = search("Python recruiter", limit=3, vault=vault, index_dir=index)
    assert results
    assert results[0]["path"] == "project.md"
    assert "Python" in results[0]["snippet"]
