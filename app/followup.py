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


def build_reminder(
    manager: Manager,
    meeting: CalendarMeeting,
    reminder: MeetingReminder,
    host_names: list[str] | None = None,
) -> str:
    """Pre-meeting briefing for whoever runs the 1:1."""
    blocks = [
        f"Сьогодні о {meeting.start_at:%H:%M} — 1:1 з {manager.manager_name}\n"
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
) -> str:
    """What the other side of the 1:1 owes, and nothing else.

    Returns an empty string when they owe nothing, so nothing is sent.
    """
    _, theirs = _split_by_owner(reminder.commitments, host_names)
    hanging = [item for item in reminder.carried_over if not is_host(item.who, host_names)]
    if not theirs and not hanging:
        return ""

    blocks = [
        f"Сьогодні о {meeting.start_at:%H:%M} — 1:1\n{meeting.title}\n\n"
        f"Перед зустріччю перевір, що готово з твого боку."
    ]
    if theirs:
        blocks.append(_section("ТВОЇ ДОМОВЛЕНОСТІ", [_entry(item) for item in theirs]))
    if hanging:
        blocks.append(
            _section("ЗАВИСЛО З ПОПЕРЕДНІХ ЗУСТРІЧЕЙ", [_entry(item) for item in hanging])
        )
    return "\n\n".join(blocks)


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
