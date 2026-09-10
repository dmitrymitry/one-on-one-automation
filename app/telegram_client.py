from typing import Any

import httpx

from .config import Settings
from .meeting_summary import split_for_telegram
from .models import Manager


class TelegramClient:
    def __init__(self, settings: Settings):
        if not settings.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        self.base_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}"

    def send_message(
        self,
        chat_id: str,
        text: str,
        thread_id: str = "",
        reply_markup: dict | None = None,
    ) -> int:
        """Send a message, splitting it if needed. Returns the last message id."""
        if not chat_id:
            raise ValueError("Telegram chat ID is required")
        chunks = split_for_telegram(text)
        message_id = 0
        for index, chunk in enumerate(chunks):
            payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if thread_id:
                payload["message_thread_id"] = int(thread_id)
            # Buttons belong on the final chunk, where the reader ends up.
            if reply_markup and index == len(chunks) - 1:
                payload["reply_markup"] = reply_markup
            message_id = self._call("sendMessage", payload)["message_id"]
        return message_id

    def edit_message(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        reply_markup: dict | None = None,
    ) -> None:
        """Rewrite an existing message in place."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text[:4096],
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        self._call("editMessageText", payload)

    def set_webhook(self, url: str, secret: str) -> dict:
        """Point Telegram at a URL. This also ends any long polling on this bot."""
        return self._call(
            "setWebhook",
            {
                "url": url,
                "secret_token": secret,
                "allowed_updates": ["message", "edited_message", "callback_query"],
                "drop_pending_updates": False,
            },
        )

    def delete_webhook(self) -> dict:
        """Back to long polling."""
        return self._call("deleteWebhook", {"drop_pending_updates": False})

    def webhook_info(self) -> dict:
        return self._call("getWebhookInfo", {})

    def get_username(self) -> str:
        """The bot's @username, so status lines can say where to connect."""
        return self._call("getMe", {}).get("username", "")

    def delete_message(self, chat_id: str, message_id: int) -> None:
        """Remove one of the bot's own messages (Telegram allows this for 48h)."""
        self._call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        self._call("answerCallbackQuery", payload)

    def get_updates(self, offset: int, timeout: int = 25) -> list[dict]:
        response = httpx.post(
            f"{self.base_url}/getUpdates",
            json={
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": ["message", "edited_message", "callback_query"],
            },
            timeout=timeout + 10,
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError(f"Telegram rejected getUpdates: {result}")
        return result["result"]

    def send_followup(self, manager: Manager, text: str) -> int:
        if not manager.telegram_chat_id:
            raise ValueError(f"No Telegram chat ID configured for {manager.manager_name}")
        return self.send_message(manager.telegram_chat_id, text, manager.telegram_thread_id)

    def _call(self, method: str, payload: dict) -> dict:
        response = httpx.post(f"{self.base_url}/{method}", json=payload, timeout=40)
        # Never `raise_for_status()` here: httpx puts the request URL in the
        # message, and our URL carries the bot token. One 400 would print the
        # token into Cloud Logging, where it is enough to take the bot over.
        if response.status_code >= 400:
            raise RuntimeError(
                f"Telegram {method} failed with {response.status_code}: {response.text[:300]}"
            )
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError(f"Telegram rejected {method}: {result}")
        return result.get("result", {})
