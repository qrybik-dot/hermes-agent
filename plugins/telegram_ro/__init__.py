"""Telegram personal-account read-only plugin.

The plugin never imports Telethon and never reads the Telegram session. It only
speaks a tiny JSON protocol over a Unix socket owned by a separate Linux user.
The service exposes exactly two data operations: message read and media download.
"""

from .tools import (
    TELEGRAM_RO_DOWNLOAD_SCHEMA,
    TELEGRAM_RO_READ_SCHEMA,
    _check_available,
    _handle_download,
    _handle_read,
)


def register(ctx) -> None:
    ctx.register_tool(
        name="telegram_ro_read",
        toolset="telegram_ro",
        schema=TELEGRAM_RO_READ_SCHEMA,
        handler=_handle_read,
        check_fn=_check_available,
        emoji="👁️",
    )
    ctx.register_tool(
        name="telegram_ro_download",
        toolset="telegram_ro",
        schema=TELEGRAM_RO_DOWNLOAD_SCHEMA,
        handler=_handle_download,
        check_fn=_check_available,
        emoji="📥",
    )
