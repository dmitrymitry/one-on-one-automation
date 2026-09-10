import json
from pathlib import Path

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials

from .config import Settings


def get_google_credentials(settings: Settings):
    if settings.google_auth_mode.casefold() == "service_account":
        return _service_account_credentials(settings)
    return _oauth_credentials(settings)


def _service_account_credentials(settings: Settings):
    path = Path(settings.google_service_account_file)
    if not path.exists():
        raise FileNotFoundError(f"Service account file not found: {path}")
    credentials = service_account.Credentials.from_service_account_file(
        path,
        scopes=settings.google_scope_list,
    )
    if settings.google_delegated_user:
        credentials = credentials.with_subject(settings.google_delegated_user)
    return credentials


def _oauth_credentials(settings: Settings):
    """Load the OAuth token, preferring the env var so the app can run read-only.

    On a server there is no writable secrets directory, so the token comes from
    GOOGLE_TOKEN_JSON and is refreshed in memory. The refresh token is long
    lived, so nothing has to be persisted between restarts.
    """
    raw = settings.google_token_json.strip()
    if raw:
        credentials = Credentials.from_authorized_user_info(
            json.loads(raw), settings.google_scope_list
        )
        return _ensure_valid(credentials, persist_to=None)

    path = Path(settings.google_token_file)
    if not path.exists():
        raise FileNotFoundError(
            f"OAuth token not found: set GOOGLE_TOKEN_JSON or create {path} "
            f"by running `python -m app.google_oauth`."
        )
    credentials = Credentials.from_authorized_user_file(path, settings.google_scope_list)
    return _ensure_valid(credentials, persist_to=path)


def _ensure_valid(credentials: Credentials, persist_to: Path | None):
    if not credentials.valid and credentials.expired and credentials.refresh_token:
        from google.auth.transport.requests import Request

        credentials.refresh(Request())
        if persist_to is not None:
            persist_to.write_text(credentials.to_json(), encoding="utf-8")
    if not credentials.valid:
        raise RuntimeError("Google OAuth token is invalid; run `python -m app.google_oauth` again")
    return credentials
