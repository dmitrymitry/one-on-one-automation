from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.service import VegasAutomationService

KYIV = ZoneInfo("Europe/Kyiv")


def service(start: int = 10, end: int = 19, lookahead: int = 90) -> VegasAutomationService:
    svc = VegasAutomationService.__new__(VegasAutomationService)
    svc.settings = SimpleNamespace(
        app_timezone="Europe/Kyiv",
        work_hours_start=start,
        work_hours_end=end,
        calendar_lookahead_minutes=lookahead,
    )
    return svc


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """2026-09-day at that local time. 7 Sep 2026 is a Monday."""
    return datetime(2026, 9, day, hour, minute, tzinfo=KYIV)


def test_runs_during_the_working_day() -> None:
    assert service().within_working_hours(at(7, 12))
    assert service().within_working_hours(at(9, 15, 58))


def test_opens_early_enough_for_the_first_meeting_of_the_day() -> None:
    """A 10:00 meeting needs its 90-minute reminder at 08:30."""
    svc = service()
    assert svc.within_working_hours(at(7, 8, 30))
    assert not svc.within_working_hours(at(7, 8, 29))


def test_closes_at_the_end_of_the_working_day() -> None:
    svc = service()
    assert svc.within_working_hours(at(7, 18, 59))
    assert not svc.within_working_hours(at(7, 19, 0))


def test_night_is_skipped() -> None:
    svc = service()
    assert not svc.within_working_hours(at(7, 3))
    assert not svc.within_working_hours(at(7, 23))


def test_weekend_is_skipped_entirely() -> None:
    svc = service()
    assert not svc.within_working_hours(at(12, 12))  # Saturday
    assert not svc.within_working_hours(at(13, 12))  # Sunday
    assert svc.within_working_hours(at(11, 12))  # Friday


def test_window_follows_configuration() -> None:
    svc = service(start=9, end=18, lookahead=30)
    assert svc.within_working_hours(at(7, 8, 30))
    assert not svc.within_working_hours(at(7, 8, 29))
    assert not svc.within_working_hours(at(7, 18, 0))


def test_run_cycle_does_nothing_outside_the_window() -> None:
    svc = service()
    svc.within_working_hours = lambda now=None: False

    assert svc.run_cycle() == {"skipped": "outside working hours"}
