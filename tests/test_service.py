from datetime import datetime, timezone
from types import SimpleNamespace

from app.models import CalendarMeeting, Manager
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


def test_follow_up_gets_the_meetings_own_agenda_as_its_checklist() -> None:
    from app.followup import merge_calendar_notes
    from app.models import MeetingSummary

    calls: dict = {}
    svc = VegasAutomationService.__new__(VegasAutomationService)
    svc.settings = SimpleNamespace(
        reminder_followup_count=6, host_telegram_chat_id="", summary_auto_send=False
    )
    svc.sheets = SimpleNamespace(
        get_meeting=lambda meeting_id: {},
        get_recent_followups=lambda manager_id, limit, before="": ["минулий фоллоуап"],
        get_managers=lambda: [],
        patch_meeting=lambda meeting_id, changes: calls.update(patched=changes),
    )

    def summarize(manager, meeting, transcript, previous_followups, agenda=""):
        calls.update(previous=previous_followups, agenda=agenda)
        return MeetingSummary()

    svc.llm = SimpleNamespace(summarize=summarize)
    base = meeting()
    notes = merge_calendar_notes("Meet: https://meet.google.com/abc", "— Запустити чат-бот")
    with_agenda = CalendarMeeting(
        base.meeting_id,
        base.title,
        base.start_at,
        base.end_at,
        base.calendar_id,
        description=notes,
    )
    ksu = Manager("ksu", "Ksu", ("Ksu",))

    svc._prepare_meeting_summary(with_agenda, ksu, "транскрипт")

    assert calls["agenda"] == "— Запустити чат-бот"
    assert calls["previous"] == ["минулий фоллоуап"]
    assert calls["patched"]["summary_status"] == "draft"
