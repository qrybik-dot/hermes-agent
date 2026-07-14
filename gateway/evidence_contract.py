"""Typed, privacy-safe evidence assessments for user-visible action claims.

The model remains free to choose how it solves a task.  This module only
checks structured results already returned by approved tools.  It never opens
domain files, expands permissions, or adds content to the model prompt.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from typing import Any, Mapping


EVIDENCE_MODES = frozenset({"off", "observe", "enforce"})


@dataclass(frozen=True)
class EvidenceAssessment:
    action_type: str
    status: str  # verified | partial | missing | conflict | unsupported
    missing: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    usable_result: bool = False

    @property
    def verified(self) -> bool:
        return self.status == "verified"

    def audit_record(self) -> dict[str, Any]:
        """Return a bounded record containing no raw tool output."""
        return asdict(self)


_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "kanban.task_created": ("task_id", "read_back"),
    "calendar.event_created": (
        "calendar_id", "event_id", "summary", "start", "end",
        "event_link", "read_back", "status",
    ),
    "knowledge.item_saved": ("status", "readback_count"),
    "telegram.document_delivered": ("message_id", "delivered", "generation"),
}

_ALLOWED_STATUS: dict[str, frozenset[str]] = {
    "calendar.event_created": frozenset({"created"}),
    "knowledge.item_saved": frozenset({"saved", "already_exists"}),
}


def evidence_mode(value: str | None = None) -> str:
    candidate = str(
        value if value is not None else os.getenv("HERMES_EVIDENCE_CONTRACT_MODE", "off")
    ).strip().lower()
    return candidate if candidate in EVIDENCE_MODES else "off"


def _present(action_type: str, key: str, value: Any) -> bool:
    if key in {"read_back", "delivered"}:
        return value is True
    if key == "readback_count":
        try:
            return int(value) > 0
        except (TypeError, ValueError):
            return False
    if key == "status":
        return str(value or "").strip().lower() in _ALLOWED_STATUS.get(action_type, frozenset())
    return value is not None and bool(str(value).strip())


def assess_action_evidence(
    action_type: str,
    evidence: Mapping[str, Any] | None,
) -> EvidenceAssessment:
    """Assess allowlisted structured evidence without inspecting raw content."""
    action = str(action_type or "").strip().lower()
    required = _REQUIRED_FIELDS.get(action)
    if required is None:
        return EvidenceAssessment(action, "unsupported")
    if not isinstance(evidence, Mapping):
        return EvidenceAssessment(action, "missing", required, usable_result=False)

    missing = tuple(key for key in required if not _present(action, key, evidence.get(key)))
    present_count = len(required) - len(missing)
    explicit_failure = str(evidence.get("status") or "").strip().lower() in {
        "failed", "error", "blocked", "conflict",
    }
    if explicit_failure:
        return EvidenceAssessment(
            action,
            "conflict",
            missing,
            ("structured result reports failure",),
            usable_result=present_count > 0,
        )
    if not missing:
        return EvidenceAssessment(action, "verified", usable_result=True)
    if present_count:
        return EvidenceAssessment(action, "partial", missing, usable_result=True)
    return EvidenceAssessment(action, "missing", missing, usable_result=False)


def verdict_from_evidence(
    assessment: EvidenceAssessment,
    *,
    internal_status: str | None = None,
    blocker: bool = False,
    mode: str | None = None,
) -> str | None:
    """Return an enforcing public verdict, or None outside enforce mode.

    Unsupported actions deliberately keep the legacy path.  A validator
    outage or missing proof can never upgrade an action to READY.
    """
    if evidence_mode(mode) != "enforce" or assessment.status == "unsupported":
        return None
    if blocker or assessment.status == "conflict":
        return "BLOCKED"
    if assessment.verified:
        value = str(internal_status or "").strip().upper()
        return "BLOCKED" if value in {"BLOCKED", "FAILED", "ERROR"} else "READY"
    return "PARTIAL" if assessment.usable_result else "BLOCKED"


__all__ = [
    "EVIDENCE_MODES",
    "EvidenceAssessment",
    "assess_action_evidence",
    "evidence_mode",
    "verdict_from_evidence",
]
