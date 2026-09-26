# Controlled Improvement Policy

- Hermes owns candidate skill changes and self-correction.
- External eval gate owns PASS/FAIL and production promotion evidence.
- Runner, golden cases, acceptance policy and rollback data are protected from Hermes writes.
- No daemon or cron for self-improvement; real failures/corrections/regressions create Kanban tasks.
- Outcome, Process, Trust/Safety are the only top-level eval classes.
- Baseline and candidate use the same cases/model/config; off-target regression is blocking.
- Deterministic/mechanical evidence is primary; LLM judgment is secondary.
- Tie or noise keeps the incumbent.
- Core/runtime, secrets, deployment/rollback infrastructure and Knowledge originals/provenance are outside the autonomous mutation boundary.
