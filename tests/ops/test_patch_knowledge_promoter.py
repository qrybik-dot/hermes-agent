import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[2] / "ops" / "artifacts" / "patch_knowledge_promoter.py"
spec = importlib.util.spec_from_file_location("patch_knowledge_promoter", MODULE_PATH)
PATCH = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(PATCH)


def sample_promoter() -> str:
    return """
import json

def render(payload, row):
    lines = [
        f'type: {yaml_string(row["classified_type"])}',
        'status: accepted',
    ]
    sections = [
        ('Следующие действия', bullet_lines(as_list(payload.get('next_actions')))),
        ('Источники', bullet_lines(as_list(payload.get('sources')))),
    ]
"""


def test_patch_adds_entity_key_tags_and_relations_idempotently():
    updated, changed = PATCH.patch_text(sample_promoter())
    assert changed is True
    assert "entity_key" in updated
    assert "artifact_id" in updated
    assert "tags:" in updated
    assert "('Связи'" in updated

    second, changed_again = PATCH.patch_text(updated)
    assert changed_again is False
    assert second == updated
