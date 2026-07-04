# Graphify 0.9.5 production pilot

Date: 2026-07-04

## Decision

Graphify 0.9.5 is approved for the deterministic Personal Anton pilot and is active in production.

## Isolation and rollback

- Previous global tool remains available as Graphify 0.8.50.
- Production uses the pinned environment `/home/hermes/.hermes/tools/graphify-0.9.5/venv`.
- Previous skill backup: `/home/hermes/.hermes/backups/graphify-skill-0.8.50-20260704T073445Z.tar.gz`.
- Previous scripts:
  - `/srv/hermes-memory/bin/run_graphify_pilot.sh.bak-0.8.50-20260704`
  - `/srv/hermes-memory/bin/build_graphify_pilot.py.bak-0.8.50-20260704`

## Reproducible comparison

Both versions processed the same graph input.

| Metric | 0.8.50 | 0.9.5 |
|---|---:|---:|
| Nodes | 23 | 23 |
| Links | 40 | 40 |
| Communities | 4 | 4 |
| graph.json SHA256 | `20a3a07f5d0e64ec6a4cb23efdc2029beb0b59386ec9610e79e3f121d357c96c` | identical |
| Wall time | 1.60 s | 4.09 s |
| Peak RSS | 38,500 KB | 42,728 KB |

The report differed only by the temporary test path. Labels were identical. Version 0.9.5 additionally writes a signature file.

## Production verification

Manual service execution succeeded:

- source files: 8
- final graph: 23 nodes, 40 links
- communities: 4
- exit status: 0
- `hermes-graphify-pilot.timer`: active/waiting

The pipeline remains deterministic and does not invoke an LLM.
