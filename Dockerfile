FROM python:3.12.8-slim

# --- system deps ---
RUN apt-get update && apt-get install -y \
    cron \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# --- env ---
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOME=/app

WORKDIR /app

# --- install uv ---
RUN pip install --no-cache-dir uv

# --- copy project ---
COPY pyproject.toml ./
RUN uv pip install --system --no-deps .

COPY . .

# --- create non-root user ---
RUN useradd -r -u 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

USER appuser

# Set timezone
ENV TZ=America/New_York
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# --- cron setup ---
# Daily used-word sync at 06:00 UTC
RUN echo "0 1 * * * cd /app && uv run python cli.py used-sync >> /app/data/cron.log 2>&1" > /app/cronfile \
    && crontab /app/cronfile

# --- expose ---
EXPOSE 8000

# --- start both cron + app ---
CMD ["sh", "-c", "cron && uv run uvicorn app:app --host 0.0.0.0 --port 8000"]