from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "agent" / "conversation_loop.py"


def test_classified_is_not_used_before_api_error_classification():
    source = SOURCE.read_text(encoding="utf-8")
    assignment = source.index("classified = classify_api_error(")
    pre_classification = source[:assignment]

    assert "reason=classified.reason" not in pre_classification
    assert "reason=FailoverReason.rate_limit" in pre_classification
    assert "reason=FailoverReason.content_policy_blocked" in pre_classification
    assert "reason=FailoverReason.unknown" in pre_classification


def test_classified_is_still_used_after_assignment():
    source = SOURCE.read_text(encoding="utf-8")
    assignment = source.index("classified = classify_api_error(")
    assert "reason=classified.reason" in source[assignment:]
