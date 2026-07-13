"""Single public messaging contract shared by chat and report delivery."""

from __future__ import annotations

import re


PUBLIC_VERDICTS = frozenset({"READY", "PARTIAL", "BLOCKED"})
_VERDICT_LINE_RE = re.compile(
    r"(?im)^(?P<prefix>\s*(?:статус|status|итог|verdict)?\s*:?\s*[#>*-]*\s*)"
    r"(?P<status>READY|PARTIAL|BLOCKED|INCOMPLETE)\b"
)


def public_verdict(
    internal_status: str | None,
    *,
    has_usable_result: bool = True,
    blocker: bool = False,
) -> str:
    """Map internal execution states onto the three user-visible verdicts."""
    value = str(internal_status or "").strip().upper()
    if blocker or value in {"BLOCKED", "FAILED", "ERROR"}:
        return "BLOCKED"
    if value in {"INCOMPLETE", "PARTIAL", "INTERRUPTED", "PAUSED"}:
        return "PARTIAL" if has_usable_result else "BLOCKED"
    return "READY"


def public_response_text(text: str) -> str:
    """Remove legacy public verdict labels while preserving response content."""
    value = str(text or "")

    def replace(match: re.Match[str]) -> str:
        status = public_verdict(match.group("status"), has_usable_result=True)
        return match.group("prefix") + status

    return _VERDICT_LINE_RE.sub(replace, value)


__all__ = ["PUBLIC_VERDICTS", "public_response_text", "public_verdict"]
