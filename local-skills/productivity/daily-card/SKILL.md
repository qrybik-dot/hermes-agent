---
name: daily-card
description: Structured personal day planning for Telegram: today card, evening preparation, timed events, and action reminders.
version: 1.0.0
metadata:
  hermes:
    category: productivity
---
# Daily Card

Use this skill for personal reminders, appointments, trips, deadlines, and tasks that should appear in the pinned Telegram day card.

## Source of truth

- Actionable work belongs in Kanban (`~/.hermes/kanban.db`).
- Timed appointments and meetings belong in Google Calendar when they already exist there. Calendar remains read-only unless the user explicitly confirms creation or editing.
- Timed reminders that are not in Calendar belong in the structured event registry (`~/.hermes/daily_cards.db`).
- Cron is only a trigger. Never use cron text as a second copy of an event.
- The Telegram card is only a view. Never store task truth in the message text.

## Classification

### Kanban task

Use for actions that can be completed:

- call, pay, send, prepare, buy, check;
- tasks without a fixed appointment time;
- preparation steps for an event.

Set `planned_for` to an ISO date when the user says today, tomorrow, or names a day. Set `due_at` only for a real deadline with a clock time. An undated backlog task must not appear in the daily card automatically.

For a dated daily task, write UTF-8 JSON to `/home/hermes/.hermes/tmp/daily-task.json` with `title` and either `planned_for` or `due_at`. Optional fields: `body`, `timezone`, `importance`, `assignee`, and `idempotency_key`. Then run only:

```bash
/home/hermes/hermes-v018-integration/.venv/bin/python -m hermes_cli.daily_card task-import
```

Success requires JSON with `status=saved` and a `task_id`. Never put the task text directly into a shell command.

### Timed event

Use for:

- doctor or official appointment;
- trip, train, flight, school or child activity;
- in-person or online meeting;
- fixed-time pickup or visit.

Events never receive a Done button. If preparation is required, create a separate Kanban task or list confirmed preparation facts in the event.

## Safe event write

Write UTF-8 JSON to the fixed path:

`/home/hermes/.hermes/tmp/daily-event.json`

Allowed fields:

- `title` required;
- `event_at` required, ISO 8601 with timezone preferred;
- `timezone`, default `Europe/Moscow`;
- `person`, `address`, `location`, `online_url`;
- `requires_travel` boolean;
- `preparation` string list, only confirmed items;
- `importance` integer;
- `source_kind`, `source_id`, `event_id` for deduplication;
- `all_day` boolean;
- `travel_mode`: `walk`, `drive`, `transit`, or `unknown`;
- `route_minutes`, `route_buffer_minutes` only when a route was actually checked.

Then run only this fixed command:

```bash
/home/hermes/hermes-v018-integration/.venv/bin/python -m hermes_cli.daily_card event-import
```

Success requires JSON with `status=saved` and an `event_id`. Never put user text directly into a shell command.

## Notification purposes

The same event may appear more than once only when the purpose differs:

- evening: preparation or planning for tomorrow;
- morning: overview of today;
- action: leave, depart, connect, or start now;
- change: confirmed time or location changed.

Never send two messages with the same event, purpose, and time window.

## Evening filter

Show only events that affect behaviour before sleep or the next morning:

- preparation is required;
- travel is required;
- event starts before 10:00;
- high cost of missing it;
- confirmed change or missing address.

Ordinary quick tasks such as call, pay, write, or check stay in the morning card unless there is a hard early deadline.

## Action reminder

- Known route: use `Пора выходить` or `Пора выезжать`, route duration, and explicit buffer.
- Unknown route or transport: use neutral `Через час`; do not guess transport or travel time.
- Online meeting with confirmed URL: remind 10 minutes before.
- Do not create an action reminder for every ordinary calendar item.

## Privacy and wording

- Use explicit person names when known. Do not invent which child an appointment concerns.
- Do not copy policy numbers, medical identifiers, or full private Calendar descriptions into the card database or Telegram message.
- Say `Не отмечено`, not `Не выполнено`, because the user may have completed a task without pressing the button.
- Keep Telegram output plain text. Do not use Rich Messages or technical cron identifiers.
