from __future__ import annotations

import hashlib
import json
import logging
import logging.handlers
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from jinja2 import Environment, FileSystemLoader, select_autoescape

# -----------------------------
# Paths / config
# -----------------------------

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
TEMPLATES_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"

DB_PATH = DATA_DIR / "app.db"
ALLOWED_WORDS_PATH = DATA_DIR / "allowed_words.txt"
USED_WORDS_PATH = DATA_DIR / "used_words.json"

APP_SECRET_KEY = os.environ.get("APP_SECRET_KEY", "")
SESSION_TTL_SECONDS = 36 * 60 * 60  # 36 hours
PASSWORD_MIN_LEN = 15

TRUSTED_PROXY_IPS = {
    ip.strip()
    for ip in os.environ.get("TRUSTED_PROXY_IPS", "").split(",")
    if ip.strip()
}

# Cookie security: behind an HTTPS reverse proxy, keep Secure=True.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").strip().lower() not in {
    "0",
    "false",
    "no",
}

MAX_GUESSES = 5
INITIAL_GUESSES_SHOWN = 3

FAILS_TO_BAN = 3
BAN_DURATION = timedelta(hours=24)

AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
}

# -----------------------------
# Logging
# -----------------------------

DATA_DIR.mkdir(parents=True, exist_ok=True)


def _make_logger(name: str, filename: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    fh = logging.handlers.RotatingFileHandler(
        DATA_DIR / filename,
        maxBytes=5_000_000,
        backupCount=5,
    )

    class JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            payload = {
                "ts": datetime.utcnow().isoformat() + "Z",
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if hasattr(record, "extra_data"):
                payload.update(record.extra_data)
            return json.dumps(payload, separators=(",", ":"))

    fh.setFormatter(JsonFormatter())
    logger.addHandler(fh)
    logger.propagate = False
    return logger


def log_security_event(
    *,
    event: str,
    ip: str,
    outcome: str,
    user: str | None = None,
    reason: str | None = None,
    attempts: int | None = None,
    banned_until: datetime | None = None,
    extra: dict | None = None,
) -> None:
    payload: dict[str, object] = {
        "type": "security",
        "event": event,  # login / register
        "ip": ip,
        "outcome": outcome,  # success / fail / blocked
        "user": user or "anonymous",
    }

    if reason:
        payload["reason"] = reason

    if attempts is not None:
        payload["attempts"] = attempts

    if banned_until is not None:
        payload["banned_until"] = banned_until.isoformat()

    if extra:
        payload.update(extra)

    sec_logger.info(event, extra={"extra_data": payload})


app_logger = _make_logger("app", "app.log")
sec_logger = _make_logger("security", "security.log")

# -----------------------------
# Templates
# -----------------------------

jinja = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)

# -----------------------------
# Crypto / auth helpers
# -----------------------------

ph = PasswordHasher()  # Argon2id default
serializer = URLSafeTimedSerializer(APP_SECRET_KEY or "dev-unsafe-secret")


def require_secret_config() -> None:
    if not APP_SECRET_KEY:
        raise RuntimeError("APP_SECRET_KEY is required (set env var).")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def normalize_username(username: str) -> str:
    return username.strip().lower()


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    if peer in TRUSTED_PROXY_IPS:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            ip = xff.split(",")[0].strip()
            return ip or peer
    return peer


def sign_session(username: str) -> str:
    return serializer.dumps({"u": username})


def verify_session(token: str) -> str | None:
    try:
        data = serializer.loads(token, max_age=SESSION_TTL_SECONDS)
        u = data.get("u")
        if isinstance(u, str) and u:
            return u
        return None
    except BadSignature:
        return None


# -----------------------------
# DB
# -----------------------------

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


# -----------------------------
# Word lists
# -----------------------------


def load_allowed_words() -> list[str]:
    if not ALLOWED_WORDS_PATH.exists():
        raise RuntimeError(
            f"Missing {ALLOWED_WORDS_PATH}. Run: uv run python cli.py words-sync"
        )
    words: list[str] = []
    for line in ALLOWED_WORDS_PATH.read_text(encoding="utf-8").splitlines():
        w = line.strip().lower()
        if re.fullmatch(r"[a-z]{5}", w):
            words.append(w)
    return words


def load_used_words() -> set[str]:
    if not USED_WORDS_PATH.exists():
        return set()
    data = json.loads(USED_WORDS_PATH.read_text(encoding="utf-8"))
    used = data.get("used", [])
    out: set[str] = set()
    for w in used:
        if isinstance(w, str) and re.fullmatch(r"[a-z]{5}", w.lower().strip()):
            out.add(w.lower().strip())
    return out


# -----------------------------
# Solver
# -----------------------------

CellState = Literal["unknown", "green", "yellow", "gray"]


@dataclass(frozen=True)
class Guess:
    word: str
    states: tuple[CellState, CellState, CellState, CellState, CellState]


@dataclass
class Constraints:
    greens: dict[int, str]
    yellows: dict[str, set[int]]
    grays: set[str]
    min_counts: dict[str, int]
    max_counts: dict[str, int]


@dataclass(frozen=True)
class Contradiction:
    code: str
    detail: str


def derive_constraints(guesses: list[Guess]) -> tuple[Constraints, list[Contradiction]]:
    greens: dict[int, str] = {}
    yellows: dict[str, set[int]] = {}
    grays: set[str] = set()
    min_counts: dict[str, int] = {}
    max_counts: dict[str, int] = {}
    contradictions: list[Contradiction] = []

    for g in guesses:
        word = g.word
        states = g.states

        if not re.fullmatch(r"[a-z]{5}", word):
            contradictions.append(
                Contradiction("bad_guess_word", f"Invalid guess word: {word!r}")
            )
            continue

        present_counts: dict[str, int] = {}
        gray_counts: dict[str, int] = {}

        for i, (ch, st) in enumerate(zip(word, states)):
            if st == "green":
                if i in greens and greens[i] != ch:
                    contradictions.append(
                        Contradiction(
                            "green_conflict",
                            f"Position {i + 1} cannot be both {greens[i]} and {ch}",
                        )
                    )
                greens[i] = ch
                present_counts[ch] = present_counts.get(ch, 0) + 1

            elif st == "yellow":
                yellows.setdefault(ch, set()).add(i)
                present_counts[ch] = present_counts.get(ch, 0) + 1

            elif st == "gray":
                gray_counts[ch] = gray_counts.get(ch, 0) + 1

        for ch, n in present_counts.items():
            min_counts[ch] = max(min_counts.get(ch, 0), n)

        for ch in gray_counts.keys():
            if ch in present_counts:
                # Gray + present in same guess => upper bound = present count in that guess.
                max_counts[ch] = min(
                    max_counts.get(ch, present_counts[ch]), present_counts[ch]
                )

    any_present = set(min_counts.keys())
    for g in guesses:
        for ch, st in zip(g.word, g.states):
            if st == "gray" and ch not in any_present:
                grays.add(ch)

    for ch, mn in min_counts.items():
        mx = max_counts.get(ch)
        if mx is not None and mn > mx:
            contradictions.append(
                Contradiction(
                    "count_conflict",
                    f"Letter {ch} requires at least {mn} but at most {mx}",
                )
            )

    return Constraints(greens, yellows, grays, min_counts, max_counts), contradictions


def word_matches(w: str, c: Constraints) -> bool:
    for i, ch in c.greens.items():
        if w[i] != ch:
            return False

    for ch in c.grays:
        if ch in w:
            return False

    for ch, forb in c.yellows.items():
        if ch not in w:
            return False
        for i in forb:
            if w[i] == ch:
                return False

    for ch, mn in c.min_counts.items():
        if w.count(ch) < mn:
            return False
    for ch, mx in c.max_counts.items():
        if w.count(ch) > mx:
            return False

    return True


def solve_words(
    *, allowed: list[str], used: set[str], guesses: list[Guess]
) -> tuple[list[str], Constraints, list[Contradiction]]:
    c, contradictions = derive_constraints(guesses)
    candidates: list[str] = [
        w for w in allowed if word_matches(w, c)
    ]  # [w for w in allowed if w not in used and word_matches(w, c)]
    if not candidates and not contradictions:
        contradictions.append(
            Contradiction(
                "no_candidates",
                "No candidates remain. A color marking may be incorrect (often repeated letters).",
            )
        )
    return candidates, c, contradictions


# -----------------------------
# Anti-brute-force
# -----------------------------


def is_ip_banned(conn: sqlite3.Connection, ip: str) -> tuple[bool, datetime | None]:
    row = conn.execute(
        "SELECT banned_until_utc FROM login_attempts WHERE ip = ?", (ip,)
    ).fetchone()
    if not row:
        return False, None
    val = row["banned_until_utc"]
    if not val:
        return False, None
    banned_until = datetime.fromisoformat(val)
    return (now_utc() < banned_until), banned_until


def record_login_failure(
    conn: sqlite3.Connection, ip: str
) -> tuple[bool, datetime | None, int]:
    """
    Returns: (now_banned, banned_until, attempt_count)
    Decay: counters reset only on successful login from that IP.
    """
    now = now_utc()
    row = conn.execute(
        "SELECT attempt_count, banned_until_utc FROM login_attempts WHERE ip = ?", (ip,)
    ).fetchone()

    if not row:
        attempt_count = 1
        banned_until = None
        if attempt_count >= FAILS_TO_BAN:
            banned_until = now + BAN_DURATION
        conn.execute(
            "INSERT INTO login_attempts (ip, attempt_count, banned_until_utc, last_attempt_at_utc) VALUES (?, ?, ?, ?)",
            (
                ip,
                attempt_count,
                banned_until.isoformat() if banned_until else None,
                now.isoformat(),
            ),
        )
        return (banned_until is not None), banned_until, attempt_count

    attempt_count = int(row["attempt_count"]) + 1
    banned_until_val = row["banned_until_utc"]
    banned_until = (
        datetime.fromisoformat(banned_until_val) if banned_until_val else None
    )

    now_banned = False
    new_banned_until = banned_until
    if attempt_count >= FAILS_TO_BAN:
        new_banned_until = now + BAN_DURATION
        now_banned = True

    conn.execute(
        "UPDATE login_attempts SET attempt_count = ?, banned_until_utc = ?, last_attempt_at_utc = ? WHERE ip = ?",
        (
            attempt_count,
            new_banned_until.isoformat() if new_banned_until else None,
            now.isoformat(),
            ip,
        ),
    )
    return now_banned, new_banned_until, attempt_count


def clear_login_failures_on_success(conn: sqlite3.Connection, ip: str) -> None:
    conn.execute("DELETE FROM login_attempts WHERE ip = ?", (ip,))


# -----------------------------
# FastAPI
# -----------------------------

require_secret_config()
init_db()

app = FastAPI()
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def current_user(request: Request) -> str | None:
    token = request.cookies.get("session", "")
    if not token:
        return None
    return verify_session(token)


def require_auth(request: Request) -> str:
    if not AUTH_ENABLED:
        return "anonymous"
    u = current_user(request)
    if not u:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return u


@app.middleware("http")
async def add_headers(request: Request, call_next):
    resp = await call_next(request)

    csp = "; ".join(
        [
            "default-src 'self'",
            "img-src 'self' data:",
            "style-src 'self'",
            "script-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "frame-ancestors 'none'",
            "form-action 'self'",
        ]
    )

    resp.headers["Content-Security-Policy"] = csp
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return resp


@app.middleware("http")
async def structured_request_logger(request: Request, call_next):
    start = now_utc()
    ip = client_ip(request)
    method = request.method
    path = request.url.path
    ua = request.headers.get("user-agent", "")
    request_id = uuid.uuid4().hex

    if AUTH_ENABLED:
        user = current_user(request) or "anonymous"
    else:
        user = "anonymous"

    response = await call_next(request)

    duration_ms = int((now_utc() - start).total_seconds() * 1000)

    app_logger.info(
        "http_request",
        extra={
            "extra_data": {
                "type": "http",
                "request_id": request_id,
                "ip": ip,
                "user": user or "anonymous",
                "method": method,
                "path": path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "ua": ua,
            }
        },
    )

    return response


def render(name: str, **ctx: Any) -> HTMLResponse:
    tpl = jinja.get_template(name)
    return HTMLResponse(tpl.render(**ctx))


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return RedirectResponse("/solve", status_code=303)


@app.get("/readme", response_class=HTMLResponse)
def readme(request: Request):
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    return render(
        "readme.html",
        request=request,
        user=current_user(request),
        content_pre=text,
        title="README",
    )


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if not AUTH_ENABLED:
        return RedirectResponse("/solve", status_code=303)
    return render("login.html", request=request, error=None, title="Login")


@app.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request, username: str = Form(...), password: str = Form(...)
):
    if not AUTH_ENABLED:
        return RedirectResponse("/solve", status_code=303)

    u = normalize_username(username)
    ip = client_ip(request)

    conn = db()
    try:
        banned, _until = is_ip_banned(conn, ip)
        if banned:
            # sec_logger.info(f"ip={ip} action=login status=blocked reason=ip_banned")
            log_security_event(
                event="login",
                ip=ip,
                user=u,
                outcome="blocked",
                reason="ip_banned",
            )
            return render(
                "login.html", request=request, error="Login failed", title="Login"
            )

        row = conn.execute(
            "SELECT id, password_hash, is_active FROM users WHERE username = ?", (u,)
        ).fetchone()

        ok = False
        if row and int(row["is_active"]) == 1:
            try:
                ph.verify(row["password_hash"], password)
                ok = True
            except VerifyMismatchError:
                ok = False

        if not ok:
            now_banned, new_until, attempt_count = record_login_failure(conn, ip)
            conn.commit()
            # sec_logger.info(
            #     f"ip={ip} action=login status=fail attempts={attempt_count} now_banned={now_banned} banned_until={new_until.isoformat() if new_until else None}"
            # )
            log_security_event(
                event="login",
                ip=ip,
                outcome="fail",
                user=u,
                reason="invalid_credentials",
                attempts=attempt_count,
                banned_until=new_until if now_banned else None,
            )
            return render(
                "login.html", request=request, error="Login failed", title="Login"
            )

        clear_login_failures_on_success(conn, ip)
        conn.commit()
        # sec_logger.info(f"ip={ip} action=login status=success user={u}")
        log_security_event(
            event="login",
            ip=ip,
            outcome="success",
            user=u,
        )

        resp = RedirectResponse("/solve", status_code=303)
        token = sign_session(u)
        resp.set_cookie(
            "session",
            token,
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            samesite="lax",
            secure=COOKIE_SECURE,
            path="/",
        )
        return resp
    finally:
        conn.close()


@app.post("/logout")
def logout(request: Request):
    return RedirectResponse("/solve", status_code=303)


@app.get("/register", response_class=HTMLResponse)
def register_form(request: Request, code: str = ""):
    return render(
        "register.html", request=request, code=code, error=None, title="Register"
    )


@app.post("/register", response_class=HTMLResponse)
def register_submit(
    request: Request,
    code: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
):
    if not AUTH_ENABLED:
        return RedirectResponse("/solve", status_code=303)

    u = normalize_username(username)
    ip = client_ip(request)

    conn = db()
    try:
        banned, _ = is_ip_banned(conn, ip)
        if banned:
            # sec_logger.info(f"ip={ip} action=register status=blocked reason=ip_banned")
            log_security_event(
                event="register",
                ip=ip,
                user=u,
                outcome="blocked",
                reason="ip_banned",
            )
            return render(
                "register.html",
                request=request,
                code="",
                error="Registration failed",
                title="Register",
            )

        if len(password) < PASSWORD_MIN_LEN:
            record_login_failure(conn, ip)
            conn.commit()
            # sec_logger.info(
            #     f"ip={ip} action=register status=fail reason=password_short user={u}"
            # )
            log_security_event(
                event="register",
                ip=ip,
                user=u,
                outcome="fail",
                reason="password_short",
            )
            return render(
                "register.html",
                request=request,
                code="",
                error="Registration failed",
                title="Register",
            )

        code = code.strip()
        code_hash = sha256_hex(code)

        inv = conn.execute(
            "SELECT id, intended_username, expires_at_utc, used_at_utc FROM invites WHERE code_hash = ?",
            (code_hash,),
        ).fetchone()

        ok = True
        if not inv:
            ok = False
        else:
            expires_at = datetime.fromisoformat(inv["expires_at_utc"])
            if now_utc() > expires_at:
                ok = False
            if inv["used_at_utc"] is not None:
                ok = False
            if normalize_username(inv["intended_username"]) != u:
                ok = False

        if not ok:
            record_login_failure(conn, ip)
            conn.commit()
            # sec_logger.info(f"ip={ip} action=register status=fail user={u}")
            log_security_event(
                event="register",
                ip=ip,
                user=u,
                outcome="fail",
                reason="invalid invite",
            )
            return render(
                "register.html",
                request=request,
                code="",
                error="Registration failed",
                title="Register",
            )

        pw_hash = ph.hash(password)
        created_at = now_utc().isoformat()
        try:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, created_at_utc, is_active) VALUES (?, ?, ?, 1)",
                (u, pw_hash, created_at),
            )
        except sqlite3.IntegrityError:
            record_login_failure(conn, ip)
            conn.commit()
            # sec_logger.info(
            #     f"ip={ip} action=register status=fail reason=username_taken user={u}"
            # )
            log_security_event(
                event="register",
                ip=ip,
                user=u,
                outcome="fail",
                reason="username_taken",
            )
            return render(
                "register.html",
                request=request,
                code="",
                error="Registration failed",
                title="Register",
            )

        row_id = cur.lastrowid
        if row_id is None:
            raise RuntimeError("User insert did not return rowid")

        user_id: int = row_id
        used_at = now_utc().isoformat()
        conn.execute(
            "UPDATE invites SET used_at_utc = ?, used_by_user_id = ? WHERE id = ?",
            (used_at, user_id, int(inv["id"])),
        )
        clear_login_failures_on_success(conn, ip)
        conn.commit()

        # sec_logger.info(
        #     f"ip={ip} action=register status=success user={u} user_id={user_id} invite_id={int(inv['id'])}"
        # )
        log_security_event(
            event="register",
            ip=ip,
            outcome="success",
            user=u,
            extra={
                "user_id": user_id,
                "invite_id": int(inv["id"]),
            },
        )

        resp = RedirectResponse("/solve", status_code=303)
        token = sign_session(u)
        resp.set_cookie(
            "session",
            token,
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            samesite="lax",
            secure=COOKIE_SECURE,
            path="/",
        )
        return resp
    finally:
        conn.close()


@app.get("/solve", response_class=HTMLResponse)
def solve_page(request: Request, user: str = Depends(require_auth)):
    return render(
        "solve.html",
        request=request,
        user=user,
        max_guesses=MAX_GUESSES,
        initial_rows=INITIAL_GUESSES_SHOWN,
        title="Solve",
    )


@app.post("/solve", response_class=HTMLResponse)
async def solve_submit(request: Request, user: str = Depends(require_auth)):
    payload = await request.json()
    guesses_in = payload.get("guesses", [])
    debug = bool(payload.get("debug", False))

    guesses: list[Guess] = []
    for item in guesses_in[:MAX_GUESSES]:
        w = str(item.get("word", "")).strip().lower()
        states_raw = item.get("states", [])
        if not isinstance(states_raw, list) or len(states_raw) != 5:
            continue

        states = [str(s) for s in states_raw]
        if any(s not in {"unknown", "green", "yellow", "gray"} for s in states):
            continue

        guesses.append(Guess(w, tuple(states)))  # type: ignore[arg-type]

    # Only guesses with all colors set count
    locked_guesses = [g for g in guesses if "unknown" not in g.states]

    # Enforce minimum deduction threshold
    if len(locked_guesses) < INITIAL_GUESSES_SHOWN:
        return render(
            "_results.html",
            request=request,
            user=user,
            remaining=None,
            fresh_candidates=[],
            used_candidates=[],
            need_more=INITIAL_GUESSES_SHOWN - len(locked_guesses),
            debug=False,
            constraints=None,
            contradictions=[],
        )

    allowed = load_allowed_words()
    used = load_used_words()

    all_candidates, constraints, contradictions = solve_words(
        allowed=allowed,
        used=set(),  # do NOT exclude used here
        guesses=locked_guesses,  # important
    )

    fresh = [w for w in all_candidates if w not in used]
    previously_used = [w for w in all_candidates if w in used]

    app_logger.info(
        "solver_run",
        extra={
            "extra_data": {
                "type": "solver",
                "ip": client_ip(request),
                "user": user or "anonymous",
                "locked_guesses": len(locked_guesses),
                "remaining_candidates": len(all_candidates),
                "debug": debug,
            }
        },
    )

    return render(
        "_results.html",
        request=request,
        user=user,
        remaining=len(all_candidates),
        fresh_candidates=fresh,
        used_candidates=previously_used,
        debug=debug,
        constraints=constraints if debug else None,
        contradictions=contradictions if debug else None,
        need_more=0,
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
