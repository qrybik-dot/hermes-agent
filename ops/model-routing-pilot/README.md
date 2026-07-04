# Safe model routing rollout

Date: 2026-07-04
Branch: `feature/model-routing-pilot-safe-20260704`
Baseline tag: `model-routing-pilot-baseline-20260704-9a8e97e`

## Goal

Improve response speed, answer quality and provider resilience without breaking existing Telegram, travel, calendar, memory, coding or VPS flows.

## Changes implemented behind flags

- New `agentic` role for narrow multi-step read/search/transform workflows.
- New `long_context_extract` role for fast fact/date/version extraction from large inputs.
- Existing roles remain unchanged when both experiment flags are false.
- Specialized travel, calendar, research, coding, planning and server-debug intents take precedence over `agentic`.
- Agentic role has no terminal tool by default and receives an explicit no-destructive-actions contract.
- Long-context extraction avoids reviewer escalation and is capped at 12 iterations.
- New roles use `gemini-3-flash-agent` as primary.
- New roles have one independent `gpt-5.5` Codex fallback for provider/auth/quota failures, only before any tool call, with isolated context and no tools.

## Rollout stages

1. Code and tests with flags off.
2. Deploy roles with `pilot_mode: manual_only` and restart once.
3. Smoke-test explicit `/role agentic` and `/role long_context_extract` requests.
4. Enable `long_context_split` first for narrow extraction requests.
5. Enable `agentic_enabled` for safe multi-step requests.
6. Observe 20–30 real tasks and compare completion, latency, tool errors and user corrections.
7. Keep or revert each flag independently.

## Stop conditions

- Any travel/calendar/coding/server request is stolen by `agentic`.
- Any prohibited or duplicated action is attempted.
- Completion falls below 90%.
- Error or correction rate exceeds 10%.
- Antigravity 429/timeouts materially increase.
- Telegram, memory, calendar or existing route regression appears.

## Rollback

- Disable both `routing_experiments` flags without reverting code.
- Restore `/home/hermes/.hermes/config.yaml.pre-model-routing-pilot-20260704` if configuration rollback is needed.
- Return Git to tag `model-routing-pilot-baseline-20260704-9a8e97e` if code rollback is needed.
- Perform at most one controlled gateway restart.
