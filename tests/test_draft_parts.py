"""A long follow-up goes out as several Telegram messages: every part must stay reachable."""

from types import SimpleNamespace

import httpx
import pytest

from app.meeting_summary import split_for_telegram
from app.service import VegasAutomationService, _text_hash
from app.telegram_client import TelegramClient

NOT_MODIFIED = (
    '{"ok":false,"error_code":400,"description":"Bad Request: message is not modified: '
    'specified new message content and reply markup are exactly the same"}'
)
HEADER = "28.09.26\nIton & Vegas / weekly"
PART_ONE = HEADER + "\n\n1. ПЕРША ТЕМА\nКонтекст: " + " ".join(["перша"] * 450)
PART_TWO = "2. ДРУГА ТЕМА\nКонтекст: " + " ".join(["друга"] * 450)
# Long enough that the draft still needs two messages after the reply.
NEW_PART_TWO = "2. ДРУГА ТЕМА\nВиправлено: " + " ".join(["нове"] * 400)
LONG = f"{PART_ONE}\n\n{PART_TWO}"


class RecordingTelegram:
    def __init__(self, fail_delete: bool = False) -> None:
        self.calls: list[tuple] = []
        self.next_id = 500
        self.fail_delete = fail_delete

    def send_parts(self, chat_id, text, thread_id="", reply_markup=None):
        parts = split_for_telegram(text)
        ids = []
        for index, part in enumerate(parts):
            self.next_id += 1
            ids.append(self.next_id)
            markup = reply_markup if index == len(parts) - 1 else None
            self.calls.append(("send", self.next_id, part, markup))
        return ids

    def send_message(self, chat_id, text, thread_id="", reply_markup=None):
        return self.send_parts(chat_id, text, thread_id, reply_markup)[-1]

    def edit_message(self, chat_id, message_id, text, reply_markup=None):
        self.calls.append(("edit", message_id, text, reply_markup))
        return True

    def edit_reply_markup(self, chat_id, message_id, reply_markup):
        self.calls.append(("markup", message_id, reply_markup))
        return True

    def delete_message(self, chat_id, message_id):
        if self.fail_delete:
            raise RuntimeError("message can't be deleted")
        self.calls.append(("delete", message_id))


def draft(text: str = LONG, ids: str = "11;12", synced: bool = True, **extra) -> dict:
    return {
        "meeting_id": "m-1",
        "title": "Iton & Vegas / weekly",
        "summary_status": "draft",
        "summary_text": text,
        "summary_message_id": ids,
        "summary_synced_hash": _text_hash(text) if synced else "stale",
        **extra,
    }


def make_service(record: dict, telegram) -> tuple[VegasAutomationService, list[dict]]:
    patched: list[dict] = []
    svc = VegasAutomationService.__new__(VegasAutomationService)
    svc.settings = SimpleNamespace(host_telegram_chat_id="1", host_telegram_thread_id="")
    svc.sheets = SimpleNamespace(
        get_meeting=lambda meeting_id: record,
        list_meetings_with_drafts=lambda: [record],
        patch_meeting=lambda meeting_id, changes: patched.append(changes),
        followup_cell_url=lambda meeting_id: "",
    )
    svc.telegram = telegram
    return svc, patched


def test_fixture_really_needs_two_messages() -> None:
    assert split_for_telegram(LONG) == [PART_ONE, PART_TWO]


def test_every_part_of_a_new_draft_is_remembered() -> None:
    telegram = RecordingTelegram()
    svc, _ = make_service(draft(), telegram)

    stored = svc._show_draft("m-1", LONG)

    assert stored["summary_message_id"] == "501;502"


# --- edits made in the sheet ---


def test_sheet_edit_rewrites_each_part_in_place() -> None:
    edited = LONG.replace("друга", "змінена")
    telegram = RecordingTelegram()
    svc, patched = make_service(draft(edited, synced=False), telegram)

    svc.sync_summary_edits()

    edits = [call for call in telegram.calls if call[0] == "edit"]
    # The head no longer lands in the last message: each part goes to its own.
    assert [(call[1], call[2]) for call in edits] == [
        (11, PART_ONE),
        (12, split_for_telegram(edited)[1]),
    ]
    assert edits[0][3] is None and edits[1][3] is not None  # buttons on the last only
    assert patched == [{"summary_synced_hash": _text_hash(edited)}]


def test_sheet_edit_that_changes_the_part_count_reissues_the_draft() -> None:
    telegram = RecordingTelegram()
    svc, patched = make_service(draft(LONG, ids="12", synced=False), telegram)

    svc.sync_summary_edits()

    assert ("delete", 12) in telegram.calls
    assert [call[0] for call in telegram.calls].count("send") == 2
    assert patched[0]["summary_message_id"] == "501;502"


def test_unchanged_message_counts_as_synced(monkeypatch) -> None:
    """The 28.09 prod loop: Telegram said "not modified", the hash never got stored."""
    short = "28.09.26\nIton & Vegas / weekly\n\n1. ТЕМА"
    monkeypatch.setattr(
        httpx, "post", lambda url, json, timeout: httpx.Response(400, text=NOT_MODIFIED)
    )
    client = TelegramClient(SimpleNamespace(telegram_bot_token="123:secret"))
    svc, patched = make_service(draft(short, ids="26", synced=False), client)

    result = svc.sync_summary_edits()

    assert result["failed"] == 0
    assert patched == [{"summary_synced_hash": _text_hash(short)}]


# --- edits made by reply ---


def test_reply_to_the_second_part_replaces_only_that_part() -> None:
    telegram = RecordingTelegram()
    svc, patched = make_service(draft(), telegram)

    result = svc.apply_summary_edit("m-1", NEW_PART_TWO, "12")

    assert result == {"part": 2, "parts": 2}
    assert patched[0]["summary_text"] == f"{PART_ONE}\n\n{NEW_PART_TWO}"
    # Re-issued at the bottom, and both old parts are gone, not just the last.
    assert ("delete", 11) in telegram.calls and ("delete", 12) in telegram.calls
    assert patched[0]["summary_message_id"] == "501;502"


def test_reply_to_the_first_part_still_refuses_another_meeting() -> None:
    svc, patched = make_service(draft(), RecordingTelegram())

    with pytest.raises(ValueError, match="Omega"):
        svc.apply_summary_edit("m-1", "09.09.26\nOmega / Vegas weekly\n\n1. ТЕМА", "11")
    assert patched == []


def test_a_later_part_has_no_header_to_check() -> None:
    svc, patched = make_service(draft(), RecordingTelegram())

    svc.apply_summary_edit("m-1", "2. ДРУГА ТЕМА\nКонтекст: інакше.", "12")

    assert patched


def test_a_whole_follow_up_pasted_into_a_later_part_is_refused() -> None:
    """It would land mid-draft and reach the PM; another meeting's even worse."""
    svc, patched = make_service(draft(), RecordingTelegram())

    with pytest.raises(ValueError, match="частину 2"):
        svc.apply_summary_edit("m-1", "09.09.26\nOmega / Vegas weekly\n\n1. ТЕМА", "12")
    assert patched == []


def test_part_reply_waits_while_a_sheet_edit_is_not_in_the_chat_yet() -> None:
    """Parts are matched by position, so the chat must show the current text."""
    svc, patched = make_service(draft(synced=False), RecordingTelegram())

    with pytest.raises(ValueError, match="Зачекай"):
        svc.apply_summary_edit("m-1", "2. ДРУГА ТЕМА\nВиправлено.", "12")
    assert patched == []


def test_a_draft_sent_before_parts_were_kept_answers_as_its_last_part() -> None:
    svc, patched = make_service(draft(ids="12"), RecordingTelegram())

    result = svc.apply_summary_edit("m-1", "2. ДРУГА ТЕМА\nВиправлено.", "12")

    assert result == {"part": 2, "parts": 2}
    assert patched[0]["summary_text"].startswith(PART_ONE)


def test_short_draft_reply_is_still_the_whole_follow_up() -> None:
    short = f"{HEADER}\n\n1. ТЕМА"
    svc, patched = make_service(draft(short, ids="26"), RecordingTelegram())

    result = svc.apply_summary_edit("m-1", f"{HEADER}\n\n1. НОВА ТЕМА", "26")

    assert result == {"part": 1, "parts": 1}
    assert patched[0]["summary_text"] == f"{HEADER}\n\n1. НОВА ТЕМА"


def test_parts_too_old_to_delete_lose_only_the_last_button() -> None:
    telegram = RecordingTelegram(fail_delete=True)
    svc, _ = make_service(draft(), telegram)

    svc.apply_summary_edit("m-1", "2. ДРУГА ТЕМА\nВиправлено.", "12")

    assert [call for call in telegram.calls if call[0] == "markup"] == [
        ("markup", 12, {"inline_keyboard": []})
    ]


# --- confirm ---


def test_confirm_takes_the_button_off_without_rewriting_the_text() -> None:
    """Rewriting here once put the head of a long follow-up into its last part."""
    telegram = RecordingTelegram()
    svc, _ = make_service(draft(), telegram)

    svc._retire_confirm_button(draft())

    assert telegram.calls == [("markup", 12, {"inline_keyboard": []})]
