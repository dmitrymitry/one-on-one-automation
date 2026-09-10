from datetime import datetime, timezone

from app.meeting_matcher import match_manager
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
