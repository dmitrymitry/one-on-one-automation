import re
from collections.abc import Iterable

from .meeting_matcher import normalize_text
from .models import CalendarMeeting, Manager, MeetingSummary

TELEGRAM_LIMIT = 4096

# "до 28.07" inside a task line: the deadline is settled, no gloss needed.
DATE_IN_TEXT = re.compile(r"\d{1,2}\.\d{2}")


def collect_responsible(summary: MeetingSummary) -> set[str]:
    """Every person named responsible for at least one task."""
    return {
        task.responsible.strip()
        for theme in summary.themes
        for task in theme.tasks
        if task.responsible.strip()
    }


def summary_recipients(
    owner: Manager,
    managers: Iterable[Manager],
    summary: MeetingSummary,
) -> list[Manager]:
    """Owner of the 1:1 plus every directory member responsible for a task.

    People are matched by name or alias; anyone without a chat ID is skipped,
    since the bot cannot reach them.
    """
    responsible = {normalize_text(name) for name in collect_responsible(summary)}
    recipients: list[Manager] = []
    seen: set[str] = set()
    for manager in [owner, *managers]:
        chat_id = manager.telegram_chat_id.strip()
        if not chat_id or chat_id in seen:
            continue
        names = {normalize_text(name) for name in (manager.manager_name, *manager.aliases)}
        names.discard("")
        if manager.manager_id != owner.manager_id and not _named_in(names, responsible):
            continue
        recipients.append(manager)
        seen.add(chat_id)
    return recipients


def _named_in(names: set[str], responsible: set[str]) -> bool:
    """True when any known name appears as a whole word in a responsible field."""
    for text in responsible:
        for name in names:
            if re.search(rf"(?:^|\s){re.escape(name)}(?:$|\s)", text):
                return True
    return False


def build_meeting_summary(meeting: CalendarMeeting, summary: MeetingSummary) -> str:
    blocks = [f"{meeting.start_at:%d.%m.%y}\n{meeting.title}"]

    if summary.topics:
        topics = "\n".join(f"— {topic}" for topic in summary.topics)
        blocks.append(f"Підсумок зустрічі\n{topics}")

    for index, theme in enumerate(summary.themes, 1):
        lines = [f"{index}. {theme.title}"]
        if theme.context:
            lines.append(f"Контекст: {theme.context}")
        for task_index, task in enumerate(theme.tasks, 1):
            responsible = task.responsible or "не призначений"
            lines.append("")
            # Only when the timing stayed ambiguous. A task that already carries
            # "до 28.07" needs no gloss; one without a date needs to show why.
            note = ""
            if task.deadline_note and not DATE_IN_TEXT.search(task.action):
                note = f" ({task.deadline_note})"
            lines.append(f"{index}.{task_index}. {task.action}{note}")
            detail = f"Відповідальний: {responsible}."
            if task.expected_result:
                detail += f" Очікуваний результат — {task.expected_result}"
            lines.append(detail)
        blocks.append("\n".join(lines))

    if not summary.themes:
        blocks.append("Тем для фіксації не знайдено.")

    return "\n\n".join(blocks)


def split_for_telegram(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split a long follow-up on block boundaries so Telegram accepts it."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(block) > limit:
            chunks.append(block[:limit])
            block = block[limit:]
        current = block
    if current:
        chunks.append(current)
    return chunks
