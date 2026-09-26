# Hermes production backup snapshot — 2026-09-27

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
   - remote: `origin` → private `qrybik-dot/hermes-agent`
   - canonical branch: `prod/hermes-autonomous-core`
   - this snapshot synchronizes the branch to the active production source tree and stores the external production overlay under this directory.

## External overlay included

- `control-plane/improvement-auto-dispatch.sh`
- `control-plane/review-call-card.sh`
- `control-plane/hermes-improvement-watch.service`
- `control-plane/hermes-improvement-watch.path`
- protected eval policy, ownership, cases and runners under `eval/`
- production skill snapshots under `skills/`
- sanitized effective config under `effective-config.json`
- restricted worker/reviewer config under `improvement-profiles.json`

## Recovery rules

- Secrets, OAuth credentials, Telegram tokens and API keys are **not** stored in this snapshot.
- Restore source from the canonical Git branch or a `backup/hermes-release-*` tag.
- Restore profile configuration using the sanitized values here, then inject credentials from the normal secret stores.
- Restore the self-improvement overlay outside the immutable release; do not patch release source in place.
- Production activation/rollback remains controlled through the managed release manager.
- `current` + `previous` are the only local runtime copies that must be retained once every historical deployed commit has a remote Git ref.
