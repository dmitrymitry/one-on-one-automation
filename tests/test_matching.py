from datetime import datetime, timezone

from app.meeting_matcher import match_manager, mentions_manager
from app.models import CalendarMeeting, Manager


def meeting(title: str) -> CalendarMeeting:
    start = datetime(2026, 9, 8, 10, tzinfo=timezone.utc)
    return CalendarMeeting("event-1", title, start, start, "primary")


def test_matches_supported_vegas_title_variants() -> None:
    managers = [
        Manager("alpha", "Alpha", ("Alpha",)),
        Manager("gamma", "Gamma", ("Gamma",)),
        Manager("ruslanivna", "Ruslanivna", ("Ruslanivna",)),
    ]

    assert match_manager(meeting("Alpha / Vegas"), managers, ["Vegas"]).manager_id == "alpha"
    assert match_manager(meeting("Vegas & Gamma"), managers, ["Vegas"]).manager_id == "gamma"
    assert (
        match_manager(meeting("Ruslanivna & Vegas"), managers, ["Vegas"]).manager_id == "ruslanivna"
    )


def test_ignores_non_vegas_and_ambiguous_titles() -> None:
    managers = [
        Manager("madam", "Madam", ("Madam",)),
        Manager("madam-ops", "Madam Ops", ("Madam", "Madam Ops")),
    ]

    assert match_manager(meeting("Madam weekly"), managers, ["Vegas"]) is None
    assert match_manager(meeting("Madam & Madam Ops / Vegas"), managers, ["Vegas"]) is None


def test_mentions_manager_matches_across_alphabets() -> None:
    ksu = Manager("ksu", "Ksu", ("Vegas Ksu", "Ksu Vegas", "Ксю Vegas", "Vegas Ксю"))

    assert mentions_manager("домовились обговорити це з Ксю на наступному дзвінку", ksu)
    assert mentions_manager("I'll ask Ksu about the budget", ksu)
    assert not mentions_manager("зустріч пройшла без зауважень", ksu)


def test_mentions_manager_ignores_the_shared_vegas_keyword() -> None:
    ksu = Manager("ksu", "Ksu", ("Vegas Ksu", "Ksu Vegas", "Ксю Vegas", "Vegas Ксю"))

    # Every meeting title and most follow-ups mention "Vegas" — that alone
    # must never count as naming this specific person.
    assert not mentions_manager("Vegas & Iton / weekly, підсумок зустрічі", ksu)
