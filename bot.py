"""
Event Reminder Bot — orchestrator / daily runner.

Entry point:  python bot.py            (runs the daily loop)
              python bot.py --once     (run one check pass now, then exit)

Env vars (loaded from .env in the data dir if present, else process env):
  DATA_DIR             data directory (default: env dir)
  EVENT_CSV            CSV path      (default: $DATA_DIR/event.csv)
  STATE_JSON           state path    (default: $DATA_DIR/state.json)
  CHECK_HOUR           local hour to run daily (default: 11)
  TZ_NAME              IANA tz (default: Europe/London)
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (Telegram; TELEGRAM_CHAT_ID may
       list several chat ids, comma-separated, e.g. 123456,654231)
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS
  SMTP_FROM, MAIL_TO, SMTP_USE_SSL (default false)

Run modes for local testing:
  DRY_RUN=1            log instead of sending
  OVERRIDE_TODAY=YYYY-MM-DD   pretend today is this date (fake-date tests)

Stdlib only.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from event_reminder import (
    CsvStore,
    CyclePlanner,
    DueInfo,
    EmailSender,
    EventRecord,
    MessageBuilder,
    StateStore,
    TelegramSender,
)

ENV_FILE = ".env"


def load_env_file(path: str) -> None:
    """Minimal .env loader (KEY=VALUE lines, no quoting support needed here)."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


def next_run_time(tz: ZoneInfo, hour: int) -> datetime:
    now = datetime.now(tz)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def run_pass(
    csv_path: str,
    state_path: str,
    today: date,
    telegram: TelegramSender | None,
    email: EmailSender | None,
    dry_run: bool,
    log=print,
) -> int:
    """One daily check pass. Returns number of rows notified (0 = silent)."""
    csv_store = CsvStore(csv_path)
    records = csv_store.read()
    state = StateStore(state_path)

    sent_set = state.sent_set()

    planner = CyclePlanner(today)
    due_rows: list[tuple[str, EventRecord, DueInfo]] = []
    for rec in records:
        if rec.status.strip().lower() != "active":
            continue
        if rec.expires_on_date is None:
            log(f"[warn] {rec.id}: bad/blank expires_on, skipped")
            continue

        known_ids = {t[0] for t in state.first_seen_set()}
        infos, voided = planner.due_points(rec, sent_set, state.first_seen_set(), known_ids)
        # New-row points that already passed at first sight are voided:
        # mark them sent so they can never surface as catch-up later.
        for o in voided:
            state.mark_sent((rec.id, rec.expires_on, rec.early_remind_months, o))
        for info in infos:
            due_rows.append((rec.id, rec, info))
        # Mark first-seen now (so tomorrow it is no longer a new row)
        state.mark_first_seen(rec.id, rec.expires_on)

    # Rows inside the same grouped message are ordered by nearest
    # expires_on first (ties broken by id).
    due_rows.sort(key=lambda t: (t[1].expires_on, t[0]))

    total_lines = 0
    if due_rows:
        text, subject, body = MessageBuilder.build(today, due_rows)
        # Independent sends — one failure must not block the other
        if not dry_run:
            # Log the exact message text too, so the container log shows
            # verbatim what was sent (convenient for checking later).
            for line in text.splitlines():
                log(f"[sent] {line}")
            if telegram is not None:
                ok = telegram.send(text)
                log(f"[telegram] {'OK' if ok else 'FAILED'} ({len(due_rows)} rows)")
            if email is not None:
                ok = email.send(subject, body)
                log(f"[email] {'OK' if ok else 'FAILED'}")
        else:
            log("[dry-run] would send:\n" + text)
        # Record marks as sent (dedup survives even if a channel failed)
        for _rid, rec, info in due_rows:
            if info.kind == "immediate":
                state.mark_sent((rec.id, rec.expires_on, rec.early_remind_months, "immediate"))
            else:
                for o in info.offsets:
                    state.mark_sent((rec.id, rec.expires_on, rec.early_remind_months, o))
        total_lines = len(due_rows)

    # Prune stale sent entries (deleted rows / changed expires_on)
    active_bases = {
        (rec.id, rec.expires_on, rec.early_remind_months)
        for rec in records
        if rec.status.strip().lower() == "active" and rec.expires_on_date is not None
    }
    state.active_keys(active_bases)
    state.save()

    # Re-sort CSV (only when there was a pass; cheap either way)
    csv_store.write_sorted(records)
    log(f"[done] run {today.isoformat()}: {total_lines} notification group(s), CSV re-sorted")
    return total_lines


def once_flag() -> bool:
    return os.environ.get("ONCE", "0") == "1" or "--once" in sys.argv


def consume_trigger(path: str) -> bool:
    """Return True if a run-now request file exists and mark it consumed."""
    if os.path.exists(path):
        try:
            os.unlink(path)
            return True
        except OSError:
            return False
    return False


def build_senders(dry_run: bool):
    telegram = None
    email = None
    if not dry_run:
        if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
            telegram = TelegramSender(
                os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"])
        if os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER"):
            email = EmailSender(
                host=os.environ["SMTP_HOST"],
                port=int(os.environ.get("SMTP_PORT", "587")),
                user=os.environ["SMTP_USER"],
                password=os.environ.get("SMTP_PASS", ""),
                sender_email=os.environ.get("SMTP_FROM", os.environ["SMTP_USER"]),
                to_email=os.environ.get("MAIL_TO", os.environ["SMTP_USER"]),
                use_ssl=os.environ.get("SMTP_USE_SSL", "false").lower()
                in ("1", "true", "yes"),
            )
    return telegram, email


def main() -> None:
    # Data dir holds event.csv, .env, state.json (mounted NAS dir in production)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.environ.get("DATA_DIR") or os.path.join(script_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    env_path = os.environ.get("ENV_FILE", os.path.join(data_dir, ENV_FILE))
    load_env_file(env_path)

    csv_path = os.environ.get("EVENT_CSV", os.path.join(data_dir, "event.csv"))
    state_path = os.environ.get("STATE_JSON", os.path.join(data_dir, "state.json"))
    # On-demand trigger files: touch to make the main process run a pass now
    # (its logs then appear in `docker logs` / Container Manager).
    trigger_path = os.path.join(data_dir, "run-now")
    trigger_dry_path = os.path.join(data_dir, "run-now-dry")
    tz = ZoneInfo(os.environ.get("TZ_NAME", "Europe/London"))
    hour = int(os.environ.get("CHECK_HOUR", "11"))
    dry_run = os.environ.get("DRY_RUN", "0") == "1"

    override = os.environ.get("OVERRIDE_TODAY", "").strip()
    if once_flag():
        telegram, email = build_senders(dry_run)
        today = date.fromisoformat(override) if override else datetime.now(tz).date()
        run_pass(csv_path, state_path, today, telegram, email, dry_run)
        return

    if not (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")) \
            and not (os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER")) \
            and not dry_run:
        print("ERROR: no channels configured (need TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID and/or SMTP_*)", file=sys.stderr)
        sys.exit(1)

    loop_dry = dry_run
    print(f"[loop] started (dry_run={loop_dry}); touch {data_dir}/run-now "
          f"or {data_dir}/run-now-dry to request a pass now")

    def _today() -> date:
        return date.fromisoformat(override) if override else datetime.now(tz).date()

    # Daily loop at CHECK_HOUR: sleeps in 60 s steps (DST-safe), caps each
    # step so the :00 run is never later than ~1 min, and checks the on-
    # demand trigger files on every step. The scheduled-run line is printed
    # only when the target changes (at start and after each pass), not on
    # every 60 s tick, to keep the container log readable.
    next_target = next_run_time(tz, hour)
    announced = None
    while True:
        now = datetime.now(tz)
        wait = (next_target - now).total_seconds()
        if next_target != announced:
            print(f"[loop] next scheduled run at {next_target.isoformat()} "
                  f"({max(0, int(wait // 60))} min)")
            announced = next_target
        time.sleep(60.0 if wait > 60 else max(wait, 1.0))
        now = datetime.now(tz)
        if now >= next_target:
            telegram, email = build_senders(loop_dry)
            run_pass(csv_path, state_path, _today(), telegram, email, loop_dry)
            next_target = next_run_time(tz, hour)
        elif consume_trigger(trigger_path):
            print("[trigger] run-now requested")
            telegram, email = build_senders(loop_dry)
            run_pass(csv_path, state_path, _today(), telegram, email, loop_dry)
        elif consume_trigger(trigger_dry_path):
            print("[trigger] run-now-dry requested (dry-run pass)")
            telegram, email = build_senders(True)
            run_pass(csv_path, state_path, _today(), telegram, email, True)


if __name__ == "__main__":
    main()
