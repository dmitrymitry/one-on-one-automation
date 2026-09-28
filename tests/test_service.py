import logging
from datetime import datetime, timezone
from types import SimpleNamespace

from app.followup import merge_calendar_notes
from app.models import CalendarMeeting, Manager, MeetingSummary
from app.service import VegasAutomationService


def meeting() -> CalendarMeeting:
    start = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
    return CalendarMeeting("event-1", "Vegas & Ksu / Weekly", start, start, "primary")


def make_service(followups_by_manager: dict[str, list[str]]) -> VegasAutomationService:
    svc = VegasAutomationService.__new__(VegasAutomationService)
    svc.sheets = SimpleNamespace(
        get_recent_followups=lambda manager_id, limit, before="": followups_by_manager.get(
            manager_id, []
        )[:limit]
    )
    return svc


def test_gathers_a_promise_made_in_someone_elses_meeting() -> None:
    ksu = Manager("ksu", "Ksu", ("Vegas Ksu", "Ksu Vegas", "Ксю Vegas", "Vegas Ксю"))
    iton = Manager("iton", "Iton", ("Iton",))
    svc = make_service({"iton": ["обіцяв обговорити бюджет з Ксю на наступному дзвінку"]})

    refs = svc._gather_cross_references(ksu, meeting(), [ksu, iton])

    assert refs == [("Iton", "обіцяв обговорити бюджет з Ксю на наступному дзвінку")]


def test_ignores_followups_that_never_mention_this_manager() -> None:
    ksu = Manager("ksu", "Ksu", ("Vegas Ksu", "Ksu Vegas", "Ксю Vegas", "Vegas Ксю"))
    iton = Manager("iton", "Iton", ("Iton",))
    svc = make_service({"iton": ["звичайний статус без згадок"]})

    assert svc._gather_cross_references(ksu, meeting(), [ksu, iton]) == []


def test_never_scans_the_managers_own_followups_as_a_cross_reference() -> None:
    ksu = Manager("ksu", "Ksu", ("Vegas Ksu", "Ksu Vegas", "Ксю Vegas", "Vegas Ксю"))
    # Ksu's own follow-up naturally names her too — that is not a cross-reference.
    svc = make_service({"ksu": ["Ксю попросила надіслати звіт"]})

    assert svc._gather_cross_references(ksu, meeting(), [ksu]) == []


def drafting_service(row: dict, calls: dict) -> VegasAutomationService:
    """Just enough of the service to run _prepare_meeting_summary offline."""
    svc = VegasAutomationService.__new__(VegasAutomationService)
    svc.settings = SimpleNamespace(
        reminder_followup_count=6, host_telegram_chat_id="", summary_auto_send=False
    )
    svc.sheets = SimpleNamespace(
        get_meeting=lambda meeting_id: row,
        get_recent_followups=lambda manager_id, limit, before="": ["минулий фоллоуап"],
        get_managers=lambda: [],
        patch_meeting=lambda meeting_id, changes: calls.update(patched=changes),
    )

    def summarize(manager, meeting, transcript, previous_followups, agenda=""):
        calls.update(previous=previous_followups, agenda=agenda)
        return MeetingSummary()

    svc.llm = SimpleNamespace(summarize=summarize)
    return svc


def meeting_with_notes(description: str) -> CalendarMeeting:
    base = meeting()
    return CalendarMeeting(
        base.meeting_id,
        base.title,
        base.start_at,
        base.end_at,
        base.calendar_id,
        description=description,
    )


def test_follow_up_gets_the_meetings_own_agenda_as_its_checklist() -> None:
    calls: dict = {}
    svc = drafting_service({}, calls)
    notes = merge_calendar_notes("Meet: https://meet.google.com/abc", "— Запустити чат-бот")

    svc._prepare_meeting_summary(meeting_with_notes(notes), Manager("ksu", "Ksu", ()), "текст")

    assert calls["agenda"] == "— Запустити чат-бот"
    assert calls["previous"] == ["минулий фоллоуап"]
    assert calls["patched"]["summary_status"] == "draft"


def test_warns_when_a_synced_agenda_no_longer_reads_back(caplog) -> None:
    # Edited in the Calendar UI, the notes can come back as HTML without our
    # marker: the follow-up then silently loses its main checklist.
    calls: dict = {}
    svc = drafting_service({"calendar_notes_synced_hash": "abc123"}, calls)

    with caplog.at_level(logging.WARNING, logger="app.service"):
        svc._prepare_meeting_summary(
            meeting_with_notes("<p>Meet</p><br>ПОРЯДОК ДЕННИЙ"), Manager("ksu", "Ksu", ()), "т"
        )

    assert calls["agenda"] == ""
    assert "Agenda block not found" in caplog.text


def test_no_warning_when_no_agenda_was_ever_written(caplog) -> None:
    calls: dict = {}
    svc = drafting_service({}, calls)

    with caplog.at_level(logging.WARNING, logger="app.service"):
        svc._prepare_meeting_summary(meeting_with_notes(""), Manager("ksu", "Ksu", ()), "т")

    assert "Agenda block not found" not in caplog.text
