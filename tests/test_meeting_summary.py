from datetime import datetime, timezone

from app.llm_analyzer import RawSummary, RawSummaryTask, RawSummaryTheme, normalize_summary
from app.meeting_summary import (
    build_meeting_summary,
    collect_responsible,
    split_for_telegram,
    summary_recipients,
)
from app.models import CalendarMeeting, Manager, MeetingSummary, SummaryTask, SummaryTheme


def make_meeting() -> CalendarMeeting:
    start = datetime(2026, 7, 27, 15, 58, tzinfo=timezone.utc)
    return CalendarMeeting("event", "Beta&Vegas / Weekly", start, start, "primary")


def test_summary_renders_arcv_structure() -> None:
    summary = MeetingSummary(
        topics=("Збір фідбеку від клієнта",),
        themes=(
            SummaryTheme(
                title="ЗБІР ФІДБЕКУ",
                context="Клієнт не дає відкритого зворотного зв'язку.",
                tasks=(
                    SummaryTask(
                        action="Запитати фідбек на дзвінку до 28.07",
                        responsible="Ірина Коваленко",
                        expected_result="отримано відповіді",
                    ),
                ),
            ),
        ),
    )

    text = build_meeting_summary(make_meeting(), summary)

    assert text.startswith("27.07.26\nBeta&Vegas / Weekly")
    assert "Підсумок зустрічі\n— Збір фідбеку від клієнта" in text
    assert "1. ЗБІР ФІДБЕКУ" in text
    assert "Контекст: Клієнт не дає відкритого зворотного зв'язку." in text
    assert "1.1. Запитати фідбек на дзвінку до 28.07" in text
    assert "Відповідальний: Ірина Коваленко. Очікуваний результат — отримано відповіді" in text


def test_summary_header_names_the_meeting() -> None:
    summary = MeetingSummary(themes=(SummaryTheme(title="ТЕМА"),))

    text = build_meeting_summary(make_meeting(), summary)

    assert text.splitlines()[0] == "27.07.26"
    assert text.splitlines()[1] == "Beta&Vegas / Weekly"


def test_status_only_theme_has_no_subtasks() -> None:
    theme = SummaryTheme(title="СТАТУС ПРОЕКТУ", context="Все за планом.")
    summary = MeetingSummary(themes=(theme,))

    text = build_meeting_summary(make_meeting(), summary)

    assert "1. СТАТУС ПРОЕКТУ" in text
    assert "1.1." not in text


def test_normalize_summary_strips_forbidden_characters() -> None:
    raw = RawSummary(
        topics=["**Важлива** тема"],
        themes=[
            RawSummaryTheme(
                title="тема з `бектіками`",
                context="Текст із _підкресленням_ і [дужками]",
                tasks=[RawSummaryTask(action="Зробити #щось", responsible="Ірина Коваленко")],
            )
        ],
    )

    summary = normalize_summary(raw)

    assert summary.topics == ("Важлива тема",)
    theme = summary.themes[0]
    assert theme.title == "ТЕМА З БЕКТІКАМИ"
    assert theme.context == "Текст із підкресленням і дужками"
    assert theme.tasks[0].action == "Зробити щось"
    assert theme.tasks[0].responsible == "Ірина Коваленко"


def test_normalize_summary_strips_invented_telegram_tags() -> None:
    raw = RawSummary(
        themes=[
            RawSummaryTheme(
                title="ТЕМА",
                tasks=[RawSummaryTask(action="Зробити", responsible="@olena_step")],
            )
        ]
    )

    summary = normalize_summary(raw)

    assert summary.themes[0].tasks[0].responsible == "olenastep"


def test_normalize_summary_drops_themes_without_title() -> None:
    raw = RawSummary(themes=[RawSummaryTheme(title="  ", context="текст")])

    assert normalize_summary(raw).themes == ()


def make_summary(*responsible: str) -> MeetingSummary:
    tasks = tuple(SummaryTask(action="Зробити", responsible=name) for name in responsible)
    return MeetingSummary(themes=(SummaryTheme(title="ТЕМА", tasks=tasks),))


def test_collect_responsible_lists_named_people() -> None:
    summary = make_summary("Ірина Коваленко", "Олег Ткаченко", "")

    assert collect_responsible(summary) == {"Ірина Коваленко", "Олег Ткаченко"}


def test_recipients_include_owner_and_named_directory_members() -> None:
    owner = Manager("dmytro", "Dmytro", ("Олег Ткаченко",), telegram_chat_id="1")
    olena = Manager("olena", "Olena", ("Ірина",), telegram_chat_id="2")
    other = Manager("beta", "Beta", ("Бета",), telegram_chat_id="3")
    summary = make_summary("Ірина Коваленко")

    recipients = summary_recipients(owner, [owner, olena, other], summary)

    assert [manager.manager_id for manager in recipients] == ["dmytro", "olena"]


def test_recipients_skip_people_without_chat_id() -> None:
    owner = Manager("dmytro", "Dmytro", ("Дмитро",), telegram_chat_id="1")
    olena = Manager("olena", "Olena", ("Ірина",))
    summary = make_summary("Ірина Коваленко")

    recipients = summary_recipients(owner, [owner, olena], summary)

    assert [manager.manager_id for manager in recipients] == ["dmytro"]


def test_recipients_deduplicate_shared_chat_id() -> None:
    owner = Manager("dmytro", "Dmytro", ("Дмитро",), telegram_chat_id="1")
    duplicate = Manager("olena", "Olena", ("Ірина",), telegram_chat_id="1")
    summary = make_summary("Ірина")

    recipients = summary_recipients(owner, [owner, duplicate], summary)

    assert [manager.manager_id for manager in recipients] == ["dmytro"]


def test_owner_receives_summary_even_without_own_tasks() -> None:
    owner = Manager("dmytro", "Dmytro", ("Дмитро",), telegram_chat_id="1")
    summary = make_summary("Хтось Зовнішній")

    recipients = summary_recipients(owner, [owner], summary)

    assert [manager.manager_id for manager in recipients] == ["dmytro"]


def test_split_for_telegram_breaks_on_block_boundaries() -> None:
    text = "\n\n".join(["block" * 40] * 20)

    chunks = split_for_telegram(text, limit=1000)

    assert len(chunks) > 1
    assert all(len(chunk) <= 1000 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_host_tasks_keep_only_valid_iso_deadlines() -> None:
    from app.llm_analyzer import RawHostTask, RawHostTasks, normalize_host_tasks

    raw = RawHostTasks(
        tasks=[
            RawHostTask(task="обробити відео", deadline="2026-07-29"),
            RawHostTask(task="Обробити відео", deadline="2026-07-29"),
            RawHostTask(task="надіслати звіт", deadline="29.07"),
            RawHostTask(task="   ", deadline="2026-08-01"),
        ]
    )

    tasks = normalize_host_tasks(raw)

    assert [(t.task, t.deadline) for t in tasks] == [
        ("обробити відео", "2026-07-29"),
        ("надіслати звіт", ""),
    ]


def test_settled_deadline_needs_no_gloss() -> None:
    """A task that already carries a date must not repeat the wording behind it."""
    summary = MeetingSummary(
        themes=(
            SummaryTheme(
                title="ТЕМА",
                tasks=(
                    SummaryTask(
                        action="Запитати фідбек до 28.07",
                        responsible="Ірина Коваленко",
                        deadline_note="завтра на дзвінку",
                    ),
                ),
            ),
        )
    )

    text = build_meeting_summary(make_meeting(), summary)

    assert "1.1. Запитати фідбек до 28.07\n" in text
    assert "завтра на дзвінку" not in text


def test_timing_without_a_date_still_reaches_the_reader() -> None:
    """A vague phrase yields no date, but must not vanish from the follow-up."""
    summary = MeetingSummary(
        themes=(
            SummaryTheme(
                title="ТЕМА",
                tasks=(
                    SummaryTask(
                        action="Обробити відеозапис",
                        responsible="Олег Ткаченко",
                        deadline_note="не знаю, чи встигну на цьому тижні",
                    ),
                ),
            ),
        )
    )

    text = build_meeting_summary(make_meeting(), summary)

    assert "1.1. Обробити відеозапис (не знаю, чи встигну на цьому тижні)" in text


def test_no_timing_means_no_brackets() -> None:
    summary = MeetingSummary(
        themes=(SummaryTheme(title="ТЕМА", tasks=(SummaryTask(action="Зробити"),)),)
    )

    text = build_meeting_summary(make_meeting(), summary)

    assert "1.1. Зробити\n" in text
    assert "(" not in text.split("1.1.")[1]
