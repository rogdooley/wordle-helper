#!/bin/sh
set -e

DATA_DIR="/app/data"
WORDS_FILE="$DATA_DIR/allowed_words.txt"

echo "[entrypoint] starting"

if [ ! -f "$WORDS_FILE" ]; then
  echo "[entrypoint] allowed_words.txt missing, bootstrapping"

  if ! python /app/cli.py words-sync; then
    echo "[entrypoint] FATAL: words-sync failed on first boot"
    exit 1
  fi
fi

exec "$@"