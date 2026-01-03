\
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "app.db"
ALLOWED_WORDS_PATH = DATA_DIR / "allowed_words.txt"
USED_WORDS_PATH = DATA_DIR / "used_words.json"

INVITE_TTL_HOURS = 48

TABATKINS_WORDS_URL = "https://raw.githubusercontent.com/tabatkins/wordle-list/main/words"
FIVEFORKS_BLOCK_URL = "https://www.fiveforks.com/wordle/block/"

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  created_at_utc TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS invites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  intended_username TEXT NOT NULL,
  code_hash TEXT NOT NULL UNIQUE,
  created_at_utc TEXT NOT NULL,
  expires_at_utc TEXT NOT NULL,
  used_at_utc TEXT,
  used_by_user_id INTEGER,
  FOREIGN KEY(used_by_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS login_attempts (
  ip TEXT PRIMARY KEY,
  attempt_count INTEGER NOT NULL,
  banned_until_utc TEXT,
  last_attempt_at_utc TEXT NOT NULL
);
"""

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def normalize_username(username: str) -> str:
    return username.strip().lower()

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    conn = db()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()

def token_groups(nbytes: int = 18) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = secrets.token_bytes(nbytes)
    out = []
    for b in raw:
        out.append(alphabet[b % len(alphabet)])
    s = "".join(out)
    return "-".join([s[i : i + 4] for i in range(0, len(s), 4)])

def words_sync() -> None:
    print(f"Downloading allowed words from: {TABATKINS_WORDS_URL}")
    r = httpx.get(TABATKINS_WORDS_URL, timeout=30)
    r.raise_for_status()
    text = r.text
    words: list[str] = []
    for w in text.split():
        w = w.strip().lower()
        if re.fullmatch(r"[a-z]{5}", w):
            words.append(w)
    ALLOWED_WORDS_PATH.write_text("\n".join(words) + "\n", encoding="utf-8")
    print(f"Wrote {len(words)} words -> {ALLOWED_WORDS_PATH}")

def used_sync() -> None:
    print(f"Fetching used answers from: {FIVEFORKS_BLOCK_URL}")
    r = httpx.get(FIVEFORKS_BLOCK_URL, timeout=30, headers={"User-Agent": "wordle-assistant/1.0"})
    r.raise_for_status()
    html = r.text

    found = re.findall(r"\b[A-Z]{5}\b", html)
    used = sorted({w.lower() for w in found})

    data: dict[str, Any] = {
        "source": "fiveforks",
        "last_synced_utc": now_utc().isoformat(),
        "used": used,
    }
    USED_WORDS_PATH.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {len(used)} used words -> {USED_WORDS_PATH}")

def invite_create(intended_username: str) -> None:
    base_url = os.environ.get("BASE_URL", "").rstrip("/")
    if not base_url:
        raise SystemExit("BASE_URL is required (e.g., https://wordle.example.com)")

    init_db()
    code = token_groups()
    code_hash = sha256_hex(code)
    created = now_utc()
    expires = created + timedelta(hours=INVITE_TTL_HOURS)

    u = normalize_username(intended_username)

    conn = db()
    try:
        conn.execute(
            "INSERT INTO invites (intended_username, code_hash, created_at_utc, expires_at_utc) VALUES (?, ?, ?, ?)",
            (u, code_hash, created.isoformat(), expires.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

    link = f"{base_url}/register?code={code}"
    print(link)

def main() -> None:
    p = argparse.ArgumentParser(prog="cli.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("words-sync", help="Download allowed words list into data/allowed_words.txt")
    sub.add_parser("used-sync", help="Download used answers list into data/used_words.json")

    inv = sub.add_parser("invite-create", help="Create a 48h invite link tied to a specific username")
    inv.add_argument("--username", required=True)

    args = p.parse_args()

    if args.cmd == "words-sync":
        words_sync()
    elif args.cmd == "used-sync":
        used_sync()
    elif args.cmd == "invite-create":
        invite_create(args.username)
    else:
        raise SystemExit(f"Unknown cmd: {args.cmd}")

if __name__ == "__main__":
    main()
