from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main as main_module

SECRET = "s3cret-token"


class RecordingBot:
    def __init__(self, fail: bool = False) -> None:
        self.seen: list[dict] = []
        self.fail = fail

    def handle(self, update: dict) -> None:
        if self.fail:
            raise RuntimeError("boom")
        self.seen.append(update)

    def stop(self) -> None:
        """Lifespan shutdown calls this on whatever sits in app.state.bot."""


@pytest.fixture
def client(monkeypatch):
    """App with the lifespan side effects replaced: no Google, no scheduler."""
    settings = SimpleNamespace(
        telegram_webhook_secret=SECRET,
        app_env="test",
        llm_provider="gemini",
        internal_job_token="",
        enable_scheduler=False,
    )
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    with TestClient(main_module.app) as test_client:
        main_module.app.state.automation = object()
        main_module.app.state.bot = RecordingBot()
        yield test_client


def post(client, update=None, secret=SECRET):
    headers = {"x-telegram-bot-api-secret-token": secret} if secret is not None else {}
    return client.post("/telegram/webhook", json=update or {"update_id": 1}, headers=headers)


def test_valid_update_reaches_the_bot(client) -> None:
    response = post(client, {"update_id": 7, "message": {"text": "hi"}})

    assert response.status_code == 200
    assert main_module.app.state.bot.seen == [{"update_id": 7, "message": {"text": "hi"}}]


def test_wrong_secret_is_refused(client) -> None:
    """The URL is public; the secret is the only thing guarding it."""
    response = post(client, secret="guessed")

    assert response.status_code == 403
    assert main_module.app.state.bot.seen == []


def test_missing_secret_header_is_refused(client) -> None:
    response = post(client, secret=None)

    assert response.status_code == 403
    assert main_module.app.state.bot.seen == []


def test_broken_update_still_returns_200(client) -> None:
    """A non-2xx makes Telegram redeliver the same bad update forever."""
    main_module.app.state.bot = RecordingBot(fail=True)

    response = post(client)

    assert response.status_code == 200


def test_webhook_refuses_when_no_secret_is_configured(monkeypatch) -> None:
    settings = SimpleNamespace(
        telegram_webhook_secret="",
        app_env="test",
        llm_provider="gemini",
        internal_job_token="",
        enable_scheduler=False,
    )
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    with TestClient(main_module.app) as test_client:
        response = test_client.post("/telegram/webhook", json={"update_id": 1})

    assert response.status_code == 503
