FROM python:3.12.8-slim

# System deps:
# - ca-certificates: HTTPS validation for word list sync (GitHub / FiveForks)
# - curl: handy for debugging inside container
# - cron: daily used-word sync
# - tzdata: local timezone for cron schedules
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    cron \
    tzdata \
  && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOME=/app \
    TZ=America/New_York

WORKDIR /app

RUN pip install --no-cache-dir uv

RUN useradd -r -u 10001 appuser

COPY pyproject.toml /app/pyproject.toml
RUN uv venv && uv sync --no-dev

COPY . /app

RUN mkdir -p /app/data \
  && chown -R appuser:appuser /app

# Cron: daily used word sync at 06:00 local time (container TZ)
RUN printf "0 6 * * * cd /app && uv run python cli.py used-sync >> /app/data/cron.log 2>&1\n" > /etc/cron.d/wordle \
  && chmod 0644 /etc/cron.d/wordle \
  && crontab /etc/cron.d/wordle

EXPOSE 8000

CMD ["sh", "-c", "cron && exec su -s /bin/sh -c 'uv run uvicorn app:app --host 0.0.0.0 --port 8000' appuser"]
