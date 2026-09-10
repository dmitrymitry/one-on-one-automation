import re
from collections.abc import Iterable

from .models import CalendarMeeting, Manager


def normalize_text(value: str) -> str:
    value = value.casefold().replace("ё", "е")
    return re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).strip()


def contains_keyword(title: str, keywords: Iterable[str]) -> bool:
    normalized = normalize_text(title)
    return any(normalize_text(keyword) in normalized for keyword in keywords)


def match_manager(
    meeting: CalendarMeeting,
    managers: Iterable[Manager],
    keywords: Iterable[str],
) -> Manager | None:
    if not contains_keyword(meeting.title, keywords):
        return None

    normalized_title = normalize_text(meeting.title)
    candidates: list[tuple[int, Manager]] = []
    for manager in managers:
        matched_aliases = [
            alias for alias in manager.aliases if _alias_matches(normalized_title, alias)
        ]
        if matched_aliases:
            candidates.append((max(map(len, matched_aliases)), manager))

    if not candidates:
        return None
    if len(candidates) > 1:
        return None
    return candidates[0][1]


def _alias_matches(normalized_title: str, alias: str) -> bool:
    normalized_alias = normalize_text(alias)
    if not normalized_alias:
        return False
    pattern = rf"(?:^|\s){re.escape(normalized_alias)}(?:$|\s)"
    return re.search(pattern, normalized_title) is not None
