from gateway.evidence_contract import (
    assess_action_evidence,
    evidence_mode,
    verdict_from_evidence,
)


def test_mode_is_fail_safe(monkeypatch):
    monkeypatch.delenv("HERMES_EVIDENCE_CONTRACT_MODE", raising=False)
    assert evidence_mode() == "off"
    assert evidence_mode("unexpected") == "off"


def test_complete_calendar_evidence_is_verified():
    assessment = assess_action_evidence(
        "calendar.event_created",
        {
            "calendar_id": "primary",
            "event_id": "evt-1",
            "summary": "Встреча",
            "start": "2026-07-14T10:00:00+03:00",
            "end": "2026-07-14T11:00:00+03:00",
            "event_link": "calendar://evt-1",
            "read_back": True,
            "status": "created",
        },
    )
    assert assessment.verified
    assert assessment.audit_record()["missing"] == ()


def test_partial_evidence_preserves_usable_result():
    assessment = assess_action_evidence(
        "knowledge.item_saved",
        {"status": "saved", "readback_count": 0},
    )
    assert assessment.status == "partial"
    assert assessment.usable_result is True
    assert verdict_from_evidence(assessment, mode="observe") is None
    assert verdict_from_evidence(assessment, mode="enforce") == "PARTIAL"


def test_missing_evidence_cannot_be_ready_in_enforce_mode():
    assessment = assess_action_evidence("telegram.document_delivered", None)
    assert assessment.status == "missing"
    assert verdict_from_evidence(assessment, internal_status="READY", mode="enforce") == "BLOCKED"


def test_explicit_failure_is_blocked():
    assessment = assess_action_evidence(
        "calendar.event_created",
        {"status": "failed", "event_id": "evt-1"},
    )
    assert assessment.status == "conflict"
    assert verdict_from_evidence(assessment, mode="enforce") == "BLOCKED"


def test_calendar_generic_success_is_not_creation_proof():
    assessment = assess_action_evidence(
        "calendar.event_created",
        {
            "calendar_id": "primary", "event_id": "evt-1", "summary": "Встреча",
            "start": "start", "end": "end", "event_link": "calendar://evt-1",
            "read_back": True, "status": "success",
        },
    )
    assert assessment.status == "partial"
    assert assessment.missing == ("status",)


def test_unknown_action_keeps_legacy_path():
    assessment = assess_action_evidence("creative.story_written", {"text": "ok"})
    assert assessment.status == "unsupported"
    assert verdict_from_evidence(assessment, mode="enforce") is None


def test_audit_record_never_contains_raw_evidence():
    raw = {"status": "saved", "readback_count": 1, "secret_payload": "do-not-store"}
    record = assess_action_evidence("knowledge.item_saved", raw).audit_record()
    assert "secret_payload" not in record
    assert "do-not-store" not in repr(record)
