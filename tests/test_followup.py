from datetime import datetime, timezone

from app.followup import build_participant_reminder, build_reminder, is_host
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

    text = build_reminder(Manager("alpha", "Alpha", ("Alpha",)), make_meeting(), reminder)

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
        Manager("alpha", "Alpha", ("Alpha",)), make_meeting(), MeetingReminder()
    )

    assert "Відкритих домовленостей з попередніх зустрічей немає." in text


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
