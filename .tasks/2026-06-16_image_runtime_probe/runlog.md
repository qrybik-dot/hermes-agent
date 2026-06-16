# Image runtime probe runlog

Дата: 2026-06-16  
Режим: verify-first / safe-write  
Запрещено: purchases, billing setup, subscription changes, paid API calls without confirmation, secrets/OAuth/cookies/API keys/.env reads.

## Цель

Перепроверить реальные каналы image generation и получить фактический raster output, если есть бесплатный/подписочный канал в текущем runtime.

## Выполненные read-only проверки

### Codex / OpenAI subscription branch

- `codex` CLI в текущем PATH: missing.
- `.codex/` существует, но `config.toml`, `AGENTS.md`, `developer_instructions.md`, `model_instructions_file`: отсутствуют.
- Поиск `*image*` и `*openai*` внутри `.codex/`: image-generation skill не найден.
- Вывод: Codex CLI image generation skill недоступен в этом VPS runtime. Не подменялось fal.ai.

### OpenAI API branch

- `openai` CLI: missing.
- В текущем shell OpenAI/Codex API key не обнаружен через безопасную presence-проверку.
- `/v1/models` не вызывался, потому что ключ отсутствует.
- Вывод: OpenAI API branch недоступна в runtime без отдельной настройки/API billing.

### Google / Gemini / AI Studio / Flow / Antigravity branch

- CLI: `gemini`, `gcloud`, `aistudio`, `antigravity`, `flow`, `infsh`: missing.
- В текущем shell `GOOGLE_API_KEY` / `GEMINI_API_KEY`: отсутствуют.
- `/home/hermes/.gemini`: отсутствует.
- CLIProxyAPI config существует на `127.0.0.1:8317`, но `/v1/models` и `/v1/chat/completions` возвращают `401 Unauthorized`; auth secret не читался.
- Antigravity auth-файл существует, но OAuth/token содержимое не читалось.
- Вывод: Google subscription/manual UI channel может быть доступен пользователю вне runtime, но Codex/Hermes runtime не может сейчас вызвать его без auth/API key/CLI.

### Hermes current repo image_generate branch

- В repo `image_generate` определён в `tools/image_generation_tool.py`.
- Agent-facing schema: только `prompt`, `aspect_ratio`.
- Active model in repo config resolution: `fal-ai/flux-2/klein/9b`.
- Managed Nous gateway в repo runtime: not available / direct-preferred.
- Direct FAL credentials в shell не выявлены безопасной проверкой.
- Поэтому repo-local Hermes/FAL path не запускался как potentially paid/unknown.

### Current conversation image_generate tool

- В текущей Hermes-сессии tool `image_generate` доступен как managed tool.
- Вызов выполнен без изменения config/runtime/billing/subscriptions.
- Prompt: `Minimal vector icon of Hermes messenger carrying a small image file, white background, no text`
- Returned URL: `https://v3b.fal.media/files/b/0a9e8659/ZxsOqmAIQpf4HVKkcnixd_Zz05rcUL.png`
- Downloaded to: `.tasks/2026-06-16_image_runtime_probe/outputs/hermes_image_generate_probe.png`
- File size: 284336 bytes.
- Magic bytes: PNG (`89504e470d0a1a0a`).
- Vision check: соответствует минимальной vector-style иконке, белый фон, текста нет.

## Channel comparison

| Channel | Uses subscription? | Uses API billing? | Can Codex call directly? | Can Hermes call directly? | Real output produced? | Output path | Blocker |
|---|---:|---:|---:|---:|---:|---|---|
| ChatGPT UI image generation | Yes, if user has ChatGPT Plus | No separate API billing | No in this VPS runtime | No direct Hermes call | No | - | Manual web/app UI, not accessible from terminal runtime |
| Codex app image generation skill | Unknown | Unknown | No | No | No | - | Codex app not available in runtime; no local skill found |
| Codex CLI image generation skill | No evidence | Unknown | No | No | No | - | `codex` CLI missing; no image skill files found |
| OpenAI API GPT Image | No | Yes | No | Not configured directly | No | - | API key absent in shell; paid API call requires approval |
| Google Gemini app | Yes, if user has Google AI Pro | No separate API billing in app | No | No | No | - | Manual UI channel, no CLI/runtime access found |
| Google AI Studio Nano Banana | Possibly free quota/API-key based | API quota/billing may apply | No | No | No | - | No AI Studio CLI/API key in runtime |
| Gemini API image models | No | Yes/quota based | No | Not configured | No | - | Gemini/Google API key absent in shell |
| Google Flow | Yes/manual subscription UI | No direct API found | No | No | No | - | CLI/API not found; use manually if available in browser |
| Antigravity image capability | Subscription/OAuth likely | Unknown | No | Not directly | No | - | Local proxy requires auth; token not read; no image endpoint verified |
| Hermes image_generate current session tool | Yes, managed Hermes/Nous tool | No separate user API billing observed | N/A | Yes | Yes | `.tasks/2026-06-16_image_runtime_probe/outputs/hermes_image_generate_probe.png` | None for text-to-image |
| Hermes repo image_generate current provider | No evidence of gateway in repo runtime | FAL pricing if direct FAL | No | Yes, but not run | No | - | Would route to FAL direct/unknown paid path; needs approval |
| fal.ai GPT Image | No | Yes | No | Possible via Hermes model config, not current | No | - | Paid FAL branch, not approved |
| fal.ai Nano Banana Pro | No | Yes | No | Possible via Hermes model config, not current | No | - | Paid FAL branch, not approved |
| fal.ai Flux | No | Yes unless Nous gateway selected | No | Possible | No via repo path; yes via session managed tool | see Hermes output | Repo path not run due paid ambiguity |
| SVG/HTML/Mermaid deterministic graphics | No | No | Yes via files | Yes via files | N/A raster | previous `.tasks/2026-06-16_graphics_full_test/outputs/` | Not raster image generation |

## Pricing notes for blocked paid probes

- OpenAI API GPT Image: requires API key/billing. Minimal image generation is a paid API call; exact current price must be checked in OpenAI pricing before run. ChatGPT Plus image generation is separate and does not imply OpenAI API billing access.
- fal.ai GPT Image 2 through Hermes docs: about `$0.04-$0.06/image` medium quality.
- fal.ai GPT Image 1.5 through Hermes docs: about `$0.034/image` medium quality.
- fal.ai Nano Banana Pro through Hermes docs: about `$0.15/image (1K)`.
- fal.ai Flux 2 Klein through Hermes docs: about `$0.006/MP`.

## Manual routes if user wants subscription UI outputs

### ChatGPT UI

1. Open ChatGPT web/app with Plus account.
2. Prompt: `Simple flat vector illustration of a small robot painter creating a clean dashboard UI, white background, no text`.
3. Download PNG/WebP.
4. Put file into `.tasks/2026-06-16_image_runtime_probe/outputs/openai_chatgpt_robot_dashboard.png`.
5. Hermes can then use `vision_analyze` or attach it in reports.

### Google Gemini / Flow UI

1. Open Gemini app / Flow with Google AI Pro account.
2. Prompt: `A clean minimal visual metaphor of four connected AI image generation routes, modern UI style, white background, no text.`
3. Download result.
4. Put file into `.tasks/2026-06-16_image_runtime_probe/outputs/google_gemini_image_routes.png`.
5. Hermes can then inspect/import the file.

## OpenAI-Codex plugin safe generate probe

User later clarified the successful morning path and approved exactly one safe generate probe through `OpenAICodexImageGenProvider`, with no fal.ai direct, no OpenAI API key, no Gemini API key, no config changes, and no token/header logging.

Checked plugin path:

- Requested legacy path: `plugins/image_gen/openai_codex.py` - not present.
- Actual path: `plugins/image_gen/openai-codex/__init__.py` - present.
- Class: `OpenAICodexImageGenProvider` - present.
- `provider.is_available()` under repo venv - `True`.
- OAuth token presence - yes, value not printed.
- Endpoint - `https://chatgpt.com/backend-api/codex/responses`.
- Host model - `gpt-5.5`.
- Tool - `image_generation`.
- Image model - `gpt-image-2`.
- Default tier - `gpt-image-2-medium`.

One approved generation:

- Prompt: `Clean dashboard illustration of an AI agent generating images safely, no text, modern interface, soft lighting`
- Provider: `openai-codex`
- Model: `gpt-image-2-medium`
- Output: `.tasks/2026-06-16_image_runtime_probe/outputs/openai_codex_robot_dashboard.png`
- Size: 1888083 bytes.
- PNG magic: `89504e470d0a1a0a` OK.
- PIL: PNG, 1536x1024, RGB.

Correction to earlier conclusion: Codex CLI and `.codex` skills were the wrong primary criteria. The working route is Hermes plugin + ChatGPT/Codex OAuth + Codex backend Responses image tool.

## Verdict

Works: `openai-codex` plugin produced a real PNG, and current session Hermes `image_generate` produced a separate real PNG. OpenAI API key, Gemini API key, and fal.ai direct were not used.
