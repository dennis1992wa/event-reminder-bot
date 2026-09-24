# TESTING.md — Event Reminder Bot

## Automated tests (all stdlib, no network, no real tokens)

```bash
cd /app/workspace/event-reminder-bot
python3 -m unittest discover -s tests -v
```

`tests/test_event_reminder.py` covers every acceptance item in
`REQUIREMENTS.md` §10 using fake dates and in-memory fake senders:

| # | Item | Test |
|---|---|---|
| 1 | Standard points fire once, no repeats (incl. skip-2-days catch-up) | `TestStandardPoints` |
| 2 | Early points (N=2 spec example; N=3; blank/<2 no early; runtime early→standard) | `TestEarlyPoints` (incl. `offset_to_date` clamp) |
| 3 | Missed days (incl. multi-day downtime after DST) collapse into ONE combined message per channel | `TestCatchUp`, `TestDst` |
| 4 | `expires_on` changed mid-cycle → silent, fresh cycle, old points never fire | `TestRenewal` |
| 5 | Row deleted / `inactive` → silent; state pruned | `TestInactiveOrDeleted` |
| 6 | New row: ≤ threshold → one immediate + remaining future points; > threshold → silent; passed early points never back-filled | `TestNewRow` |
| 7 | CSV re-sort: order, `.bak`, byte-identical values, header intact | `TestCsvStoreReSort` |
| + | Two rows due same run → exactly ONE Telegram + ONE email, both rows in id order | `TestGrouping` |
| + | One channel failing does not suppress the other | `TestSenderIndependence` |
| + | 11:00 run resolves in the correct UK offset across the 2026 spring-forward (03-29) and autumn-back (10-25) | `TestDst` |
| + | `MAIL_TO` multi-recipient parse (comma-separated, blanks dropped) and `SMTP_USE_SSL` flag | `TestEmailRecipients` |
| + | On-demand trigger files: `consume_trigger` / `consume_trigger_dry` in `bot.py` | `TestRunTrigger` |

Last full run: **30/30 OK**.

Test conventions:
- Fake dates are passed straight into `CyclePlanner(today)` / `run_pass` —
  the real clock is never touched under test.
- `FakeSender` records calls in memory; sender independence is tested by
  making one sender raise and asserting the other still sends.
- State/CSV live under `tempfile.TemporaryDirectory()`.
- DST assertions use `ZoneInfo("Europe/London")` from stdlib `zoneinfo`.

## Build / syntax checks

```bash
python3 -m py_compile event_reminder.py bot.py
```

## Manual / live test (requires real credentials)

1. `DRY_RUN=1` in `data/.env`; start the daemon; confirm the log shows the
   rendered message and that nothing was sent.
2. Force a due row: set `OVERRIDE_TODAY` to a date on one of its points in
   `data/.env`, restart with `DRY_RUN=0`, confirm Telegram + email arrive.
3. Restart without the override; confirm the same point does not repeat
   (dedup), and the run is silent until the next due point.

## Runtime checks (container)

```bash
docker compose ps            # Up
docker compose logs --tail 50
```

Expected at startup (daemon mode): one line like
`[loop] next run at 2026-09-24T11:00:00+01:00 (NN min)`; after each pass:
`[done] run 2026-09-24: N notification group(s), CSV re-sorted`.

## Not tested here

- Real SMTP/Telegram delivery (needs credentials; see DEPLOYMENT.md
  rollback step for a manual one-live-send check).
- Container image build on the NAS (needs the NAS online).
