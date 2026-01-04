FROM python:3.13-slim

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

# Download supercronic (static binary)
ADD https://github.com/aptible/supercronic/releases/download/v0.2.29/supercronic-linux-arm64 /usr/local/bin/supercronic
RUN chmod +x /usr/local/bin/supercronic

# App code + cron
COPY . /app
COPY cron/wordle.cron /app/wordle.cron

# Create non-root user (no home needed now)
RUN useradd -u 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app


USER appuser

EXPOSE 8000

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["docker-entrypoint.sh"]

CMD ["sh", "-c", "supercronic /app/wordle.cron & exec python -m uvicorn app:app --host 0.0.0.0 --port 8000"]