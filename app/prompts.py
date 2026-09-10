from .models import CalendarMeeting, Manager

LANGUAGE_RULE = """
Write every text value in the JSON response in UKRAINIAN, regardless of the
transcript language. This applies to action, responsible, evidence,
evidence_excerpt, verification, next_check_question, context, title and topic
fields. Only evidence_excerpt may keep the original wording when quoting
verbatim. Never answer in English or Russian.
""".strip()

GROUNDING_RULE = """
Every statement must be traceable to something a participant actually said.
You are recording a conversation, not explaining it.

- Roles, statuses and relationships ("нова менеджерка", "керівник напряму",
  "новий клієнт") only if the transcript says so. If people are referred to
  vaguely, stay vague: "ті, з ким працюємо" is honest, an invented job title
  is not.
- Causes and motives ("через те, що...", "оскільки...") only when a participant
  states the cause. Do not supply the reason yourself, however obvious it seems.
- Names, companies, tools, numbers, amounts: only those spoken. Never normalise
  a half-heard name into a plausible one.
- Never smooth a gap. If the transcript is unclear about who does what or why,
  write less rather than filling it in. A short honest context beats a fluent
  invented one.
- Prefer the participants' own words over a polished paraphrase, especially for
  anything a person could later be held to.
- expected_result is what the participants said they would get, not what you
  judge a good outcome would be.

The reader validates this text and sends it to the other participant. A fluent
sentence they never said is worse than a gap they can fill in themselves.
""".strip()

DEADLINE_RULES = """
Deadlines: never invent one, but do resolve the ones that were spoken.

A deadline may be set ONLY if the transcript contains an actual time expression
attached to that task. If the people agreed on the task but said nothing about
timing, the deadline stays EMPTY. An empty deadline is a correct answer; a
plausible-looking date that nobody said is a defect, because a human will trust
it. When in doubt, leave it empty.

When timing IS spoken, resolve it against the meeting start date above and
return an ISO date (YYYY-MM-DD):
- "через тиждень" / "за тиждень" -> anchor + 7 days.
- "через пару днів" / "через кілька днів" -> anchor + 3 days.
- "через два тижні" -> anchor + 14 days. "через місяць" -> anchor + 1 month.
- "завтра" -> anchor + 1 day. "післязавтра" -> anchor + 2 days.
- "до п'ятниці" / any weekday -> the next occurrence of that weekday after the
  anchor (if the meeting itself is on that weekday, use the following week).
- "до кінця тижня" -> the Friday of the meeting's week.
- "до кінця місяця" -> the last day of the meeting's month.
- An explicit date without a year ("до 28.07") -> that date in the meeting's
  year, unless it would fall before the anchor, then the next year.

These do NOT justify a date, because they fix no moment: "як буде час",
"після релізу", "коли звільнюсь", "до наступної зустрічі", "найближчим часом",
"на цьому тижні" said as doubt rather than a commitment ("не знаю, чи встигну
на цьому тижні"). Leave deadline empty and put the phrase in deadline_note.

Always copy the wording the transcript actually used into deadline_note, so a
human can verify what was agreed against what you computed.
""".strip()


def build_summary_prompt(
    manager: Manager,
    meeting: CalendarMeeting,
    transcript: str,
) -> str:
    return f"""You are an operations assistant writing a post-meeting follow-up.

Manager: {manager.manager_name}
Meeting title: {meeting.title}
Meeting start: {meeting.start_at.isoformat()}

{LANGUAGE_RULE}

{GROUNDING_RULE}

{DEADLINE_RULES}

Name people by their full name exactly as the transcript introduces them:
first name and last name (for example "Ірина Коваленко"). Use the full name on
first mention inside a theme. Never invent a Telegram nickname or handle, and
never write a bare @ tag.

Return JSON only with this exact shape:
{{
  "topics": ["short topic, 3-7 words"],
  "themes": [
    {{
      "title": "THEME NAME IN UPPERCASE, 3-5 words",
      "context": "1-3 sentences: facts, decisions, what affects next steps",
      "tasks": [
        {{
          "action": "verb + what to do, plus до DD.MM ONLY if timing was spoken",
          "responsible": "full name, first and last",
          "expected_result": "concrete expected outcome",
          "deadline_note": "the timing wording from the transcript, or empty"
        }}
      ]
    }}
  ]
}}

Rules:
- topics is the meeting summary: one short line per main theme discussed.
- Every theme discussed becomes an entry in themes, in the order it came up.
- A theme that is only a status update has an empty tasks list. Do not invent tasks.
- Add a task only when the transcript contains a concrete commitment.
- A date inside action text appears ONLY when the transcript gave timing for that
  task; write it as DD.MM. Never append a date to make the task look complete.
- deadline_note carries the timing words as spoken ("завтра", "найближчим часом",
  "не знаю, чи встигну на цьому тижні"), so a human can check the date you
  computed, or see that no real deadline existed. Empty when timing never
  came up at all.
- Never use these characters anywhere in your output: * # _ ~ ` [ ] @
- Keep everything grounded in the transcript. Do not speculate, do not explain,
  do not add a detail because it makes the sentence read better.

Transcript:
---
{transcript}
---
"""


def build_reminder_prompt(
    manager: Manager,
    meeting: CalendarMeeting,
    followups: list[str],
) -> str:
    """Brief the person running the 1:1, using the earlier follow-ups as input."""
    blocks = []
    for index, followup in enumerate(followups, 1):
        label = "most recent" if index == 1 else f"{index} meetings ago"
        blocks.append(f"--- Follow-up {index} ({label}) ---\n{followup}")
    followups_text = "\n\n".join(blocks) or "(no earlier follow-ups found)"

    return f"""You are briefing the person who runs a recurring 1:1, shortly before it starts.

Manager they are meeting: {manager.manager_name}
Upcoming meeting: {meeting.title}
Upcoming meeting start: {meeting.start_at.isoformat()}

{LANGUAGE_RULE}

You are given the follow-ups written after the previous meetings, newest first.
Each one already lists the themes, the tasks agreed and who owns them. Work only
from these: every item you report must appear in them.

Each follow-up opens with a short list under the heading "Підсумок зустрічі".
IGNORE that list completely. It is only a table of contents, and the host prunes
the numbered theme blocks below it without always pruning the list, so a line
there may name a theme that was deliberately removed. Take every item solely
from the numbered theme blocks ("1. THEME", "1.1. task") and their tasks. If a
topic appears in that opening list but has no numbered block, it does not exist
for you: never turn it into a question, a commitment or a carried-over item.

Return JSON only with this exact shape:
{{
  "commitments": [
    {{
      "who": "full name of the person who promised",
      "what": "what they promised to have ready",
      "timing": "the deadline agreed, as the follow-up states it, or empty",
      "since": "the meeting it was agreed at, e.g. 'зустріч 27.07'"
    }}
  ],
  "carried_over": [
    {{
      "what": "the unresolved item",
      "who": "full name of the person who owns it",
      "timing": "the deadline that was agreed and has passed, or empty",
      "since": "where it came from and whether it resurfaced, e.g. "
               "'зустріч 20.07' or 'зустріч 20.07, більше не згадувалась'"
    }}
  ],
  "open_topics": [
    {{
      "topic": "what was raised and left undecided",
      "question": "what the host should ask to close it",
      "since": "the meeting it was raised at, e.g. 'зустріч 27.07'"
    }}
  ]
}}

How to fill each section:
- commitments: tasks from the MOST RECENT follow-up that should be ready by the
  upcoming meeting. Include the person who owns each one.
- carried_over: anything from an EARLIER follow-up that still has no resolution.
  Two cases both belong here, and both matter:
  1. Raised again in a later follow-up without being closed — it keeps coming
     back. Put such a task here rather than in commitments.
  2. Raised once and never mentioned again in any later follow-up — it was
     quietly dropped. A follow-up records what was agreed, not what was done,
     so silence is not completion. Say in `since` which meeting it came from
     and that it has not come up since.
  Leave this list empty only when there is a single follow-up to work from.
- open_topics: things that were RAISED BUT LEFT UNDECIDED, with nobody owning
  them. This is the section that stops things from being lost. A task has an
  owner and a deadline, so it survives on its own; a topic without a decision
  survives nowhere, and disappears unless you list it here.
  Go looking for them deliberately in every follow-up:
  - a theme block that carries context but no task under it;
  - a decision explicitly deferred ("повернемось пізніше", "поки не вирішили");
  - a risk or a problem named, with no action agreed against it;
  - something waiting on a person who was not in the meeting.
  A topic that appears undecided in more than one follow-up is the strongest
  signal: list it first and say in `since` that it has come up repeatedly.
  Never restate a commitment or a carried-over item here: the host already reads
  those lists and will ask about them. This section is only for what those two
  lists cannot hold. Return an empty list when nothing was left undecided.
  At most 10, most important first.

Always fill who, timing and since when the follow-up states them: the host reads
this to know who owes what, by when, and which meeting it came from. Leave a
field empty only when the follow-up genuinely does not say.

Do not invent progress: a follow-up records what was agreed, not what was
delivered, so treat every task as still open unless a later follow-up says
otherwise. Never use these characters: * # _ ~ ` [ ] @

{followups_text}
"""


def build_host_tasks_prompt(
    followup: str,
    host_names: list[str],
    meeting_date: str,
) -> str:
    """Pull out only what the host personally committed to, with resolvable dates."""
    names = ", ".join(host_names) or "(unknown)"
    return f"""You are reading a confirmed 1:1 follow-up and listing the tasks that belong to
the person who runs the meeting, so they can be put on their calendar.

The host is known by these names: {names}
The meeting took place on: {meeting_date}

{LANGUAGE_RULE}

Return JSON only with this exact shape:
{{
  "tasks": [
    {{
      "task": "what the host has to do, as a short imperative line",
      "deadline": "YYYY-MM-DD or empty"
    }}
  ]
}}

Rules:
- Include a task ONLY when the follow-up names the host as responsible for it
  (the "Відповідальний:" line, or the task text itself). Tasks owned by anyone
  else are not the host's and must be left out.
- Deadlines in the follow-up are written as "до DD.MM". Resolve them to a full
  ISO date using the meeting year; a date earlier than the meeting means the
  following year. Leave deadline empty when the follow-up gives none — never
  invent a plausible one, and never reuse the meeting date as a stand-in.
- Keep the task text close to the follow-up wording. Do not invent tasks.
- Never use these characters: * # _ ~ ` [ ] @

Follow-up:
---
{followup}
---
"""
