FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --no-cache-dir .

# Cloud Run assigns the port and scales to zero: no in-process scheduler and no
# long polling here. Cloud Scheduler calls /jobs/run-cycle, Telegram posts to
# /telegram/webhook, and the container sleeps in between.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
