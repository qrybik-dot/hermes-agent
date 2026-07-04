# Model qualification

Verified on 2026-07-04 on Hermes v0.18 production.

## Production decisions

| Model | Provider path | Evidence | Verdict | Intended role |
|---|---|---|---|---|
| `gemini-3.5-flash-low` | `custom` via CLIProxyAPI 7.2.50 | catalog pass; exact-response smoke after proxy update | **ACTIVE** | default/simple/fast execution |
| `gemini-3.1-pro-low` | `custom` via CLIProxyAPI | catalog pass; basic response pass during v0.18 qualification | **ACTIVE** | `expert_analysis`, planning and stronger reasoning |
| `gemini-3-flash-agent` | `custom` via CLIProxyAPI | catalog pass; real file-tool smoke on v0.18 | **ACTIVE** | fast tool execution |
| `claude-sonnet-4-6` | `custom` via Antigravity/CLIProxyAPI 7.2.50 | catalog pass; exact-response smoke after proxy update; prior real file-tool pass | **FALLBACK_ONLY** | qualified strong fallback for expert/planning work |
| `claude-opus-4-6-thinking` | `custom` via Antigravity/CLIProxyAPI 7.2.50 | catalog pass; exact-response smoke after proxy update; prior real file-tool pass | **FALLBACK_ONLY** | critical review only; not a default due latency/cost of context |
| `gpt-5.5` | direct `openai-codex`, not CLIProxy | exact-response smoke after CLIProxy update; prior file-tool pass | **ACTIVE** | coding, code review and specialist fallback |
| `gpt-oss-120b-medium` | `custom` catalog only | catalog present; no production qualification | **QUARANTINE** | no automatic routing |
| `claude-opus-4-6` | unavailable | absent from catalog | **QUARANTINE** | none |
| `claude-opus-4-7` | unavailable | absent from catalog | **QUARANTINE** | none |
| `claude-opus-4-8` | unavailable | absent from catalog | **QUARANTINE** | none |

## Important provider distinction

`gpt-5.5` is intentionally absent from CLIProxy `/v1/models`: the running CLIProxy instance has one Antigravity OAuth client and no Codex keys. GPT-5.5 is qualified through the separate `openai-codex` provider and works there. This is not a CLIProxy 7.2.50 regression.

## Current routing rule

- Default model: `custom/gemini-3.5-flash-low`
- `expert_analysis`: `custom/gemini-3.1-pro-low`
- Maximum model fallbacks per task: 1
- Sonnet is a qualified strong fallback, not a global default
- Opus Thinking is reserved for critical review
- Free public-model fallback is disabled
- Coding/server fallback must remain read-only before any tool call

## Observed smoke latency

Approximate cold one-shot timings during final qualification:

- Gemini Flash: 28 seconds
- Sonnet 4.6: 36 seconds
- Opus 4.6 Thinking: 35 seconds
- GPT-5.5 direct Codex: 50 seconds

These figures are operational observations, not performance guarantees.
