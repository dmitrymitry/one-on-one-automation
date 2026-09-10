# Vegas 1:1 automation MVP design

## Goal

Automatically connect a Vegas 1:1 meeting to its transcript, write an ARCV
follow-up to Google Sheets for review, and brief the person who runs the 1:1
shortly before the next meeting.

Superseded 2026-09-05: the original design reconciled the transcript against a
tab of open action items with per-item statuses. That tab required manual
upkeep, so it was removed. The follow-up itself is now the only state, and the
pre-meeting reminder is derived from the last few follow-ups. See CLAUDE.md.

## Decisions

- Google Sheets is the only state store. No PostgreSQL, Make, or hidden local
  database is required.
- Google Calendar is the meeting source. Events are considered candidates when
  their title contains a configured marker (`Vegas` by default) and one manager
  alias from the `Managers` tab.
- Gmail is read-only and is used to locate the transcript/meeting notes. A
  sender, label, or additional Gmail query can narrow the search.
- The LLM turns a transcript into an ARCV follow-up: themes, context, and the
  tasks agreed, with the person who owns each one.
- The follow-up is drafted, never auto-sent. It reaches the host in Telegram
  with an edit button and a link to its cell; dispatch to the people named in
  it is an explicit command.
- The pre-meeting reminder is generated from the previous follow-ups, not from
  a task list: what was promised, what is still hanging, what to ask.
- A lightweight APScheduler loop is sufficient for the MVP. Each cycle is
  split into transcript processing and pre-meeting Telegram delivery, with
  deterministic identifiers making retries safe.

## Data flow

```text
Calendar events with Vegas marker
          │
          ├── past event ──> Gmail transcript ──> LLM follow-up draft
          │                                  ├── store summary_text
          │                                  ├── push draft to host chat
          │                                  └── mark Meetings completed
          │
          └── upcoming event ──> recent follow-ups ──> Telegram reminder
```

## State and retry model

`Meetings.meeting_id` is the Calendar event ID. A completed meeting is not
reprocessed, and a meeting that already has a drafted follow-up is not redrafted,
so a retry cannot overwrite an edited follow-up. `summary_synced_hash` records
which text the Telegram message currently shows, so the sync only touches a
message whose text actually changed.

The Google Sheets update is intentionally applied only after the LLM response
passes validation. If Gmail or the LLM fails, the meeting remains retryable and
the error is written to `Meetings.analysis_error`.

## Components

- `app/calendar_client.py`: reads Google Calendar events.
- `app/gmail_client.py`: searches and extracts transcript text from Gmail.
- `app/sheets_store.py`: schema, reads, writes, and idempotent action updates.
- `app/llm_analyzer.py`: follow-up and reminder prompts, response validation.
- `app/meeting_summary.py`: ARCV rendering, recipients, Telegram chunking.
- `app/followup.py`: pre-meeting reminder rendering.
- `app/telegram_bot.py`: long-polling loop that serves the edit button.
- `app/telegram_client.py`: sends Bot API messages with optional topic thread.
- `app/service.py`: orchestration and job boundaries.
- `app/main.py`: health/job endpoints and scheduler lifecycle.

## Out of scope for the MVP

- Automatic edits to the original transcript or calendar event.
- Editing individual follow-up items with buttons; the edit button replaces
  the whole text, and finer edits are made in the Sheet.
- Multiple calendars per manager beyond the Calendar ID stored in `Managers`.
- Audio transcription; the service expects a transcript or notes already sent
  to Gmail.

