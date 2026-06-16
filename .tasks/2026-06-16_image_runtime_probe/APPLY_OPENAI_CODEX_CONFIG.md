# Apply openai-codex image config

Date: 2026-06-16
Mode: safe-write

## Applied config

Backup created:

`/home/hermes/.hermes/backups/config.yaml.2026-06-16-133503`

Only these keys were changed:

```bash
hermes config set image_gen.provider openai-codex
hermes config set image_gen.model gpt-image-2-medium
```

`image_gen.use_gateway` was left unchanged.

## Final config values

| Key | Value |
|---|---|
| `image_gen.provider` | `openai-codex` |
| `image_gen.model` | `gpt-image-2-medium` |
| `image_gen.use_gateway` | `true` |

## Availability

`openai_codex_available=yes`

## Smoke-test

One ordinary repo-local `image_generate` handler call was executed after config change with API billing env vars unset:

```bash
env -u OPENAI_API_KEY -u CODEX_API_KEY -u GOOGLE_API_KEY -u GEMINI_API_KEY -u FAL_KEY -u FAL_API_KEY \
  /home/hermes/.hermes/hermes-agent/venv/bin/python -c '... _handle_image_generate(...) ...'
```

Prompt:

`Minimal futuristic AI image generation control panel, no text, clean UI, soft light`

Result:

| Field | Value |
|---|---|
| success | `true` |
| provider | `openai-codex` |
| model | `gpt-image-2-medium` |
| route/tool | `tools.image_generation_tool._handle_image_generate -> image_generate -> plugin provider` |
| output path | `.tasks/2026-06-16_image_runtime_probe/outputs/image_generate_after_config_openai_codex.png` |
| source cache path | `/home/hermes/.hermes/cache/images/openai_codex_gpt-image-2-medium_20260616_133649_493fa01c.png` |
| file size | `1322531` bytes |
| PNG magic | `89504e470d0a1a0a` |
| PIL | `PNG`, `1672x941`, `RGB` |
| fallback used | `false` |

No fal.ai direct, OpenAI API key, Gemini API key, paid API billing path, or service restart was used.

## Gateway restart note

No service was restarted. Existing long-lived Telegram gateway processes may need `hermes-gateway` restart to reload config if they keep config/provider state in memory. Do not restart without separate confirmation.
