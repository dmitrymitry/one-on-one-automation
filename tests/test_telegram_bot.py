from types import SimpleNamespace

import httpx
import pytest

from app.telegram_bot import TelegramBot

HOST = "100000001"
STRANGER = "999"
DRAFT_MSG = 26


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.answered: list[tuple[str, str]] = []

    def send_message(self, chat_id, text, thread_id="", reply_markup=None):
        self.sent.append((chat_id, text))
        return len(self.sent)

    def answer_callback(self, callback_id, text=""):
        self.answered.append((callback_id, text))

    def get_username(self):
        return "vegas_1on1_bot"


class FakeSheets:
    """One draft, living in Telegram message DRAFT_MSG."""

    def __init__(self, status: str = "draft") -> None:
        self.status = status

    def get_meeting_by_message_id(self, message_id: str):
        if str(message_id) == str(DRAFT_MSG):
            return {"meeting_id": "m-1", "summary_status": self.status}
        return None


class FakeAutomation:
    def __init__(self, telegram: FakeTelegram, fail: bool = False, result: dict | None = None):
        self.telegram = telegram
        self.sheets = FakeSheets()
        self.settings = SimpleNamespace(host_telegram_chat_id=HOST, app_timezone="Europe/Kyiv")
        self.applied: list[tuple[str, str]] = []
        self.confirmed: list[str] = []
        self.fail = fail
        self.result = result or {"status": "sent", "delivered": ["beta"], "failed": []}

    def apply_summary_edit(self, meeting_id: str, text: str) -> None:
        if self.fail:
            raise RuntimeError("sheets down")
        self.applied.append((meeting_id, text))

    def send_meeting_summary(self, meeting_id: str) -> dict:
        self.confirmed.append(meeting_id)
        return self.result


def make_bot(**kwargs) -> tuple[TelegramBot, FakeAutomation, FakeTelegram]:
    telegram = FakeTelegram()
    automation = FakeAutomation(telegram, **kwargs)
    return TelegramBot(automation), automation, telegram


def callback(chat_id: str = HOST, data: str = "confirm:m-1") -> dict:
    return {"callback_query": {"id": "cb", "data": data, "message": {"chat": {"id": chat_id}}}}


def reply(text: str, to: int = DRAFT_MSG, chat_id: str = HOST, edited: bool = False) -> dict:
    key = "edited_message" if edited else "message"
    return {key: {"chat": {"id": chat_id}, "text": text, "reply_to_message": {"message_id": to}}}


def plain(text: str, chat_id: str = HOST) -> dict:
    return {"message": {"chat": {"id": chat_id}, "text": text}}


# --- editing by reply ---


def test_reply_to_the_draft_applies_the_edit() -> None:
    bot, automation, telegram = make_bot()

    bot.handle(reply("Новий текст фоллоуапу"))

    assert automation.applied == [("m-1", "Новий текст фоллоуапу")]
    # The re-issued draft looks the same and the old one vanishes, so the bot
    # must say something or the edit reads as lost.
    assert len(telegram.sent) == 1
    assert telegram.sent[0][1].endswith("правку збережено")


def test_plain_message_never_touches_a_draft() -> None:
    """The safety property: only a reply can change a follow-up."""
    bot, automation, _ = make_bot()

    bot.handle(plain("випадкове повідомлення"))
    bot.handle(plain("Новий текст фоллоуапу"))

    assert automation.applied == []


def test_reply_to_something_else_explains_what_to_reply_to() -> None:
    bot, automation, telegram = make_bot()

    bot.handle(reply("текст", to=999))

    assert automation.applied == []
    assert "не чернетка" in telegram.sent[-1][1]


def test_editing_your_own_reply_reapplies() -> None:
    bot, automation, _ = make_bot()

    bot.handle(reply("Перша версія"))
    bot.handle(reply("Виправлена версія", edited=True))

    assert automation.applied == [("m-1", "Перша версія"), ("m-1", "Виправлена версія")]


def test_reply_to_a_sent_draft_is_refused() -> None:
    bot, automation, telegram = make_bot()
    automation.sheets = FakeSheets(status="sent")

    bot.handle(reply("пізно"))

    assert automation.applied == []
    assert "уже надіслано" in telegram.sent[-1][1]


def test_strangers_cannot_edit() -> None:
    bot, automation, _ = make_bot()

    bot.handle(reply("підміна", chat_id=STRANGER))

    assert automation.applied == []


def test_failed_edit_is_reported_and_not_claimed_as_saved() -> None:
    bot, _, telegram = make_bot(fail=True)

    bot.handle(reply("Новий текст"))

    assert "Не вдалося" in telegram.sent[-1][1]
    assert not any("збережено" in t for _, t in telegram.sent)


# --- confirm button ---


def test_confirm_sends_the_followup_and_reports_recipients() -> None:
    bot, automation, telegram = make_bot(
        result={
            "status": "sent",
            "meeting_date": "27.07",
            "delivered": ["beta"],
            "failed": ["madam"],
        }
    )

    bot.handle(callback())

    assert automation.confirmed == ["m-1"]
    assert telegram.answered[-1][1] == "Надіслано"
    lines = telegram.sent[-1][1].splitlines()
    assert lines[0] == "Фоллоуап від 27.07"
    assert lines[1].startswith("beta ") and lines[1].endswith(" доставлено")
    assert lines[2] == "madam — не підключений, має написати боту @vegas_1on1_bot"


def test_confirm_twice_does_not_resend() -> None:
    bot, automation, telegram = make_bot(result={"status": "already_sent", "delivered": []})

    bot.handle(callback())

    assert automation.confirmed == ["m-1"]
    assert telegram.answered[-1][1] == "Уже надіслано раніше"
    assert telegram.sent == []


def test_confirm_with_nobody_reachable_says_who_is_missing() -> None:
    bot, _, telegram = make_bot(
        result={"status": "failed", "meeting_date": "27.07", "delivered": [], "failed": ["beta"]}
    )

    bot.handle(callback())

    assert telegram.sent[-1][1] == "beta — не підключений, має написати боту @vegas_1on1_bot"


def test_status_reports_calendar_tasks() -> None:
    bot, _, telegram = make_bot(
        result={
            "status": "sent",
            "meeting_date": "27.07",
            "delivered": ["beta"],
            "failed": [],
            "calendar": {"status": "scheduled", "created": 2, "without_deadline": 1},
        }
    )

    bot.handle(callback())

    assert telegram.sent[-1][1].splitlines()[-1] == "У календар: 2 (без строку: 1)"


def test_status_says_when_calendar_failed() -> None:
    bot, _, telegram = make_bot(
        result={
            "status": "sent",
            "meeting_date": "27.07",
            "delivered": ["beta"],
            "failed": [],
            "calendar": {"status": "failed", "created": 0},
        }
    )

    bot.handle(callback())

    assert "У календар не додано" in telegram.sent[-1][1]


def test_calendar_line_shows_even_when_nobody_was_reached() -> None:
    bot, _, telegram = make_bot(
        result={
            "status": "failed",
            "meeting_date": "27.07",
            "delivered": [],
            "failed": ["beta"],
            "calendar": {"status": "scheduled", "created": 1, "without_deadline": 0},
        }
    )

    bot.handle(callback())

    # No delivery, so no date header: it would only confuse.
    assert telegram.sent[-1][1].splitlines() == [
        "beta — не підключений, має написати боту @vegas_1on1_bot",
        "У календар: 1",
    ]


def test_strangers_cannot_confirm() -> None:
    bot, automation, _ = make_bot()

    bot.handle(callback(chat_id=STRANGER))

    assert automation.confirmed == []


def test_unknown_callback_is_acknowledged_and_ignored() -> None:
    bot, automation, telegram = make_bot()

    bot.handle(callback(data="edit:m-1"))

    assert automation.confirmed == []
    assert telegram.answered == [("cb", "")]


# --- polling loop ---


def test_offset_advances_past_handled_updates() -> None:
    bot, _, _ = make_bot()

    for update in [{"update_id": 5, **callback()}, {"update_id": 6, **plain("текст")}]:
        bot.offset = update["update_id"] + 1
        bot.handle(update)

    assert bot.offset == 7


def test_conflict_backs_off_instead_of_hammering() -> None:
    """A second poller must not spin: it waits and says which switch to flip."""
    bot, _, _ = make_bot()
    waits: list[float] = []
    calls = {"n": 0}

    def conflicting(offset, timeout=25):
        calls["n"] += 1
        if calls["n"] > 3:
            bot._stop.set()
            return []
        response = httpx.Response(409, request=httpx.Request("POST", "https://api.telegram.org"))
        raise httpx.HTTPStatusError("conflict", request=response.request, response=response)

    bot.telegram.get_updates = conflicting
    bot._stop.wait = lambda d: waits.append(d)

    bot.run_forever()

    assert waits == [5, 10, 15]


def test_missing_participant_line_survives_a_getme_failure() -> None:
    bot, _, telegram = make_bot(result={"status": "failed", "delivered": [], "failed": ["beta"]})

    def broken():
        raise RuntimeError("network")

    telegram.get_username = broken

    bot.handle(callback())

    assert telegram.sent[-1][1] == "beta — не підключений, має написати боту"


# --- onboarding of participants ---


class OnboardingSheets(FakeSheets):
    def __init__(self) -> None:
        super().__init__()
        self.bound: list[tuple[str, str]] = []
        self.known: dict[str, str] = {}  # chat_id -> manager name
        self.taken: set[str] = set()

    def manager_for_chat(self, chat_id: str):
        return SimpleNamespace(manager_name=self.known[chat_id]) if chat_id in self.known else None

    def bind_manager_chat(self, handle: str, chat_id: str):
        if handle == "beta":
            if "beta" in self.taken:
                return "taken", "Beta"
            self.bound.append((handle, chat_id))
            self.known[chat_id] = "Beta"
            return "bound", "Beta"
        return "unknown", ""


def newcomer(text: str, chat_id: str = STRANGER, username: str = "olena_s") -> dict:
    return {
        "message": {
            "chat": {"id": chat_id},
            "text": text,
            "from": {"username": username, "first_name": "Ірина"},
        }
    }


def test_first_contact_gets_instructions_not_a_binding() -> None:
    bot, automation, telegram = make_bot()
    automation.sheets = OnboardingSheets()

    bot.handle(newcomer("/start"))

    assert automation.sheets.bound == []
    assert "напиши свій нік як в ПУПі" in telegram.sent[-1][1]
    assert telegram.sent[-1][0] == STRANGER


def test_valid_handle_binds_and_tells_the_host() -> None:
    bot, automation, telegram = make_bot()
    automation.sheets = OnboardingSheets()

    bot.handle(newcomer("@beta"))

    assert automation.sheets.bound == [("beta", STRANGER)]
    to_newcomer = [t for c, t in telegram.sent if c == STRANGER]
    to_host = [t for c, t in telegram.sent if c == HOST]
    assert to_newcomer[-1].startswith("Готово, Beta")
    assert to_host == ["Підключився: Beta ← @olena_s"]


def test_unknown_handle_asks_again() -> None:
    bot, automation, telegram = make_bot()
    automation.sheets = OnboardingSheets()

    bot.handle(newcomer("whoami"))

    assert automation.sheets.bound == []
    assert "напиши свій нік як в ПУПі" in telegram.sent[-1][1]


def test_already_connected_chat_is_told_so() -> None:
    bot, automation, telegram = make_bot()
    automation.sheets = OnboardingSheets()
    automation.sheets.known[STRANGER] = "Beta"

    bot.handle(newcomer("beta"))

    assert automation.sheets.bound == []
    assert "вже підключений" in telegram.sent[-1][1]


def test_taken_handle_is_refused_without_overwrite() -> None:
    bot, automation, telegram = make_bot()
    automation.sheets = OnboardingSheets()
    automation.sheets.taken.add("beta")

    bot.handle(newcomer("beta"))

    assert automation.sheets.bound == []
    assert "уже прив'язаний" in telegram.sent[-1][1]
    assert all(c != HOST for c, _ in telegram.sent)


def test_onboarding_never_touches_drafts() -> None:
    bot, automation, _ = make_bot()
    automation.sheets = OnboardingSheets()

    bot.handle(newcomer("Новий текст фоллоуапу"))

    assert automation.applied == []


def test_token_never_reaches_the_error_message(monkeypatch) -> None:
    """httpx would print the request URL, and our URL carries the bot token."""
    import httpx

    from app.telegram_client import TelegramClient

    token = "123456789:AA-fake-token-for-tests-only-not-a-real-bot"
    client = TelegramClient(SimpleNamespace(telegram_bot_token=token))

    def fake_post(url, **kwargs):
        return httpx.Response(400, text='{"ok":false,"description":"query is too old"}')

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(RuntimeError) as caught:
        client._call("answerCallbackQuery", {})

    assert token not in str(caught.value)
    assert "query is too old" in str(caught.value)


def test_confirm_acknowledges_before_doing_the_work() -> None:
    """A callback query expires in seconds; sending takes an LLM call."""
    order: list[str] = []

    bot = TelegramBot.__new__(TelegramBot)
    bot.settings = SimpleNamespace(app_timezone="Europe/Kyiv")
    bot.telegram = SimpleNamespace(
        answer_callback=lambda cid, text="": order.append(f"ack:{text}"),
        send_message=lambda *a, **k: order.append("status"),
    )
    bot.automation = SimpleNamespace(
        send_meeting_summary=lambda mid: (
            order.append("send"),
            {"status": "sent", "delivered": [], "failed": [], "meeting_date": ""},
        )[1]
    )

    bot._confirm("42", "cb-1", "m-1")

    assert order[0] == "ack:Надсилаю…"
    assert order.index("send") > 0


def test_expired_callback_does_not_sink_the_update() -> None:
    """The toast is cosmetic; losing it must not lose the follow-up."""
    sent: list[str] = []

    bot = TelegramBot.__new__(TelegramBot)
    bot.settings = SimpleNamespace(app_timezone="Europe/Kyiv")
    bot.telegram = SimpleNamespace(
        answer_callback=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("query is too old")),
        send_message=lambda *a, **k: sent.append("status"),
    )
    bot.automation = SimpleNamespace(
        send_meeting_summary=lambda mid: {
            "status": "sent",
            "delivered": ["delta"],
            "failed": [],
            "meeting_date": "08.09.26",
        }
    )

    bot._confirm("42", "cb-1", "m-1")

    assert sent == ["status"]
