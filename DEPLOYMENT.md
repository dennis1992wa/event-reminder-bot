# DEPLOYMENT.md — Event Reminder Bot

## Prerequisites

- Docker + Compose on the target host (Synology DS920+ Container Manager works).
- A Telegram bot token (`@BotFather`) and your Telegram chat id
  (`@userinfobot`).
- An SMTP account with app password (e.g. Gmail app password, or any
  provider that supports STARTTLS).
- Python 3.10+ is NOT needed on the host — the container image ships it.

## Installation

1. Copy the whole project directory to the host, e.g.:
   `/volume1/docker/event-reminder-bot/`
2. `cp .env.example data/.env` and fill in real values:
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (comma-separated for multiple
     chats)
   - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`
   - optional: `SMTP_FROM`, `MAIL_TO` (comma-separated for multiple
     recipients), `SMTP_USE_SSL` (Gmail 587: `false`), `CHECK_HOUR`, `TZ_NAME`
3. Review `data/event.csv` — add, edit, or remove rows as needed.
   Keep the header line exactly as is. `id` must be unique and never
   reused after deletion.

## Running

```bash
cd /volume1/docker/event-reminder-bot
docker compose up -d
```

- The container loops: it waits until the next `CHECK_HOUR`
  (default 11:00 in `TZ_NAME`, default Europe/London) each day and runs
  one reminder pass, repeating. No immediate first pass — start it with
  `DRY_RUN=1` first to see exactly what it would send.
- `data/event.csv` and `data/state.json` persist via the host bind-mount
  (`./data:/app/data`) — restarting or recreating the container never
  loses data or sent-point state.
- The NAS can be off overnight: missed days are caught up at the next run
  as a single consolidated message per channel (by design).
- **On-demand pass with visible logs**: `touch data/run-now` (real send)
  or `touch data/run-now-dry` (dry-run) — the main process picks it up
  within 60 s and the pass logs appear in `docker logs` / Container
  Manager. (`docker exec ... python /app/bot.py --once` still works for a
  quick check, but its output only goes to your shell, never the
  container log.)
- **Container Manager 日志 panel = the container's own stdout**, i.e.
  everything the running bot process prints: startup line, each daily
  pass (`[sent] … / [telegram] OK / [email] OK / [done] run …`), trigger
  passes, and warnings. Use it as the single place to check what happened.

## Updating (copy & paste workflow)

After any source change (`bot.py`, `event_reminder.py`, `Dockerfile`,
`docker-compose.yml`, `tests/`, or the docs), the updated files must be
copied into the NAS project directory first, then the container is
rebuilt and restarted:

1. Copy the updated source files into `/volume1/docker/event-reminder-bot/`
   on the NAS (overwrite the existing files).
   **Do NOT copy `data/`** — `data/.env`, `data/event.csv` and
   `data/state.json` on the NAS must be left untouched, or you will lose
   your credentials, events, and sent-point state.
2. On the NAS (SSH or terminal):

   ```bash
   ssh <nas username>@<nas ip address> -p <ssh port number>
   cd /volume1/docker/event-reminder-bot
   docker compose down
   docker compose build
   docker compose up -d
   docker logs event-reminder-bot
   ```

   - `docker compose down` stops and removes the current container (your
     `data/` folder is on the host and is NOT affected).
   - `docker compose build` rebuilds the image from the updated source.
   - `docker compose up -d` starts a fresh container from the new image.
   - `docker logs event-reminder-bot` shows the startup line, including
     the next scheduled run time — this confirms the new code is running.

3. `data/event.csv` and `data/state.json` survive the rebuild via the
   host bind-mount (`./data:/app/data`), so no sent-point state is lost.
4. The same `down → build → up -d` sequence is required whenever
   `data/.env` changes (it is baked into the container at startup, so
   `docker compose restart` alone is not enough).

If deployment is confirmed and the project proves stable, bump `VERSION`
to `1.0.0` and add a CHANGELOG entry.

## Post-deployment verification

1. `docker compose ps` → container Up.
2. `docker compose logs -f` → startup log line with next run time.
3. DRY-RUN first (recommended): put `DRY_RUN=1` in `data/.env`, restart,
   check the logs show the exact message text without sending, then set
   `DRY_RUN=0` and restart for real.
4. Force one live send: temporarily edit `data/event.csv` so one row's
   due date falls inside the reminder window, wait for the run (or use
   `OVERRIDE_TODAY` in `data/.env` and restart the container), and
   confirm the Telegram + email message arrive. Remove the test row after.

## Rollback / recovery

- `data/event.csv.bak` is a byte-identical copy written next to
  `data/event.csv` on every save. Deleting `data/state.json` re-arms every
  point for the current cycle (the bot will re-send today's points once).
- If the CSV was edited by hand and is malformed, the bot logs an error at
  startup and does not send until the file parses again — restore from
  `event.csv.bak` or git.

## Security notes

- `.env` is never committed (`.gitignore`); the compose file does not bake
  secrets into the image.
- Telegram sender only sends to the chat id(s) in `TELEGRAM_CHAT_ID` (the
  token goes only to `api.telegram.org`); any other chat id can never be
  targeted.
