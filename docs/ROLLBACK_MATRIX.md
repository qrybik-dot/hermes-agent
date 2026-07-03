# Rollback matrix

Baseline: `ops/production/PRODUCTION_SNAPSHOT.json`.

| Layer | Recovery point | Verification |
|---|---|---|
| Hermes | Tag `rollback/pre-routing-modernization-20260703T153808Z` | Gateway health, Telegram smoke, runtime HEAD |
| CLIProxyAPI | Saved binary `cli-proxy-api-7.2.42` | Model catalog and model smoke tests |
| System packages | Previous package versions where available | Network, browser and services |
| Routing | Previous routing commit and config checksum | Routing replay |
| v0.18 integration | Existing production tag and environment | Regression suite and continuation test |

Change and recovery of different layers are handled separately.
