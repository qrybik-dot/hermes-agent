# Hermes VPS provider artifacts cleanup

Date: 2026-08-20
Status: COMPLETE

## Scope

Remove only superseded CLIProxyAPI qualification bundles, inactive auth copies,
obsolete config backups, two redundant proxy binaries, the obsolete `agy`
binary, and the Antigravity installer staging cache. Production runtime,
profiles, active proxy config/auth, current `agy`, and one known proxy rollback
binary are protected.

## Protected artifacts

- `/home/hermes/.local/bin/cli-proxy-api`
  - SHA-256: `8f4c9e1e3ddcabede29236569cb835fb160e167255ca340e9df5b6a3d910bc5a`
- `/home/hermes/.cli-proxy-api/config.yaml`
  - SHA-256: `2aba82e73da223cc12b79b4bdd99398017f2216c21db2937dc1db79de99faa26`
- active auth directory selected by that config;
- `/home/hermes/.local/bin/cli-proxy-api.7.2.61.bak-20260714T191453Z`
  - SHA-256: `89c9e47898a146374dbdee93c39b75186abc3c0adac0863ed9f99799eb160d84`
- `/home/hermes/.local/bin/agy`
  - SHA-256: `b233e6a4f38564a06a0d3220aa79f6a7c8f11da2b85fc8f0957f8a14d46e6cc9`

## Removal set

- `/home/hermes/.cli-proxy-api/qualification-20260714`
- `/home/hermes/.cli-proxy-api/upgrade-20260730-v7111`
- `/home/hermes/.cli-proxy-api/upgrade-20260731-v72112`
- inactive `/home/hermes/.cli-proxy-api/auth`
- `/home/hermes/.backups/cli-proxy-api`
- `/home/hermes/.hermes-private-backups/pre-switch-20260714T210830Z`
- `/home/hermes/.local/bin/cli-proxy-api.bak_7.2.50_2026-07-07-111023`
- `/home/hermes/.local/bin/cli-proxy-api.bak`
- `/home/hermes/.local/bin/agy.1787248630641946824.old`
- `/home/hermes/.cache/antigravity/staging`
- six root-level `config.yaml.bak*` files under `.cli-proxy-api`.

Exact regular-file payload before removal: `1,071,791,375` bytes.

## Preconditions

- active CLIProxy executable resolves to the protected current binary;
- none of the removal paths is open by a running process;
- active config selects the separate protected Antigravity auth directory;
- gateway, CLIProxyAPI, and Knowledge are active; failed units are zero;
- `current`, `previous`, core profile, and routing config are unchanged.

## Result

- All listed roots and six config backups are absent after deletion.
- Exact regular-file payload removed: `1,071,791,375` bytes (about 1.00 GiB).
- Root filesystem changed from `58% used / 19 GiB free` to
  `56% used / 20 GiB free`.
- Protected current CLIProxy binary, config, active auth directory, rollback
  binary, and current `agy` still exist with the exact pre-cleanup hashes.
- Active auth directory still contains exactly one credential file.
- CLIProxy PID and InvocationID did not change; `NRestarts=0`.
- Gateway, CLIProxyAPI, and Knowledge remained active; failed units remained
  zero. No service was restarted.

The deleted inactive auth/config copies are not recoverable from this VPS. This
is intentional: they were superseded, unreferenced, and some contained legacy
credentials that must not be restored. Current production and the single
retained binary rollback remain recoverable.
