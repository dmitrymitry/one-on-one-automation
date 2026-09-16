from datetime import datetime, timezone

from app.followup import (
    CALENDAR_AGENDA_MARKER,
    build_calendar_agenda,
    build_participant_reminder,
    build_reminder,
    is_host,
    merge_calendar_notes,
    relative_day,
)
from app.llm_analyzer import (
    RawCarryOver,
    RawCommitment,
    RawOpenTopic,
    RawReminder,
    normalize_reminder,
)
from app.models import (
    CalendarMeeting,
    Manager,
    MeetingReminder,
    ReminderCarryOver,
    ReminderCommitment,
    ReminderOpenTopic,
)


def make_meeting() -> CalendarMeeting:
    start = datetime(2026, 9, 8, 10, tzinfo=timezone.utc)
    return CalendarMeeting("event", "Alpha / Vegas", start, start, "primary")


def test_reminder_has_three_sections() -> None:
    reminder = MeetingReminder(
        commitments=(
            ReminderCommitment(who="Ірина Коваленко", what="зібрати фідбек", timing="до 28.07"),
        ),
        carried_over=(
            ReminderCarryOver(
                what="оплата Did Global",
                who="Мадам",
                timing="до 24.07",
                since="зустріч 20.07, більше не згадувалась",
            ),
        ),
        open_topics=(
            ReminderOpenTopic(
                topic="ризик перетину відпусток у серпні",
                question="хто підстрахує на час відсутності?",
                since="зустріч 27.07",
            ),
        ),
    )

    # now = the meeting's own day, so the header reads "Сьогодні".
    text = build_reminder(
        Manager("alpha", "Alpha", ("Alpha",)),
        make_meeting(),
        reminder,
        now=datetime(2026, 9, 8, 8, tzinfo=timezone.utc),
    )

    assert text.startswith("Сьогодні о 10:00 — 1:1 з Alpha\nAlpha / Vegas")
    assert "Ознайомся перед зустріччю." in text
    assert "ЗАДАЧІ З ЇХНЬОГО БОКУ" in text
    assert "— зібрати фідбек\n   Хто: Ірина Коваленко\n   Строк: до 28.07" in text
    assert "ЗАВИСЛО З ПОПЕРЕДНІХ ЗУСТРІЧЕЙ" in text
    assert (
        "— оплата Did Global\n   Хто: Мадам\n   Строк: до 24.07\n"
        "   Звідки: зустріч 20.07, більше не згадувалась"
    ) in text
    assert "ПІДНІМАЛИ, АЛЕ НЕ ВИРІШИЛИ" in text
    assert (
        "— ризик перетину відпусток у серпні\n"
        "   Спитати: хто підстрахує на час відсутності?\n"
        "   Звідки: зустріч 27.07"
    ) in text


def test_reminder_omits_empty_sections() -> None:
    reminder = MeetingReminder(open_topics=(ReminderOpenTopic(topic="що зі статусом"),))

    text = build_reminder(Manager("alpha", "Alpha", ("Alpha",)), make_meeting(), reminder)

    assert "ЗАДАЧІ" not in text
    assert "ЗАВИСЛО" not in text
    assert "ПІДНІМАЛИ, АЛЕ НЕ ВИРІШИЛИ" in text


def test_reminder_without_anything_says_so() -> None:
    text = build_reminder(
        Manager("alpha", "Alpha", ("Alpha",)),
        make_meeting(),
        MeetingReminder(),
        now=datetime(2026, 9, 8, 8, tzinfo=timezone.utc),
    )

    assert "Відкритих домовленостей з попередніх зустрічей немає." in text


def test_reminder_header_says_tomorrow_when_sent_a_day_ahead() -> None:
    # Meeting Tue 08.09, reminder sent Mon 07.09 -> "Завтра".
    text = build_reminder(
        Manager("alpha", "Alpha", ("Alpha",)),
        make_meeting(),
        MeetingReminder(),
        now=datetime(2026, 9, 7, 15, tzinfo=timezone.utc),
    )

    assert text.startswith("Завтра о 10:00 — 1:1 з Alpha")


def test_reminder_header_names_the_weekday_for_a_monday_sent_on_friday() -> None:
    # Monday 14.09 meeting, reminder sent Friday 11.09 (weekend skipped).
    monday = datetime(2026, 9, 14, 16, tzinfo=timezone.utc)
    meeting = CalendarMeeting("event", "Astra&Vegas / weekly", monday, monday, "primary")
    text = build_reminder(
        Manager("astra", "Astra", ("Astra",)),
        meeting,
        MeetingReminder(),
        now=datetime(2026, 9, 11, 16, tzinfo=timezone.utc),
    )

    assert text.startswith("У понеділок о 16:00 — 1:1 з Astra")


def test_relative_day_falls_back_to_a_date_when_far_off() -> None:
    start = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
    assert relative_day(start, datetime(2026, 9, 8, 12, tzinfo=timezone.utc)) == "30.09"


def test_normalize_reminder_drops_empty_and_duplicate_entries() -> None:
    raw = RawReminder(
        commitments=[
            RawCommitment(who="Ірина", what="зробити звіт", timing="до 28.07"),
            RawCommitment(who="Дмитро", what="   "),
        ],
        carried_over=[RawCarryOver(what="  ", since="з 20.07")],
        open_topics=[
            RawOpenTopic(topic="бюджет"),
            RawOpenTopic(topic="бюджет"),
            RawOpenTopic(topic="  "),
        ],
    )

    reminder = normalize_reminder(raw)

    assert len(reminder.commitments) == 1
    assert reminder.commitments[0].who == "Ірина"
    assert reminder.carried_over == ()
    assert [item.topic for item in reminder.open_topics] == ["бюджет"]


def test_normalize_reminder_caps_open_topics_at_ten() -> None:
    raw = RawReminder(open_topics=[RawOpenTopic(topic=f"тема {i}") for i in range(15)])

    assert len(normalize_reminder(raw).open_topics) == 10


def test_commitments_split_between_host_and_the_other_side() -> None:
    reminder = MeetingReminder(
        commitments=(
            ReminderCommitment(who="Олег Ткаченко", what="обробити відео", timing="до 29.07"),
            ReminderCommitment(who="Ірина Коваленко", what="зібрати фідбек", timing="до 28.07"),
        )
    )

    text = build_reminder(
        Manager("beta", "Beta", ("Beta",)),
        make_meeting(),
        reminder,
        host_names=["Oleh Tkachenko", "Олег Ткаченко"],
    )

    assert "ТВОЇ ЗАДАЧІ" in text
    assert "ЗАДАЧІ З ЇХНЬОГО БОКУ" in text
    assert text.index("обробити відео") < text.index("ЗАДАЧІ З ЇХНЬОГО БОКУ")
    assert text.index("зібрати фідбек") > text.index("ЗАДАЧІ З ЇХНЬОГО БОКУ")


def test_host_matches_across_spellings_and_surname_only() -> None:
    names = ["Oleh Tkachenko", "Олег Ткаченко"]

    assert is_host("Ткаченко", names)
    assert is_host("олег ткаченко", names)
    # The transcript spelling must match too, not just the Ukrainian one.
    assert is_host("Oleh Tkachenko", names)
    assert is_host("tkachenko", names)
    assert not is_host("Ірина Коваленко", names)
    assert not is_host("", names)
    assert not is_host("Олег Ткаченко", [])


def test_without_a_host_name_everything_is_theirs() -> None:
    reminder = MeetingReminder(
        commitments=(ReminderCommitment(who="Олег Ткаченко", what="обробити відео"),)
    )

    text = build_reminder(Manager("beta", "Beta", ("Beta",)), make_meeting(), reminder)

    assert "ТВОЇ ЗАДАЧІ" not in text
    assert "ЗАДАЧІ З ЇХНЬОГО БОКУ" in text


def test_participant_reminder_holds_only_their_side() -> None:
    reminder = MeetingReminder(
        commitments=(
            ReminderCommitment(who="Олег Ткаченко", what="обробити відео", timing="до 29.07"),
            ReminderCommitment(who="Ірина Коваленко", what="зібрати фідбек", timing="до 28.07"),
        ),
        carried_over=(
            ReminderCarryOver(what="оплата Did Global", who="Ірина Коваленко", timing="до 24.07"),
            ReminderCarryOver(what="звіт для керівництва", who="Олег Ткаченко"),
        ),
        open_topics=(ReminderOpenTopic(topic="перетин відпусток"),),
    )

    text = build_participant_reminder(
        Manager("beta", "Beta", ("Beta",)), make_meeting(), reminder, ["Олег Ткаченко"]
    )

    assert "Перед зустріччю перевір, що готово з твого боку." in text
    assert "зібрати фідбек" in text
    assert "оплата Did Global" in text
    # The host's own items and undecided topics are not their business here.
    assert "обробити відео" not in text
    assert "звіт для керівництва" not in text
    assert "перетин відпусток" not in text


def test_participant_reminder_is_empty_when_they_owe_nothing() -> None:
    reminder = MeetingReminder(
        commitments=(ReminderCommitment(who="Олег Ткаченко", what="обробити відео"),)
    )

    text = build_participant_reminder(
        Manager("beta", "Beta", ("Beta",)), make_meeting(), reminder, ["Олег Ткаченко"]
    )

    assert text == ""


def test_calendar_agenda_lists_both_sides_by_name() -> None:
    reminder = MeetingReminder(
        commitments=(
            ReminderCommitment(who="Олег Ткаченко", what="обробити відео", timing="до 29.07"),
            ReminderCommitment(who="Ірина Коваленко", what="зібрати фідбек", timing="до 28.07"),
        ),
        open_topics=(ReminderOpenTopic(topic="перетин відпусток"),),
    )

    agenda = build_calendar_agenda(reminder)

    # No ТВОЇ/ЇХНІ split: the calendar event is shared, so everyone is named.
    assert "ТВОЇ ЗАДАЧІ" not in agenda
    assert "ВІДКРИТІ ДОМОВЛЕНОСТІ" in agenda
    assert "обробити відео" in agenda and "Хто: Олег Ткаченко" in agenda
    assert "зібрати фідбек" in agenda and "Хто: Ірина Коваленко" in agenda
    assert "перетин відпусток" in agenda


def test_calendar_agenda_is_empty_when_nothing_is_open() -> None:
    assert build_calendar_agenda(MeetingReminder()) == ""


def test_merge_calendar_notes_appends_after_existing_content() -> None:
    merged = merge_calendar_notes("Meet: https://meet.google.com/abc", "— зробити X")

    assert merged.startswith("Meet: https://meet.google.com/abc\n\n")
    assert CALENDAR_AGENDA_MARKER in merged
    assert merged.endswith("— зробити X")


def test_merge_calendar_notes_replaces_only_its_own_block() -> None:
    existing = merge_calendar_notes("Meet: https://meet.google.com/abc", "— стара тема")

    updated = merge_calendar_notes(existing, "— нова тема")

    assert updated.startswith("Meet: https://meet.google.com/abc")
    assert "— стара тема" not in updated
    assert "— нова тема" in updated
    assert updated.count(CALENDAR_AGENDA_MARKER) == 1


def test_merge_calendar_notes_drops_stale_block_once_resolved() -> None:
    existing = merge_calendar_notes("Meet: https://meet.google.com/abc", "— зробити X")

    cleared = merge_calendar_notes(existing, "")

    assert cleared == "Meet: https://meet.google.com/abc"
    assert CALENDAR_AGENDA_MARKER not in cleared


def test_merge_calendar_notes_works_on_an_empty_description() -> None:
    merged = merge_calendar_notes("", "— зробити X")

    assert merged == f"{CALENDAR_AGENDA_MARKER}\n\n— зробити X"
