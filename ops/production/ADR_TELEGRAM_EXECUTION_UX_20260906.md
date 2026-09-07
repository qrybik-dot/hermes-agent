# ADR: Telegram execution UX

## V3 semantic progress amendment — 2026-09-07

Status: frozen candidate. Baseline and installed release:
`d8c8b7f41d23d1f99237bb44f435901f5c201dfb`.

Objective: one editable Telegram progress bubble reports real todo progress,
the current semantic step, a short user-facing finding, the next plan step,
and elapsed processing time. Percentages exist only for a fully validated
native todo snapshot. Raw reasoning, tool arguments, and tool results remain
hidden.

Verified root cause: `ExecutionProgress.update()` replaced every successful
tool completion with `Обрабатываю результат`; the interim assistant callback
routed completed commentary through `GatewayStreamConsumer`, which renders a
separate message. Production also keeps Telegram interim messages disabled.
The todo denominator, editable snapshot queue, redaction rail, and commentary
callback already exist, so a new controller or state store is unnecessary.

Options considered:

1. No change/config only: keeps generic stages and separate commentary.
2. Minimal repair: extend the existing presentation-only state machine and
   route its existing interim callback into `__snapshot__` for Telegram.
3. Structural streaming redesign: typed-event convergence across platforms.

Decision: option 2. Successful tool completion no longer changes visible
semantic state. Todo supplies `completed/total`, active step, and next pending
step. Completed user-facing commentary is bounded to 220 characters and
updates current/finding/next; `_thinking` never enters this route. The existing
redactor still runs before queueing. Production enables only the existing
Telegram `interim_assistant_messages` leaf after source qualification.

Tradeoffs: commentary classification is intentionally shallow and
presentation-only; explicit `Сейчас/Найдено/Результат/Дальше` labels win, while
unlabelled commentary after a completed tool is a finding. This improves
readability without parsing or persisting reasoning. A failed tool drops the
possibly stale percentage. A model that never creates todo gets honest status
without a bar.

Validation/falsifiers: fail the candidate if malformed/cancelled/inconsistent
todo produces a percentage, `_thinking` becomes visible, commentary creates a
second progress bubble, a successful tool completion restores the generic
phrase, topic metadata is lost, or transient edit recovery regresses.

Rollout/recovery: focused and adjacent gateway suites, static checks, managed
immutable deploy under the scoped lease, real Telegram DM smoke/read-back,
then managed rollback and reapply if the live path passes. Config uses a
private exact-byte backup and a one-key semantic-delta assertion. No push,
new dependency, scheduler, memory write, or public message.

Independent Grill B initially rejected the candidate on four probes: an
`already_streamed` boundary duplicate, the alternate `_thinking` callback,
pending-only todo semantics, and green final copy without a known turn outcome.
All four are now explicit regressions. Streamed text retains its existing
content boundary and is not duplicated into progress; structured Codex
commentary (`already_streamed=False`) is absorbed into the snapshot. Final copy
is derived from `result_holder` and remains neutral or negative for unknown,
failed, and interrupted outcomes. The second read-only review returned READY
for controlled live verification, with production-provider path, single-final
read-back, timeout/interrupt, and transient edit recovery retained as live
acceptance checks.

Status: frozen v2, 2026-09-06 03:08 UTC. Base c23f80da9548e1b0dfc2008192fb66d5d6f6ebb7.

V2 correction: new rendering is explicitly opt-in via display.platforms.telegram.execution_progress=true and requires existing tool_progress=all/accumulate. Existing modes remain unchanged. Existing queue carries __snapshot__ records, replaces the displayed content, and preserves its bubble across stream resets. Formatter validates full todo array against integer summary, drops percentage for invalid/cancelled plans, labels percentages as model-maintained plan marks, and never renders raw tool arguments/results. Interrupt/stale/clarify guards precede projection. Final progress edit measures elapsed since TurnRunner creation (processing duration, not network arrival-to-delivery). Native config raw per-platform resolver preserves this opt-in; no new core model tool or controller.

V1 was rejected by root: plain queue strings accumulated rather than replacing snapshots, no reset retention, completion truth and numeric coercion defects. V2 focused tests exercise actual TurnRunner callback and actual sender with transport fake, including stream reset/topic preservation. Baseline 136 tests passed; this does not qualify v2 by inheritance.

Live config correction: top-level streaming already exists with enabled=false, transport=auto, fresh_final_after_seconds=60. Therefore update its existing enabled/transport leaves (nested gateway.streaming would lose precedence). Set existing fresh_final_after_seconds=0 to retain one final after >60s; preserve existing interval/threshold/cursor and all unrelated keys. Exact allowed six-key delta and private byte-for-byte backup are enforced by config_transaction.py. Independent review requested invalidation of plan percentages on any tool error; implemented with regression test. Candidate tests: 53 passed (new callback/sender + existing progress/topic/transient), then 83 passed (updated error probe + stream/final suites), no failures.

Decision: enable gateway.streaming.enabled=true, transport=edit; explicitly enable display.platforms.telegram.streaming; tool_progress=all with existing accumulate default. Preserve rich_messages, reasoning visibility, cleanup and other features. Interim messages remain disabled initially because extra commentary bubbles conflict with the requested one progress message.

Root cause verified: explicit platform streaming=false, tool_progress=false and interim=false disable their callback surfaces. Global streaming alone cannot override the explicit platform value.

Three options: (1) configuration only restores technical tool breadcrumbs but lacks semantic stages; (2) extend the existing progress queue with a compact Telegram snapshot derived from native todo results and friendly tool labels; (3) new heartbeat/execution-gate controller. Choose (2); (3) adds runtime complexity without producing durable execution. Existing scheduler is the future Revenue Loop owner.

Candidate scope: Telegram accumulate mode only, no new core tools or scheduler. Render current native todo stage and completed/total percentage only from successful validated todo result; indicate plan-based fraction, not certainty of business success. Without a plan, show a friendly tool stage without arguments or fabricated percentage. Use the same existing editable bubble; no separate heartbeat. Preserve topic metadata, edit throttling, interruption and cleanup. Show measured elapsed for the tool phase at final progress edit, not a fabricated total task duration.

Hypotheses/falsifiers: H1 config suppresses UX (confirmed parser and resolver); H2 existing queue can carry snapshots (test real TurnRunner path); H3 todo provides denominator (native result observed in source, tests required); H4 one bubble retained across interim/stream breaks (regression test); H5 edit failure remains recoverable (existing transient/429/not-found tests).

Grill A: minimal queue extension improves UX and limits argument leakage. Risks: todo entries are model-maintained, not independently verified DoD; elapsed is scoped to execution phase; callback/result redaction may remove fields. Reject any percentage computed from tool count or guessed workload. No model routing changes.

Grill B acceptance: independent review must challenge truthful stage labels, non-success/invalid todo results, no denominator, same-name parallel tools, stale generation, interrupt, short no-tool answer (zero progress), topic routing, stream duplicate delivery, 429 and missing-message recovery. Add at least one new failure probe. Test wrapper required.

Release: clean worktree from base, focused static/unit/integration tests, isolated real component smoke, managed release under codex-autonomy lease, native Telegram DM smoke/read-back and memory measurement. Config backup remains private on VPS and rollback restores exact bytes before managed baseline deploy. No raw config in Git. Retain current and previous releases; no cleanup/push.

Readiness boundaries: configuration/source tests alone do not prove real incoming Telegram DM or actual buyer payment. Report unresolved live checks honestly. Board phase has a separate credential decision and lease; no Board credential migration or publication in this phase.

## V3 live correction — single mid-turn writer

The first production smoke exposed a provider path missing from the candidate
tests: Codex commentary deltas reached the ordinary content stream before the
interim callback projected the completed commentary into the execution
snapshot. The result was a truthful progress bar plus a second prose bubble.

Decision: while Telegram execution snapshot mode is active, it is the sole
mid-turn writer. The parallel content stream consumer is not created for that
turn; structured commentary updates the existing snapshot and the completed
answer uses the normal one-shot final-delivery path. Other platforms and
Telegram turns without execution progress retain their streaming behavior.
This is narrower than adding a second controller or changing provider event
semantics, and it makes the one-bubble invariant directly testable.

The same smoke showed that provider commentary can announce a completed step
just before the authoritative todo snapshot advances its counter. To avoid a
brief text/bar contradiction, a completion-shaped `Сейчас:` line no longer
overrides the plan-derived current step while a valid todo snapshot exists;
`Найдено:` and `Дальше:` remain visible, and the next todo update advances the
bar normally.
