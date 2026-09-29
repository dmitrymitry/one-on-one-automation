from app.calendar_client import _to_meeting


def event(*attendees: dict) -> dict:
    return {
        "id": "e-1",
        "summary": "Astra&Vegas / weekly",
        "start": {"dateTime": "2026-09-29T16:00:00+03:00"},
        "end": {"dateTime": "2026-09-29T16:30:00+03:00"},
        "attendees": list(attendees),
    }


HOST = {"email": "host@x", "self": True, "responseStatus": "accepted"}


def test_a_declined_participant_marks_the_meeting_declined() -> None:
    """29.09: Astra's 1:1 had one "declined" and still got a reminder."""
    meeting = _to_meeting(event(HOST, {"email": "pm@x", "responseStatus": "declined"}), "cal")

    assert meeting.declined


def test_the_host_declining_counts_too() -> None:
    host = {**HOST, "responseStatus": "declined"}
    meeting = _to_meeting(event(host, {"email": "pm@x", "responseStatus": "accepted"}), "cal")

    assert meeting.declined


def test_no_answer_yet_is_not_a_decline() -> None:
    meeting = _to_meeting(event(HOST, {"email": "pm@x", "responseStatus": "needsAction"}), "cal")

    assert not meeting.declined


def test_rooms_and_optional_guests_cancel_nothing() -> None:
    room = {"email": "room@x", "resource": True, "responseStatus": "declined"}
    guest = {"email": "guest@x", "optional": True, "responseStatus": "declined"}
    pm = {"email": "pm@x", "responseStatus": "accepted"}

    assert not _to_meeting(event(HOST, pm, room, guest), "cal").declined


def test_event_without_attendees_is_not_declined() -> None:
    assert not _to_meeting(event(), "cal").declined
