import asyncio

import pytest

from gateway.platforms import base


def test_gateway_turn_limit_is_bounded(monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_MAX_CONCURRENT_TURNS", "0")
    assert base.gateway_turn_limit() == 1
    monkeypatch.setenv("HERMES_GATEWAY_MAX_CONCURRENT_TURNS", "99")
    assert base.gateway_turn_limit() == 8
    monkeypatch.setenv("HERMES_GATEWAY_MAX_CONCURRENT_TURNS", "broken")
    assert base.gateway_turn_limit() == 2


@pytest.mark.asyncio
async def test_gateway_turn_gate_limits_parallel_work(monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_MAX_CONCURRENT_TURNS", "2")
    base._GATEWAY_TURN_GATES.clear()
    gate = base.gateway_turn_gate()
    active = 0
    peak = 0

    async def worker():
        nonlocal active, peak
        async with gate:
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1

    await asyncio.gather(*(worker() for _ in range(6)))
    assert peak == 2
