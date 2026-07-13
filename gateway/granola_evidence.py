"""Evidence checks for Granola MCP tool results."""
from __future__ import annotations

import json
import re
from typing import Any

_FAILURE_TEXT_RE = re.compile(
    r"^\s*(?:error|failed|failure|exception|traceback|blocked|incomplete|tool error)\b",
    re.I,
)
_FAILURE_STATUS = {"error", "failed", "failure", "blocked", "incomplete"}


def granola_tool_result_successful(tool_name: str | None, result: Any) -> bool:
    """Return true only for a non-empty, non-error Granola tool result."""
    if "granola" not in str(tool_name or "").casefold() or result is None:
        return False
    if isinstance(result, str):
        value = result.strip()
        if not value or _FAILURE_TEXT_RE.match(value):
            return False
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return True
        return granola_tool_result_successful(tool_name, parsed)
    if isinstance(result, dict):
        if result.get("isError") is True or result.get("success") is False:
            return False
        if result.get("error") not in (None, "", False):
            return False
        status = str(result.get("status") or "").casefold()
        if status in _FAILURE_STATUS:
            return False
        if result.get("success") is True or result.get("isError") is False:
            return True
        return any(
            key in result and result.get(key) not in (None, "", [], {})
            for key in ("content", "data", "result", "meeting", "transcript", "meetings")
        )
    if isinstance(result, (list, tuple)):
        return bool(result) and all(
            granola_tool_result_successful(tool_name, item) for item in result
        )
    return bool(result)


__all__ = ["granola_tool_result_successful"]
