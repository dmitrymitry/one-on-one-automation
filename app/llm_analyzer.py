import json
import logging
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .models import (
    CalendarMeeting,
    HostTask,
    Manager,
    MeetingReminder,
    MeetingSummary,
    ReminderCarryOver,
    ReminderCommitment,
    ReminderOpenTopic,
    SummaryTask,
    SummaryTheme,
)
from .prompts import build_host_tasks_prompt, build_reminder_prompt, build_summary_prompt

LOGGER = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)


class RawCommitment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    who: str = ""
    what: str
    timing: str = ""
    since: str = ""


class RawCarryOver(BaseModel):
    model_config = ConfigDict(extra="ignore")

    what: str
    who: str = ""
    timing: str = ""
    since: str = ""


class RawOpenTopic(BaseModel):
    model_config = ConfigDict(extra="ignore")

    topic: str
    question: str = ""
    since: str = ""


class RawReminder(BaseModel):
    model_config = ConfigDict(extra="ignore")

    commitments: list[RawCommitment] = Field(default_factory=list)
    carried_over: list[RawCarryOver] = Field(default_factory=list)
    open_topics: list[RawOpenTopic] = Field(default_factory=list)


class RawHostTask(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task: str
    deadline: str = ""


class RawHostTasks(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tasks: list[RawHostTask] = Field(default_factory=list)


class RawSummaryTask(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str
    responsible: str = ""
    expected_result: str = ""
    deadline_note: str = ""


class RawSummaryTheme(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    context: str = ""
    tasks: list[RawSummaryTask] = Field(default_factory=list)


class RawSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    topics: list[str] = Field(default_factory=list)
    themes: list[RawSummaryTheme] = Field(default_factory=list)


class LLMAnalyzer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = settings.llm_provider.casefold()
        self.client = self._build_client()

    def prepare_reminder(
        self,
        manager: Manager,
        meeting: CalendarMeeting,
        followups: list[str],
    ) -> MeetingReminder:
        prompt = build_reminder_prompt(manager, meeting, followups)
        raw = self._generate(prompt, RawReminder)
        return normalize_reminder(raw)

    def extract_host_tasks(
        self,
        followup: str,
        host_names: list[str],
        meeting_date: str,
    ) -> list[HostTask]:
        prompt = build_host_tasks_prompt(followup, host_names, meeting_date)
        raw = self._generate(prompt, RawHostTasks)
        return normalize_host_tasks(raw)

    def summarize(
        self,
        manager: Manager,
        meeting: CalendarMeeting,
        transcript: str,
    ) -> MeetingSummary:
        prompt = build_summary_prompt(manager, meeting, transcript)
        raw = self._generate(prompt, RawSummary)
        return normalize_summary(raw)

    def _build_client(self) -> Any:
        if self.provider == "gemini":
            if not self.settings.gemini_api_key_list:
                raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")
            from google import genai

            return {key: genai.Client(api_key=key) for key in self.settings.gemini_api_key_list}
        if self.provider == "openai":
            if not self.settings.openai_api_key:
                raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
            from openai import OpenAI

            kwargs = {"api_key": self.settings.openai_api_key}
            if self.settings.openai_base_url:
                kwargs["base_url"] = self.settings.openai_base_url
            return OpenAI(**kwargs)
        raise ValueError("LLM_PROVIDER must be gemini or openai")

    def _generate(self, prompt: str, schema: type[ModelT]) -> ModelT:
        if self.provider == "gemini":
            return self._generate_gemini(prompt, schema)
        return self._generate_openai(prompt, schema)

    # Transient or capacity errors: worth trying a weaker model instead of failing.
    FALLBACK_CODES = (429, 500, 502, 503, 504)
    # Out of daily quota. Unlike an overload, another project's key fixes it.
    QUOTA_CODE = 429

    def _generate_gemini(self, prompt: str, schema: type[ModelT]) -> ModelT:
        """Walk the model chain, strongest first, until one answers.

        The newest models are exactly the ones that return 503 under load, so a
        follow-up written by a weaker model beats no follow-up at all. A 400
        never falls through: that is our bug, and hiding it would be worse.
        """
        chain = self.settings.gemini_model_list
        keys = self.settings.gemini_api_key_list
        last_error: Exception | None = None
        attempt = 0
        for model in chain:
            for key in keys:
                try:
                    response = self.client[key].models.generate_content(
                        model=model,
                        contents=prompt,
                        config={
                            "response_mime_type": "application/json",
                            "response_json_schema": schema.model_json_schema(),
                        },
                    )
                    if attempt:
                        LOGGER.warning(
                            "Gemini answered on attempt %s: model %s, key %s",
                            attempt + 1,
                            model,
                            _key_label(key),
                        )
                    return parse_json_response(response.text, schema)
                except Exception as exc:
                    status = _status_code(exc)
                    if status not in self.FALLBACK_CODES:
                        raise
                    last_error = exc
                    attempt += 1
                    LOGGER.warning(
                        "Gemini %s/%s failed (%s), trying next",
                        model,
                        _key_label(key),
                        status,
                    )
                    # An overloaded model stays overloaded whoever asks: swapping
                    # keys only helps when the wall is a per-project quota.
                    if status != self.QUOTA_CODE:
                        break
        raise RuntimeError(
            f"No Gemini model in {chain} answered on {len(keys)} key(s)"
        ) from last_error

    def _generate_openai(self, prompt: str, schema: type[ModelT]) -> ModelT:
        response = self.client.chat.completions.create(
            model=self.settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        content = response.choices[0].message.content or "{}"
        return parse_json_response(content, schema)


def parse_json_response(content: str, schema: type[ModelT]) -> ModelT:
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        return schema.model_validate(json.loads(cleaned))
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("LLM returned invalid JSON") from exc


def normalize_reminder(raw: RawReminder) -> MeetingReminder:
    commitments = tuple(
        ReminderCommitment(
            who=sanitize_text(item.who, 120),
            what=what,
            timing=sanitize_text(item.timing, 120),
            since=sanitize_text(item.since, 120),
        )
        for item in raw.commitments
        if (what := sanitize_text(item.what, 400))
    )
    carried_over = tuple(
        ReminderCarryOver(
            what=what,
            who=sanitize_text(item.who, 120),
            timing=sanitize_text(item.timing, 120),
            since=sanitize_text(item.since, 120),
        )
        for item in raw.carried_over
        if (what := sanitize_text(item.what, 400))
    )
    open_topics: list[ReminderOpenTopic] = []
    seen: set[str] = set()
    for item in raw.open_topics:
        topic = sanitize_text(item.topic, 400)
        if not topic or topic.casefold() in seen:
            continue
        seen.add(topic.casefold())
        open_topics.append(
            ReminderOpenTopic(
                topic=topic,
                question=sanitize_text(item.question, 300),
                since=sanitize_text(item.since, 120),
            )
        )
    return MeetingReminder(
        commitments=commitments,
        carried_over=carried_over,
        open_topics=tuple(open_topics[:10]),
    )


def normalize_host_tasks(raw: RawHostTasks) -> list[HostTask]:
    tasks: list[HostTask] = []
    seen: set[str] = set()
    for item in raw.tasks:
        task = sanitize_text(item.task, 300)
        if not task or task.casefold() in seen:
            continue
        seen.add(task.casefold())
        deadline = item.deadline.strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
            deadline = ""
        tasks.append(HostTask(task=task, deadline=deadline))
    return tasks


def normalize_summary(raw: RawSummary) -> MeetingSummary:
    topics = tuple(cleaned for topic in raw.topics if (cleaned := sanitize_text(topic, 200)))
    themes = tuple(theme for item in raw.themes if (theme := _normalize_theme(item)) is not None)
    return MeetingSummary(topics=topics, themes=themes)


def _normalize_theme(item: RawSummaryTheme) -> SummaryTheme | None:
    title = sanitize_text(item.title, 120)
    if not title:
        return None
    tasks = tuple(
        task for raw_task in item.tasks if (task := _normalize_summary_task(raw_task)) is not None
    )
    return SummaryTheme(title=title.upper(), context=sanitize_text(item.context, 800), tasks=tasks)


def _normalize_summary_task(item: RawSummaryTask) -> SummaryTask | None:
    action = sanitize_text(item.action, 500)
    if not action:
        return None
    return SummaryTask(
        action=action,
        responsible=sanitize_text(item.responsible, 200),
        expected_result=sanitize_text(item.expected_result, 500),
        deadline_note=sanitize_text(item.deadline_note, 150),
    )


FORBIDDEN_CHARS = "*#_~`[]@"
_FORBIDDEN_TABLE = str.maketrans("", "", FORBIDDEN_CHARS)


def sanitize_text(value: str, limit: int = 1000) -> str:
    """Drop markdown characters and stray tags, keeping plain names intact."""
    return " ".join(value.translate(_FORBIDDEN_TABLE).split())[:limit]


def _key_label(key: str) -> str:
    """Name a key in logs without printing it."""
    return f"...{key[-4:]}" if len(key) > 4 else "key"


def _status_code(exc: Exception) -> int | None:
    """HTTP status behind an SDK error, whichever attribute it hides it in."""
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None
