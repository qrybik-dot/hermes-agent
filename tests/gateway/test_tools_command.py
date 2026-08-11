from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.run import GatewayRunner
from gateway.session import SessionSource


def _event(text: str) -> MessageEvent:
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="123",
            chat_type="dm",
        ),
        message_id="456",
    )


@pytest.mark.asyncio
async def test_tools_command_lists_configured_gateway_toolsets(monkeypatch):
    import gateway.run as gateway_run

    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {
            "platform_toolsets": {"telegram": ["file", "cronjob"]},
            "mcp_servers": {"knowledge": {"url": "https://unused.invalid"}},
        },
    )

    result = await GatewayRunner._handle_tools_command(
        SimpleNamespace(), _event("/tools list")
    )

    assert "Configured toolsets for telegram" in result
    assert "`cronjob`" in result
    assert "`file`" in result
    assert "`knowledge`" in result
    assert "unused.invalid" not in result


@pytest.mark.asyncio
async def test_tools_command_refuses_gateway_config_mutation():
    result = await GatewayRunner._handle_tools_command(
        SimpleNamespace(), _event("/tools enable web")
    )

    assert "read-only" in result
    assert "hermes tools enable web" in result
