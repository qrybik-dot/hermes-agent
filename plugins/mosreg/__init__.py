"""Native Mosreg/Zdrav read-only refresh tool.

The model gets one narrow capability: ``mosreg_refresh``.  Secrets stay on the
Mac, 2FA is bound to a real Telegram bot message, and the tool never exposes an
arbitrary command/path/host surface.
"""
from __future__ import annotations

from plugins.mosreg.tool import MOSREG_REFRESH_SCHEMA, handle_mosreg_refresh, block_mosreg_fallback


def register(ctx) -> None:
    ctx.register_tool(
        name="mosreg_refresh",
        toolset="mosreg",
        schema=MOSREG_REFRESH_SCHEMA,
        handler=handle_mosreg_refresh,
        is_async=True,
        description=(
            "Refresh current Mosreg/Zdrav appointments for the account owner or linked family profiles. "
            "Use this tool for fresh Mosreg data. It owns ЕСИА login and Telegram 2FA; do not use terminal "
            "or execute_code to inspect Mosreg state or TOTP files."
        ),
        emoji="🏥",
    )
    ctx.register_hook("pre_tool_call", block_mosreg_fallback)
