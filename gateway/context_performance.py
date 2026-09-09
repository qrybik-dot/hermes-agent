"""Small UX helpers for context-size latency signalling.

These thresholds are performance hints, not model context-window safety limits.
They intentionally stay independent from compression: compression protects
correctness near the model limit, while this module only tells users when a
large prompt is likely to feel slower.
"""

from __future__ import annotations

CONTEXT_LATENCY_WARN_TOKENS = 100_000
CONTEXT_LATENCY_SLOW_TOKENS = 180_000


def context_latency_zone(context_tokens: int) -> str:
    """Return fast, large or slow for the current prompt size."""
    try:
        used = max(0, int(context_tokens or 0))
    except (TypeError, ValueError):
        used = 0
    if used >= CONTEXT_LATENCY_SLOW_TOKENS:
        return "slow"
    if used >= CONTEXT_LATENCY_WARN_TOKENS:
        return "large"
    return "fast"


def crossed_context_latency_zone(previous_tokens: int, current_tokens: int) -> str | None:
    """Return a newly crossed warning zone, preferring the most severe one."""
    try:
        previous = max(0, int(previous_tokens or 0))
    except (TypeError, ValueError):
        previous = 0
    try:
        current = max(0, int(current_tokens or 0))
    except (TypeError, ValueError):
        current = 0

    if previous < CONTEXT_LATENCY_SLOW_TOKENS <= current:
        return "slow"
    if previous < CONTEXT_LATENCY_WARN_TOKENS <= current:
        return "large"
    return None
