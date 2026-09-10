from datetime import date, datetime

from googleapiclient.discovery import build

from .config import Settings
from .models import CalendarMeeting


class GoogleCalendarClient:
    def __init__(self, credentials, settings: Settings):
        self.settings = settings
        self.service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        self.tasks = build("tasks", "v1", credentials=credentials, cache_discovery=False)

    def list_events(self, time_min: datetime, time_max: datetime) -> list[CalendarMeeting]:
        response = (
            self.service.events()
            .list(
                calendarId=self.settings.google_calendar_id,
                timeMin=time_min.isoformat(),
                timeMax=time_max.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=250,
            )
            .execute()
        )
        return [
            _to_meeting(event, self.settings.google_calendar_id)
            for event in response.get("items", [])
        ]

    def create_task(self, title: str, day: date, notes: str = "") -> str:
        """Add a Google Task due on that day. It shows up in Calendar and can be ticked off.

        Needs the tasks scope and the Tasks API enabled on the OAuth client's project.
        """
        body = {
            "title": title,
            "notes": notes,
            # Tasks keep only the date part of `due`; the time is ignored.
            "due": f"{day.isoformat()}T00:00:00.000Z",
        }
        created = self.tasks.tasks().insert(tasklist="@default", body=body).execute()
        return created.get("id", "")


def _to_meeting(event: dict, calendar_id: str) -> CalendarMeeting:
    start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
    end = event.get("end", {}).get("dateTime") or event.get("end", {}).get("date")
    if not start or not end:
        raise ValueError(f"Calendar event {event.get('id')} has no start or end")
    return CalendarMeeting(
        meeting_id=event["id"],
        title=event.get("summary", ""),
        start_at=_parse_datetime(start),
        end_at=_parse_datetime(end),
        calendar_id=calendar_id,
        html_link=event.get("htmlLink", ""),
    )


def _parse_datetime(value: str) -> datetime:
    if len(value) == 10:
        return datetime.fromisoformat(value).astimezone()
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
