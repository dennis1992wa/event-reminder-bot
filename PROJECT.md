# PROJECT.md — Event Reminder Bot

Persistent context for future AI sessions.

## Purpose

Daily reminder daemon: watches `data/event.csv` (renewals/subscriptions) and,
each day at `CHECK_HOUR` (default 11:00, `TZ_NAME` default Europe/London),
sends ONE grouped message per channel (Telegram bot + email) per due day.
Points per row: 30/21/14/7/4/0 days before expiry, plus optional early
points at N, N−0.5, … months (`early_remind_months`). Stdlib only.

## Components

- `bot.py` — entry point: loads `.env` from `data/`, daily loop,
  `run_pass()` (one check pass; `--once` / `ONCE=1` for one pass), builds
  real senders, logs.
- `event_reminder.py` — pure core: `EventRecord`, `HEADER` (11 columns),
  `CsvStore` (read/verify/re-sort + `.bak`), `CyclePlanner` (point dates,
  catch-up, new-row threshold, DST-safe via `date` math), `StateStore`
  (sent points keyed (id, expires_on, early_remind_months, point);
  first_seen list), `MessageBuilder` (fixed Chinese format),
  `TelegramSender` (HTTPS urllib), `EmailSender` (smtplib),
  `add_months` / `offset_to_date` / `format_offset`.

## Data

- `data/event.csv` — 11 columns:
  `id,people,status,expires_on,early_remind_months,event,type,amount,currency,paid_on,note`
- `data/state.json` — `{"sent": [[id, expires_on, early_remind_months, point], …],
  "first_seen": [[id, expires_on], …]}`; atomic tmp+rename writes.
- `data/.env` — credentials/config (never committed; see `.env.example`).
  Keys: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SMTP_HOST, SMTP_PORT,
  SMTP_USER, SMTP_PASS,
  SMTP_FROM (opt), MAIL_TO (opt; comma-separated for multiple recipients),
  SMTP_USE_SSL (opt, default false), CHECK_HOUR, TZ_NAME, DATA_DIR, EVENT_CSV,
  STATE_JSON, DRY_RUN,
  OVERRIDE_TODAY, ONCE, ENV_FILE.

## Rules (locked — see REQUIREMENTS.md §10 for acceptance behavior)

- One grouped message per channel per run day; rows in id order. Fixed
   line layout: `ID: {id} | {people} | 事件: {event} | 到期日: {expires_on} | 即將{days}日後到期`
  (+ `| {note}` when non-empty; due day = `| 今日到期`).
- Missed days → single catch-up line per row ending `| 補發`, no
  retroactive timestamps.
- Points fire once per cycle; points are marked sent in `run_pass` after the
  send attempt (dedup survives channel failure — no re-sends).
- Channel failures are independent (one failing never suppresses the other).
- `expires_on` change or reactivation → state silently voided, new cycle.
- New row past threshold → silent until nearest future point; within
  threshold → one immediate notice with the remaining-days layout.
- Inactive/deleted rows → silent, state pruned.
- CSV re-sorted (expires_on asc, inactive bottom) + `.bak` after EVERY
  pass, including dry-runs.

## Deploy

Docker: `Dockerfile` + `docker-compose.yml`; bind-mount `./data:/app/data`
so CSV/state/.env persist. Synology DS920+ tested target; full steps in
DEPLOYMENT.md. Docker images built with `docker build` (no host access to
Synology from this machine; verify with build + test here).

## Verification

- `python3 -m unittest discover -s tests -v` → 30 tests, stdlib only, fake
  dates + in-memory senders (see TESTING.md).
- Dry-run smoke: `DRY_RUN=1 OVERRIDE_TODAY=<date> python3 bot.py --once`.
  In a running container, `touch data/run-now` (or `data/run-now-dry`) asks
  the main process for a pass within 60 s; those logs appear in `docker logs`
  / Container Manager (unlike `docker exec ... --once`, whose output goes
  only to the invoking shell).

## Backlog / notes

- No retry/backoff within a run; failed senders' points are marked sent and
  NOT re-sent (spec: one attempt per point per cycle). If Dennis wants
  automatic retry, that is a spec change.
- No automatic email-undeliverability detection beyond SMTP errors.
- `state.json` schema is internal; change needs a migration note here.
- `CHECK_HOUR`/`TZ_NAME` read at startup; edit `data/.env` + restart to change.
