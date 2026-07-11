# Minimal Hermes production recipe

## Purpose

This document is the source of truth for rebuilding the smallest useful Hermes
installation without copying the historical VPS layout. It describes control
plane components only. Secrets and raw memory are deliberately excluded.

## Production boundary

- Hermes Agent runs from `/home/hermes/hermes-runtime/current`.
- Hermes Knowledge runs from `/opt/hermes-knowledge-runtime/current`.
- Both runtimes retain exactly one tested rollback release through `previous`.
- Mutable state lives under `/home/hermes/.hermes` and `/srv/hermes-memory`.
- Agent source, runtime releases, personal memory, Knowledge indexes, secrets,
  and report artifacts are separate domains.

## Required services

1. `hermes-gateway.service` - Telegram and task execution.
2. `hermes-dashboard.service` - local dashboard.
3. `hermes-knowledge-mcp.service` - Knowledge API.
4. `hermes-vps-admin-mcp.service` - narrow VPS administration interface.
5. `cli-proxy-api.service` - model provider proxy.

## Required safety services

- `hermes-startup-notify.service` - reports recovery after gateway start.
- `hermes-report-finalizer.timer` - deterministic final HTML delivery.
- `hermes-restic-backup.timer` - encrypted Personal Anton backup.
- `hermes-restic-check.timer` - repository integrity check.
- `hermes-restic-retention-dry-run.timer` - retention preview, never prune.
- `hermes-disk-guard.timer` - disk thresholds and weekly growth warning.
- `hermes-backup-inventory.timer` - local backup retention inventory.

## Rebuild order

1. Provision a clean Linux host and dedicated service users.
2. Install pinned system dependencies from the machine-readable manifest.
3. Create immutable Agent and Knowledge release roots.
4. Restore configuration and secrets from the secret store, never from Git.
5. Restore each memory domain independently into staging.
6. Verify counts, total bytes, relative paths, and SHA-256 before promotion.
7. Install systemd units, but keep Telegram routing on the old host.
8. Run `verify_minimal_runtime.py` and `hermes_user_smoke.py`.
9. Switch Telegram only after every required check passes.

## Recovery rule

Never wipe the current server to test this recipe. Rebuild on an isolated host
or isolated filesystem root, prove the smoke matrix, then perform a reversible
traffic switch. A model may coordinate the process, but credentials, service
permissions, destructive actions, and acceptance gates are enforced outside
the model.

## Data that must not enter Git

- `.env`, OAuth tokens, cookies, provider credentials, and recovery keys;
- raw Personal Anton, Wife, or Family Shared memory;
- Knowledge database/index contents and restore output;
- Telegram chat exports and user content.

## Acceptance

- manifest validator passes;
- current and previous releases resolve to existing immutable directories;
- all required services are active and failed unit count is zero;
- Knowledge health is OK;
- Telegram target is configured without printing its token;
- backup repository check and isolated restore drill pass;
- startup notification, HTML report validation, and user smoke matrix pass.
