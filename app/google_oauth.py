from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from .config import get_settings


def main() -> None:
    settings = get_settings()
    client_secret = Path(settings.google_client_secret_file)
    if not client_secret.exists():
        raise FileNotFoundError(f"Google OAuth client secret not found: {client_secret}")

    flow = InstalledAppFlow.from_client_secrets_file(client_secret, settings.google_scope_list)
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    token_path = Path(settings.google_token_file)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    print(f"Saved Google OAuth token to {token_path}")


if __name__ == "__main__":
    main()
