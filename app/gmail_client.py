import base64
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr

from bs4 import BeautifulSoup
from googleapiclient.discovery import build

from .config import Settings
from .meeting_matcher import normalize_text
from .models import CalendarMeeting, Manager, Transcript


class GmailTranscriptClient:
    def __init__(self, credentials, settings: Settings):
        self.settings = settings
        self.service = build("gmail", "v1", credentials=credentials, cache_discovery=False)

    def find_transcript(self, meeting: CalendarMeeting, manager: Manager) -> Transcript | None:
        query = self._build_query(meeting, manager)
        response = (
            self.service.users()
            .messages()
            .list(userId=self.settings.google_user_id, q=query, maxResults=50)
            .execute()
        )
        candidates = [self._load_message(item["id"]) for item in response.get("messages", [])]
        candidates = [
            candidate
            for candidate in candidates
            if candidate.text.strip()
            and transcript_matches_meeting(
                candidate,
                manager,
                self.settings.calendar_keyword_list,
            )
        ]
        return max(candidates, key=lambda item: self._score(item, meeting, manager), default=None)

    def _build_query(self, meeting: CalendarMeeting, manager: Manager) -> str:
        after = (meeting.start_at.date() - timedelta(days=1)).isoformat()
        before = (meeting.end_at.date() + timedelta(days=2)).isoformat()
        custom = self.settings.gmail_transcript_query.strip()
        if custom:
            query = custom.format(
                after=after, before=before, manager=manager.manager_name, title=meeting.title
            )
        else:
            query = f"after:{after} before:{before}"
        sender = manager.transcript_sender or self.settings.gmail_transcript_sender
        if sender:
            query += f" from:{sender}"
        if self.settings.gmail_transcript_label:
            query += f" label:{self.settings.gmail_transcript_label}"
        return query

    def _load_message(self, message_id: str) -> Transcript:
        message = (
            self.service.users()
            .messages()
            .get(userId=self.settings.google_user_id, id=message_id, format="full")
            .execute()
        )
        payload = message.get("payload", {})
        headers = {
            item["name"].lower(): item.get("value", "") for item in payload.get("headers", [])
        }
        text, attachment_names = self._extract_text(message_id, payload)
        received_at = datetime.fromtimestamp(
            int(message.get("internalDate", "0")) / 1000,
            tz=timezone.utc,
        )
        return Transcript(
            message_id=message_id,
            subject=headers.get("subject", ""),
            received_at=received_at,
            text=text,
            sender=parseaddr(headers.get("from", ""))[1],
            attachment_names=attachment_names,
        )

    def _extract_text(self, message_id: str, payload: dict) -> tuple[str, tuple[str, ...]]:
        inline: list[str] = []
        attachments: list[str] = []
        attachment_names: list[str] = []
        self._collect_parts(message_id, payload, inline, attachments, attachment_names)
        fragments = attachments or inline
        text = "\n\n".join(fragment for fragment in fragments if fragment.strip()).strip()
        return text, tuple(attachment_names)

    def _collect_parts(
        self,
        message_id: str,
        part: dict,
        inline: list[str],
        attachments: list[str],
        attachment_names: list[str],
    ) -> None:
        mime_type = part.get("mimeType", "")
        filename = part.get("filename", "")
        body = part.get("body", {})
        data = body.get("data")
        attachment_id = body.get("attachmentId")
        is_text_attachment = _is_text_attachment(filename, mime_type)

        if attachment_id and is_text_attachment:
            response = (
                self.service.users()
                .messages()
                .attachments()
                .get(
                    userId=self.settings.google_user_id,
                    messageId=message_id,
                    id=attachment_id,
                )
                .execute()
            )
            attachment_data = response.get("data", "")
            if attachment_data:
                attachments.append(_decode_body(attachment_data))
                attachment_names.append(filename)
        elif data and is_text_attachment:
            decoded = _decode_body(data)
            target = attachments if filename else inline
            target.append(_html_to_text(decoded) if mime_type == "text/html" else decoded)
            if filename:
                attachment_names.append(filename)

        for child in part.get("parts", []):
            self._collect_parts(message_id, child, inline, attachments, attachment_names)

    def _score(self, transcript: Transcript, meeting: CalendarMeeting, manager: Manager) -> int:
        metadata = normalize_text(" ".join((transcript.subject, *transcript.attachment_names)))
        title_tokens = set(normalize_text(meeting.title).split())
        overlap = len(title_tokens.intersection(metadata.split()))
        hours_away = abs((transcript.received_at - meeting.end_at).total_seconds()) / 3600
        return overlap * 20 - int(min(hours_away, 240))


def _is_text_attachment(filename: str, mime_type: str) -> bool:
    suffix = filename.casefold().rsplit(".", 1)[-1] if "." in filename else ""
    return mime_type in {"text/plain", "text/html", "application/json"} or suffix in {
        "txt",
        "md",
        "json",
        "csv",
    }


def _decode_body(data: str) -> str:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding).decode("utf-8", errors="replace")


def _html_to_text(value: str) -> str:
    return BeautifulSoup(value, "html.parser").get_text("\n", strip=True)


def transcript_matches_meeting(
    transcript: Transcript,
    manager: Manager,
    meeting_keywords: list[str],
) -> bool:
    metadata = normalize_text(" ".join((transcript.subject, *transcript.attachment_names)))
    if not any(_contains_phrase(metadata, keyword) for keyword in meeting_keywords):
        return False
    return any(_contains_phrase(metadata, alias) for alias in manager.aliases)


def _contains_phrase(normalized_text: str, phrase: str) -> bool:
    normalized_phrase = normalize_text(phrase)
    return bool(normalized_phrase and f" {normalized_phrase} " in f" {normalized_text} ")
