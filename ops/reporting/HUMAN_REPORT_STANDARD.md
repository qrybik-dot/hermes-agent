# Human Report Standard

Use this standard for complex technical, audit, migration, comparative, infrastructure, routing, and release tasks when a full HTML report is useful or requested.

Canonical example:

- `ops/reporting/Hermes_Model_Routing_Guide_2026-07-09.html`

The canonical example was explicitly selected by the user on 2026-07-11.
Match its dark navy palette, compact status hero, metric row, linked contents,
dense numbered sections, status-colored cards, acceptance matrix, and
collapsible technical appendix. Do not substitute a light paper-style report.

## Core principle

The report is for an ordinary user first and for an engineer second.

Do not replace explanation with logs, filenames, commits, raw tables, or jargon. Explain what changed, why it matters, how the system now behaves, what remains, and what the user should expect.

## Required structure

1. Clear status badge: READY, PARTIAL, or BLOCKED.
2. One-paragraph plain-language summary.
3. Clickable table of contents.
4. Current status and remaining work.
5. What was done, grouped by user-visible outcomes.
6. How the system now works.
7. Relevant options, models, services, or routes in a readable table.
8. Fallback, safety, rollback, and failure behavior.
9. Tests and evidence translated into plain language.
10. Git, commits, files, and technical references after the explanation.
11. What happens next.

## Writing rules

- Start with the conclusion.
- Use short paragraphs and descriptive headings.
- Explain every technical term the first time it appears.
- For each major change answer: what changed, why, and what it changes for the user.
- Use examples of real user tasks.
- Distinguish clearly between available, tested, active, fallback-only, candidate, and unavailable.
- Never imply that a smoke test proves comparative superiority.
- State uncertainty and untested areas directly.
- Avoid raw command output unless it is essential evidence.
- Never invent metrics, percentages, speedups, or quality rankings.
- Keep technical identifiers in code styling and in the lower evidence sections.

## Visual rules

- Self-contained HTML only.
- Dark navy presentation matching the canonical example; high contrast text.
- Mobile responsive.
- No external CDN, scripts, fonts, or analytics.
- Prefer CSS-only navigation and `<details>` blocks.
- Use metric cards only for verified numbers.
- Use tables for comparisons and routing matrices.
- Use color consistently: green verified, amber pending/fallback, red blocker/risk, blue informational.
- Keep the main narrative non-technical; place paths, commands, hashes and raw
  evidence in lower tables or a collapsible appendix.

## Model-routing reports

Always include:

- live available model catalog by provider;
- current primary model for every task role;
- fallback for every role;
- whether the fallback may use tools;
- which models were only smoke-tested;
- which models were compared head-to-head;
- models still awaiting qualification;
- why each active model was selected;
- what would trigger a future change.

## Delivery

- Telegram message: short status and attached HTML.
- HTML: full human-readable report.
- Validate with `scripts/validate_html_report.py`.
- Record the HTML artifact in Git when it is part of a technical milestone.
