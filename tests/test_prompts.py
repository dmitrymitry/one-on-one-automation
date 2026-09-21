from datetime import datetime, timezone

from app.models import CalendarMeeting, Manager
from app.prompts import build_host_tasks_prompt, build_reminder_prompt


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
