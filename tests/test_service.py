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


def reminder_service(monkeypatch, skip_declined: bool, *meetings: CalendarMeeting):
    """send_followups over the given meetings, recording which ones got a reminder."""
    import app.service as service_module

    ksu = Manager("ksu", "Ksu", ("Ksu",))
    svc = VegasAutomationService.__new__(VegasAutomationService)
    svc.settings = SimpleNamespace(
        skip_declined_meetings=skip_declined,
        reminder_lookahead_hours=24,
        app_timezone="Europe/Kyiv",
        calendar_keyword_list=[],
    )
    svc.calendar = SimpleNamespace(list_events=lambda start, end: list(meetings))
    svc.sheets = SimpleNamespace(get_managers=lambda: [ksu])
    monkeypatch.setattr(service_module, "match_manager", lambda meeting, managers, kw: ksu)
    reminded: list[str] = []
    svc._send_one_followup = lambda meeting, manager, managers: (
        reminded.append(meeting.meeting_id) or True
    )
    return svc, reminded


def meeting_declined(meeting_id: str, declined: bool) -> CalendarMeeting:
    base = meeting()
    return CalendarMeeting(
        meeting_id, base.title, base.start_at, base.end_at, base.calendar_id, declined=declined
    )


def test_a_declined_meeting_gets_no_reminder(monkeypatch) -> None:
    svc, reminded = reminder_service(
        monkeypatch, True, meeting_declined("off", True), meeting_declined("on", False)
    )

    result = svc.send_followups()

    assert reminded == ["on"]
    assert result["skipped"] == 1 and result["sent"] == 1


def test_declined_meetings_still_remind_when_the_setting_is_off(monkeypatch) -> None:
    svc, reminded = reminder_service(monkeypatch, False, meeting_declined("off", True))

    svc.send_followups()

    assert reminded == ["off"]


def alerting_service(row: dict, sent: list, host_chat: str = "42") -> VegasAutomationService:
    """Drafting that always fails the way Gemini did on 28.10: out of credits."""
    from google.genai import errors

    svc = drafting_service(row, {})
    svc.settings = SimpleNamespace(
        reminder_followup_count=6,
        summary_auto_send=False,
        host_telegram_chat_id=host_chat,
        host_telegram_thread_id="",
        app_timezone="Europe/Kyiv",
        calendar_lookback_hours=48,
    )
    svc.sheets.patch_meeting = lambda meeting_id, changes: row.update(changes)
    svc.telegram = SimpleNamespace(send_message=lambda chat, text, thread="": sent.append(text))

    def summarize(*args, **kwargs):
        raise errors.ClientError(402, {"error": {"code": 402, "message": "m", "status": "S"}})

    svc.llm = SimpleNamespace(summarize=summarize)
    return svc


def test_a_failed_draft_alerts_the_host_once_across_retries() -> None:
    row: dict = {}
    sent: list[str] = []
    svc = alerting_service(row, sent)
    ksu = Manager("ksu", "Ksu", ())

    svc._prepare_meeting_summary(meeting(), ksu, "т")
    svc._prepare_meeting_summary(meeting(), ksu, "т")  # the next cycle's retry

    assert sent == [
        "Чернетку фоллоуапу не складено: Vegas & Ksu / Weekly, 22.09\n"
        "Причина: Gemini 402 — скінчились кредити на ключі\n"
        "Бот повторює спробу сам ще 48 год після зустрічі. Про цю зустріч більше не пишу."
    ]
    assert row["failure_alerts"] == "summary"
    assert row["manager_id"] == "ksu"


def test_a_failed_reminder_gets_its_own_alert(monkeypatch) -> None:
    row = {"failure_alerts": "summary"}
    sent: list[str] = []
    svc, _ = reminder_service(monkeypatch, True, meeting_declined("event-1", False))
    alerting = alerting_service(row, sent)
    svc.settings.host_telegram_chat_id = "42"
    svc.settings.host_telegram_thread_id = ""
    svc.settings.calendar_lookback_hours = 48
    svc.sheets.get_meeting = alerting.sheets.get_meeting
    svc.sheets.patch_meeting = alerting.sheets.patch_meeting
    svc.telegram = alerting.telegram

    def fail(meeting, manager, managers):
        raise RuntimeError("Telegram is down")

    svc._send_one_followup = fail

    result = svc.send_followups()

    assert result["failed"] == 1
    assert sent[0].splitlines()[:2] == [
        "Нагадування не надіслано: Vegas & Ksu / Weekly, 22.09",
        "Причина: RuntimeError: Telegram is down",
    ]
    assert row["failure_alerts"] == "summary;reminder"


def test_no_alert_without_a_host_chat() -> None:
    row: dict = {}
    sent: list[str] = []
    svc = alerting_service(row, sent, host_chat="")

    svc._prepare_meeting_summary(meeting(), Manager("ksu", "Ksu", ()), "т")

    assert sent == [] and "failure_alerts" not in row
