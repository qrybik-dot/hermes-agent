# Telegram read-only integration

Security contract: the Hermes process never receives the Telegram MTProto session or API credentials. A separate `telegram-ro` Linux user owns those files. Hermes can access only a Unix socket whose protocol has three exact actions: `health`, `message`, and `download`. There is no generic/raw method dispatcher and no Telegram mutation endpoint.

Forbidden by design: send/reply/forward/edit/delete/reactions, membership changes, chat/account settings changes, and arbitrary MTProto requests.
