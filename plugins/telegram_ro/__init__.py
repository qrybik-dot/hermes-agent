"""Telegram personal-account read-only plugin.

The plugin never imports Telethon and never reads the Telegram session. It talks
only to the isolated telegram-ro Unix-socket service. The service exposes a
small allowlist of read/search/history/download operations and no generic
MTProto escape hatch.
"""

from .tools import (
    TELEGRAM_RO_DOWNLOAD_SCHEMA,
    TELEGRAM_RO_HISTORY_SCHEMA,
    TELEGRAM_RO_READ_SCHEMA,
    TELEGRAM_RO_SEARCH_SCHEMA,
    _check_available,
    _handle_download,
    _handle_history,
    _handle_read,
    _handle_search,
)


def register(ctx) -> None:
    for name, schema, handler, emoji in (
        ("telegram_ro_search", TELEGRAM_RO_SEARCH_SCHEMA, _handle_search, "🔎"),
        ("telegram_ro_history", TELEGRAM_RO_HISTORY_SCHEMA, _handle_history, "📚"),
        ("telegram_ro_read", TELEGRAM_RO_READ_SCHEMA, _handle_read, "👁️"),
        ("telegram_ro_download", TELEGRAM_RO_DOWNLOAD_SCHEMA, _handle_download, "📥"),
    ):
        ctx.register_tool(
            name=name,
            toolset="telegram_ro",
            schema=schema,
            handler=handler,
            check_fn=_check_available,
            emoji=emoji,
        )
