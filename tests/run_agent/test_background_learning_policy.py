from agent.background_review import (
    _COMBINED_REVIEW_PROMPT,
    _MEMORY_REVIEW_PROMPT,
    _SKILL_REVIEW_PROMPT,
)


def test_background_skill_review_is_conservative():
    assert "most sessions produce" not in _SKILL_REVIEW_PROMPT
    assert "at most ONE narrow patch" in _SKILL_REVIEW_PROMPT
    assert "explicit /learn" in _SKILL_REVIEW_PROMPT
    assert "Personal names" in _SKILL_REVIEW_PROMPT


def test_combined_review_is_conservative():
    assert "Routine sessions normally require no skill change" in _COMBINED_REVIEW_PROMPT
    assert "at most ONE narrow patch" in _COMBINED_REVIEW_PROMPT
    assert "dedicated travel, task, or project store" in _COMBINED_REVIEW_PROMPT


def test_memory_review_rejects_transient_and_wrong_store_data():
    assert "transient failures" in _MEMORY_REVIEW_PROMPT
    assert "dedicated stores" in _MEMORY_REVIEW_PROMPT
