# Hermes production recovery overlay — 2026-09-27

## Status

This branch is the **off-VPS recovery overlay and audit snapshot** for the working Hermes installation.

It contains the external production configuration, protected eval/control-plane files, skill snapshots, sanitized profile settings and final verification report.

**Important:** the full active source tree is not yet published to the canonical remote production-history branch. The active release commit/tree currently exists only on the VPS Git/runtime object store because the VPS pre-push policy requires a one-shot ExternalPublicationApproval that is not exposed through the current Admin connector. The protection was not bypassed.

## Three-copy model

1. **Production runtime truth**
   - `/home/hermes/hermes-runtime/current`
   - active release: `d251f4446f72cff549e98cc258a1759a992ce81f`
   - active tree: `7e425485b5a80df660526ebb1af6b71d4fc99325`
   - rollback target: `77710faa2443a628ab62c20ac012aaddafcac7d2`

2. **Update / staging copy**
   - `/home/hermes/.hermes/hermes-agent`
   - used to fetch/reconcile/test future upstream changes
   - never production truth

3. **Git backup / history**
   - private repo: `qrybik-dot/hermes-agent`
   - canonical production-history branch: `prod/hermes-autonomous-core`
   - current recovery-overlay branch: `backup/hermes-production-overlay-20260927`
   - local full-source backup branch prepared on VPS: `gpt/prod-backup-sync-20260927`

## External overlay included

- `control-plane/improvement-auto-dispatch.sh`
- `control-plane/review-call-card.sh`
- `control-plane/hermes-improvement-watch.service`
- `control-plane/hermes-improvement-watch.path`
- protected eval policy, ownership, cases and runners under `eval/`
- production skill snapshots under `skills/`
- sanitized effective config under `effective-config.json`
- restricted worker/reviewer config under `improvement-profiles.json`
- final self-improvement report under `self-improvement-final-report.html`

## Recovery rules

- Secrets, OAuth credentials, Telegram tokens and API keys are **not** stored in this snapshot.
- Do not treat this overlay branch as a complete source-tree replacement for the active immutable release.
- Until full-source publication is completed, keep all local release directories whose commit/tree is not remotely reachable.
- Restore profile configuration using the sanitized values here, then inject credentials from the normal secret stores.
- Restore the self-improvement overlay outside the immutable release; do not patch release source in place.
- Production activation/rollback remains controlled through the managed release manager.
- After full-source publication and remote read-back of every deployed version, local release retention may be reduced to `current + previous` (or the managed retention policy).
