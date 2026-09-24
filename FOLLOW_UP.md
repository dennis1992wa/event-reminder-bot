# FOLLOW_UP.md — handoff note (2026-09-24, session resumed: tests + docs + Docker done)

Read **this file + `REQUIREMENTS.md` + `/app/workspace/DEVELOPMENT_RULES.md` first**, in that order.
`REQUIREMENTS.md` is the authoritative, user-locked spec. Do not re-litigate locked rules.

## 1. Project

Standalone **Event Reminder Bot** (`/app/workspace/event-reminder-bot/`):
user-maintained `event.csv`, one daily check at **11:00 `Europe/London`** (DST-aware),
dual-channel (Telegram + Email) grouped notifications, then safe re-sort of `event.csv`.
Deploys as one Docker app on Synology DS920+ (Container Manager); data dir mounted in.
**App name: `event-reminder-bot`.** Python 3 stdlib only.

## 2. Status

| Item | State |
|---|---|
| Spec (`REQUIREMENTS.md`) | **LOCKED** — user confirmed all rules incl. new-row threshold (2026-09-24) |
| `event_reminder.py` | written, works; `offset_to_date` now parses `N.5`/`1.5` float offsets |
| `bot.py` | written, works |
| Tests (§10 acceptance) | **DONE** — `tests/test_event_reminder.py`, 30 tests, `python3 -m unittest discover -s tests` → **OK** (30/30) |
| `data/event.csv` | **DONE** — initial 6-row seed in `data/` (user-maintained since; CSV re-sorted on every run) |
| Docs (README/README_zh-TW/PROJECT/DEPLOYMENT/TESTING/CHANGELOG/VERSION) | **DONE** — all present, env-var names match code |
| `.env.example` | **DONE** — placeholders only (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SMTP_*`), `.env` lives in `data/` |
| `Dockerfile`, `docker-compose.yml` | **DONE** — compose service `event-reminder-bot`, `./data:/app/data` bind mount |
| Dry-run smoke | **DONE** — `DRY_RUN=1 OVERRIDE_TODAY=… python3 bot.py --once`: immediate notice, dedup, catch-up grouping, expiry-day all verified (see §6) |
| Deployed to NAS | **DONE 2026-09-24** — user ran `compose down/build/up` in `/volume1/docker/event-reminder-bot` (data/ preserved); first live send verified (R007 MOT catch-up) |

## 3. Code layout (confirmed against source this session)

`event_reminder.py` — core, no I/O side effects except `CsvStore`/`StateStore`:
- `EventRecord` (dataclass): CSV row, `id`/`expires_on_date`/`early_remind_months_value`/`sort_key()`
- `CyclePlanner(today)`: pure date math. `point_offsets(N)`, `point_dates(rec, offsets)`, `offset_to_date(m, expires_on)`, `due_points(rec, sent, first_seen, known_ids) -> (DueInfo list, voided offsets)`
- `offset_to_date`: `whole = float(offset)`; `w = int(whole)`; `half = whole - w >= 0.5 - 1e-9`;
  `d = add_months(expires_on, -w)` then (only if `half`, after end-of-month clamp) subtract 15 days.
  So `"2.5"` = 2 calendar months minus 15 days.
- `DueInfo(kind, offsets, days_to_expiry)`: kind = `standard` | `immediate` | `catch-up`
- `StateStore(path)`: JSON, `sent` keys `(id, expires_on, early, offset)` + `first_seen` keys `(id, expires_on)`; atomic write
- `MessageBuilder.build(check_date, infos)` → `(telegram_text, email_subject, email_body)`;
  title `事件提醒 — YYYY-MM-DD`; per-row lines in id order, fixed layout
  `ID: {id} | {people} | 事件: {event} | 到期日: {expires_on} | 即將{days}日後到期` + `| {note}` when
  non-empty; due day = `| 今日到期`; catch-up ends the line with `| 補發`; body == telegram text
- `TelegramSender` (urllib, Bot API `sendMessage`) / `EmailSender` (smtplib)
- `CsvStore`: `read()` (header check, blank-line safe), safe re-sort write: `.bak` → temp → verify → atomic `os.replace`

`bot.py` — orchestrator:
- `run_pass(...)`: one daily check pass; sorts due rows in id order; independent sends; marks
  points sent in the pass (dedup survives a channel failure); prunes stale state; re-sorts CSV; logs
- `main()`: reads `.env` from `data/` (tiny inline loader), daily loop with `next_run_time`
  sleep-to-next-run at `CHECK_HOUR` in `TZ_NAME`; `--once`/`ONCE=1` runs one pass and exits.
  `DRY_RUN=1` logs instead of sending; `OVERRIDE_TODAY=YYYY-MM-DD` fakes the date.

## 4. Locked spec rules (unchanged — all honoured; verified by tests)

Standard 30/21/14/7/4/0 days; `early_remind_months N` adds N, N−0.5 … 1.5 months (calendar-month
math, 0.5 = 15 days, clamp first); new-row threshold rule (silent vs one immediate notice);
renewal silent + fresh cycle; catch-up collapses missed points into one message; dedup key
`(id, expires_on, early, offset)`; one run day → ONE Telegram + ONE email, rows in id order;
CSV re-sorted after every pass (incl. dry-run); state persists in mounted `data/`.

## 5. TODO for next session (in order)

1. ~~User deploys to the NAS~~ — **DONE 2026-09-24** (user ran `compose down/build/up` in `/volume1/docker/event-reminder-bot`; data/ preserved; first live send verified via R007 MOT catch-up).
2. ~~DRY-RUN first, then flip to real~~ — **DONE** (user tested `DRY_RUN=1` and real `--once` passes on the NAS).
3. ~~Confirm the first 11:00 run~~ — first scheduled auto run: 2026-09-25 11:00 BST.
4. **Only after user confirms it's live & stable** → bump `VERSION` to `1.0.0` + add a CHANGELOG
   entry (per `DEPLOYMENT.md`). Needs explicit user confirmation — do NOT bump unilaterally.

## 6. Dry-run smoke verified this session (fake dates, DRY_RUN=1, no sends)

- `OVERRIDE_TODAY=2026-11-19`: `ID: R001 | Dennis | 事件: OVO Energy | 到期日: 2026-12-19 | 即將30日後到期`
  (new row, within 30-day threshold → immediate notice). ✓
- Same day again: `0 notification group(s)` — dedup held. ✓
- `OVERRIDE_TODAY=2026-11-20`: `0 notification group(s)` — nothing due. ✓
- `OVERRIDE_TODAY=2026-12-19` (11:00 never ran since 11-19): 2-row grouped catch-up —
  `ID: R001 | Dennis | 事件: OVO Energy | 到期日: 2026-12-19 | 今日到期 | 補發`
  + `ID: R003 | Hobbit | 事件: Spotify | 到期日: 2026-12-31 | 即將12日後到期 | 補發 | 已代付一年（2025-12-31 起）；到期向朋友收下一年費用`
  — one line per row, missed points collapse into it, id order,
  Virgin Media (R002, 2028) correctly silent. ✓
- `data/state.json` + `data/event.csv.bak` created; CSV re-sorted. ✓

## 7. Constraints & gotchas

- **Language/stack**: Python 3 stdlib only; no pip installs; single container; no cron (in-app sleep loop, `zoneinfo`).
- **Secrets**: `.env` (in `data/`) holds Telegram bot token, chat_id, SMTP credentials — placeholders only in docs; never print/log credentials. `data/.env`, `data/state.json`, `*.bak` are gitignored.
- **NAS is off 01:00–08:00 daily** — 11:00 run is after wake; state survives it (JSON on mounted volume).
- Initial seed delivered with 2026-09-24 (see `REQUIREMENTS.md` §1 — the live CSV is user-maintained and has since grown):
- Worked example: OVO expires 2026-12-19 → fires 11-19, 11-28, 12-05, 12-12, 12-15, 12-19.
- Spec example: N=2, expires 2027-03-10 → 8 points total (2027-01-10, 2027-01-26, 30d, 21d, 14d, 7d, 4d, 0).
- Reports to the user are in Cantonese (files stay English; README_zh-TW formal written 繁體中文).
- Minimum-change rule: field order `id,people,status,expires_on,early_remind_months,event,type,amount,currency,paid_on,note` is locked.

## 8. Exact current state

All 30 tests pass, dry-run smoke verified, every doc + `.env.example` + Docker artifacts
written; deployed and live on the NAS (2026-09-24): people column added to the message line,
on-demand trigger files, `[sent]` verbatim log lines, loop log noise reduced. Live CSV
(`data/event.csv`) is user-maintained and has grown past the initial 6-row seed (R007 MOT
added with `early_remind_months=3`; R001/R002 people renamed from 本人 to Dennis).
**Next: wait for the first scheduled 11:00 run, then consider 1.0.0 (explicit user
confirmation required).**
