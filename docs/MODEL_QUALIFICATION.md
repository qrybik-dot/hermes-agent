# Model qualification

Verified on 2026-07-03 through current Antigravity and CLIProxyAPI:

- claude-sonnet-4-6: catalog yes, basic response pass, file tool pass, candidate
- claude-opus-4-6-thinking: catalog yes, basic response pass, file tool pass, critical-review candidate
- claude-opus-4-6: not in catalog, quarantine
- claude-opus-4-7: not in catalog, quarantine
- claude-opus-4-8: not in catalog, quarantine

Cold one-shot latency was about 44 to 59 seconds. Production activation requires the full reproducible suite. Final verdicts are ACTIVE, FALLBACK_ONLY or QUARANTINE.
