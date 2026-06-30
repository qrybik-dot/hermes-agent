---
name: milestone-memory-writer
description: Create structured milestone notes from verified changes.
version: 1.1.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [memory, milestone, knowledge-base, staging, evidence]
    related_skills: [obsidian, requesting-code-review]
---

# Milestone Memory Writer

## Overview

Turn a completed important change into durable knowledge that Hermes can find later. Record behavior, user impact, proof, and decisions. Keep the canonical note concise.

This is not a task logger. Do not copy raw transcripts, temporary errors, hidden reasoning, or confidential data into the vault.

The skill source belongs in Git. Generated Personal Anton notes belong only in the synchronized vault and encrypted backup.

## When to Use

Use after a completed important change. If the user explicitly signals that the result is accepted or should be remembered, initiate a structured candidate. If a durable result appears without an explicit save signal, classify it, check for duplicates, and offer saving in one short line without writing.

Good candidates:

- a user-facing capability changed;
- a production flow reached READY after live acceptance;
- an architecture, safety, routing, memory, or operating decision became durable;
- a stable commit or equivalent evidence exists;
- the note will help future work.

Do not use for routine fixes, unfinished work, speculative causes, or ordinary status reports.

## Boundaries

- Explicit save or acceptance signals may initiate a candidate without an extra textual question; the consequential action confirmation is sufficient. For implicit durable results, only classify, check duplicates, and offer saving.
- Follow runtime approval rules for knowledge-base changes.
- Stage first; do not create a new milestone directly in the canonical vault.
- Keep Personal Anton notes out of GitHub.
- Store only verified facts and omit unchecked claims.
- Keep Personal Anton, Family Shared, Hermes System, and Codex KB separate.

## Milestone Test

Treat a result as a milestone only when at least three conditions are true:

1. Durable: it should matter after the current chat ends.
2. Material: it changes behavior, architecture, safety, or a recurring workflow.
3. Verified: there is git, test, runtime, delivery, or user acceptance evidence.
4. Retrievable: a future task is likely to benefit from finding it.
5. Explicit: the user asked to save or preserve it, or clearly accepted/closed the result.

If the result fails this test, keep it in the final report or runlog instead of the knowledge base.

## Evidence Order

Use sources in this order:

1. Git status, diff, commit, branch, and remote divergence.
2. Test, lint, compile, validation, and smoke outputs.
3. Systemd or runtime state after deployment.
4. Delivery evidence for messages or artifacts.
5. Actual live acceptance from the user-facing channel.
6. User-confirmed facts.

Model text alone is not proof of an external action.

## Workflow

### 1. Inspect Before Writing

- Read the current project, system, and decision notes.
- Search for an existing milestone on the same topic.
- Check the repository state and exclude unrelated dirty files.
- Identify the exact target note and backlinks before changing anything.

### 2. Build a Staging Proposal

Create a proposal under:

`/srv/hermes-memory/staging/proposals/hermes-milestones/<date>--<slug>/`

The proposal should contain:

- `milestone.md`: the candidate canonical note;
- `manifest.json`: target path, evidence sources, checks, and affected backlink files;
- optional short evidence summaries, never full logs.

Recommended target:

`/srv/hermes-memory/vault/Personal Anton/Projects/<Project> Milestone <date> — <topic>.md`

Use another Personal Anton folder only when the note clearly belongs there. Do not write outside the Personal Anton allowlist.

### 3. Canonical Note Shape

Required frontmatter:

```yaml
---
owner: anton
scope: personal
project: <stable-project-id>
status: completed
date: YYYY-MM-DD
graph: true
tags: [project, milestone, topic]
source_commit: <hash-or-null>
source_branch: <branch-or-null>
live_acceptance: true|false
---
```

Required sections:

1. `# <Project> milestone — <date> — <topic>`
2. `## Итог`: one paragraph describing the new state.
3. `## Что изменилось`: behavior before and after, not only filenames.
4. `## Почему важно`: user value and future operational effect.
5. `## Evidence`: commit, checks, runtime, delivery, and live acceptance.
6. `## Закреплённые решения`: only durable rules accepted by the user.
7. `## Связи`: project, system, and decision backlinks.

Optional: risks, rollback, compatibility, and follow-up when they remain relevant.

### 4. Validate the Proposal

Before promotion, verify:

- YAML header is valid.
- owner, scope, and graph fields match Personal Anton policy.
- target stays inside the Personal Anton vault.
- source is regular Markdown, not a link or binary.
- no conflict markers, raw transcripts, or copied command output.
- no unsupported claims about tests, deployment, delivery, or sync.
- no duplicate milestone on the same topic.
- note is normally below 8 KB.
- backlinks and affected files are listed.

If validation fails, move the proposal to staging review. Do not partially update the vault.

### 5. Promote Safely

Promotion is allowed only when the user requested storage and validation passed.

- Back up every affected vault file before replacement.
- Preserve the existing vault owner and group. Canonical Markdown files must not be executable; use the established file mode and destination ACLs.
- Atomic promotion must reapply the destination directory ACLs because moving a staged file does not inherit them. Verify that both `hermes` and `hermes-backup` can read every promoted note, and that `hermes-promoter` retains write access where required.
- Update no more than three canonical pages: project, system, and decisions.
- Put only a summary and link in index pages.
- Preserve unrelated text and existing frontmatter.
- Record promotion in staging/promoted or a manifest.

### 6. Verify Storage and Sync

- compare staged and vault checksums;
- read the canonical file back as `hermes`;
- confirm ownership, non-executable mode, ACLs, and readability as `hermes-backup`;
- confirm Syncthing is active;
- check folder and device state when available;
- confirm the note can be found by filename or memory index.

Use stored on VPS vault when only the server copy is verified. Use sync pending when a client is offline. Say synced to a device only when Syncthing reports it current.

## Write for Future Retrieval

Describe the capability, not only the patch.
- Previous unreliable behavior
- New reliable behavior
- User-facing impact
- Proof of the new state
- Durable rule for the next similar task
- Source commit or canonical configuration

Use predictable titles, stable project names, a date, focused tags, and backlinks.
Prefer one canonical milestone over several overlapping notes.

## Final User Report

After promotion, report only verified facts:
- milestone title and canonical path;
- short summary of what was saved;
- checks passed;
- VPS vault state;
- Syncthing state: synced or pending;
- source commit for code changes;
- unresolved risk.

Report success only after validation and read-back.

## Common Pitfalls

1. Saving every fix. Only durable, material, verified changes belong here.
2. Writing directly to the vault. Create and validate a proposal first.
3. Copying the final technical report verbatim. Rewrite it as future knowledge.
4. Recording only filenames. Include behavior and user value.
5. Mixing source code and personal memory. Keep them separate.
6. Claiming sync because Syncthing is running. Verify state or say pending.
7. Repeating the full milestone in every index page. Add short backlinks.
8. Saving an unverified hypothesis as a decision.

## Verification Checklist

- [ ] User requested milestone storage
- [ ] Milestone test passed
- [ ] Evidence came from authoritative sources
- [ ] Proposal exists in staging
- [ ] Header and target path passed policy checks
- [ ] No duplicate or unsupported claim remains
- [ ] Affected vault files were backed up
- [ ] Canonical note and backlinks were promoted
- [ ] Read-back and checksum passed
- [ ] Sync status was reported precisely
- [ ] Personal memory was not committed to Git
