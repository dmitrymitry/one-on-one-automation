import json
from typing import Any

from googleapiclient.discovery import build

from .config import Settings
from .meeting_matcher import normalize_text
from .models import Manager

SCHEMA: dict[str, list[str]] = {
    "Managers": [
        "manager_id",
        "manager_name",
        "aliases",
        "telegram_chat_id",
        "telegram_thread_id",
        "calendar_id",
        "transcript_sender",
        "timezone",
        "active",
    ],
    "Meetings": [
        "meeting_id",
        "calendar_event_id",
        "manager_id",
        "title",
        "start_at",
        "end_at",
        "transcript_message_id",
        "transcript_received_at",
        "analysis_status",
        "analysis_error",
        "processed_at",
        "followup_sent_at",
        "summary_status",
        "summary_text",
        "summary_recipients",
        "summary_sent_at",
        "summary_message_id",
        "summary_synced_hash",
        "host_tasks_scheduled_at",
    ],
}


class GoogleSheetsStore:
    def __init__(self, credentials, settings: Settings):
        if not settings.google_sheet_id:
            raise ValueError("GOOGLE_SHEET_ID is required")
        self.settings = settings
        self.service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        self._gids: dict[str, int] = {}

    def ensure_schema(self) -> None:
        spreadsheet = (
            self.service.spreadsheets()
            .get(spreadsheetId=self.settings.google_sheet_id, fields="sheets.properties")
            .execute()
        )
        existing = {sheet["properties"]["title"] for sheet in spreadsheet.get("sheets", [])}
        missing = [name for name in SCHEMA if name not in existing]
        if missing:
            requests = [{"addSheet": {"properties": {"title": name}}} for name in missing]
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.settings.google_sheet_id,
                body={"requests": requests},
            ).execute()
        for tab, headers in SCHEMA.items():
            values = self._read_values(tab)
            if not values:
                self._write_range(tab, 1, headers)
                continue
            existing = _trim_trailing_blanks(values[0])
            if existing == headers:
                continue
            if headers[: len(existing)] == existing:
                # Older sheet missing columns added later: extend the header row in place.
                self._write_range(tab, 1, headers)
                continue
            raise ValueError(f"Sheet tab {tab} has unexpected headers. Expected: {headers}")

    def get_managers(self) -> list[Manager]:
        managers = [self._manager_from_row(row) for row in self._records("Managers")]
        managers = [manager for manager in managers if manager and manager.active]
        if managers:
            return managers
        try:
            fallback = json.loads(self.settings.default_managers_json)
        except json.JSONDecodeError as exc:
            raise ValueError("DEFAULT_MANAGERS_JSON is not valid JSON") from exc
        return [self._manager_from_mapping(item) for item in fallback]

    def bind_manager_chat(self, handle: str, chat_id: str) -> tuple[str, str]:
        """Attach a Telegram chat to the manager whose id/name/alias matches `handle`.

        Returns (status, manager_name): "bound", "already_bound" (this chat is
        already on file), "taken" (row belongs to another chat, never overwritten)
        or "unknown" (no such handle).
        """
        wanted = normalize_text(handle)
        chat_id = str(chat_id).strip()
        if not wanted or not chat_id:
            return "unknown", ""
        for record in self._records("Managers"):
            values = record["values"]
            names = [values.get("manager_id", ""), values.get("manager_name", "")]
            names += values.get("aliases", "").replace(";", ",").split(",")
            if wanted not in {normalize_text(name) for name in names if name.strip()}:
                continue
            name = values.get("manager_name", "") or values.get("manager_id", "")
            current = values.get("telegram_chat_id", "").strip()
            if current == chat_id:
                return "already_bound", name
            if current:
                return "taken", name
            merged = {**values, "telegram_chat_id": chat_id}
            self._write_range(
                "Managers", record["row_number"], self._mapping_to_row("Managers", merged)
            )
            return "bound", name
        return "unknown", ""

    def manager_for_chat(self, chat_id: str) -> Manager | None:
        chat_id = str(chat_id).strip()
        for manager in self.get_managers():
            if manager.telegram_chat_id == chat_id:
                return manager
        return None

    def upsert_meeting(self, meeting: dict[str, Any]) -> dict[str, Any]:
        records = self._records("Meetings")
        for record in records:
            if record["values"].get("meeting_id") == meeting["meeting_id"]:
                merged = {**record["values"], **meeting}
                self._write_range(
                    "Meetings", record["row_number"], self._mapping_to_row("Meetings", merged)
                )
                return merged
        self._append_row("Meetings", meeting)
        return meeting

    def patch_meeting(self, meeting_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Write only `changes`, merged onto the row's *current* values.

        Unlike passing a whole record to ``upsert_meeting``, this reads the row
        fresh here and overrides only the named fields. A writer that touches a
        different field cannot be lost, which is exactly what stops a background
        cycle from reverting a status a confirm has just set. Every mutation of
        an existing meeting goes through here; ``upsert_meeting`` stays for the
        few places that legitimately build a full row.
        """
        for record in self._records("Meetings"):
            if record["values"].get("meeting_id") == meeting_id:
                self._write_cells("Meetings", record["row_number"], changes)
                return {**record["values"], **changes}
        created = {"meeting_id": meeting_id, **changes}
        self._append_row("Meetings", created)
        return created

    def followup_cell_url(self, meeting_id: str) -> str:
        """Deep link straight to the summary_text cell of this meeting."""
        row = next(
            (
                record["row_number"]
                for record in self._records("Meetings")
                if record["values"].get("meeting_id") == meeting_id
            ),
            None,
        )
        if row is None:
            return ""
        column = _column_name(SCHEMA["Meetings"].index("summary_text") + 1)
        gid = self._tab_gid("Meetings")
        return (
            f"https://docs.google.com/spreadsheets/d/{self.settings.google_sheet_id}"
            f"/edit#gid={gid}&range={column}{row}"
        )

    def _tab_gid(self, tab: str) -> int:
        if tab not in self._gids:
            spreadsheet = (
                self.service.spreadsheets()
                .get(spreadsheetId=self.settings.google_sheet_id, fields="sheets.properties")
                .execute()
            )
            self._gids = {
                sheet["properties"]["title"]: sheet["properties"]["sheetId"]
                for sheet in spreadsheet.get("sheets", [])
            }
        return self._gids.get(tab, 0)

    def get_recent_followups(self, manager_id: str, limit: int, before: str = "") -> list[str]:
        """Follow-up texts for this manager, newest first.

        These are written by the bot itself, so nothing here has to be curated
        by hand. Only meetings that already have a drafted follow-up count.
        """
        rows = []
        for record in self._records("Meetings"):
            values = record["values"]
            text = values.get("summary_text", "").strip()
            start_at = values.get("start_at", "")
            if values.get("manager_id") != manager_id or not text:
                continue
            if before and start_at >= before:
                continue
            rows.append((start_at, text))
        rows.sort(key=lambda item: item[0], reverse=True)
        return [text for _, text in rows[:limit]]

    def list_meetings_with_drafts(self) -> list[dict[str, Any]]:
        return [
            record["values"]
            for record in self._records("Meetings")
            if record["values"].get("summary_text", "").strip()
            and record["values"].get("summary_message_id", "").strip()
        ]

    def get_meeting_by_message_id(self, message_id: str) -> dict[str, Any] | None:
        """The meeting whose draft lives in this Telegram message, if any."""
        message_id = str(message_id).strip()
        if not message_id:
            return None
        for record in self._records("Meetings"):
            if record["values"].get("summary_message_id", "").strip() == message_id:
                return record["values"]
        return None

    def get_meeting(self, meeting_id: str) -> dict[str, Any] | None:
        for record in self._records("Meetings"):
            if record["values"].get("meeting_id") == meeting_id:
                return record["values"]
        return None

    def _records(self, tab: str) -> list[dict[str, Any]]:
        values = self._read_values(tab)
        if not values:
            return []
        headers = values[0]
        records = []
        for row_number, row in enumerate(values[1:], start=2):
            values_by_header = {
                header: row[index] if index < len(row) else ""
                for index, header in enumerate(headers)
            }
            if any(value.strip() for value in values_by_header.values() if isinstance(value, str)):
                records.append({"row_number": row_number, "values": values_by_header})
        return records

    def _read_values(self, tab: str) -> list[list[str]]:
        response = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=self.settings.google_sheet_id, range=f"{tab}!A:Z")
            .execute()
        )
        return response.get("values", [])

    def _append_row(self, tab: str, mapping: dict[str, Any]) -> None:
        self.service.spreadsheets().values().append(
            spreadsheetId=self.settings.google_sheet_id,
            range=f"{tab}!A:Z",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [self._mapping_to_row(tab, mapping)]},
        ).execute()

    def _write_cells(self, tab: str, row_number: int, changes: dict[str, Any]) -> None:
        """Write only the named columns of one row, leaving the rest untouched.

        A whole-row write carries every other cell back as this writer last saw
        it, which is how a stale snapshot reverts a field someone else just
        changed. Writing just the changed cells means two writers touching
        different fields of the same meeting can never lose each other's value;
        the only remaining contention is two writers of the *same* field, where
        last-writer-wins is the intended behaviour anyway.
        """
        headers = SCHEMA[tab]
        data = []
        for field, value in changes.items():
            if field not in headers:
                continue
            column = _column_name(headers.index(field) + 1)
            data.append({"range": f"{tab}!{column}{row_number}", "values": [[value]]})
        if not data:
            return
        self.service.spreadsheets().values().batchUpdate(
            spreadsheetId=self.settings.google_sheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()

    def _write_range(self, tab: str, row_number: int, values: list[Any]) -> None:
        end_column = _column_name(len(SCHEMA[tab]))
        self.service.spreadsheets().values().update(
            spreadsheetId=self.settings.google_sheet_id,
            range=f"{tab}!A{row_number}:{end_column}{row_number}",
            valueInputOption="USER_ENTERED",
            body={"values": [values]},
        ).execute()

    def _manager_from_row(self, row: dict[str, Any]) -> Manager:
        return self._manager_from_mapping(row["values"] | {"row_number": row["row_number"]})

    @staticmethod
    def _manager_from_mapping(item: dict[str, Any]) -> Manager:
        aliases = item.get("aliases", "")
        if isinstance(aliases, str):
            aliases = tuple(
                alias.strip() for alias in aliases.replace(";", ",").split(",") if alias.strip()
            )
        return Manager(
            manager_id=str(item.get("manager_id", "")).strip(),
            manager_name=str(item.get("manager_name", "")).strip(),
            aliases=tuple(aliases) or (str(item.get("manager_name", "")).strip(),),
            telegram_chat_id=str(item.get("telegram_chat_id", "")).strip(),
            telegram_thread_id=str(item.get("telegram_thread_id", "")).strip(),
            calendar_id=str(item.get("calendar_id", "")).strip(),
            transcript_sender=str(item.get("transcript_sender", "")).strip(),
            timezone=str(item.get("timezone", "Europe/Kyiv")).strip(),
            active=str(item.get("active", "true")).casefold() not in {"false", "0", "no"},
        )

    @staticmethod
    def _mapping_to_row(tab: str, mapping: dict[str, Any]) -> list[Any]:
        return [mapping.get(header, "") for header in SCHEMA[tab]]


def _trim_trailing_blanks(row: list[str]) -> list[str]:
    trimmed = list(row)
    while trimmed and not str(trimmed[-1]).strip():
        trimmed.pop()
    return trimmed


def _column_name(number: int) -> str:
    column = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        column = chr(65 + remainder) + column
    return column
