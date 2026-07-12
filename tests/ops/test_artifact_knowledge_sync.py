import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[2] / "ops" / "artifacts" / "sync_knowledge.py"
spec = importlib.util.spec_from_file_location("artifact_knowledge_sync", MODULE_PATH)
SYNC = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(SYNC)


def test_build_payload_preserves_artifact_identity_and_graph_hints():
    item = {
        "artifact_id": "art_123",
        "original_name": "HR-screening.docx",
        "stored_path": "/srv/hermes-artifacts/files/2026/07/12/art_123--HR-screening.docx",
        "extension": ".docx",
        "size_bytes": 1234,
        "sha256": "a" * 64,
        "status": "final",
        "version": 2,
        "version_group": "hr-screening",
        "retention_days": 365,
        "pinned": False,
        "source_system": "hermes-telegram",
        "source_workspace": "Recruitment",
        "knowledge_project": "recruitment",
        "title": "HR-скрининг Владислава Майсеева",
        "summary": "Java screening",
        "supersedes": "art_122",
        "metadata": {
            "people": ["Владислав Майсеев"],
            "companies": ["Т-Банк"],
            "meetings": ["Собеседование 2026-07-12"],
            "tags": ["java", "candidate-screening"],
        },
    }

    payload = SYNC.build_payload(item)

    assert payload["knowledge_project"] == "recruitment"
    assert payload["entity_key"] == "artifact:art_123"
    assert "[[Владислав Майсеев]]" in payload["relations"]
    assert "[[Т-Банк]]" in payload["relations"]
    assert "java" in payload["tags"]
    assert any("art_122" in fact for fact in payload["accepted_facts"])
