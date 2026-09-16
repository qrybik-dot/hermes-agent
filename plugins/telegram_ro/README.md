# Telegram read-only integration

Hermes never receives the personal Telegram MTProto session or API credentials. A separate `telegram-ro` Linux user owns them and exposes only a narrow Unix-socket allowlist:

- `search` — bounded search across accessible dialogs, Saved Messages or one chat;
- `history` — bounded history page for one chat;
- `message` — full metadata/text/entities/reaction counters for one message;
- `download` — media for one explicitly selected message;
- `health` — local service state.

Hermes tools are `telegram_ro_search`, `telegram_ro_history`, `telegram_ro_read`, and `telegram_ro_download`. Broad scans are paginated with cursors and hard request budgets; media is never bulk-downloaded by search/history. Download cache is bounded and transient.

## Zero-side-effect contract

The gateway has no generic/raw MTProto dispatcher. It must not send/reply/forward/edit/delete, change reactions, join/leave/manage chats or contacts, acknowledge message/media/reaction reads, increment channel/story views, change online/typing state, make payments, or mutate account/chat settings. Telegram updates are disabled for this client and only one session owner exists.

Secret chats and content Telegram no longer exposes to the account are outside the cloud-history scope. Protected/paid/expiring content is never unlocked or purchased.
