from datetime import datetime, timezone

from app.meeting_summary import CLOSED_HEADER
from app.models import CalendarMeeting, Manager
from app.prompts import build_host_tasks_prompt, build_reminder_prompt, build_summary_prompt


def flat(text: str) -> str:
    """Prompt prose with line wrapping undone, so phrase checks survive rewording."""
    return " ".join(text.split())


def meeting() -> CalendarMeeting:
    start = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
    return CalendarMeeting("event-1", "Vegas & Ksu / Weekly", start, start, "primary")


def test_reminder_prompt_without_cross_references_has_no_extra_block() -> None:
    prompt = build_reminder_prompt(Manager("ksu", "Ksu", ("Ksu",)), meeting(), ["якийсь фоллоуап"])

    assert "--- From the" not in prompt
    assert "якийсь фоллоуап" in prompt


def test_reminder_prompt_includes_labeled_cross_reference_block() -> None:
    prompt = build_reminder_prompt(
        Manager("ksu", "Ksu", ("Ksu",)),
        meeting(),
        ["власний фоллоуап Ksu"],
        cross_references=[("Iton", "обіцяв обговорити бюджет з Ксю")],
    )

    assert "власний фоллоуап Ksu" in prompt
    assert "--- From the Iton 1:1, where Ksu's name comes up ---" in prompt
    assert "обіцяв обговорити бюджет з Ксю" in prompt


def test_host_tasks_prompt_without_other_managers_has_no_exclusion_rule() -> None:
    prompt = build_host_tasks_prompt("текст фоллоуапу", ["Dmytro"], "2026-09-22")

    assert "belongs to their own" not in prompt


def test_host_tasks_prompt_lists_other_managers_to_exclude() -> None:
    prompt = build_host_tasks_prompt(
        "текст фоллоуапу", ["Dmytro"], "2026-09-22", other_managers=["Ksu", "Astra"]
    )

    assert "Ksu, Astra" in prompt
    assert "belongs to their own" in prompt


def test_summary_prompt_without_checklist_has_no_closure_instructions() -> None:
    prompt = build_summary_prompt(Manager("snig", "Snig", ("Snig",)), meeting(), "транскрипт")

    assert "Below is the checklist" not in prompt
    assert "--- Agenda prepared for THIS meeting" not in prompt


def test_summary_prompt_includes_previous_followups_for_closure_check() -> None:
    prompt = build_summary_prompt(
        Manager("snig", "Snig", ("Snig",)),
        meeting(),
        "транскрипт",
        previous_followups=["минулий фоллоуап з відкритою задачею"],
    )

    assert "Below is the checklist" in prompt
    assert "минулий фоллоуап з відкритою задачею" in prompt
    assert "--- Follow-up from 1 meeting(s) ago ---" in prompt


def test_summary_prompt_uses_the_meetings_own_agenda_as_the_checklist() -> None:
    prompt = build_summary_prompt(
        Manager("snig", "Snig", ("Snig",)),
        meeting(),
        "транскрипт",
        agenda="— Додати Олену Степаненко до чатів архівних проєктів",
    )

    assert "Below is the checklist" in prompt
    assert "--- Agenda prepared for THIS meeting (from its calendar event) ---" in prompt
    assert "— Додати Олену Степаненко до чатів архівних проєктів" in prompt


def test_summary_prompt_reads_do_not_include_as_a_verdict_to_drop() -> None:
    # 28.09: "не включається в фолоап" was obeyed literally — the item was left
    # out, silence kept it open, and it came back on the next agenda.
    prompt = build_summary_prompt(
        Manager("snig", "Snig", ("Snig",)), meeting(), "транскрипт", agenda="— пункт"
    )

    assert "is itself a verdict to drop it" in flat(prompt)
    assert '"closed": [' in flat(prompt)


def test_summary_prompt_keeps_an_excluded_new_topic_out_of_the_closed_block() -> None:
    # The closed block reaches the PM: a topic someone asked to keep out of the
    # follow-up must not surface there just because it was excluded.
    prompt = build_summary_prompt(
        Manager("snig", "Snig", ("Snig",)), meeting(), "транскрипт", agenda="— пункт"
    )

    assert "stays out entirely: no theme, no closed entry" in flat(prompt)
    assert "closing it never closes the new task" in flat(prompt)
    assert f'Items already listed under "{CLOSED_HEADER}"' in flat(prompt)


def test_reminder_prompt_never_reopens_items_from_the_closed_block() -> None:
    prompt = build_reminder_prompt(Manager("ksu", "Ksu", ("Ksu",)), meeting(), ["фоллоуап"])

    assert f'A follow-up may end with a block headed "{CLOSED_HEADER}"' in flat(prompt)
    assert "That item is CLOSED as of that follow-up" in flat(prompt)
    # A newer task on the same subject must survive an older closure.
    assert "A closure never reaches forward" in flat(prompt)


def test_host_tasks_prompt_skips_the_closed_block() -> None:
    prompt = build_host_tasks_prompt("текст фоллоуапу", ["Dmytro"], "2026-09-22")

    assert f'Ignore the block headed "{CLOSED_HEADER}" entirely' in flat(prompt)
