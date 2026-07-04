# Hermes model lineup watch policy

Date: 2026-07-04
Status: active

## Goal

Detect changes in the model lineups included in the user's ChatGPT/Codex and Antigravity subscriptions before production routing starts returning model-not-found errors or continues using a weaker obsolete model.

## Sources monitored

### Antigravity

Live CLIProxyAPI `/models` catalog. Only Gemini and Claude entries are tracked for routing decisions.

Current baseline:

- `claude-opus-4-6-thinking`
- `claude-sonnet-4-6`
- `gemini-3-flash`
- `gemini-3-flash-agent`
- `gemini-3.1-flash-image`
- `gemini-3.1-flash-lite`
- `gemini-3.1-pro-low`
- `gemini-3.5-flash-extra-low`
- `gemini-3.5-flash-low`
- `gemini-pro-agent`

### ChatGPT / Codex

Live model catalog resolved by the Hermes `openai-codex` provider for the active ChatGPT subscription.

Current baseline:

- `gpt-5.4`
- `gpt-5.4-mini`
- `gpt-5.5`

## Update Radar behavior

The daily Update Radar stores separate fingerprints for:

- `antigravity_model_catalog`
- `codex_model_catalog`

No message is sent when the catalogs are unchanged.

When a new model appears, Hermes sends:

- the added model name;
- models that disappeared;
- whether an active primary or fallback route was affected;
- suggested task roles for qualification;
- the mandatory qualification gate before any production change.

When an active model disappears, the alert is urgent. Existing configured fallback remains the runtime safety mechanism until routing is reviewed.

## Qualification gate for a new model

A new model is not placed into production only because it appeared in a catalog. Before changing routing, perform:

1. Exact-response smoke test.
2. Safe file-tool call.
3. Latency and timeout observation.
4. Fallback behavior test.
5. Comparison against the current model for the proposed role.
6. Explicit approval of the routing change.

Suggested role mapping is conservative:

- Flash, Mini, Lite: `simple`, `parser`, fast tools.
- Pro, Sonnet, newer GPT: `planning`, `expert_analysis`, `long_context`.
- Codex and GPT coding models: `coding`, `server_debug`.
- Opus, Thinking, Reasoning: critical review and difficult expert analysis.
- Image and Vision: image-generation qualification only.

## Automatic reactions

Allowed automatically:

- detect catalog changes;
- calculate added and removed models;
- identify affected configured routes;
- create a recommendation;
- send one deduplicated Telegram notification;
- continue using the existing configured fallback after a runtime failure.

Not allowed automatically:

- change the production primary model;
- add an untested model to fallback;
- move sensitive tasks to another provider;
- enable paid API usage;
- restart the gateway solely because a new model appeared.

This policy favors fast notification and safe proposals over silent automatic routing changes.

## Current routing summary

- `simple`, `parser`: Gemini 3.5 Flash Low; fallback Gemini 3.1 Pro Low.
- `planning`: GPT-5.5 via Codex; fallback Gemini 3.1 Pro Low.
- `coding`, `server_debug`: GPT-5.5 via Codex; fallback Gemini 3.1 Pro Low only before tools, with isolated context and no tools.
- `long_context`: Gemini 3.1 Pro Low; public fallback Sonnet 4.6, sensitive fallback Gemini 3.5 Flash Low.
- `research`: Gemini 3.1 Pro Low; public fallback Sonnet 4.6, sensitive fallback Gemini 3.5 Flash Low.
- `expert_analysis`: Gemini 3.1 Pro Low; fallback Sonnet 4.6.
- Opus 4.6 Thinking: qualified but reserved for explicit critical review, not automatic fallback.
- Image generation: GPT Image 2 Medium through `openai-codex`.
- Maximum model fallbacks per task: one.

## Evidence

- Update Radar unit tests: 12 passed.
- Live Antigravity catalog probe: 10 models, status OK.
- Live Codex catalog probe: 3 models, status OK.
- New catalog keys were baselined without notification.
- Immediate repeated production run produced no output.
