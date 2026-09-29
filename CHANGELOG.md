# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/).
Version scheme: `MAJOR.MINOR.PATCH`.

## [Unreleased]

### Changed
- Grouped message row order: ascending by `expires_on` (soonest-expiring
  first; same `expires_on` → stable by `id`) instead of id order — the
  live 2026-09-29 message showed rows in id order, which was confusing.
  `TestGrouping` asserts the new order.
- Email HTML redesign (user-approved v10): unified dark badge — background
  `#1f1f1f`, white text, emoji keeps its native colour, vertically
  centred (replaces the per-band coloured badges); full-width dark divider
  row (`2px solid #3c4043`) between events, none after the last (replaces
  the blank spacer row); the detail column now renders the `事件: …` line
  fully bold and the `到期日: …` line bold (ID stays plain); the N-day
  count in `即將{N}日後到期` is bold, underlined, and highlighted
  `#ffe082` in every urgency band (🔴 0日 says `今日到期`, so has no
  highlight); whitespace tightened (`line-height:1.35`, no blank line
  between the `事件` and `到期日` lines) and the body sits in a white
  card (`#ffffff` on a grey page). `TestMessageBuilder` asserts the new
  markup.

## [1.0.0] — 2026-09-28 — notification visuals + stable release

### Added
- Urgency emoji prefix on every reminder row (Telegram and the email
  plain-text part — pure text; Telegram renders emoji in system colour):
  🔴 = 0 days (today), 🟠 = 1–4, 🟡 = 5–30, 🔵 = > 30 (early-month point,
  still labelled `即將{N}日後到期`).
- Email body is now `multipart/alternative`: `text/plain` part identical to
  the Telegram text (spec layout preserved) + a coloured `text/html` part
  (table with a coloured badge column 🔴 `#e53935` / 🟠 `#fb8c00` /
  🟡 `#fdd835` / 🔵 `#1e88e5`, bold `ID`, due-day line, `⚠️ 補發` red bold,
  `note` grey small). No external CSS/font links in the real email; font
  stack `-apple-system, 'Noto Sans SC', 'Noto Sans CJK SC', sans-serif`.
- Spacer between multiple rows: one blank line (text) / spacer table row
  (HTML).
- 7 new tests in `TestMessageBuilder` (emoji bands, emoji prefix, blank-line
  spacing, blue remaining-days phrasing, HTML badges & styling, multipart
  structure) plus updated wire tests — total 37 tests.

### Changed
- `EmailSender.send()` now takes `body` as a `(plain, html)` tuple and
  builds the message with `MIMEMultipart("alternative")` (plain part first,
  html part second). The wire header for the Chinese subject is RFC 2047
  encoded (normal for MIMEDeflater); test decodes it back.
- `MessageBuilder.build(...)` returns the plain text/Telegram message, the
  subject, and the `(plain, html)` body tuple instead of a single string.

## [Unreleased — initial] — initial implementation (2026-09-24)

### Added
- `bot.py`: daily 11:00 `Europe/London` loop (60 s sleep steps, DST-safe)
  with `run_pass()`, `--once`/`ONCE=1`, `DRY_RUN`, `OVERRIDE_TODAY`.
- On-demand run trigger: the main loop polls `data/run-now` and
  `data/run-now-dry` every 60 s. `touch` either file and the main process
  runs a pass immediately (dry-run pass for `run-now-dry`), and its logs
  appear in `docker logs` / Container Manager. (`docker exec ... --once`
  output only goes to your shell, never the container log.)
- Unbuffered container stdout: `Dockerfile` sets `PYTHONUNBUFFERED=1` and
  runs `python -u bot.py`, so every log line appears in `docker logs` /
  Container Manager immediately.
- `event_reminder.py`: `CsvStore` (verify, re-sort, `event.csv.bak` every
  save), `CyclePlanner` (standard 30/21/14/7/4/0-day points, early month
  points N, N−0.5 …, catch-up merge, new-row immediate notice),
  `StateStore` (`data/state.json`, dedup per point per cycle),
  `MessageBuilder` (fixed Chinese line layout `ID: … | {people} | 事件: … | 到期日: …
  | 即將N日後到期` (+ `| 補發` / `| {note}` / `今日到期`), one grouped message
  per channel per run day), `TelegramSender`, `EmailSender`.
- `EmailSender`: builds the message with `EmailMessage` and sends the
  encoded bytes via `msg.as_bytes()` — the old raw-string message crashed
  `smtplib.sendmail` on the Chinese subject/body ('ascii' codec error);
  covered by `test_chinese_subject_and_body_wire_ascii_safe`.
- `MAIL_TO` may list multiple comma-separated recipients (one email to
  all); `TELEGRAM_CHAT_ID` may list multiple comma-separated chat ids
  (the grouped message is sent to each chat).
- `tests/test_event_reminder.py`: 30 unittest tests (stdlib, fake dates,
  in-memory fake senders) covering REQUIREMENTS.md §10 items 1–7, sender
  independence, UK 2026 DST boundaries, offset math, multi-recipient /
  multi-chat parsing, `SMTP_USE_SSL` flag, and the trigger-file helpers
  in `bot.py`.
- `data/event.csv`: 6-row seed (R001–R006 from REQUIREMENTS.md §1).
- `.env.example`, `README.md`, `README_zh-TW.md`, `PROJECT.md`,
  `REQUIREMENTS.md`, `DEPLOYMENT.md`, `TESTING.md`, `FOLLOW_UP.md`,
  `Dockerfile`, `docker-compose.yml`, `VERSION`.
- `offset_to_date` parses `N.5`-style month offsets (N calendar months
  minus ~15 days, day-end clamped first).
- Environment variable names: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
  (comma-separated for multiple chats), `SMTP_HOST`, `SMTP_PORT`,
  `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` (opt), `MAIL_TO` (opt,
  comma-separated for multiple recipients), `SMTP_USE_SSL` (opt, default
  `false`) — old names `TG_TOKEN`, `TG_CHAT_ID`, `SMTP_PASSWORD`,
  `SMTP_TO`, `SMTP_TLS` are no longer read.

### Changed
- Message line layout now includes the `people` column between the id and
  the event: `ID: {id} | {people} | 事件: {event} | …` (no label on the
  people field; free text from the CSV `people` column).
- Container main loop: logs `[loop] next scheduled run at …` when the
  target changes (start + after each pass) and checks the trigger files on
  every 60 s step. Real sends also log the exact message lines as
  `[sent] …` before the sender lines, so the container log shows verbatim
  what was sent.
- Message line layout (v2): `ID: {id} | {people} | 事件: {event} | 到期日: {date} |`
  `即將{days}日後到期` (`今日到期` on the expiry day); `| 補發` and
  `| {note}` follow the status field, note last.

## [0.1.0]

### Fixed
- `offset_to_date` month-offset parsing: whole/half split handled by
  float parsing (supports "3", "2.5", "1.5", … as locked in spec).
