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
| Tests (§10 acceptance) | **DONE** — `tests/test_event_reminder.py`, 37 tests, `python3 -m unittest discover -s tests` → **OK** (37/37), incl. `TestMessageBuilder` (emoji bands, spacing, blue phrasing, HTML, multipart) |
| `data/event.csv` | **DONE** — initial 6-row seed in `data/` (user-maintained since; CSV re-sorted on every run) |
| Docs (README/README_zh-TW/PROJECT/DEPLOYMENT/TESTING/CHANGELOG/VERSION) | **DONE** — all present, env-var names match code |
| `.env.example` | **DONE** — placeholders only (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SMTP_*`), `.env` lives in `data/` |
| `Dockerfile`, `docker-compose.yml` | **DONE** — compose service `event-reminder-bot`, `./data:/app/data` bind mount |
| Dry-run smoke | **DONE** — `DRY_RUN=1 OVERRIDE_TODAY=… python3 bot.py --once`: immediate notice, dedup, catch-up grouping, expiry-day all verified (see §6) |
| Deployed to NAS | **DONE 2026-09-24** — user ran `compose down/build/up` in `/volume1/docker/event-reminder-bot` (data/ preserved); first live send verified (R007 MOT catch-up) — **NOTE: still running the original code; the 1.0.0 visual + sort changes are NOT deployed yet (see §5)** |

## 3. Code layout (confirmed against source this session)

`event_reminder.py` — core, no I/O side effects except `CsvStore`/`StateStore`:
- `EventRecord` (dataclass): CSV row, `id`/`expires_on_date`/`early_remind_months_value`/`sort_key()`
- `CyclePlanner(today)`: pure date math. `point_offsets(N)`, `point_dates(rec, offsets)`, `offset_to_date(m, expires_on)`, `due_points(rec, sent, first_seen, known_ids) -> (DueInfo list, voided offsets)`
- `offset_to_date`: `whole = float(offset)`; `w = int(whole)`; `half = whole - w >= 0.5 - 1e-9`;
  `d = add_months(expires_on, -w)` then (only if `half`, after end-of-month clamp) subtract 15 days.
  So `"2.5"` = 2 calendar months minus 15 days.
- `DueInfo(kind, offsets, days_to_expiry)`: kind = `standard` | `immediate` | `catch-up`
- `StateStore(path)`: JSON, `sent` keys `(id, expires_on, early, offset)` + `first_seen` keys `(id, expires_on)`; atomic write
- `MessageBuilder.build(check_date, infos)` → `(telegram_text, email_subject, body)` where
  `body = (plain_text, html_text)`; title `事件提醒 — YYYY-MM-DD`; per-row lines prefixed
  with an urgency emoji (🔴 0d / 🟠 1–4d / 🟡 5–30d / 🔵 >30d), fixed layout
  `ID: {id} | {people} | 事件: {event} | 到期日: {expires_on} | 即將{days}日後到期` + `| {note}` when
  non-empty; due day = `| 今日到期`; catch-up ends the line with `| 補發`; blank line
  between multiple rows (text) / full-width dark divider row (HTML). Email part is
  `multipart/alternative` (plain first, HTML second): white card on grey, unified dark
  badge (`#1f1f1f` bg, white text, emoji keeps native colour, vertically centred), bold
  事件/到期日 lines (ID plain), N-day count bold + underlined + `#ffe082` highlight
  (all bands; 🔴 0日 says 今日到期 → no highlight). No external CSS/fonts.
- `TelegramSender` (urllib, Bot API `sendMessage`) / `EmailSender` (smtplib)
- `CsvStore`: `read()` (header check, blank-line safe), safe re-sort write: `.bak` → temp → verify → atomic `os.replace`

`bot.py` — orchestrator:
- `run_pass(...)`: one daily check pass; sorts due rows by nearest `expires_on`
  (ties by id); independent sends; marks
  points sent in the pass (dedup survives a channel failure); prunes stale state; re-sorts CSV; logs
- `main()`: reads `.env` from `data/` (tiny inline loader), daily loop with `next_run_time`
  sleep-to-next-run at `CHECK_HOUR` in `TZ_NAME`; `--once`/`ONCE=1` runs one pass and exits.
  `DRY_RUN=1` logs instead of sending; `OVERRIDE_TODAY=YYYY-MM-DD` fakes the date.

## 4. Locked spec rules (unchanged — all honoured; verified by tests)

Standard 30/21/14/7/4/0 days; `early_remind_months N` adds N, N−0.5 … 1.5 months (calendar-month
math, 0.5 = 15 days, clamp first); new-row threshold rule (silent vs one immediate notice);
renewal silent + fresh cycle; catch-up collapses missed points into one message; dedup key
`(id, expires_on, early, offset)`; one run day → ONE Telegram + ONE email, rows ordered by
nearest `expires_on` (ties by id);
CSV re-sorted after every pass (incl. dry-run); state persists in mounted `data/`.

## 5. TODO for next session (in order)

1. ~~User deploys to the NAS~~ — **DONE 2026-09-24** (user ran `compose down/build/up` in `/volume1/docker/event-reminder-bot`; data/ preserved; first live send verified via R007 MOT catch-up).
2. ~~DRY-RUN first, then flip to real~~ — **DONE** (user tested `DRY_RUN=1` and real `--once` passes on the NAS).
3. ~~Confirm the first 11:00 run~~ — first scheduled auto run: 2026-09-25 11:00 BST.
4. ~~Bump to 1.0.0~~ — **DONE 2026-09-28** (user confirmed live & stable; `VERSION` = `1.0.0`, CHANGELOG entry written — but the 1.0.0 build has NOT been deployed to the NAS yet).
5. **User wants a REAL live fire (Telegram + email) to verify the 1.0.0 visuals** (emoji urgency + coloured HTML email). On 2026-09-29 the user already received a live message proving delivery still works (old code, plain format).
6. **After the visual change, also change the grouped-message row order** (2026-09-29 user feedback, verbatim: 「圖1 係telegram message, 圖2 係email, 個排序錯左, 應該係由最近到期排到最遲到期」): rows now sorted ascending by `expires_on` (ties by id). Code + `TestGrouping` + docs updated, tests 37/37 green — deploy with the 1.0.0 rebuild.
7. **Deploy to the NAS**: copy changed files to `/volume1/docker/event-reminder-bot` (NEVER copy `data/`), then `docker compose down && docker compose build && docker compose up -d`, then a real trigger: add `OVERRIDE_TODAY` to `data/.env` for a date on some row's point, restart, `docker exec event-reminder-bot python /app/bot.py --once` (or `touch data/run-now`), watch Telegram + Gmail, then remove `OVERRIDE_TODAY` + restart. NOTE: a fired point is recorded as sent — the 11:00 run will not resend it; reset `data/state.json` after the test if a clean state is wanted.
8. Commit the changes (7+ files modified, uncommitted as of 2026-09-29).

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

All 37 tests pass; `VERSION` = `1.0.0` (bumped 2026-09-28, uncommitted); the visual
changes (emoji urgency, `multipart/alternative` email), the grouped-message row-order
change (ascending by `expires_on`, ties by id — 2026-09-29 user feedback), and the
**v10 email HTML redesign** (unified dark badge `#1f1f1f` vertically centred, full-width
`2px solid #3c4043` divider rows, `#ffe082` highlight on every N-day count, bold
事件/到期日 lines, `line-height:1.35` no blank line, white card — user-approved 2026-09-29)
are implemented and tested but **not yet deployed — the NAS is still running the
original code** (user's 2026-09-29 live message proved old-code delivery; order showed
R007 → R009 → R010, id order — correctly sorted by expires_on it is R009 → R010 →
R007). **Next: user does a real live fire after rebuild (see §5 item 7), then commit.**
