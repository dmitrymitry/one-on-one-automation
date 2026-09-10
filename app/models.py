from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Manager:
    manager_id: str
    manager_name: str
    aliases: tuple[str, ...]
    telegram_chat_id: str = ""
    telegram_thread_id: str = ""
    calendar_id: str = ""
    transcript_sender: str = ""
    timezone: str = "Europe/Kyiv"
    active: bool = True


@dataclass(frozen=True)
class CalendarMeeting:
    meeting_id: str
    title: str
    start_at: datetime
    end_at: datetime
    calendar_id: str
    html_link: str = ""


@dataclass(frozen=True)
class Transcript:
    message_id: str
    subject: str
    received_at: datetime
    text: str
    sender: str = ""
    attachment_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReminderCommitment:
    """Something a person promised to have ready by the upcoming meeting."""

    who: str
    what: str
    timing: str = ""
    since: str = ""


@dataclass(frozen=True)
class ReminderCarryOver:
    """Something raised earlier that still has no resolution."""

    what: str
    who: str = ""
    timing: str = ""
    since: str = ""


@dataclass(frozen=True)
class ReminderOpenTopic:
    """Raised in a meeting, left without a decision and without an owner.

    Nothing else tracks these: they have no task and no responsible person, so
    they vanish unless the reminder surfaces them.
    """

    topic: str
    question: str = ""
    since: str = ""


@dataclass(frozen=True)
class MeetingReminder:
    commitments: tuple[ReminderCommitment, ...] = field(default_factory=tuple)
    carried_over: tuple[ReminderCarryOver, ...] = field(default_factory=tuple)
    open_topics: tuple[ReminderOpenTopic, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class HostTask:
    """A commitment the host made, pulled out of a confirmed follow-up."""

    task: str
    deadline: str = ""  # ISO date, empty when the follow-up names none


@dataclass(frozen=True)
class SummaryTask:
    action: str
    responsible: str = ""
    expected_result: str = ""
    # What was actually said about timing. Shown in the follow-up so a human can
    # check the computed date, or spot that there was never a real deadline.
    deadline_note: str = ""


@dataclass(frozen=True)
class SummaryTheme:
    title: str
    context: str = ""
    tasks: tuple[SummaryTask, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class MeetingSummary:
    topics: tuple[str, ...] = field(default_factory=tuple)
    themes: tuple[SummaryTheme, ...] = field(default_factory=tuple)
