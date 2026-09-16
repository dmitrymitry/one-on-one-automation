from datetime import datetime, timezone

from .meeting_matcher import normalize_text
from .models import (
    CalendarMeeting,
    Manager,
    MeetingReminder,
    ReminderCommitment,
    ReminderOpenTopic,
)

# Plain-text layout: Telegram markup is avoided so the same text stays readable
# in the sheet, where follow-ups are edited by hand.
RULE = "─" * 24

# Ukrainian weekday in the "коли?" (prepositional) form, indexed by weekday().
# Capitalised because it opens the reminder. Mon=0.
_WEEKDAYS = (
    "У понеділок",
    "У вівторок",
    "У середу",
    "У четвер",
    "У п'ятницю",
    "У суботу",
    "У неділю",
)


def relative_day(start_at: datetime, now: datetime | None = None) -> str:
    """When the meeting is, phrased for a reminder sent ahead of time.

    Reminders now go out a full day early — and for a Monday 1:1 they go out on
    Friday — so the meeting is rarely "today". Say Сьогодні / Завтра /
    Післязавтра for the near days, the weekday name a little further out, and
    fall back to the date for anything else (or a meeting already in the past).
    """
    now = now or datetime.now(start_at.tzinfo or timezone.utc)
    days = (start_at.date() - now.astimezone(start_at.tzinfo).date()).days
    if days == 0:
        return "Сьогодні"
    if days == 1:
        return "Завтра"
    if days == 2:
        return "Післязавтра"
    if 3 <= days <= 6:
        return _WEEKDAYS[start_at.weekday()]
    return f"{start_at:%d.%m}"


def build_reminder(
    manager: Manager,
    meeting: CalendarMeeting,
    reminder: MeetingReminder,
    host_names: list[str] | None = None,
    now: datetime | None = None,
) -> str:
    """Pre-meeting briefing for whoever runs the 1:1."""
    when = relative_day(meeting.start_at, now)
    blocks = [
        f"{when} о {meeting.start_at:%H:%M} — 1:1 з {manager.manager_name}\n"
        f"{meeting.title}\n\n"
        f"Ознайомся перед зустріччю."
    ]

    mine, theirs = _split_by_owner(reminder.commitments, host_names or [])
    if mine:
        blocks.append(_section("ТВОЇ ЗАДАЧІ", [_entry(item) for item in mine]))
    if theirs:
        blocks.append(_section("ЗАДАЧІ З ЇХНЬОГО БОКУ", [_entry(item) for item in theirs]))

    if reminder.carried_over:
        entries = [_entry(item) for item in reminder.carried_over]
        blocks.append(_section("ЗАВИСЛО З ПОПЕРЕДНІХ ЗУСТРІЧЕЙ", entries))

    if reminder.open_topics:
        blocks.append(
            _section(
                "ПІДНІМАЛИ, АЛЕ НЕ ВИРІШИЛИ",
                [_open_topic(item) for item in reminder.open_topics],
            )
        )

    if not (reminder.commitments or reminder.carried_over or reminder.open_topics):
        blocks.append("Відкритих домовленостей з попередніх зустрічей немає.")

    return "\n\n".join(blocks)


def build_participant_reminder(
    manager: Manager,
    meeting: CalendarMeeting,
    reminder: MeetingReminder,
    host_names: list[str],
    now: datetime | None = None,
) -> str:
    """What the other side of the 1:1 owes, and nothing else.

    Returns an empty string when they owe nothing, so nothing is sent.
    """
    _, theirs = _split_by_owner(reminder.commitments, host_names)
    hanging = [item for item in reminder.carried_over if not is_host(item.who, host_names)]
    if not theirs and not hanging:
        return ""

    when = relative_day(meeting.start_at, now)
    blocks = [
        f"{when} о {meeting.start_at:%H:%M} — 1:1\n{meeting.title}\n\n"
        f"Перед зустріччю перевір, що готово з твого боку."
    ]
    if theirs:
        blocks.append(_section("ТВОЇ ДОМОВЛЕНОСТІ", [_entry(item) for item in theirs]))
    if hanging:
        blocks.append(
            _section("ЗАВИСЛО З ПОПЕРЕДНІХ ЗУСТРІЧЕЙ", [_entry(item) for item in hanging])
        )
    return "\n\n".join(blocks)


CALENDAR_AGENDA_MARKER = f"{RULE}\nПОРЯДОК ДЕННИЙ (Vegas)\n{RULE}"


def build_calendar_agenda(reminder: MeetingReminder) -> str:
    """What to raise at the meeting, for the calendar event's own notes.

    Unlike `build_reminder`, this is read by both sides at once, so entries are
    named by person (`_entry` already prints "Хто") instead of split into
    ТВОЇ/ЇХНІ. Returns "" when there is nothing open, so a resolved agenda gets
    cleared rather than left stale (see `merge_calendar_notes`).
    """
    blocks = []
    if reminder.commitments:
        entries = [_entry(item) for item in reminder.commitments]
        blocks.append(_section("ВІДКРИТІ ДОМОВЛЕНОСТІ", entries))
    if reminder.carried_over:
        entries = [_entry(item) for item in reminder.carried_over]
        blocks.append(_section("ЗАВИСЛО З ПОПЕРЕДНІХ ЗУСТРІЧЕЙ", entries))
    if reminder.open_topics:
        blocks.append(
            _section(
                "ПІДНІМАЛИ, АЛЕ НЕ ВИРІШИЛИ",
                [_open_topic(item) for item in reminder.open_topics],
            )
        )
    return "\n\n".join(blocks)


def merge_calendar_notes(existing: str, agenda: str) -> str:
    """Replace our trailing agenda block in an event's description in place.

    Everything before `CALENDAR_AGENDA_MARKER` (Meet's own notes, anything
    typed by hand) is kept untouched; only our own block, always last, is
    replaced. An empty `agenda` drops the block entirely instead of leaving a
    stale one once everything is resolved.
    """
    marker_pos = existing.find(CALENDAR_AGENDA_MARKER)
    head = (existing[:marker_pos] if marker_pos != -1 else existing).rstrip()
    if not agenda:
        return head
    block = f"{CALENDAR_AGENDA_MARKER}\n\n{agenda}"
    return f"{head}\n\n{block}" if head else block


def _split_by_owner(
    commitments: tuple[ReminderCommitment, ...],
    host_names: list[str],
) -> tuple[list[ReminderCommitment], list[ReminderCommitment]]:
    """Separate what the host owes from what the other side owes."""
    if not host_names:
        return [], list(commitments)
    mine = [item for item in commitments if is_host(item.who, host_names)]
    theirs = [item for item in commitments if not is_host(item.who, host_names)]
    return mine, theirs


def is_host(who: str, host_names: list[str]) -> bool:
    """Match a responsible person against any known spelling of the host.

    The same person is spelled one way in the transcript and another in the
    Ukrainian follow-up, so every variant has to be listed.
    """
    owner = normalize_text(who)
    if not owner:
        return False
    for name in host_names:
        host = normalize_text(name)
        if not host:
            continue
        if host in owner or owner in host:
            return True
        # A surname on its own is enough; short particles are not.
        parts = [part for part in host.split() if len(part) > 2]
        if any(f" {part} " in f" {owner} " for part in parts):
            return True
    return False


def _entry(item) -> str:
    """One task, with the four things the host needs to see at a glance."""
    lines = [f"— {item.what}"]
    for label, value in (
        ("Хто", getattr(item, "who", "")),
        ("Строк", item.timing),
        ("Звідки", item.since),
    ):
        if value:
            lines.append(f"   {label}: {value}")
    return "\n".join(lines)


def _open_topic(item: ReminderOpenTopic) -> str:
    """An undecided topic: nobody owns it, so name it and say what to ask."""
    lines = [f"— {item.topic}"]
    for label, value in (("Спитати", item.question), ("Звідки", item.since)):
        if value:
            lines.append(f"   {label}: {value}")
    return "\n".join(lines)


def _section(title: str, entries: list[str]) -> str:
    return f"{RULE}\n{title}\n{RULE}\n\n" + "\n\n".join(entries)
