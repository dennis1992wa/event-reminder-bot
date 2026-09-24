# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/).
Version scheme: `MAJOR.MINOR.PATCH`.

## [Unreleased] — initial implementation (2026-09-24)

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
