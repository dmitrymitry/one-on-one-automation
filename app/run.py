"""Headless entrypoint: the scheduler and the Telegram loop, no HTTP server.

The web app exists only to trigger jobs by hand, and the CLI already does that.
Dropping uvicorn/FastAPI from the running process matters on a small machine:
nothing of the ASGI stack is imported here.
"""

import logging
import signal
import threading

from apscheduler.schedulers.background import BackgroundScheduler

from .config import get_settings
from .service import build_automation
from .telegram_bot import TelegramBot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# The Telegram URL carries the bot token; httpx logs it at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)
LOGGER = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    automation = build_automation(settings)
    stop = threading.Event()

    scheduler = BackgroundScheduler(timezone=settings.app_timezone)
    scheduler.add_job(
        automation.run_cycle,
        "interval",
        seconds=settings.poll_interval_seconds,
        id="vegas-cycle",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    LOGGER.info("Scheduler started, every %ss", settings.poll_interval_seconds)

    bot = None
    if settings.enable_telegram_bot and settings.host_telegram_chat_id:
        bot = TelegramBot(automation)
        threading.Thread(target=bot.run_forever, name="telegram-bot", daemon=True).start()
        LOGGER.info("Telegram edit loop started")
    else:
        LOGGER.info("Telegram edit loop disabled (ENABLE_TELEGRAM_BOT / HOST_TELEGRAM_CHAT_ID)")

    def shutdown(signum, _frame):
        LOGGER.info("Signal %s received, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    stop.wait()
    if bot:
        bot.stop()
    scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
