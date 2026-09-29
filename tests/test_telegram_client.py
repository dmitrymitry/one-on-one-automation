import logging
from types import SimpleNamespace

import httpx
import pytest

from app.telegram_client import MessageNotModified, TelegramClient

NOT_MODIFIED = (
    '{"ok":false,"error_code":400,"description":"Bad Request: message is not modified: '
    "specified new message content and reply markup are exactly the same as a current "
    'content and reply markup of the message"}'
)


def make_client(monkeypatch, *responses: httpx.Response) -> tuple[TelegramClient, list]:
    """A client whose HTTP calls return `responses` in order and are recorded."""
    calls: list[tuple[str, dict]] = []
    queue = list(responses)

    def fake_post(url, json, timeout):
        calls.append((url.rsplit("/", 1)[-1], json))
        return queue.pop(0)

    monkeypatch.setattr(httpx, "post", fake_post)
    return TelegramClient(SimpleNamespace(telegram_bot_token="123:secret")), calls


def ok(result: dict | None = None) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": result or {}})


def test_an_edit_that_changes_nothing_is_its_own_error(monkeypatch) -> None:
    client, _ = make_client(monkeypatch, httpx.Response(400, text=NOT_MODIFIED))

    with pytest.raises(MessageNotModified):
        client._call("editMessageText", {})


def test_other_refusals_stay_plain_errors(monkeypatch) -> None:
    refusal = '{"ok":false,"error_code":400,"description":"Bad Request: chat not found"}'
    client, _ = make_client(monkeypatch, httpx.Response(400, text=refusal))

    with pytest.raises(RuntimeError) as caught:
        client._call("sendMessage", {})
    assert not isinstance(caught.value, MessageNotModified)


def test_edit_reports_an_unchanged_message_instead_of_failing(monkeypatch) -> None:
    """The sync loop retried such an edit every cycle, logging an error each time."""
    client, _ = make_client(monkeypatch, httpx.Response(400, text=NOT_MODIFIED), ok())

    assert client.edit_message("1", 26, "той самий текст") is False
    assert client.edit_message("1", 26, "новий текст") is True


def test_overlong_edit_is_cut_and_logged(monkeypatch, caplog) -> None:
    client, calls = make_client(monkeypatch, ok())

    with caplog.at_level(logging.WARNING, logger="app.telegram_client"):
        client.edit_message("1", 26, "х" * 5000)

    assert len(calls[0][1]["text"]) == 4096
    assert "cut from 5000 to 4096" in caplog.text


def test_edit_reply_markup_leaves_the_text_alone(monkeypatch) -> None:
    client, calls = make_client(monkeypatch, ok())

    client.edit_reply_markup("1", 26, {"inline_keyboard": []})

    method, payload = calls[0]
    assert method == "editMessageReplyMarkup"
    assert "text" not in payload
    assert payload["reply_markup"] == {"inline_keyboard": []}


def test_long_text_goes_out_in_parts_and_every_id_comes_back(monkeypatch) -> None:
    text = "перший блок " * 300 + "\n\n" + "другий блок " * 300
    keyboard = {"inline_keyboard": [[{"text": "Підтвердити", "callback_data": "confirm:m"}]]}
    client, calls = make_client(monkeypatch, ok({"message_id": 101}), ok({"message_id": 102}))

    assert client.send_parts("1", text, reply_markup=keyboard) == [101, 102]
    # Buttons only on the last part, where the reader ends up.
    assert "reply_markup" not in calls[0][1]
    assert calls[1][1]["reply_markup"] == keyboard


def test_send_message_still_returns_the_last_id(monkeypatch) -> None:
    text = "перший блок " * 300 + "\n\n" + "другий блок " * 300
    client, _ = make_client(monkeypatch, ok({"message_id": 101}), ok({"message_id": 102}))

    assert client.send_message("1", text) == 102
