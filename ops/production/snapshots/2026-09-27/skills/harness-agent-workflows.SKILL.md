---
name: harness-agent-workflows
description: Use when building autonomous agent loops & harness.
---

# Harness Agent Workflows

Use this skill to orchestrate autonomous agentic execution, self-correction loops, and verification gates.

## Core Directives

1. **Zero-Trust Deliverable (Verification Gate)**:
   - Never consider a task complete until real execution (`terminal`, tests, linter, exit code 0) confirms success.
   - Run tests (`pytest`, `npm test`, etc.) or execution checks BEFORE returning the final result to the user.

2. **Self-Correction Retry Loop**:
   - On error or failed test, parse error output into context.
   - Patch the solution autonomously and re-test before asking for intervention.

3. **Execution Lifecycle**:
   - Prefer the native Hermes Kanban lifecycle for durable autonomous work, retries, isolation, and review handoff.
   - Prefer event-driven execution. Do not introduce a new daemon or polling cron when native Kanban/gateway dispatch already covers the lifecycle.
   - A persistent watcher/service is justified only when the user goal itself requires continuous external monitoring and no native scheduler/event source fits.

4. **Independent Verification**:
   - For critical or ambiguous changes, use an independent reviewer/model over the diff and verification evidence.
   - Do not spend a second model-family review on routine deterministic PASS cases.
   - For skill self-improvement, defer quality policy and promotion criteria to `skill-quality-engineering` and the external Improvement Eval Lab; do not create a second eval authority here.

5. **Project Guidelines**:
   - Check local workspace documentation for project conventions, build commands, and architectural constraints.
   - Before adding a harness/process, check for an existing Hermes skill, Kanban feature, review gate, or eval path that already owns the responsibility. Extend rather than duplicate.
