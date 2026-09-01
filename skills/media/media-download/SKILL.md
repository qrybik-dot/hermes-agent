---
name: media-download
description: "Send public videos from Instagram, LinkedIn, YouTube, TikTok, X, Reddit, Facebook, Vimeo and other supported URLs directly to the current Telegram chat. Use when the user says 'пришли/скачай/отправь видео', supplies a media URL for delivery, or sends a bare video-source URL in Telegram with no other request."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Media, Video, Telegram, Download]
    related_skills: [youtube-content]
---

# Media Download

Use the native `media_download` tool exactly once for the requested URL.

## Contract

1. Pass the URL unchanged except for ordinary whitespace removal.
2. Set `keep_local_copy=false` by default.
3. Set `keep_local_copy=true` only when the user explicitly says to save, keep,
   retain, or not delete the server copy.
4. The tool owns download, truthful progress, Telegram delivery, timeout,
   message-id confirmation, and cleanup.
5. Never use terminal, `uvx`, `yt-dlp`, `gallery-dl`, Cobalt, Instaloader,
   browser automation, proxies, package installation, or free-form research as
   a fallback.
6. Never expose downloader names, cookies, retries, stack traces, or internal
   errors to the user.

## Result handling

- If the tool returns `next_response=NO_REPLY`, respond with exactly `NO_REPLY`;
  the video is already the final Telegram response.
- If `next_response` contains other text, show exactly that short text even when
  `sent=true`; it reports a post-send save or cleanup failure.
- If it returns `sent=false`, show only its short `message` value.
- If the tool is unavailable, say only: `Скачивание видео сейчас недоступно.`
- Do not add a second progress or completion message.

## Boundaries

- Public media only. Do not bypass DRM or access controls.
- Auth-required, private, rate-limited, unsupported, oversized, and timed-out
  sources end with one bounded failure; do not search for another route.
- A bare YouTube URL means download only when the Telegram user supplied no
  transcript, summary, analysis, or question intent. Explicit analysis intent
  belongs to `youtube-content`.
