# Memory task navigation deployment — 2026-07-06

## Decision

Keep the existing category-based vault and canonical `Knowledge/<project>/...` paths. Add task-oriented navigation on top. Do not physically move canonical files until search no longer depends on paths and migration tests are complete.

Do not add qmd or another vector database now. Local FTS5 lookup is already millisecond-scale; the observed waste was document selection and archive scanning, not query execution.

## Implemented

- schema v2 metadata is indexed: `entity_type`, `knowledge_project`, `entity_key`
- travel places use stable identity and update one canonical card
- protected user notes survive automatic updates
- live city-travel skill v0.5.0 supports schema v2 payloads
- `Personal Anton/00-start/INDEX.md` routes tasks to six views
- `knowledge_context` loads INDEX, one view and at most two canonical documents
- normal search hides navigation pages and excludes Archive unless history or rollback is explicit
- validator checks links, required sections, canonical-key conflicts and archive use in default routes
- human index generator applies ACL required by Hermes, backup and promoter users

## Versioned source in this branch

- `hermes_cli/memory_search.py`
- `local-skills/productivity/city-travel-concierge/`
- `tools/memory_navigation/hermes_knowledge_search.py` — canonical deployment source for `/opt/hermes-knowledge-mcp/search.py`
- `tools/memory_navigation/validate_task_navigation.py`
- `tools/memory_navigation/build_human_indexes.py`
- related tests

## Live deployment paths

- `/opt/hermes-knowledge-mcp/search.py`
- `/opt/hermes-knowledge-mcp/test_search_navigation.py`
- `/srv/hermes-memory/bin/validate_task_navigation.py`
- `/srv/hermes-memory/bin/build_human_indexes.py`
- `/srv/hermes-memory/vault/Personal Anton/00-start/INDEX.md`
- `/srv/hermes-memory/vault/Personal Anton/views/*.md`
- `/home/hermes/.hermes/AGENT_OPERATIONS.md`
- `/home/hermes/.hermes/skills/productivity/city-travel-concierge/`

## Acceptance commands

```bash
python3 tools/memory_navigation/test_validate_task_navigation.py
python3 -m pytest -q tests/hermes_cli/test_memory_search_fts.py tests/hermes_cli/test_memory_search_metadata.py
python3 -m unittest discover -s local-skills/productivity/city-travel-concierge/tests
sudo -u hermes /home/hermes/.hermes/bin/hermes-memory-search --reindex --json
sudo -u hermes python3 /srv/hermes-memory/bin/validate_task_navigation.py
curl -fsS http://127.0.0.1:2092/health
```

## Rollback

Primary snapshot:

`/srv/hermes-admin/backups/memory-navigation-20260706T103655Z/`

Travel skill backup created by the sync script:

`/home/hermes/.hermes/backups/city-travel-concierge/pre-sync-20260706T105600Z/`

Restore the snapshot, reindex FTS, restart only `hermes-knowledge-mcp`, then repeat health and search smoke tests. Gateway restart is not part of this deployment.
