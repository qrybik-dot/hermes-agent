# Image runtime probe summary

## Verdict

Works for two verified paths:

1. `openai-codex` plugin via ChatGPT/Codex OAuth and `/backend-api/codex/responses`.
2. Current session managed `image_generate` tool.

No fal.ai direct, OpenAI API key, Gemini API key, config changes, or service restarts were used.

## Real outputs

| File | Channel | Model/provider | Prompt | Verified |
|---|---|---|---|---|
| `.tasks/2026-06-16_image_runtime_probe/outputs/openai_codex_robot_dashboard.png` | `OpenAICodexImageGenProvider` | `openai-codex`, `gpt-image-2-medium` via `image_generation` tool | Clean dashboard illustration of an AI agent generating images safely, no text, modern interface, soft lighting | PNG magic bytes OK; PIL: PNG, 1536x1024, RGB |
| `.tasks/2026-06-16_image_runtime_probe/outputs/ordinary_image_generate_after_config.png` | ordinary repo-local `image_generate` after config | `openai-codex`, `gpt-image-2-medium` via `image_generation` tool | Minimal futuristic AI image generation control panel, no text, clean UI, soft light | PNG magic bytes OK; PIL: PNG, 1536x1024, RGB; no visible text |
| `.tasks/2026-06-16_image_runtime_probe/outputs/hermes_image_generate_probe.png` | Current Hermes session `image_generate` | Managed image tool, returned `v3b.fal.media` PNG URL | Minimal Hermes messenger image-file icon | PNG magic bytes OK; PIL/vision check OK |

## Channel matrix update

| Channel | Verified generation | Uses subscription/OAuth? | Uses API billing? | Can Hermes use now? | Output path | Notes |
|---|---:|---:|---:|---:|---|---|
| `openai-codex` plugin | Yes | Yes, ChatGPT/Codex OAuth | No OpenAI API key used | Yes, provider available | `.tasks/2026-06-16_image_runtime_probe/outputs/openai_codex_robot_dashboard.png` | Endpoint `/backend-api/codex/responses`, host `gpt-5.5`, tool `image_generation`, image model `gpt-image-2` |
| Managed `image_generate` session tool | Yes | Yes, managed Hermes/Nous tool | No separate user API key used | Yes in current session | `.tasks/2026-06-16_image_runtime_probe/outputs/hermes_image_generate_probe.png` | Verified separately |
| Gemini official free / subscription UI | Pending setup/probe | Possibly Google AI Pro UI | No API billing if manual UI | Not currently from this runtime | - | Needs separate manual UI or authorized runtime path |
| Gemini API / AI Studio API | No | API key/quota | Possible API billing | No | - | Not approved and key not used |
| fal.ai direct | No | No | Yes | Not used | - | Explicitly not allowed |
| OpenAI API key images | No | No | Yes | Not used | - | Explicitly not allowed |

## Key distinction

- `openai-codex` plugin is not Codex CLI and does not require `.codex` skills or `OPENAI_API_KEY`.
- It reads ChatGPT/Codex OAuth through Hermes auth helpers and calls ChatGPT backend Codex Responses with `image_generation`.
- Repo-local provider availability must be checked with the repo venv; system `python3` lacked `httpx`, while repo venv had it.

## Provider selection finding

Current `image_generate` dispatch is config-driven:

- `tools/image_generation_tool.py::_read_configured_image_provider()` reads `image_gen.provider`.
- `_dispatch_to_plugin_provider()` dispatches to a plugin only when `image_gen.provider` is explicitly set.
- Current config read-only audit: `image_gen.provider=None`, `image_gen.model=None`, `image_gen.use_gateway=True`.
- Registered providers include `fal`, `krea`, `openai`, `openai-codex`, `xai`.
- `openai-codex` is registered and `is_available()==True`.

This explains why ordinary voice/image commands could use the managed/legacy `image_generate` path instead of `openai-codex`: no explicit `image_gen.provider` was set.

## Applied safe config

Applied after explicit user confirmation. Backup created before changes.

| Config key | Previous | Applied | Why |
|---|---|---|---|
| `image_gen.provider` | unset / `None` | `openai-codex` | Force `image_generate` dispatcher to use verified ChatGPT/Codex OAuth provider instead of legacy FAL path |
| `image_gen.model` | unset / `None` | `gpt-image-2-medium` | Use verified tier from successful probe |
| `image_gen.use_gateway` | `true` | keep unchanged | Preserve existing managed fallback setting; do not enable paid fal.ai direct |

Applied commands:

```bash
mkdir -p ~/.hermes/backups
cp ~/.hermes/config.yaml ~/.hermes/backups/config.yaml.$(date +%F-%H%M)
hermes config set image_gen.provider openai-codex
hermes config set image_gen.model gpt-image-2-medium
```

Backup path: `/home/hermes/.hermes/backups/config.yaml.2026-06-16-131034`.

No gateway restart was performed. The `image_generate` tool reads `image_gen.provider` from config at call time, so restart should not be required for the setting itself. If a long-lived Telegram/gateway runtime still uses an old path, restart only `hermes-gateway` after separate approval.

## Fallback policy

Config-only fallback is limited: current dispatcher uses `image_gen.provider` first and does not automatically retry another provider on `auth_required`, `403`, `quota`, `network`, or `empty_response`. Until a code-level fallback wrapper is added, safe policy is:

1. Primary: `openai-codex` when `OpenAICodexImageGenProvider.is_available()` is true.
2. If unavailable or generation fails: do not auto-call fal.ai, OpenAI API key, or Gemini API key.
3. Allowed fallback: already verified managed `image_generate` channel only if available in the current runtime and explicitly not paid direct API.
4. Otherwise return blocker and ask for approval or manual Gemini/Flow UI import.

## Response metadata requirement

After image generation, user-facing response should include:

- provider
- model
- output path or attachment
- fallback used yes/no

## Config applied and smoke-test verified

Applied with backup `/home/hermes/.hermes/backups/config.yaml.2026-06-16-133503`:

| Config key | Final value |
|---|---|
| `image_gen.provider` | `openai-codex` |
| `image_gen.model` | `gpt-image-2-medium` |
| `image_gen.use_gateway` | `true` |

Post-config ordinary repo-local `image_generate` smoke-test:

| Field | Value |
|---|---|
| success | `true` |
| provider | `openai-codex` |
| model | `gpt-image-2-medium` |
| output path | `.tasks/2026-06-16_image_runtime_probe/outputs/image_generate_after_config_openai_codex.png` |
| PNG magic | `89504e470d0a1a0a` |
| PIL | `PNG`, `1672x941`, `RGB` |
| fallback used | `false` |
