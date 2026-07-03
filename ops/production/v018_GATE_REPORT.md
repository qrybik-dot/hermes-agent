# Hermes v0.18 integration gate report

Status: PARTIAL, not switched to production yet.

## Branch

- Branch: `integration/hermes-v018-routing-lite`
- Base: official `v2026.7.1` / Hermes `0.18.0`
- Latest commit: `640217087 feat: integrate gateway runtime and telegram ux on v0.18`

## Verified on isolated v0.18 environment

Environment:

- Path: `/home/hermes/hermes-v018-integration/.venv`
- Python: CPython 3.11.15
- Package: `hermes-agent==0.18.0` installed from worktree
- Runtime deps installed with `uv sync --frozen --no-dev`
- Test deps installed with `uv sync --frozen --extra dev`

Passed suites on the isolated v0.18 `.venv`:

- Routing, task continuation, family router, task status, fallback isolation, memory search: 165 passed
- Provider fallback, empty fallback, background review, fallback eviction, model override routing: 75 passed
- Telegram task recovery/status/format/reply/clarify/approval/rich/send-draft: 223 passed
- Travel concierge, write approval, Avito tool, browser circuit breaker, Google Workspace, display/runtime/progress callbacks: 233 passed

Total targeted gate executions on v0.18 `.venv`: 696 passed.

## Production state

Production service was not switched.

Current production remains on the tested v0.16 routing hotfix:

- Commit: `ddbe62867 fix: harden adaptive routing and learning policy`
- Tag: `prod/routing-hotfix-20260703T1605Z`

## Infrastructure updated

- CLIProxyAPI: 7.2.49, service active
- Tailscale: 1.98.8, service active
- Chrome: 150.0.7871.46
- uv: 0.11.26
- cloudflared binary: 2026.6.1
- `hermes-mcp-cloudflared.service`: restarted after binary update, active/running with new executable
- apt pending updates: 0 during earlier check

## Known gate gaps before production switch

1. Direct one-shot v0.18 CLI LLM smoke was blocked by the admin tool safety filter, not verified.
2. Full upstream test suite was not run as one monolithic command; production gate is targeted suite only.
3. No production service switch yet.
4. No live Telegram smoke on v0.18 gateway yet.
5. No post-switch rollback drill yet.
6. Graphify 0.9.5 pilot still not executed.
7. Update Radar still needs rewrite; old radar is not counted as complete.

## Next switch plan

1. Create a staging systemd unit or temporary foreground gateway for v0.18 with separate env path and same config read-only.
2. Run live Telegram smoke against staging if safe, or stop production briefly and perform controlled production switch during a maintenance window.
3. Verify: startup, Telegram message, routing role logs, memory read/write, Calendar smoke, family screenshot routing, YouTube/Instagram route, final HTML report delivery, graceful stop.
4. If any failure: revert systemd ExecStart/env to production v0.16 path and restart gateway.
5. If pass: tag production, push branch, send HTML dashboard to Telegram, update Hermes Knowledge with read-back.
