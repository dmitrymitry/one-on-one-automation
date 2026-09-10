from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_timezone: str = "Europe/Kyiv"
    enable_scheduler: bool = True
    poll_interval_seconds: int = Field(default=300, ge=30)
    internal_job_token: str = ""

    google_auth_mode: str = "oauth"
    google_sheet_id: str = ""
    google_calendar_id: str = "primary"
    google_user_id: str = "me"
    google_client_secret_file: str = "secrets/google-oauth-client.json"
    google_token_file: str = "secrets/google-token.json"
    # Full token JSON. Set on servers, where there is no writable secrets dir;
    # takes precedence over google_token_file and is refreshed in memory only.
    google_token_json: str = ""
    google_service_account_file: str = "secrets/google-service-account.json"
    google_delegated_user: str = ""
    google_scopes: str = (
        "https://www.googleapis.com/auth/calendar.readonly,"
        "https://www.googleapis.com/auth/gmail.readonly,"
        "https://www.googleapis.com/auth/spreadsheets"
    )

    calendar_title_keywords: str = "Vegas"
    calendar_lookback_hours: int = Field(default=48, ge=1)
    calendar_lookahead_minutes: int = Field(default=90, ge=5)
    gmail_transcript_sender: str = ""
    gmail_transcript_label: str = ""
    gmail_transcript_query: str = ""

    telegram_bot_token: str = ""
    telegram_send_empty_followup: bool = False
    # Pre-meeting reminders go to whoever runs the 1:1, not to the manager.
    host_telegram_chat_id: str = ""
    host_telegram_thread_id: str = ""
    # Every spelling of the person running the 1:1, comma separated. Transcripts
    # and Ukrainian follow-ups spell the same person differently, so list both.
    host_name: str = ""
    # Shared secret Telegram echoes back in X-Telegram-Bot-Api-Secret-Token.
    # Without it the webhook URL is world-writable: anyone could forge updates.
    telegram_webhook_secret: str = ""
    # Telegram allows one getUpdates consumer per bot. Keep this on for the
    # deployed instance and off everywhere else, or one of them gets 409s.
    enable_telegram_bot: bool = True
    # Working day in APP_TIMEZONE. Outside it the cycle does nothing: no
    # meetings happen, so polling Calendar and Gmail is pure waste.
    work_hours_start: int = Field(default=10, ge=0, le=23)
    work_hours_end: int = Field(default=19, ge=1, le=24)
    # How many past follow-ups feed the pre-meeting reminder. Wide enough that a
    # topic raised months ago and never decided still surfaces.
    reminder_followup_count: int = Field(default=6, ge=1)
    # Meeting summaries are drafted for manual review; flip on to dispatch automatically.
    summary_auto_send: bool = False

    llm_provider: str = "gemini"
    gemini_api_key: str = ""
    # Comma-separated fallback chain, strongest first. The newest models are the
    # ones that return 503 under load, so a weaker but available model beats a
    # failed follow-up.
    gemini_model: str = "gemini-2.5-flash"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = ""
    llm_max_transcript_chars: int = Field(default=60000, ge=1000)
    default_managers_json: str = "[]"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def google_scope_list(self) -> list[str]:
        return [scope.strip() for scope in self.google_scopes.split(",") if scope.strip()]

    @property
    def gemini_model_list(self) -> list[str]:
        return [name.strip() for name in self.gemini_model.split(",") if name.strip()]

    @property
    def gemini_api_key_list(self) -> list[str]:
        """Every key we may use, in order of preference.

        The free tier counts requests per project, so keys from *different*
        projects each carry their own daily allowance. Keys from the same
        project share one, and rotating between them buys nothing.
        """
        return [key.strip() for key in self.gemini_api_key.split(",") if key.strip()]

    @property
    def host_name_list(self) -> list[str]:
        return [name.strip() for name in self.host_name.split(",") if name.strip()]

    @property
    def calendar_keyword_list(self) -> list[str]:
        return [
            keyword.strip()
            for keyword in self.calendar_title_keywords.split(",")
            if keyword.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
