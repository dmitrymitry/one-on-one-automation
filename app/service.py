import hashlib
import logging
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from zoneinfo import ZoneInfo

from .calendar_client import GoogleCalendarClient
from .config import Settings
from .followup import build_participant_reminder, build_reminder
from .gmail_client import GmailTranscriptClient
from .llm_analyzer import LLMAnalyzer
from .meeting_matcher import match_manager, normalize_text
from .meeting_summary import build_meeting_summary, summary_recipients
from .models import CalendarMeeting, Manager
from .sheets_store import GoogleSheetsStore
from .telegram_client import TelegramClient

LOGGER = logging.getLogger(__name__)


class VegasAutomationService:
    def __init__(
        self,
        settings: Settings,
        calendar: GoogleCalendarClient,
        gmail: GmailTranscriptClient,
        sheets: GoogleSheetsStore,
        llm: LLMAnalyzer,
        telegram: TelegramClient,
    ):
        self.settings = settings
        self.calendar = calendar
        self.gmail = gmail
        self.sheets = sheets
        self.llm = llm
        self.telegram = telegram
        self.lock = Lock()

    def run_cycle(self) -> dict:
        if not self.within_working_hours():
            return {"skipped": "outside working hours"}
        with self.lock:
            transcript_result = self.process_transcripts()
            followup_result = self.send_followups()
            sync_result = self.sync_summary_edits()
        return {
            "transcripts": transcript_result,
            "followups": followup_result,
            "summary_sync": sync_result,
        }

    def within_working_hours(self, now: datetime | None = None) -> bool:
        """Weekdays only, and only while 1:1s can happen.

        The window opens `calendar_lookahead_minutes` before the working day so a
        meeting at the very start of it still gets its 90-minute reminder.
        """
        now = now or datetime.now(ZoneInfo(self.settings.app_timezone))
        if now.weekday() >= 5:  # Saturday, Sunday
            return False
        opens = now.replace(
            hour=self.settings.work_hours_start, minute=0, second=0, microsecond=0
        ) - timedelta(minutes=self.settings.calendar_lookahead_minutes)
        closes = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            hours=self.settings.work_hours_end
        )
        return opens <= now < closes

    def process_transcripts(self) -> dict:
        now = datetime.now(timezone.utc)
        events = self.calendar.list_events(
            now - timedelta(hours=self.settings.calendar_lookback_hours), now
        )
        managers = self.sheets.get_managers()
        summary = {"seen": 0, "processed": 0, "waiting_for_transcript": 0, "failed": 0}
        for meeting in events:
            manager = match_manager(meeting, managers, self.settings.calendar_keyword_list)
            if not manager:
                continue
            summary["seen"] += 1
            if self._process_one_transcript(meeting, manager):
                summary["processed"] += 1
            else:
                summary["waiting_for_transcript"] += 1
        return summary

    def send_followups(self) -> dict:
        now = datetime.now(timezone.utc)
        events = self.calendar.list_events(
            now, now + timedelta(minutes=self.settings.calendar_lookahead_minutes)
        )
        managers = self.sheets.get_managers()
        summary = {"seen": 0, "sent": 0, "skipped": 0, "failed": 0}
        for meeting in events:
            manager = match_manager(meeting, managers, self.settings.calendar_keyword_list)
            if not manager:
                continue
            summary["seen"] += 1
            try:
                if self._send_one_followup(meeting, manager):
                    summary["sent"] += 1
                else:
                    summary["skipped"] += 1
            except Exception:
                summary["failed"] += 1
                LOGGER.exception("Could not send follow-up for meeting %s", meeting.meeting_id)
        return summary

    def _process_one_transcript(self, meeting: CalendarMeeting, manager: Manager) -> bool:
        current = self.sheets.get_meeting(meeting.meeting_id) or {}
        # "completed" alone is not enough: drafting can fail after the transcript
        # is stored (an LLM quota error, say), and that must stay retryable
        # instead of leaving the meeting permanently without a follow-up.
        if current.get("analysis_status") == "completed" and current.get("summary_text"):
            return True
        base_record = _meeting_record(meeting, manager)
        # Seed the row's identity (stable fields); patch_meeting creates it if
        # missing and leaves any concurrently-written state untouched.
        self.sheets.patch_meeting(meeting.meeting_id, base_record)
        try:
            transcript = self.gmail.find_transcript(meeting, manager)
            if not transcript:
                return False
            transcript_text = transcript.text[: self.settings.llm_max_transcript_chars]
            self.sheets.patch_meeting(
                meeting.meeting_id,
                {
                    "transcript_message_id": transcript.message_id,
                    "transcript_received_at": transcript.received_at.isoformat(),
                    "analysis_status": "completed",
                    "analysis_error": "",
                    "processed_at": _now_iso(),
                },
            )
            self._prepare_meeting_summary(meeting, manager, transcript_text)
            return True
        except Exception as exc:
            self.sheets.patch_meeting(
                meeting.meeting_id,
                {"analysis_status": "failed", "analysis_error": str(exc)[:1000]},
            )
            LOGGER.exception("Could not process transcript for meeting %s", meeting.meeting_id)
            return False

    def _prepare_meeting_summary(
        self,
        meeting: CalendarMeeting,
        manager: Manager,
        transcript_text: str,
    ) -> None:
        """Draft the follow-up and park it for review. Sending stays manual."""
        # Read status fresh: a draft may have been made since the transcript was
        # stored, and re-drafting would drop the host's edits.
        current = self.sheets.get_meeting(meeting.meeting_id) or {}
        if current.get("summary_status") in {"draft", "sent"}:
            return
        try:
            summary = self.llm.summarize(manager, meeting, transcript_text)
            text = build_meeting_summary(meeting, summary)
            recipients = summary_recipients(manager, self.sheets.get_managers(), summary)
            changes = {
                "summary_status": "draft",
                "summary_text": text,
                "summary_recipients": ",".join(item.manager_id for item in recipients),
            }
            changes.update(self._show_draft(meeting.meeting_id, text))
            self.sheets.patch_meeting(meeting.meeting_id, changes)
            if self.settings.summary_auto_send:
                self.send_meeting_summary(meeting.meeting_id)
        except Exception:
            LOGGER.exception("Could not draft meeting summary for %s", meeting.meeting_id)

    def _show_draft(self, meeting_id: str, text: str) -> dict[str, str]:
        """Put the draft in front of the host, with both ways to edit it."""
        chat_id = self.settings.host_telegram_chat_id
        if not chat_id:
            return {}
        message_id = self.telegram.send_message(
            chat_id,
            text,
            self.settings.host_telegram_thread_id,
            reply_markup=_draft_keyboard(meeting_id),
        )
        url = self.sheets.followup_cell_url(meeting_id)
        if url:
            self.telegram.send_message(
                chat_id,
                f"Чернетка вище. Правити — відповіддю на неї новим текстом, або в таблиці:\n{url}",
                self.settings.host_telegram_thread_id,
            )
        return {"summary_message_id": str(message_id), "summary_synced_hash": _text_hash(text)}

    def sync_summary_edits(self) -> dict:
        """Push follow-up edits made in the sheet onto the Telegram message."""
        chat_id = self.settings.host_telegram_chat_id
        summary = {"checked": 0, "updated": 0, "failed": 0}
        if not chat_id:
            return summary
        for record in self.sheets.list_meetings_with_drafts():
            message_id = record.get("summary_message_id", "").strip()
            text = record.get("summary_text", "").strip()
            if not message_id or not text:
                continue
            summary["checked"] += 1
            if record.get("summary_synced_hash") == _text_hash(text):
                continue
            try:
                self.telegram.edit_message(
                    chat_id,
                    int(message_id),
                    text,
                    reply_markup=_draft_keyboard(
                        record["meeting_id"], sent=record.get("summary_status") == "sent"
                    ),
                )
                self.sheets.patch_meeting(
                    record["meeting_id"], {"summary_synced_hash": _text_hash(text)}
                )
                summary["updated"] += 1
            except Exception:
                summary["failed"] += 1
                LOGGER.exception("Could not sync summary edit for %s", record.get("meeting_id"))
        return summary

    def apply_summary_edit(self, meeting_id: str, text: str) -> None:
        """Store a follow-up rewritten by reply and re-issue the draft at the bottom.

        The edited draft is posted as a new message, and the old one removed, so
        the current version with its button always sits next to the input box
        and the next reply lands on it.
        """
        record = self.sheets.get_meeting(meeting_id)
        if not record:
            raise ValueError(f"Unknown meeting {meeting_id}")
        text = text.strip()
        if not text:
            raise ValueError("Empty follow-up text")
        _reject_foreign_followup(record, text)
        changes = {"summary_text": text, "summary_synced_hash": _text_hash(text)}
        chat_id = self.settings.host_telegram_chat_id
        old_id = record.get("summary_message_id", "").strip()
        if chat_id:
            new_id = self.telegram.send_message(
                chat_id,
                text,
                self.settings.host_telegram_thread_id,
                reply_markup=_draft_keyboard(
                    meeting_id, sent=record.get("summary_status") == "sent"
                ),
            )
            changes["summary_message_id"] = str(new_id)
            if old_id:
                try:
                    self.telegram.delete_message(chat_id, int(old_id))
                except Exception:
                    # Past Telegram's 48h delete window: strip its button instead.
                    LOGGER.warning("Could not delete old draft %s, removing its button", old_id)
                    try:
                        self.telegram.edit_message(
                            chat_id,
                            int(old_id),
                            record.get("summary_text", text),
                            reply_markup={"inline_keyboard": []},
                        )
                    except Exception:
                        LOGGER.exception("Could not neutralise old draft %s", old_id)
        self.sheets.patch_meeting(meeting_id, changes)

    def send_meeting_summary(self, meeting_id: str) -> dict:
        """Dispatch a drafted follow-up after a human has validated it."""
        record = self.sheets.get_meeting(meeting_id)
        if not record:
            raise ValueError(f"Unknown meeting {meeting_id}")
        if record.get("summary_status") == "sent":
            # Already delivered. Still worth a pass over the calendar: the first
            # confirm may have run before the calendar scope was granted.
            calendar = self.schedule_host_tasks(record, record.get("summary_text", ""))
            return {
                "meeting_id": meeting_id,
                "meeting_date": _short_date(record.get("start_at", "")),
                "status": "already_sent",
                "delivered": [],
                "calendar": calendar,
            }
        text = record.get("summary_text", "").strip()
        if not text:
            raise ValueError(f"Meeting {meeting_id} has no drafted summary")

        wanted = [item for item in record.get("summary_recipients", "").split(",") if item]
        by_id = {manager.manager_id: manager for manager in self.sheets.get_managers()}
        delivered, failed = [], []
        for manager_id in wanted:
            manager = by_id.get(manager_id)
            if not manager or not manager.telegram_chat_id:
                failed.append(manager_id)
                continue
            try:
                self.telegram.send_followup(manager, text)
                delivered.append(manager_id)
            except Exception:
                failed.append(manager_id)
                LOGGER.exception("Could not deliver summary %s to %s", meeting_id, manager_id)

        if delivered:
            self.sheets.patch_meeting(
                meeting_id,
                {
                    "summary_status": "sent",
                    "summary_recipients": ",".join(delivered),
                    "summary_sent_at": _now_iso(),
                },
            )
            self._retire_confirm_button(record, text)
        # Confirm means "validated": the host's own tasks go to the calendar even
        # when nobody could be reached, e.g. while the directory has no chat IDs.
        calendar = self.schedule_host_tasks(record, text)
        return {
            "meeting_id": meeting_id,
            "meeting_date": _short_date(record.get("start_at", "")),
            "status": "sent" if delivered else "failed",
            "delivered": delivered,
            "failed": failed,
            "calendar": calendar,
        }

    def schedule_host_tasks(self, record: dict, text: str) -> dict:
        """After a follow-up is confirmed, turn the host's own tasks into Google Tasks.

        Runs once per meeting: a second confirm must not duplicate events.
        """
        meeting_id = record["meeting_id"]
        if record.get("host_tasks_scheduled_at"):
            return {"status": "already_scheduled", "created": 0}
        if not self.settings.host_name_list:
            return {"status": "skipped", "reason": "HOST_NAME not set", "created": 0}
        try:
            tasks = self.llm.extract_host_tasks(
                text, self.settings.host_name_list, _short_date(record.get("start_at", ""))
            )
        except Exception:
            LOGGER.exception("Could not extract host tasks for %s", meeting_id)
            return {"status": "failed", "reason": "extraction", "created": 0}

        meeting_day = _iso_day(record.get("start_at", ""))
        title_prefix = f"1:1 {record.get('manager_id', '')}".strip()
        created, without_deadline, failed = 0, 0, 0
        for task in tasks:
            if task.deadline:
                day = date.fromisoformat(task.deadline)
            elif meeting_day:
                # No date agreed: park it on the eve of the next weekly so it is not lost.
                day = meeting_day + timedelta(days=6)
                without_deadline += 1
            else:
                continue
            note = f"Домовленість з фоллоуапу зустрічі {_short_date(record.get('start_at', ''))}."
            if not task.deadline:
                note += " Строк не називався, поставлено на наступний тиждень."
            try:
                self.calendar.create_task(f"{title_prefix}: {task.task}", day, note)
                created += 1
            except Exception:
                failed += 1
                LOGGER.exception("Could not create task for %s", meeting_id)
        # Mark only when something was filed. A draft with no host tasks yet may
        # gain some after an edit, and the next confirm must pick them up.
        if created:
            self.sheets.patch_meeting(record["meeting_id"], {"host_tasks_scheduled_at": _now_iso()})
        return {
            "status": "scheduled" if created else ("nothing" if not tasks else "failed"),
            "created": created,
            "without_deadline": without_deadline,
            "failed": failed,
        }

    def _retire_confirm_button(self, record: dict, text: str) -> None:
        """Drop the confirm button from the host's copy once the follow-up is out."""
        message_id = record.get("summary_message_id", "").strip()
        if not message_id or not self.settings.host_telegram_chat_id:
            return
        try:
            self.telegram.edit_message(
                self.settings.host_telegram_chat_id,
                int(message_id),
                text,
                reply_markup=_draft_keyboard(record["meeting_id"], sent=True),
            )
        except Exception:
            LOGGER.exception("Could not update draft buttons for %s", record.get("meeting_id"))

    def _send_one_followup(self, meeting: CalendarMeeting, manager: Manager) -> bool:
        current = self.sheets.get_meeting(meeting.meeting_id) or {}
        if current.get("followup_sent_at"):
            return False
        if not self.settings.host_telegram_chat_id:
            LOGGER.warning(
                "HOST_TELEGRAM_CHAT_ID is not set, skipping reminder for meeting %s",
                meeting.meeting_id,
            )
            return False
        followups = self.sheets.get_recent_followups(
            manager.manager_id,
            self.settings.reminder_followup_count,
            before=meeting.start_at.isoformat(),
        )
        if not followups and not self.settings.telegram_send_empty_followup:
            return False
        reminder = self.llm.prepare_reminder(manager, meeting, followups)
        text = build_reminder(manager, meeting, reminder, self.settings.host_name_list)
        # The full briefing goes to whoever runs the 1:1.
        self.telegram.send_message(
            self.settings.host_telegram_chat_id,
            text,
            self.settings.host_telegram_thread_id,
        )
        # The other side gets only their own share, so they arrive prepared too.
        if manager.telegram_chat_id:
            theirs = build_participant_reminder(
                manager, meeting, reminder, self.settings.host_name_list
            )
            if theirs:
                try:
                    self.telegram.send_followup(manager, theirs)
                except Exception:
                    LOGGER.exception(
                        "Could not send participant reminder for %s", meeting.meeting_id
                    )
        self.sheets.patch_meeting(meeting.meeting_id, {"followup_sent_at": _now_iso()})
        return True


def build_automation(settings: Settings) -> VegasAutomationService:
    from .google_auth import get_google_credentials

    credentials = get_google_credentials(settings)
    sheets = GoogleSheetsStore(credentials, settings)
    sheets.ensure_schema()
    return VegasAutomationService(
        settings=settings,
        calendar=GoogleCalendarClient(credentials, settings),
        gmail=GmailTranscriptClient(credentials, settings),
        sheets=sheets,
        llm=LLMAnalyzer(settings),
        telegram=TelegramClient(settings),
    )


def _reject_foreign_followup(record: dict, text: str) -> None:
    """Refuse text whose header names a different meeting.

    Editing re-issues the draft at the bottom of the chat, so the order of
    drafts keeps changing and replying to the wrong one is easy. Accepting it
    silently would send one manager the contents of another's 1:1.
    """
    expected = (record.get("title") or "").strip()
    lines = text.splitlines()
    header = lines[1].strip() if len(lines) > 1 else ""
    if not expected or not header:
        return
    if normalize_text(header) != normalize_text(expected):
        raise ValueError(
            f"Цей текст із зустрічі «{header}», а чернетка — «{expected}». "
            f"Правку не збережено: відповідай на чернетку тієї самої зустрічі."
        )


def _meeting_record(meeting: CalendarMeeting, manager: Manager) -> dict[str, str]:
    return {
        "meeting_id": meeting.meeting_id,
        "calendar_event_id": meeting.meeting_id,
        "manager_id": manager.manager_id,
        "title": meeting.title,
        "start_at": meeting.start_at.isoformat(),
        "end_at": meeting.end_at.isoformat(),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_day(iso: str) -> date | None:
    try:
        return datetime.fromisoformat(iso).date()
    except ValueError:
        return None


def _short_date(iso: str) -> str:
    """'2026-07-27T15:58:00+03:00' -> '27.07', or '' when unknown."""
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m")
    except ValueError:
        return ""


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()[:16]


def _draft_keyboard(meeting_id: str, sent: bool = False) -> dict:
    """One button under the draft. Editing is a reply, so it needs none.

    Once the follow-up has gone out there is nothing left to do.
    """
    if sent:
        return {"inline_keyboard": []}
    return {
        "inline_keyboard": [[{"text": "Підтвердити", "callback_data": f"confirm:{meeting_id}"}]]
    }
