# Model qualification

Verified on 2026-07-12 on the active Hermes production code and current OAuth accounts.

## Production decisions

| Model | Provider path | Evidence | Verdict | Intended role |
|---|---|---|---|---|
| `gemini-3.5-flash-low` | `custom` via CLIProxyAPI | live catalog; existing production smoke and routing regression suite | **ACTIVE** | default, `simple`, `parser` |
| `gemini-3.1-pro-low` | `custom` via CLIProxyAPI | live catalog; existing production smoke | **ACTIVE** | `research`, `expert_analysis`, isolated quality fallback |
| `gemini-3-flash-agent` | `custom` via CLIProxyAPI | live catalog; existing file-tool smoke | **ACTIVE, MANUAL PILOT** | agentic execution only when explicitly routed |
| `claude-sonnet-4-6` | `custom` via CLIProxyAPI | live catalog; existing response and file-tool smoke | **FALLBACK_ONLY** | public research/expert fallback |
| `claude-opus-4-6-thinking` | `custom` via CLIProxyAPI | live catalog; existing response and file-tool smoke | **FALLBACK_ONLY** | critical review only |
| `gpt-5.6-sol` | direct `openai-codex` OAuth | live catalog; exact text, JSON, tool call and `reasoning_effort=low` passed | **ACTIVE** | `planning`, `server_debug`, high-risk work |
| `gpt-5.6-terra` | direct `openai-codex` OAuth | live catalog; exact text, JSON, tool call, `reasoning_effort=low`, live fallback canary and context preservation passed | **ACTIVE** | daily `coding`; bounded fallback for agentic/extraction roles |
| `gpt-5.6-luna` | direct `openai-codex` OAuth | live catalog; exact text, JSON, tool call and `reasoning_effort=low` passed | **QUALIFIED, NO AUTO ROUTE** | future low-cost/mechanical comparison only |
| `gpt-5.5` | direct `openai-codex` OAuth | current production baseline and live smoke still pass | **ROLLBACK_ONLY** | manual rollback candidate, not active routing |
| `gpt-oss-120b-medium` | `custom` catalog only | catalog present; no production qualification | **QUARANTINE** | no automatic routing |

## Provider distinction

GPT-5.6 models are intentionally absent from CLIProxy `/v1/models`. They are available through the separate `openai-codex` OAuth provider. The live Codex catalog returned:

- `gpt-5.6-sol`
- `gpt-5.6-terra`
- `gpt-5.6-luna`
- `gpt-5.5`
- `gpt-5.4`
- `gpt-5.4-mini`

All four tested Codex routes resolved a 272,000-token context limit in the current Hermes runtime.

## Production routing rule

- Default: `custom/gemini-3.5-flash-low`
- `coding`: `openai-codex/gpt-5.6-terra`
- `planning`, `server_debug`: `openai-codex/gpt-5.6-sol`
- `research`, `expert_analysis`: `custom/gemini-3.1-pro-low`
- Maximum model fallbacks per task: 1
- Quality-role fallback remains isolated, before tool execution, with no allowed tools
- Delegation remains sequential and flat: one child, no nested orchestrator, no automatic approvals
- GPT-5.5 is retained only by Git/config backup for rollback
- Unpinned aliases such as `gpt-5.6`, `latest`, and `auto-latest` are forbidden in production config

## Live observations

Approximate one-shot timings from the qualification run:

| Model | Exact text | Tool call | JSON | Low reasoning |
|---|---:|---:|---:|---:|
| `gpt-5.6-sol` | 2.98 s | 3.10 s | 4.34 s | 1.42 s |
| `gpt-5.6-terra` | 1.31 s | 1.65 s | 1.03 s | 1.08 s |
| `gpt-5.6-luna` | 0.91 s | 1.20 s | 3.45 s | 0.98 s |
| `gpt-5.5` | 1.15 s | 1.38 s | 1.62 s | 1.31 s |

These are operational observations, not performance guarantees.

## Fallback and continuation evidence

A live canary used an intentionally invalid primary model on the local OpenAI-compatible endpoint and `openai-codex/gpt-5.6-terra` as fallback. Hermes:

1. received the primary 404;
2. switched to Terra;
3. completed the first turn;
4. accepted the previous message history on the next turn;
5. returned the stored marker correctly.

The repository regression gate additionally covers fallback-chain advancement, primary restoration on a new turn, 429-after-timeout recovery, child fallback inheritance, bounded delegation depth, and adaptive reviewer routing.

Direct low-level client probes for tool-result continuation and streaming are not used as acceptance evidence because the Codex adapter exposes a higher-level buffered interface. Acceptance is based on the Hermes runtime and gateway tests, not on bypassing that adapter.
