# Hermes Artifact Store

Hermes Artifact Store keeps user-facing files outside transient chat sandboxes and links them to Hermes Knowledge.

## Responsibility split

- `/srv/hermes-artifacts` owns physical bytes, SHA-256, versions, retention, quota and restore.
- Hermes Knowledge owns semantic descriptions, tags, wiki-links and graph navigation.
- `artifact_id` is the stable join key. Knowledge cards are derived indexes, not a second source of file truth.

## Automatic capture

When `HERMES_ARTIFACT_STORE_ENABLED=1`, Hermes archives non-image documents before native delivery through the normal response, post-stream, background task and `send_message` Telegram paths.

Supported extensions include Markdown, text, DOC/DOCX, XLS/XLSX/CSV, PDF, HTML, PPT/PPTX, JSON, YAML and XML. Images, voice, video, cache files and empty files are excluded.

ChatGPT can upload a generated file from any chat through the globally connected Hermes VPS Admin `artifact_upload` action. The connector accepts base64 payloads up to 20 MiB decoded and queues a Knowledge card automatically.

## Storage policy

- Warning: 3 GiB
- Critical: 3.6 GiB
- Emergency: 3.9 GiB
- Hard limit: 4 GiB

Physical usage includes the trash directory. Soft deletion therefore cannot bypass the hard limit.

Default retention:

- temporary: 7 days
- draft: 30 days
- superseded: 90 days
- trash: 14 days
- final: 365 days
- pinned: no automatic expiry

Automated cleanup only removes expired `temporary`, `draft`, `superseded` and `trash` bytes. It never removes the sole local copy of a `final` or `pinned` artifact.

## Version and duplicate rules

- Identical SHA-256: one physical copy.
- Explicit `version_group`: creates a version chain.
- Automatic versioning requires the same normalized group, workspace and extension.
- Similar names with different content are not merged across workspaces.
- Re-capturing bytes that are still in trash restores the prior artifact instead of creating a duplicate.

## Services

- `hermes-artifact-maintenance.timer`: every 15 minutes; reconciliation, safe cleanup and Telegram quota alerts.
- `hermes-artifact-knowledge-sync.timer`: every 5 minutes; publishes pending semantic cards through the existing guarded Hermes Knowledge pipeline.
- Restic snapshots physical files plus a transactionally consistent SQLite registry copy under `/srv/hermes-artifacts/backup`.

## Operational commands

```bash
python ops/artifacts/maintenance.py --health-only
python ops/artifacts/maintenance.py
python ops/artifacts/maintenance.py --apply-cleanup
python ops/artifacts/sync_knowledge.py --dry-run
python ops/artifacts/snapshot_registry.py
```

The agent-facing `artifact_store` tool supports search, get, versions, health, capture, resend to Telegram, pin, soft delete, restore, dry-run cleanup and reconciliation.

## Failure handling

- Writes use staging, fsync, checksum validation and atomic rename.
- Registry changes use SQLite WAL and an exclusive process lock.
- Failed Knowledge publication leaves the file intact with `knowledge_sync_status=failed`; the timer retries it.
- Reconciliation marks missing or unexpectedly restored bytes without deleting semantic cards.
- Delivery continues if archival fails; the failure is logged rather than hiding the user response.

## Rollback

1. Disable capture by removing or overriding the gateway drop-in variable `HERMES_ARTIFACT_STORE_ENABLED`.
2. Disable both timers.
3. Roll back the Hermes immutable runtime release.
4. Restore timestamped backups of the Knowledge promoter, VPS Admin server, Restic script and excludes if required.
5. Keep `/srv/hermes-artifacts` untouched until the rollback is verified. The store is additive and does not modify original generated files.
