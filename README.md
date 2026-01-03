# Wordle Assistant (Invite-Only)

This is a small private web app that helps you **deduce Wordle answers** from a sequence of guesses.

It’s deliberately not a “classic solver” where you type constraints once and it spits out words.
Instead, you enter up to **5 guesses** and mark each letter as:

- **Green**: right letter, right place
- **Yellow**: right letter, wrong place
- **Gray**: not in the word

The app then narrows down **only possible answers** (and excludes already-used answers by default).

This project is meant to stay simple and enjoyable for a couple years.

---

## How to think about deduction

Wordle deduction is about **constraints**:

- Green means: “This position is fixed.”
- Yellow means: “This letter exists, but not here.”
- Gray means: “This letter doesn’t exist.”  
  (Except when you’re dealing with repeated letters — then it might mean “no more of this letter than we already confirmed.”)

As you add guesses, you add more constraints. The list of possible answers shrinks.

If you ever get **zero** possible answers, it usually means:

- You clicked the wrong color on a letter, or
- A repeated-letter situation caused a contradiction (example: one guess suggests there are 2 L’s, another suggests there’s only 1)

The app can show a “why” panel to help you spot contradictions.

---

## For technical readers: design overview

- **FastAPI + Jinja templates**
- **No CDNs**: all CSS/JS is served locally
- **Dark-mode only**
- **Invite-only registration**
- **Argon2** password hashing
- Password length: **>= 15**
- Login error is always: **“Login failed”**
- Anti-brute-force:
  - 3 failed attempts per IP -> ban for 24h
  - “Decay” rule: counters reset only after a successful login from that IP
  - Banned attempts are logged and uniformly rejected
- Reverse proxy deployment:
  - App binds to **127.0.0.1** by default
  - Uses X-Forwarded-For only if the request comes from a trusted proxy IP

---

## Data sources

This project caches two local files under `data/`:

- `allowed_words.txt` — allowed 5-letter guesses list (downloaded via `cli.py words-sync`)
- `used_words.json` — used answers list (downloaded via `cli.py used-sync`), excluded by default

The app does **not** scrape websites at runtime.

---

## Setup (local)

### 1) Create venv and install deps (uv)

```bash
uv venv
uv sync
```

### 2) Initialize word lists

Download the allowed guesses list locally:

```bash
uv run python cli.py words-sync
```

Fetch the used answers list (through yesterday) locally:

```bash
uv run python cli.py used-sync
```

### 3) Create an invite (CLI)

Set a base URL for the invite link you’ll text/email:

```bash
export BASE_URL="https://wordle.example.com"
uv run python cli.py invite-create --username "someone"
```

That prints a link like:

```
https://wordle.example.com/register?code=XXXX-XXXX-XXXX
```

### 4) Run the app locally

```bash
export APP_SECRET_KEY="change-me-long-random"
export TRUSTED_PROXY_IPS="127.0.0.1"   # set to your reverse proxy IP(s) in prod
uv run uvicorn app:app --host 127.0.0.1 --port 8000
```

---

## Cron jobs

You said cron-only updates for used words. Run daily:

```cron
0 6 * * * cd /path/to/wordle-assistant && /usr/bin/env -i \
  PATH=/usr/bin:/bin \
  BASE_URL="https://wordle.example.com" \
  uv run python cli.py used-sync >> data/cron.log 2>&1
```

Optional: re-sync allowed words rarely (it’s stable):

```cron
0 5 1 * * cd /path/to/wordle-assistant && uv run python cli.py words-sync >> data/cron.log 2>&1
```

---

## Why solvers can be wrong (teachable moment)

1. **Repeated letters**: a single wrong click can “prove” there are 2 of a letter when there’s only 1.

2. **Human mistakes**: one bad color choice can make the puzzle impossible. The contradiction panel helps you find these.

3. **Word list differences**: different solvers use different lists. This app uses locally cached files.

---

## Quick use

1. Log in.
2. Enter a guess.
3. Click each tile to set the color (unknown → green → yellow → gray).
4. Click **Lock** for that row.
5. Repeat (up to 5).
6. Click **Reset All** to start over.

Use the **“Show why”** toggle if you hit a contradiction.
