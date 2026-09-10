import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from .service import VegasAutomationService

LOGGER = logging.getLogger(__name__)


class TelegramBot:
    """Long-polling loop: confirm a draft with the button, edit it by replying.

    Editing is a reply to the draft message. The reply carries the message id,
    which is all the bot needs to know which draft the text belongs to. A
    message that is not a reply to a draft is ignored, so a stray message can
    never overwrite a follow-up.
    """

    def __init__(self, automation: VegasAutomationService):
        self.automation = automation
        self.settings = automation.settings
        self.telegram = automation.telegram
        self.offset = 0
        self._stop = threading.Event()
        self._username = ""

    def run_forever(self) -> None:
        conflicts = 0
        while not self._stop.is_set():
            try:
                for update in self.telegram.get_updates(self.offset):
                    self.offset = update["update_id"] + 1
                    self.handle(update)
                conflicts = 0
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 409:
                    LOGGER.exception("Telegram polling cycle failed")
                    self._stop.wait(5)
                    continue
                # Another instance is polling the same bot. Back off instead of
                # fighting it: whichever one the user meant to keep will win.
                conflicts += 1
                delay = min(60, 5 * conflicts)
                LOGGER.warning(
                    "Another process is polling this bot (409). Retrying in %ss. "
                    "Only one instance may poll: set ENABLE_TELEGRAM_BOT=false on the other.",
                    delay,
                )
                self._stop.wait(delay)
            except Exception:
                LOGGER.exception("Telegram polling cycle failed")
                self._stop.wait(5)

    def stop(self) -> None:
        self._stop.set()

    def handle(self, update: dict) -> None:
        # Which branch an update takes is otherwise invisible: a message that is
        # not a reply is dropped in silence, and that looks exactly like a dead
        # button. Keys only — never the text, which is the follow-up itself.
        LOGGER.info(
            "Update %s: keys=%s callback=%s reply=%s",
            update.get("update_id"),
            sorted(k for k in update if k != "update_id"),
            (update.get("callback_query") or {}).get("data", "")[:20],
            bool((update.get("message") or {}).get("reply_to_message")),
        )
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
            return
        message = update.get("message") or update.get("edited_message")
        if message:
            self._handle_message(message)

    def _ack(self, callback_id: str, text: str = "") -> None:
        """Best-effort toast. An expired query must never sink the whole update."""
        try:
            self.telegram.answer_callback(callback_id, text)
        except Exception as exc:
            LOGGER.warning("Could not answer callback %s: %s", callback_id, exc)

    def _handle_callback(self, callback: dict) -> None:
        chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
        data = callback.get("data", "")
        if self._is_host(chat_id) and data.startswith("confirm:"):
            self._confirm(chat_id, callback["id"], data.removeprefix("confirm:"))
        else:
            self._ack(callback["id"])

    def _confirm(self, chat_id: str, callback_id: str, meeting_id: str) -> None:
        """Send the follow-up exactly as it stands on screen right now."""
        # Telegram invalidates a callback query within seconds, and sending the
        # follow-up takes an LLM call plus several messages. Acknowledge first,
        # then work: the real outcome arrives as a status message anyway.
        self._ack(callback_id, "Надсилаю…")
        try:
            result = self.automation.send_meeting_summary(meeting_id)
        except Exception:
            LOGGER.exception("Could not send summary for %s", meeting_id)
            self._ack(callback_id, "Не вдалося надіслати")
            self.telegram.send_message(chat_id, "Не вдалося надіслати фоллоуап, глянь логи.")
            return
        status = result.get("status")
        if status == "already_sent":
            self._ack(callback_id, "Уже надіслано раніше")
            return
        # One terse line per recipient: who, when, what happened.
        stamp = datetime.now(ZoneInfo(self.settings.app_timezone)).strftime("%H:%M")
        delivered = result.get("delivered", [])
        lines = []
        # The date only means something next to a delivery line.
        if delivered and result.get("meeting_date"):
            lines.append(f"Фоллоуап від {result['meeting_date']}")
        lines += [f"{who} {stamp} доставлено" for who in delivered]
        missing = result.get("failed", [])
        if missing:
            handle = self._bot_handle()
            where = f"написати боту {handle}" if handle else "написати боту"
            lines += [f"{who} — не підключений, має {where}" for who in missing]
        cal = result.get("calendar") or {}
        if cal.get("created"):
            note = f"У календар: {cal['created']}"
            if cal.get("without_deadline"):
                note += f" (без строку: {cal['without_deadline']})"
            lines.append(note)
        elif cal.get("status") == "failed":
            lines.append("У календар не додано: немає прав або помилка, глянь логи")
        self._ack(callback_id, "Надіслано" if status == "sent" else "Не дійшло")
        self.telegram.send_message(chat_id, "\n".join(lines) or "Нікому надсилати")

    def _handle_message(self, message: dict) -> None:
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = (message.get("text") or "").strip()
        if not chat_id or not text:
            return
        if not self._is_host(chat_id):
            self._onboard(chat_id, text, message.get("from") or {})
            return
        replied = message.get("reply_to_message") or {}
        if not replied:
            return
        record = self.automation.sheets.get_meeting_by_message_id(str(replied.get("message_id")))
        if not record:
            # Silence here reads as "the edit was lost". Say what to reply to.
            self.telegram.send_message(
                chat_id,
                "Це не чернетка фоллоуапу. Щоб правити, відповідай на саме "
                "повідомлення з чернеткою — воно з кнопкою Підтвердити.",
            )
            return
        if record.get("summary_status") == "sent":
            self.telegram.send_message(chat_id, "Цей фоллоуап уже надіслано, правити нема чого.")
            return
        try:
            self.automation.apply_summary_edit(record["meeting_id"], text)
        except ValueError as exc:
            # A refusal the host can act on, e.g. text from another meeting.
            self.telegram.send_message(chat_id, str(exc))
            return
        except Exception:
            LOGGER.exception("Could not apply summary edit for %s", record.get("meeting_id"))
            self.telegram.send_message(chat_id, "Не вдалося зберегти правку, глянь логи.")
            return
        # The re-issued draft looks identical to the old one and the old one just
        # vanishes, so without a word there is no way to tell it worked.
        stamp = datetime.now(ZoneInfo(self.settings.app_timezone)).strftime("%H:%M")
        self.telegram.send_message(chat_id, f"{stamp} правку збережено")

    def _onboard(self, chat_id: str, text: str, sender: dict) -> None:
        """A participant connects: ask for their handle, then bind their chat.

        Stateless on purpose: the first message ("/start", "hi") matches no
        handle and gets the instructions; the next one is tried as a handle.
        """
        sheets = self.automation.sheets
        try:
            if sheets.manager_for_chat(chat_id):
                self.telegram.send_message(
                    chat_id, "Ти вже підключений, фоллоуапи приходитимуть сюди."
                )
                return
            handle = text.lstrip("@").strip()
            status, name = (
                ("unknown", "")
                if handle.startswith("/")
                else sheets.bind_manager_chat(handle, chat_id)
            )
        except Exception:
            LOGGER.exception("Onboarding failed for chat %s", chat_id)
            self.telegram.send_message(chat_id, "Щось пішло не так, спробуй ще раз пізніше.")
            return
        if status == "bound":
            self.telegram.send_message(
                chat_id, f"Готово, {name}. Фоллоуапи й нагадування приходитимуть сюди."
            )
            who = sender.get("username")
            who = (
                f"@{who}"
                if who
                else " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")]))
            )
            self.telegram.send_message(
                self.settings.host_telegram_chat_id, f"Підключився: {name} ← {who or chat_id}"
            )
        elif status == "already_bound":
            self.telegram.send_message(chat_id, "Ти вже підключений, фоллоуапи приходитимуть сюди.")
        elif status == "taken":
            self.telegram.send_message(
                chat_id,
                f"Нік {name} уже прив'язаний до іншого чату. Напиши Дмитру, він розбереться.",
            )
        else:
            self.telegram.send_message(
                chat_id,
                "Привіт. Щоб отримувати фоллоуапи, напиши свій нік як в ПУПі.",
            )

    def _bot_handle(self) -> str:
        if not self._username:
            try:
                self._username = self.telegram.get_username()
            except Exception:
                LOGGER.warning("Could not resolve bot username via getMe")
        return f"@{self._username}" if self._username else ""

    def _is_host(self, chat_id: str) -> bool:
        return bool(chat_id) and chat_id == self.settings.host_telegram_chat_id
