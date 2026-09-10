# one-on-one automation

[Українською](README.uk.md) · English

Automates follow-ups after 1:1 meetings. The bot reads the meeting transcript,
drafts a follow-up in the ARCV format, sends the draft to the meeting host in
Telegram for review, and — once confirmed — delivers it to the participant and
files the host's own action items as Google Tasks.

```
Google Calendar ──> upcoming 1:1 ──> 90-min reminder (built from past follow-ups)
       │                              ├─> host: full briefing
       │                              └─> participant: only their own part
       └────────> finished 1:1 ──> transcript from Gmail ──> LLM ──> follow-up draft
                                                                       │
                                        Telegram («Підтвердити» button) ┤
                                                                       ├─> follow-up to the participant
                                                                       └─> host's tasks to Google Tasks
```

Google Sheets is the only state store — no PostgreSQL, no Make, and **no Google
Apps Script**: everything runs in the Python service. **The follow-up is the
single source of state** — the pre-meeting reminder is rebuilt from past
follow-ups, there is no separate task database (see `CLAUDE.md`, Rule 0).

## How it works, end to end

1. A **cycle** (`run-cycle`) runs on a schedule and scans the calendar during
   working hours (weekdays, `WORK_HOURS_START`–`WORK_HOURS_END`).
2. For a **finished** 1:1 it finds the transcript in Gmail, hands it to the LLM
   and parks a follow-up draft (`summary_status=draft`) in the `Meetings` sheet.
3. The draft goes to the **host** in Telegram with a «Підтвердити» (Confirm)
   button. Editing is a **reply** with new text (the bot re-issues the draft).
4. On Confirm the follow-up is delivered to the **participant**, and the items
   whose owner is the host (`HOST_NAME`) are created as **Google Tasks** with
   due dates.
5. For an **upcoming** 1:1, 90 minutes before it, a reminder goes out (rebuilt
   from past follow-ups): the **host** gets the full briefing (everything open +
   what to raise), and the **participant** gets only their own part.

Full rules are in [`CLAUDE.md`](CLAUDE.md) (in Ukrainian).

## Participants, matching and onboarding

The `Managers` sheet is the **participant directory**. One row per person you
run 1:1s with; columns: `manager_id`, `manager_name`, `aliases` (comma or
semicolon separated), `telegram_chat_id`, `telegram_thread_id`, `calendar_id`,
`transcript_sender`, `timezone`, `active`.

**A participant must start the bot to receive anything.** Nobody fills
`telegram_chat_id` by hand — it is set through self-service onboarding:

1. The participant opens the bot in Telegram and sends any first message.
2. The bot replies asking for their nickname (in our case «як в ПУПі» — as in
   the internal directory).
3. The bot finds their row by `manager_id` / `manager_name` / `aliases`
   (case-insensitive, a leading `@` is ignored) and writes their
   `telegram_chat_id`.
4. The host gets a `Підключився: <nick> ← @username` notice.

Two anti-hijack safeguards: a row that **already has** a `telegram_chat_id` is
never overwritten (the bot answers "already bound to another chat"), and every
successful bind is reported to the host — so a stranger cannot quietly replace a
real participant's chat, and any attempt is visible.

**Matching a transcript to a person** is by the calendar-title keyword
(`CALENDAR_TITLE_KEYWORDS`) plus the manager's aliases, matched word by word, so
an alias like `Олена` matches a responsible person written as `Олена Коваленко`.

**What each participant receives** (only if they have a `telegram_chat_id`, i.e.
they onboarded):

- the **follow-up** — after the host confirms it;
- the **90-minute reminder** — only their own part (their commitments and what
  is pending on them), and only if something is actually pending.

## Stack

Python 3.12, FastAPI, Google APIs (Calendar/Gmail/Sheets/Tasks), Telegram Bot
API, Gemini (or an OpenAI-compatible provider). Tests: pytest, lint: ruff.

## Requirements

- A **Google Cloud project** with these APIs enabled: Calendar, Gmail, Sheets,
  Tasks.
- An **OAuth client** (Desktop) → `secrets/google-oauth-client.json`. Scopes:
  `calendar.readonly`, `gmail.readonly`, `spreadsheets`, `tasks`.
- A **Google spreadsheet** (see below).
- A **Telegram bot** (token from @BotFather).
- A **Gemini API key** (or OpenAI). Gemini's free tier is 20 requests per day
  per model per project; `GEMINI_API_KEY` accepts a comma-separated list of keys
  from **different** GCP projects for headroom (see `CLAUDE.md`, Rule 5).

> **Note:** the service is **single-tenant** — it is bound to one Google account
> and one host (`HOST_*`), with one spreadsheet. For someone else to use it,
> they deploy their own instance with their own keys and their own spreadsheet.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env      # fill in the values
```

Every variable is documented in [`.env.example`](.env.example). Secrets
(`.env`, `secrets/`) are never committed (see `.gitignore`).

### The Google spreadsheet

1. Create a Google spreadsheet (any name), open it, and copy its ID from the URL
   (`docs.google.com/spreadsheets/d/<THIS>/edit`) into `GOOGLE_SHEET_ID`.
2. Leave it empty — on first run the bot creates the `Managers` and `Meetings`
   tabs with the correct headers automatically (there is **no Apps Script** to
   install; the `scripts/` directory in this repo is intentionally empty).
3. Fill the `Managers` tab: one row per participant (`manager_id`,
   `manager_name`, `aliases`, and `calendar_id`/`transcript_sender` if they
   differ from the defaults). Leave `telegram_chat_id` blank — onboarding fills
   it (see above).

### OAuth token

```bash
python -m app.google_oauth        # opens a browser once, creates secrets/google-token.json
```

## Running locally

```bash
uvicorn app.main:app --reload     # ENABLE_SCHEDULER/ENABLE_TELEGRAM_BOT=true → in-process cycle and long polling
```

### CLI jobs

```bash
python -m app.cli --job cycle                          # full cycle (transcripts + reminders + sync)
python -m app.cli --job transcripts                    # only draft follow-ups from new transcripts
python -m app.cli --job followups                      # only send the 90-min reminders
python -m app.cli --job send-summary --meeting-id <id> # dispatch one reviewed follow-up
```

The same jobs are exposed over HTTP: `POST /jobs/run-cycle`,
`/jobs/process-transcripts`, `/jobs/send-followups`,
`/jobs/send-summary/{meeting_id}`, plus `/telegram/webhook` and `/health`.

Only one process may poll the bot (Telegram returns 409 to the rest), so it is
either long polling or a webhook, never both at once.

## Deploying to Cloud Run

The container scales to zero, so there is no long-lived process:

- `ENABLE_SCHEDULER=false`, `ENABLE_TELEGRAM_BOT=false`;
- **Cloud Scheduler** calls `POST /jobs/run-cycle` (header
  `Authorization: Bearer <INTERNAL_JOB_TOKEN>`) on the working-hours schedule;
- a **Telegram webhook** posts updates to `POST /telegram/webhook` (the header
  `X-Telegram-Bot-Api-Secret-Token` must equal `TELEGRAM_WEBHOOK_SECRET`).

```bash
# 1. deploy
gcloud run deploy one-on-one-automation --source . --region <region> \
  --allow-unauthenticated --min-instances 0 --max-instances 1 \
  --env-vars-file env.yaml

# 2. point Telegram at the service (turns long polling off by itself)
#    setWebhook url=<service-url>/telegram/webhook secret_token=<TELEGRAM_WEBHOOK_SECRET>

# 3. create the Cloud Scheduler job hitting <service-url>/jobs/run-cycle
gcloud scheduler jobs create http one-on-one-cycle --location <region> \
  --schedule "*/5 8-18 * * 1-5" --time-zone "Europe/Kyiv" \
  --uri "<service-url>/jobs/run-cycle" --http-method POST \
  --headers "Authorization=Bearer <INTERNAL_JOB_TOKEN>"
```

`setWebhook` turns long polling off by itself, so the migration order is strict:
bring the new service up first, switch the webhook, and only then shut down any
old instance.

## Tests and lint

```bash
python -m pytest -q
python -m ruff check app/ tests/
```
