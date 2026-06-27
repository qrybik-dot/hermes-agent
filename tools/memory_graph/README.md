# Personal Anton Graphify pilot

Cloud-first read-only knowledge graph for the allowlisted Personal Anton notes.

## Architecture

- Canonical Markdown remains in `/srv/hermes-memory/vault/Personal Anton`.
- The builder copies only the explicit allowlist into an isolated source directory.
- Graphify outputs live under `/srv/hermes-memory/indexes/graphify/personal-anton-pilot/output/graphify-out`.
- A systemd timer rebuilds the graph every 15 minutes.
- A localhost-only HTTP viewer serves `graph.html` on `127.0.0.1:8765`.
- The Mac is only a viewer. The VPS remains the always-on cloud copy and graph builder.

## Mac access

Open an SSH tunnel:

```bash
ssh -N -L 8765:127.0.0.1:8765 ubuntu@141.147.31.16
```

Then open:

```text
http://127.0.0.1:8765/graph.html
```

Nothing is exposed publicly. Closing the SSH session closes Mac access, while the VPS graph continues updating.

## Files

- `build_graphify_pilot.py` — deterministic extraction from frontmatter, Wikilinks, commits, skills and an allowlisted concept vocabulary
- `run_graphify_pilot.sh` — rebuild wrapper
- `systemd/hermes-graphify-pilot.service` — hardened one-shot rebuild
- `systemd/hermes-graphify-pilot.timer` — 15-minute refresh
- `systemd/hermes-graphify-view.service` — localhost-only viewer

## Safety

- No LLM is used by the pilot builder
- Vault files are never modified
- Staging, raw Granola, medical archive and family details are excluded
- Graph outputs are derived and may be deleted/rebuilt without affecting memory
- Graphify is pinned to `graphifyy==0.8.50`

## Rollback

Disable services and remove only the derived index. Do not touch the vault.

```bash
sudo systemctl disable --now hermes-graphify-pilot.timer hermes-graphify-view.service
```
