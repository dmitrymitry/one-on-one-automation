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


# Shared across every manager's aliases (e.g. "Vegas Ksu", "Ksu Vegas") because
# aliases are built for matching a calendar TITLE, not for spotting the
# person's own name inside free-form prose — matching on it here would flag
# nearly every follow-up as "mentioning" everyone.
_NOISE_WORDS = {"vegas", "вегас"}


def mentions_manager(text: str, manager: Manager) -> bool:
    """Whether free-form text (e.g. a DIFFERENT meeting's follow-up) names this
    person, in whichever alphabet it happened to use (Rule 6, pastka 2).
    """
    normalized = f" {normalize_text(text)} "
    tokens = {normalize_text(manager.manager_name)}
    for alias in manager.aliases:
        tokens.update(normalize_text(alias).split())
    tokens -= _NOISE_WORDS
    return any(len(token) > 2 and f" {token} " in normalized for token in tokens if token)
