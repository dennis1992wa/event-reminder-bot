# Event Reminder Bot — Requirements

Standalone event reminder bot (renewals, subscriptions, and other dated events). Reads a user-maintained CSV, checks it daily, sends grouped dual-channel (Telegram + Email) notifications, and re-sorts the CSV. Runs as a small Docker container on a Synology NAS (Container Manager).

## 1. Inputs the user maintains (the ONLY manual file)

File: `event.csv` — user add/edits/deletes rows by hand.

Header (fixed order):

```
id,people,status,expires_on,early_remind_months,event,type,amount,currency,paid_on,note
```

| Field | Meaning |
|---|---|
| `id` | Unique row id (`R001`, `R002`, …). Bot uses it for dedup/state. Never duplicate; never reuse an id after a row is deleted. |
| `people` | Who it is for: free text, may list several people (e.g. the owner's name for personal items, or a friend's name(s) for shared / paid-for-them items). |
| `status` | `active` or `inactive`. Bot acts on `active` rows only. |
| `expires_on` | `YYYY-MM-DD`. **The single date all notification math is based on.** |
| `early_remind_months` | Optional integer **≥ 2**. Blank → standard cycle (30/21/14/7/4/0 days) only. Value `N` → adds extra early points: `N months`, `N−0.5 months`, … down to `1.5 months` (0.5-month steps), ahead of the standard cycle (see §3). |
| `event` | Event name (e.g. `OVO Energy`, `MOT annual checking`). |
| `type` | Free text: 合同 / 代付年度訂閱 / 共享訂閱; blank if n/a. |
| `amount` | Amount if any (e.g. `258.56`); blank if n/a. |
| `currency` | e.g. `HKD`; blank if n/a. |
| `paid_on` | `YYYY-MM-DD` or blank if n/a. |
| `note` | Free text (incl. what to do at expiry). |

Initial seed data (as first delivered, 2026-09-24 — the live CSV is user-maintained and has since grown; see `data/event.csv` for the current rows):

```
id,people,status,expires_on,early_remind_months,event,type,amount,currency,paid_on,note
R001,本人,active,2026-12-19,,OVO Energy,合同,,,,合約 2026-12-19 到期
R002,本人,active,2028-03-10,,Virgin Media,合同,,,,合約 2028-03-10 到期
R003,Hobbit,active,2026-12-31,,Spotify,代付年度訂閱,258.56,HKD,2025-12-31,已代付一年（2025-12-31 起）；到期向朋友收下一年費用
R004,Jason Lou,active,2027-02-23,,Spotify,代付年度訂閱,258.56,HKD,2026-02-23,已代付一年（2026-02-23 起）；到期向朋友收下一年費用
R005,aleXson,active,2027-05-01,,Spotify,代付年度訂閱,258.56,HKD,2026-05-01,已代付一年（2026-05-01 起）；到期向朋友收下一年費用
R006,angel / aleXson / 雄少,active,2028-03-17,,NordVPN,共享訂閱,,,,合共 6 個位；2028-03-17 到期一起續約
```

The live CSV is edited by the user on the NAS (`data/event.csv`); rows are added, removed, and renamed freely. New rows keep unused ids in sequence (R007, R008, …).

## 2. Schedule

- Runs **once daily at 11:00 UK time** (`Europe/London`; must honour BST/DST — use IANA timezone, never a fixed UTC offset).
- NAS (DS920+) is switched off 01:00–08:00 daily, so a missed run is possible → catch-up required (see §4).

## 3. Notification points

For **each `active` row**, one cycle = the **standard points** plus any **early points** from `early_remind_months`:

- **Standard points:** expiry minus **30, 21, 14, 7, 4** days, plus day **0** (expiry day itself).
- **Early points** (only when `early_remind_months` is set to an integer `N ≥ 2`): **`N` months, `N−0.5` months, … down to `1.5` months** before expiry, in 0.5-month steps (i.e. `N−k×0.5` for `k = 0 … N−2`), each **ahead of** the standard cycle.
- Field **blank** → standard points only (no early points).

Date math (calendar months, never fixed day counts):
- `M` months before `expires_on` = subtract `M` calendar months (same day of month; end-of-month clamped, e.g. 2027-03-31 − 1 month = 2027-02-28).
- `0.5` month = subtract 15 days. So `1.5 months` = one calendar month plus 15 days before `expires_on`, `2.5 months` = two calendar months plus 15 days, etc.

Per daily run, for each active row:
- If today equals one of its not-yet-sent points (early or standard) → that point is "due".
- **New row / first ever appearance in state** (threshold = `N` months if `early_remind_months = N`, else 30 days):
  - days-to-expiry **> threshold** → send nothing yet; wait for the nearest future point in the full cycle (the `N`-month point if `N` is set, else the 30-day point).
  - days-to-expiry **≤ threshold** → send **one immediate notice** ("expires in X days") on the next run, then the remaining future points (early + standard, any not yet passed) proceed normally.
- If `today > expires_on` → silent; no further notices (the last one was the day-0 notice).

Example: `early_remind_months = 2`, `expires_on = 2027-03-10` → points: 2027-01-10 (2 months), 2027-01-26 (1.5 months = 1 calendar month + 15 days before), 2027-02-08 (30 days), 2027-02-17 (21), 2027-02-24 (14), 2027-03-03 (7), 2027-03-06 (4), 2027-03-10 (0).

## 4. Catch-up (NAS was down)

- If one or more points became due while the bot did not run: on the next successful run, **collapse ALL due-but-unsent points for that row into ONE "catch-up" message** (explicitly listing the missed offsets), not one message per point.
- Future points proceed normally afterwards.
- No point is ever re-sent twice.

## 5. Dedup / state

- Bot maintains a state file on disk (JSON), e.g. `state.json`. **Must persist across container restarts** (mounted volume); never reset counters on downtime/restart.
- Dedup key: `(id, expires_on, early_remind_months, offset)`, plus a first-seen/immediate-notice marker per `(id, expires_on)`; `offset` covers both standard (30/21/14/7/4/0) and early (N, N−0.5, …, 1.5 months) points.
- User actions:
  - **Renewed / friend paid → user updates `expires_on`** (and optionally `early_remind_months`) → old cycle's remaining (unsent) points are automatically voided; a fresh cycle (standard + any early points) starts on the new dates. **The update itself sends no notification.**
  - **Friend does not join → user deletes the row or sets `status=inactive`** → no further notifications of any kind.

## 6. Grouping (multiple triggers same day)

- If on a given check day **two or more notifications are due** (same run, any mix of standard / immediate / catch-up), **merge them into ONE email and ONE Telegram message** — do not send one per row.
- Grouping is by **check day**, not by expiry date: rows due on the same run go into the same message even if their `expires_on` differ.
- Message format: one line per row, e.g.:

```
R001 OVO Energy 2026-12-19 事件提醒
R002 Virgin Media 2028-03-10 事件提醒
```

- (Include a short title line such as the check date; content per spec above.)

## 7. Channels

- **Telegram**: official Bot API `sendMessage` (bot token + user chat_id from config).
- **Email**: SMTP (host/user/password from config); `MAIL_TO` may list
  multiple recipients comma-separated (one email to all).
- Both channels fire for each (grouped) notification; **independent** send — one channel's failure must not block or cancel the other.
- All credentials live in **`.env`** (never in the CSV, never committed).

## 8. CSV maintenance by the bot (sorting only)

After the daily check finishes and notifications are sent, the bot **rewrites `event.csv` sorted**:
- **Ascending by `expires_on`** — soonest-expiring row at top, latest at bottom.
- All `active` rows first, `inactive` rows at the **bottom** (Option A).
- Same `expires_on`: stable order by `id`.
- The bot **only reorders rows**: header and all cell values remain byte-identical; it never adds/removes rows or edits fields.
- Safe write: back up to `event.csv.bak` before writing; verify row count after write; atomic replace.

## 9. Deployment

- Docker container on Synology DS920+ Container Manager, single service. **App name: `event-reminder-bot`.**
- Data directory on the NAS (e.g. `/docker/event-reminder-bot`) **mounted into the container** so the user can directly add/edit/delete rows in `event.csv` from the NAS (File Station) — same for `state.json`, `event.csv.bak`, and `.env`, which must persist across container restarts.
- Daily trigger at 11:00 `Europe/London` (container cron or `TZ`-aware scheduler).
- Python, stdlib only where possible (no heavy deps).

## 10. Acceptance / testing

Must be tested with fake dates (no real tokens) before deploy:
1. Point day → one notification at each of 30/21/14/7/4/0; no repeats.
2. New row added: threshold = N months (if `early_remind_months=N`) else 30 days; days-to-expiry > threshold → silence until the nearest future point; days-to-expiry ≤ threshold → one immediate notice on the next run, then remaining future points (early + standard, not yet passed) proceed normally.
2b. `early_remind_months = 2` (or 3) → early points at N, N−0.5, …, 1.5 months fire on their dates (calendar-month math per §3), before the standard 30/21/14/7/4/0 points; blank → standard cycle only.
3. Missed days (simulated NAS downtime) → single collapsed catch-up message listing missed offsets, then normal cadence.
4. `expires_on` changed mid-cycle → old cycle voided, new cycle (standard + any early points) on new dates, no notification at the moment of change.
5. Row deleted / set `inactive` → no further notifications.
6. Two rows due on the same day → exactly one Telegram message + one email, both containing both rows, in id order.
7. After each run, CSV is re-sorted ascending by `expires_on`, `inactive` at bottom, values otherwise unchanged, `.bak` created.

Worked example: OVO expires 2026-12-19 → notifications fire 2026-11-19 (30), 2026-11-28 (21), 2026-12-05 (14), 2026-12-12 (7), 2026-12-15 (4), 2026-12-19 (0).
