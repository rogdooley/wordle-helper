FROM python:3.12.8-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    cron \
    tzdata \
  && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=America/New_York

WORKDIR /app

# Install uv ONLY for build-time dependency resolution
RUN pip install --no-cache-dir uv

# Copy dependency metadata
COPY pyproject.toml /app/pyproject.toml

# Install deps into system Python (no venv, no cache at runtime)
RUN uv pip install --system .

# Copy application
COPY . /app

# Create non-root user (no home needed now)
RUN useradd -u 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

# Cron: daily used-word sync at 06:00 local time
RUN printf "0 6 * * * cd /app && python cli.py used-sync >> /app/data/cron.log 2>&1\n" \
    > /etc/cron.d/wordle \
    && chmod 0644 /etc/cron.d/wordle \
    && crontab /etc/cron.d/wordle

USER appuser

EXPOSE 8000

CMD ["sh", "-c", "cron && exec python -m uvicorn app:app --host 0.0.0.0 --port 8000"]