#!/bin/sh
set -e

DATA_DIR="/app/data"
WORDS_FILE="$DATA_DIR/allowed_words.txt"

echo "[entrypoint] starting"

# One-time bootstrap
if [ ! -f "$WORDS_FILE" ]; then
  echo "[entrypoint] allowed_words.txt missing, running words-sync"
  python /app/cli.py words-sync
else
  echo "[entrypoint] allowed_words.txt present, skipping"
fi

exec "$@"