import pytest

from app.sheets_store import GoogleSheetsStore


def make_store(rows: list[dict]) -> GoogleSheetsStore:
    """A store whose Meetings tab is the given rows, without touching Google."""
    store = GoogleSheetsStore.__new__(GoogleSheetsStore)
    records = [{"row_number": index, "values": row} for index, row in enumerate(rows, start=2)]
    store._records = lambda tab: records if tab == "Meetings" else []
    return store


def meeting(manager_id: str, start_at: str, text: str) -> dict:
    return {
        "meeting_id": f"{manager_id}-{start_at}",
        "manager_id": manager_id,
        "start_at": start_at,
        "summary_text": text,
    }


def test_followups_are_scoped_to_one_manager() -> None:
    store = make_store(
        [
            meeting("beta", "2026-07-27T15:58:00+03:00", "beta 27.07"),
            meeting("alpha", "2026-07-28T10:00:00+03:00", "alpha 28.07"),
            meeting("beta", "2026-07-20T15:58:00+03:00", "beta 20.07"),
        ]
    )

    assert store.get_recent_followups("beta", 5) == ["beta 27.07", "beta 20.07"]
    assert store.get_recent_followups("alpha", 5) == ["alpha 28.07"]


def test_followups_come_back_newest_first_and_capped() -> None:
    store = make_store(
        [
            meeting("beta", f"2026-07-{day:02d}T15:58:00+03:00", f"beta {day}")
            for day in (6, 27, 13, 20)
        ]
    )

    assert store.get_recent_followups("beta", 3) == ["beta 27", "beta 20", "beta 13"]


def test_only_followups_before_the_upcoming_meeting_are_used() -> None:
    store = make_store(
        [
            meeting("beta", "2026-07-27T15:58:00+03:00", "beta 27.07"),
            # A later meeting that already has a follow-up must not leak backwards.
            meeting("beta", "2026-08-10T15:58:00+03:00", "beta 10.08"),
        ]
    )

    assert store.get_recent_followups("beta", 5, before="2026-08-03T15:58:00+03:00") == [
        "beta 27.07"
    ]


def test_meetings_without_a_followup_are_skipped() -> None:
    store = make_store(
        [
            meeting("beta", "2026-07-27T15:58:00+03:00", "beta 27.07"),
            meeting("beta", "2026-07-20T15:58:00+03:00", "   "),
        ]
    )

    assert store.get_recent_followups("beta", 5) == ["beta 27.07"]


def test_seven_meetings_still_return_only_the_newest_three() -> None:
    store = make_store(
        [
            meeting("beta", f"2026-0{6 + day // 30}-{(day % 30) + 1:02d}T15:58:00+03:00", f"s{day}")
            for day in range(7)
        ]
        + [meeting("delta", "2026-07-01T10:00:00+03:00", "delta")]
    )

    followups = store.get_recent_followups("beta", 3)

    assert len(followups) == 3
    assert "delta" not in followups


def test_zero_host_tasks_does_not_mark_meeting_as_scheduled() -> None:
    """A confirm that files nothing must leave the door open for a later confirm."""
    from types import SimpleNamespace

    from app.service import VegasAutomationService

    upserts: list[dict] = []
    sheets = SimpleNamespace(patch_meeting=lambda mid, changes: upserts.append(changes))
    llm = SimpleNamespace(extract_host_tasks=lambda *_: [])
    svc = VegasAutomationService.__new__(VegasAutomationService)
    svc.settings = SimpleNamespace(host_name_list=["Олег Ткаченко"])
    svc.sheets, svc.llm = sheets, llm

    result = svc.schedule_host_tasks(
        {"meeting_id": "m-1", "start_at": "2026-07-27T15:58:00+03:00"}, "текст без моїх задач"
    )

    assert result["status"] == "nothing"
    assert upserts == []


class BindableStore(GoogleSheetsStore):
    """A store with a Managers tab in memory and a writable row."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.written: list[tuple[int, list]] = []

    def _records(self, tab: str):
        if tab != "Managers":
            return []
        return [{"row_number": i, "values": r} for i, r in enumerate(self.rows, start=2)]

    def _write_range(self, tab: str, row_number: int, values: list) -> None:
        self.written.append((row_number, values))


def manager_row(manager_id: str, aliases: str = "", chat_id: str = "") -> dict:
    return {
        "manager_id": manager_id,
        "manager_name": manager_id.capitalize(),
        "aliases": aliases,
        "telegram_chat_id": chat_id,
        "telegram_thread_id": "",
        "calendar_id": "",
        "transcript_sender": "",
        "timezone": "",
        "active": "true",
    }


def test_bind_by_id_writes_the_chat_into_the_row() -> None:
    store = BindableStore([manager_row("beta", aliases="Beta,Бета")])

    status, name = store.bind_manager_chat("beta", "777")

    assert (status, name) == ("bound", "Beta")
    row_number, values = store.written[0]
    assert row_number == 2
    assert values[3] == "777"  # telegram_chat_id column


def test_bind_by_alias_and_at_prefix_are_case_insensitive() -> None:
    store = BindableStore([manager_row("beta", aliases="Beta,Бета")])

    assert store.bind_manager_chat("БЕТА", "777")[0] == "bound"


def test_bind_never_overwrites_another_chat() -> None:
    store = BindableStore([manager_row("beta", chat_id="111")])

    assert store.bind_manager_chat("beta", "777") == ("taken", "Beta")
    assert store.written == []


def test_bind_is_idempotent_for_the_same_chat() -> None:
    store = BindableStore([manager_row("beta", chat_id="777")])

    assert store.bind_manager_chat("beta", "777") == ("already_bound", "Beta")
    assert store.written == []


def test_bind_unknown_handle() -> None:
    store = BindableStore([manager_row("beta")])

    assert store.bind_manager_chat("nobody", "777") == ("unknown", "")
    assert store.bind_manager_chat("", "777") == ("unknown", "")


def test_edit_from_another_meeting_is_refused() -> None:
    """Re-issuing moves drafts around; replying to the wrong one must not leak."""
    from types import SimpleNamespace

    from app.service import VegasAutomationService

    svc = VegasAutomationService.__new__(VegasAutomationService)
    svc.sheets = SimpleNamespace(
        get_meeting=lambda _: {"meeting_id": "m-1", "title": "Delta&Vegas / weekly"},
        patch_meeting=lambda *_: pytest.fail("must not write a foreign follow-up"),
    )
    svc.settings = SimpleNamespace(host_telegram_chat_id="1", host_telegram_thread_id="")

    foreign = "09.09.26\nOmega / Vegas weekly\n\nПідсумок зустрічі\n— щось"
    with pytest.raises(ValueError, match="Omega"):
        svc.apply_summary_edit("m-1", foreign)


def test_edit_for_the_same_meeting_passes_the_guard() -> None:
    from types import SimpleNamespace

    from app.service import VegasAutomationService

    written: list[dict] = []
    svc = VegasAutomationService.__new__(VegasAutomationService)
    svc.sheets = SimpleNamespace(
        get_meeting=lambda _: {"meeting_id": "m-1", "title": "Delta&Vegas / weekly"},
        patch_meeting=lambda mid, changes: written.append(changes),
    )
    svc.settings = SimpleNamespace(host_telegram_chat_id="", host_telegram_thread_id="")

    own = "09.09.26\nDelta&Vegas / weekly\n\nПідсумок зустрічі\n— щось"
    svc.apply_summary_edit("m-1", own)

    assert written and written[0]["summary_text"] == own


def test_guard_stays_out_of_the_way_when_there_is_no_header() -> None:
    """A one-line note is not a cross-meeting paste; do not block it."""
    from types import SimpleNamespace

    from app.service import VegasAutomationService

    written: list[dict] = []
    svc = VegasAutomationService.__new__(VegasAutomationService)
    svc.sheets = SimpleNamespace(
        get_meeting=lambda _: {"meeting_id": "m-1", "title": "Delta&Vegas / weekly"},
        patch_meeting=lambda mid, changes: written.append(changes),
    )
    svc.settings = SimpleNamespace(host_telegram_chat_id="", host_telegram_thread_id="")

    svc.apply_summary_edit("m-1", "просто один рядок")

    assert written


def test_meeting_without_a_draft_is_retried() -> None:
    """Drafting can fail on an LLM quota error after the transcript is stored."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.service import VegasAutomationService

    svc = VegasAutomationService.__new__(VegasAutomationService)
    looked_up: list[str] = []
    svc.sheets = SimpleNamespace(
        get_meeting=lambda _: {"analysis_status": "completed", "summary_text": ""},
        patch_meeting=lambda *_: None,
    )
    svc.gmail = SimpleNamespace(find_transcript=lambda m, _: looked_up.append(m.meeting_id))
    svc.settings = SimpleNamespace(llm_max_transcript_chars=60000)
    when = datetime(2026, 9, 8, 13, 0, tzinfo=timezone.utc)
    meeting = SimpleNamespace(meeting_id="m-1", title="T", start_at=when, end_at=when)
    manager = SimpleNamespace(manager_id="beta")

    # Falls through the early return and reaches the transcript lookup.
    assert svc._process_one_transcript(meeting, manager) is False
    assert looked_up == ["m-1"]


def test_meeting_with_a_draft_is_not_reprocessed() -> None:
    from types import SimpleNamespace

    from app.service import VegasAutomationService

    svc = VegasAutomationService.__new__(VegasAutomationService)
    svc.sheets = SimpleNamespace(
        get_meeting=lambda _: {"analysis_status": "completed", "summary_text": "готовий текст"},
    )
    meeting = SimpleNamespace(meeting_id="m-1")
    manager = SimpleNamespace(manager_id="beta")

    assert svc._process_one_transcript(meeting, manager) is True


def _in_memory_store(row: dict) -> "GoogleSheetsStore":
    """A real GoogleSheetsStore whose sheet I/O is a single in-memory row."""
    from app.sheets_store import GoogleSheetsStore

    store = GoogleSheetsStore.__new__(GoogleSheetsStore)
    state = {"row": dict(row)}

    def _records(tab):
        return [{"row_number": 2, "values": dict(state["row"])}]

    def _write_cells(tab, row_number, changes):
        # Model per-cell writes: only the named fields land, exactly as the
        # Sheets batchUpdate does — nothing else in the row is touched.
        state["row"].update(changes)

    store._records = _records
    store._write_cells = _write_cells
    store._append_row = lambda tab, mapping: state.update(row=dict(mapping))
    store._state = state
    return store


def test_patch_touches_only_named_fields() -> None:
    """A writer of one field must merge onto the row's current other fields."""
    store = _in_memory_store({"meeting_id": "m-1", "summary_status": "draft", "summary_text": "T"})

    store.patch_meeting("m-1", {"summary_synced_hash": "abc"})

    assert store._state["row"]["summary_status"] == "draft"
    assert store._state["row"]["summary_text"] == "T"
    assert store._state["row"]["summary_synced_hash"] == "abc"


def test_a_stale_field_writer_cannot_revert_a_fresh_status() -> None:
    """The Omega race: confirm sets sent, a later cycle patches a hash.

    Because the hash writer names only its own field, patch_meeting re-reads
    the row and keeps the sent status a confirm wrote in between.
    """
    store = _in_memory_store({"meeting_id": "m-1", "summary_status": "draft"})

    # A background cycle read the row as draft here (its snapshot is stale)...
    store.patch_meeting("m-1", {"summary_status": "sent"})  # ...then a confirm lands
    store.patch_meeting("m-1", {"summary_synced_hash": "h"})  # ...then the cycle writes

    assert store._state["row"]["summary_status"] == "sent"


def test_patch_creates_the_row_when_missing() -> None:
    store = _in_memory_store({"meeting_id": "other"})

    store.patch_meeting("m-new", {"analysis_status": "completed"})

    assert store._state["row"]["meeting_id"] == "m-new"
    assert store._state["row"]["analysis_status"] == "completed"


def test_write_cells_emits_only_the_changed_columns() -> None:
    """The batchUpdate must address just the patched fields, by column letter."""
    from types import SimpleNamespace

    from app.sheets_store import SCHEMA, GoogleSheetsStore, _column_name

    store = GoogleSheetsStore.__new__(GoogleSheetsStore)
    captured = {}

    class _Values:
        def batchUpdate(self, spreadsheetId, body):
            captured["body"] = body
            return SimpleNamespace(execute=lambda: None)

    store.service = SimpleNamespace(spreadsheets=lambda: SimpleNamespace(values=lambda: _Values()))
    store.settings = SimpleNamespace(google_sheet_id="sheet")

    store._write_cells("Meetings", 5, {"summary_status": "sent"})

    col = _column_name(SCHEMA["Meetings"].index("summary_status") + 1)
    assert captured["body"]["data"] == [{"range": f"Meetings!{col}5", "values": [["sent"]]}]
