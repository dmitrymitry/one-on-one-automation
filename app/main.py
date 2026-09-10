import logging
import threading
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from .config import get_settings
from .service import VegasAutomationService, build_automation
from .telegram_bot import TelegramBot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# httpx logs every request line at INFO, and our Telegram URL carries the bot
# token: at INFO the token would be written to Cloud Logging on every single
# call, which is enough for anyone with log access to take the bot over.
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.automation = None
    app.state.scheduler = None
    app.state.bot = None
    if settings.enable_scheduler:
        automation = build_automation(settings)
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
        app.state.automation = automation
        app.state.scheduler = scheduler
        if settings.enable_telegram_bot and settings.host_telegram_chat_id:
            # Separate loop: edits from the chat must land without waiting a cycle.
            # Only one process may poll the bot, hence the switch.
            bot = TelegramBot(automation)
            threading.Thread(target=bot.run_forever, name="telegram-bot", daemon=True).start()
            app.state.bot = bot
    yield
    if app.state.bot:
        app.state.bot.stop()
    if app.state.scheduler:
        app.state.scheduler.shutdown(wait=False)


app = FastAPI(title="Vegas 1:1 automation", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health(request: Request) -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "environment": settings.app_env,
        "llm_provider": settings.llm_provider,
        "scheduler_enabled": bool(request.app.state.scheduler),
    }


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> dict:
    """Receive one Telegram update.

    Telegram echoes the secret we registered with setWebhook, which is the only
    thing separating a real update from a forged one: the URL itself is public.
    """
    settings = get_settings()
    secret = settings.telegram_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="TELEGRAM_WEBHOOK_SECRET is not set")
    if request.headers.get("x-telegram-bot-api-secret-token") != secret:
        raise HTTPException(status_code=403, detail="Bad webhook secret")

    update = await request.json()
    automation = await _get_automation(request)
    bot = request.app.state.bot or TelegramBot(automation)
    request.app.state.bot = bot
    # Telegram retries on any non-2xx, so never fail on a bad single update:
    # one malformed message would be redelivered forever.
    try:
        await run_in_threadpool(bot.handle, update)
    except Exception:
        logging.getLogger(__name__).exception("Webhook update failed")
    return {"ok": True}


@app.post("/jobs/run-cycle")
async def run_cycle(request: Request) -> dict:
    automation = await _get_automation(request)
    _authorize(request)
    return await run_in_threadpool(automation.run_cycle)


@app.post("/jobs/process-transcripts")
async def process_transcripts(request: Request) -> dict:
    automation = await _get_automation(request)
    _authorize(request)
    return await run_in_threadpool(automation.process_transcripts)


@app.post("/jobs/send-followups")
async def send_followups(request: Request) -> dict:
    automation = await _get_automation(request)
    _authorize(request)
    return await run_in_threadpool(automation.send_followups)


@app.post("/jobs/send-summary/{meeting_id}")
async def send_summary(meeting_id: str, request: Request) -> dict:
    """Dispatch a drafted meeting summary after manual review."""
    automation = await _get_automation(request)
    _authorize(request)
    try:
        return await run_in_threadpool(automation.send_meeting_summary, meeting_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _get_automation(request: Request) -> VegasAutomationService:
    if request.app.state.automation:
        return request.app.state.automation
    settings = get_settings()
    try:
        automation = await run_in_threadpool(build_automation, settings)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    request.app.state.automation = automation
    return automation


def _authorize(request: Request) -> None:
    token = get_settings().internal_job_token
    if not token:
        return
    if request.headers.get("authorization") != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="Invalid job token")
