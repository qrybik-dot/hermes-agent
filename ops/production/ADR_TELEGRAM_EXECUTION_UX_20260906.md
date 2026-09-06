# ADR: Telegram execution UX, candidate v1

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
