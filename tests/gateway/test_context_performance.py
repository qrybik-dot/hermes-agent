from gateway.context_performance import (
    CONTEXT_LATENCY_SLOW_TOKENS,
    CONTEXT_LATENCY_WARN_TOKENS,
    context_latency_zone,
    crossed_context_latency_zone,
)


def test_context_latency_zone_boundaries():
    assert context_latency_zone(0) == "fast"
    assert context_latency_zone(CONTEXT_LATENCY_WARN_TOKENS - 1) == "fast"
    assert context_latency_zone(CONTEXT_LATENCY_WARN_TOKENS) == "large"
    assert context_latency_zone(CONTEXT_LATENCY_SLOW_TOKENS - 1) == "large"
    assert context_latency_zone(CONTEXT_LATENCY_SLOW_TOKENS) == "slow"


def test_crossing_prefers_most_severe_zone_and_does_not_repeat():
    assert crossed_context_latency_zone(99_999, 100_000) == "large"
    assert crossed_context_latency_zone(99_999, 180_000) == "slow"
    assert crossed_context_latency_zone(150_000, 180_000) == "slow"
    assert crossed_context_latency_zone(100_000, 150_000) is None
    assert crossed_context_latency_zone(180_000, 220_000) is None


def test_compression_reentry_can_warn_again_without_persistent_state():
    assert crossed_context_latency_zone(190_000, 70_000) is None
    assert crossed_context_latency_zone(70_000, 110_000) == "large"
