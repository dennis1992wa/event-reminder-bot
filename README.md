# Event Reminder Bot

A small, self-contained Telegram + email reminder daemon that watches a CSV
file of renewals/subscriptions and sends one consolidated reminder message
per due day (per channel), at a fixed daily run time. Stdlib only — no
third-party packages.

## Purpose

Remind Dennis (and the named event owners) before recurring payments and
contract renewals expire, via:

- Email (SMTP)
- Telegram bot message

## Main features

### 1. Data source

Events live in `data/event.csv` with columns:

```
id,people,status,expires_on,early_remind_months,event,type,amount,currency,paid_on,note
```

- `id`: stable code (R001, R002, …), unique, never reused after deletion.
- `people`: free text, may list several people.
- `status`: `active` or `inactive`. Only `active` rows are reminded.
- `expires_on`: `YYYY-MM-DD`.
- `early_remind_months`: optional integer N. If present, early reminder
  points at N, N−0.5, N−1, … months before expiry (calendar-month math,
  half = 15 days) are added before the standard points.
- Remaining columns are shown in the reminder message.

After every run the CSV is re-sorted ascending by `expires_on` with
`inactive` rows at the bottom; cell values are byte-identical (values are
preserved exactly, including UTF-8 Chinese). A backup `event.csv.bak` is
written before each save.

### 2. Reminder logic

- One run per day at `CHECK_HOUR` (default 11) in `TZ_NAME`
  (default `Europe/London`); DST handled via `zoneinfo`.
- Standard points: 30, 21, 14, 7, 4, 0 days before expiry, e.g. OVO
  expiring 2026-12-19 fires 11-19 / 11-28 / 12-05 / 12-12 / 12-15 / 12-19.
- Missed days (e.g. the NAS was off) collapse into a single catch-up
  message at the next run, listing the missed offsets — never a backlog of
  daily messages, never a retroactive timestamp.
- Each point fires once per cycle; `data/state.json` records what has been
  sent per row, keyed by (id, expires_on, early_remind_months, point).
- Changing `expires_on` voids the old cycle silently and starts a new one.
- A row added mid-cycle: if beyond its threshold (N months, or 30 days if
  blank) it stays silent until the nearest future point; if within it, it
  gets one "immediate" notice, then the remaining future points proceed.
  Rows already past expiry are silent forever.
- `inactive` or deleted rows are pruned from state silently.
- Rows due on the same run are grouped: exactly ONE Telegram message and
  ONE email, each containing both rows in id order.
- Telegram and email senders are **independent** — one failing does not
  suppress the other (the point is still recorded so it is not re-sent).

### 3. Message format (Chinese, fixed layout)

```
事件提醒 — 2026-11-19
ID: R001 | Dennis | 事件: OVO Energy | 到期日: 2026-12-19 | 即將30日後到期
```

Each line is a fixed pipe-separated layout:
`ID: {id} | {people} | 事件: {event} | 到期日: {expires_on} | 即將{days}日後到期`;
when `note` is non-empty, `| {note}` is appended. The due-day line reads
`| 今日到期`. A missed-days run is one line per row ending in `| 補發`
(missed points collapse into that single line — no retroactive
timestamps). The email subject is `事件提醒 — YYYY-MM-DD` and the body
equals the Telegram text.

Example of a row with a `note` (verified via dry-run):

```
事件提醒 — 2026-11-19
ID: R001 | Dennis | 事件: OVO Energy | 到期日: 2026-12-19 | 即將30日後到期 | 到期向朋友收下一年費用
```

## Usage / quick start

```bash
cp .env.example data/.env      # fill in real values
# edit data/event.csv
python3 bot.py                 # daily loop: next run at CHECK_HOUR
python3 bot.py --once          # one pass now, then exit (bot.run_pass)
DRY_RUN=1 python3 bot.py --once  # log instead of sending
touch data/run-now             # ask the running container for a pass (logs go to the container log)
touch data/run-now-dry         # same, dry-run pass
```

Run the test suite:

```bash
python3 -m unittest discover -s tests -v
```

All 30 acceptance tests use fake dates, in-memory senders, and the
standard library only — no network, no real tokens.

## Docker

```bash
cp .env.example data/.env      # fill in real values
docker compose up -d
```

The container mounts the project directory, so `data/event.csv` and
`data/state.json` persist on the host across restarts.

## Recipients

`MAIL_TO` is optional (defaults to `SMTP_USER`). For multiple recipients,
list them comma-separated — one email is sent to all of them:

    MAIL_TO=dennis@example.com,family@example.com

## Supported platforms

- Python 3.10+ (native run)
- Any Docker host, e.g. Synology DS920+ (x86_64)

## Important limitations

- One grouped message per channel per run day; delivery happens at
  `CHECK_HOUR` (missed days are caught up at the next run, not at the
  original times).
- `early_remind_months` is a whole number of months; "0.5" steps are
  always 15 days.
- `CHECK_HOUR` and `TZ_NAME` are read at startup; change them by editing
  `data/.env` and restarting.

## Files

| File | Purpose |
|---|---|
| `bot.py` | daily loop, sender wiring, message dispatch |
| `event_reminder.py` | core logic: planner, CSV store, state, senders |
| `data/event.csv` | source of truth (initially seeded; user-maintained since) |
| `data/state.json` | per-row sent-point state (created at first run) |
| `data/.env` | credentials/config (never committed) |
| `.env.example` | config template |
| `tests/test_event_reminder.py` | acceptance tests (stdlib only) |
| `REQUIREMENTS.md` | locked specification |
| `PROJECT.md` / `DEPLOYMENT.md` / `TESTING.md` / `CHANGELOG.md` / `VERSION` | project docs |
| `Dockerfile` / `docker-compose.yml` | container run |
| `FOLLOW_UP.md` | handoff notes (kept up to date) |
