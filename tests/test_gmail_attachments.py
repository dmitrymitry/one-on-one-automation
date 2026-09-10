import base64
from datetime import datetime, timezone

from app.gmail_client import _decode_body, _is_text_attachment, transcript_matches_meeting
from app.models import Manager, Transcript


def test_text_attachment_detection_includes_octet_stream_txt() -> None:
    assert _is_text_attachment("meeting-transcript.txt", "application/octet-stream")
    assert not _is_text_attachment("recording.mp4", "video/mp4")


def test_urlsafe_body_decode_handles_missing_padding() -> None:
    encoded = base64.urlsafe_b64encode("Повний транскрипт".encode()).decode().rstrip("=")

    assert _decode_body(encoded) == "Повний транскрипт"


def test_transcript_must_match_manager_and_vegas_metadata() -> None:
    manager = Manager("gamma", "Gamma", ("Vegas Gamma", "Gamma Vegas"))
    transcript = Transcript(
        message_id="message",
        subject="Транскрипт зустрічі: Meet - Vegas & Gamma / Weekly",
        received_at=datetime.now(timezone.utc),
        text="full transcript",
        attachment_names=("Vegas_&_Gamma_transcript.txt",),
    )

    assert transcript_matches_meeting(transcript, manager, ["Vegas"])


def test_unrelated_transcript_is_rejected_even_when_close_in_time() -> None:
    manager = Manager("gamma", "Gamma", ("Vegas Gamma", "Gamma Vegas"))
    transcript = Transcript(
        message_id="message",
        subject="Транскрипт зустрічі: Meet - Netpeak Aura / Weekly Status",
        received_at=datetime.now(timezone.utc),
        text="Gamma may be mentioned inside the body, but metadata does not match",
        attachment_names=("Netpeak_Aura_Weekly_transcript.txt",),
    )

    assert not transcript_matches_meeting(transcript, manager, ["Vegas"])
