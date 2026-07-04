# Hermes v0.18 release tails closeout

Date: 2026-07-04

## Status

**PARTIAL only because the first natural Update Radar timer trigger is scheduled for 2026-07-05.** All checks available on 2026-07-04 are closed.

## Closed tails

- Git HEAD, origin branch and final release tag are aligned at `7056c2e9826703d90f1ebbbf392dff058a986b06`.
- `PRODUCTION_SNAPSHOT.json` records both `capture_head` and `runtime_head` as the final release commit.
- The live gateway command line points to the Hermes v0.18 integration runtime.
- The live cloudflared process PID `1503764` and `/usr/bin/cloudflared` share inode `2049:2432`; active version is `2026.6.1`.
- Graphify natural timer run passed at `2026-07-04T11:00:21Z`, exit status 0. Three consecutive scheduled runs were observed as successful.
- NotebookLM watchdog natural run passed at `2026-07-04T11:00:09.762351Z`; next run was scheduled for `11:15Z`; delivery target is `telegram:256818214`.
- Update Radar production service run passed at `2026-07-04T09:00:08Z` and stayed silent.

## Remaining observation

The first natural `hermes-update-radar.timer` trigger is scheduled for `2026-07-05T07:17:04Z`. A one-time verification is scheduled for `2026-07-05 09:35 Europe/Stockholm`.

The verification may inspect service/timer evidence and update this closeout, but it must not install or update any component.

## Canonical references

- Final release tag: `prod/hermes-v018-ready-20260704T1001Z`
- Release commit: `7056c2e9826703d90f1ebbbf392dff058a986b06`
- Snapshot: `ops/production/PRODUCTION_SNAPSHOT.json`
- Version lock: `ops/production/components.lock.yaml`
- READY dashboard: `ops/production/hermes_v018_dashboard.html`
- Knowledge closeout: `Hermes v0.18 production READY — closeout, commits and artifact index`
