"""
Event Reminder Bot — core logic.

Pure stdlib. All dates are date objects in the Europe/London zone;
"today" is injected (dependency-injected) by the caller for testability.

Responsibilities split per class (see PROJECT.md):
  CyclePlanner   : point-list math (pure, no I/O)
  EventRecord    : CSV row dataclass (byte-identical preservation)
  StateStore     : state.json read/write + dedup
  MessageBuilder : grouped / immediately / catch-up message text
  TelegramSender : Telegram Bot API (urllib)
  EmailSender    : SMTP (smtplib)
  CsvStore       : event.csv read + safe re-sorting write
"""

from __future__ import annotations

import csv
import io
import json
import os
import smtplib
import ssl
import sys
import tempfile
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HEADER = [
    "id", "people", "status", "expires_on", "early_remind_months",
    "event", "type", "amount", "currency", "paid_on", "note",
]

STANDARD_OFFSETS_DAYS = [30, 21, 14, 7, 4, 0]
VALID_OFFSETS = {"30", "21", "14", "7", "4", "0"}


# ---------------------------------------------------------------------------
# Date helpers (calendar months, end-of-month clamped)
# ---------------------------------------------------------------------------

def add_months(d: date, months: int) -> date:
    """Add/subtract whole calendar months, clamping end-of-month.

    2027-03-31 + (-1 month) -> 2027-02-28
    2027-01-31 + (-1 month) -> 2026-12-31
    """
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    # Clamp day to last day of target month
    if month == 12:
        next_month_first = date(year + 1, 1, 1)
    else:
        next_month_first = date(year, month + 1, 1)
    last_day = (next_month_first - timedelta(days=1)).day
    return date(year, month, min(d.day, last_day))


def offset_to_date(offset: str, expires_on: date) -> date:
    """Turn a point offset id (e.g. '30', '3', '2.5') into an absolute date."""
    if offset in VALID_OFFSETS:
        return expires_on - timedelta(days=int(offset))
    # Month offsets: "N" (whole months) or "N.5" (half-month steps)
    whole = float(offset)
    w = int(whole)
    half = whole - w >= 0.5 - 1e-9
    d = add_months(expires_on, -w)
    if half:
        d = d - timedelta(days=15)
    return d


def format_offset(offset: str) -> str:
    """Human label for an offset id ('30' → 30 日前, '2.5' → 2.5 個月前)."""
    if offset in VALID_OFFSETS:
        if offset == "0":
            return "到期日 (0日)"
        return f"{offset} 日前"
    # Month-offset ids like "3", "2", "2.5", "1.5"
    return f"{offset} 個月前"


# ---------------------------------------------------------------------------
# EventRecord
# ---------------------------------------------------------------------------

@dataclass
class EventRecord:
    id: str
    people: str
    status: str
    expires_on: str            # stored as raw string; parsed when needed
    early_remind_months: str   # "" or "2"/"3"...
    event: str
    type: str
    amount: str
    currency: str
    paid_on: str
    note: str

    @classmethod
    def from_row(cls, row: list[str]) -> "EventRecord":
        row = list(row) + [""] * (len(HEADER) - len(row))
        return cls(**dict(zip(HEADER, row[: len(HEADER)])))

    def to_row(self) -> list[str]:
        return [
            self.id, self.people, self.status, self.expires_on,
            self.early_remind_months, self.event, self.type,
            self.amount, self.currency, self.paid_on, self.note,
        ]

    @property
    def expires_on_date(self) -> Optional[date]:
        try:
            return date.fromisoformat(self.expires_on)
        except ValueError:
            return None

    @property
    def early_remind_months_value(self) -> Optional[int]:
        v = self.early_remind_months.strip()
        if not v:
            return None
        try:
            n = int(v)
        except ValueError:
            return None
        return n if n >= 2 else None


# ---------------------------------------------------------------------------
# CyclePlanner (pure)
# ---------------------------------------------------------------------------

@dataclass
class DueInfo:
    kind: str                # "standard" | "early" | "immediate" | "catch-up"
    offsets: list[str]       # e.g. ["30"], or ["2","1.5","30","21"] for catch-up
    days_to_expiry: int
    message_date: date       # the expiry date shown in the row line


class CyclePlanner:
    """Computes the full point list and which points are due today."""

    def __init__(self, today: date):
        self.today = today

    def point_offsets(self, early_remind_months: Optional[int]) -> list[str]:
        """Full ordered list of point-offset ids for a cycle:
        early points first (N, N-0.5, ..., 1.5 months), then standard 30/21/14/7/4/0.
        Month offsets are ids like "3", "2.5", "2", "1.5"."""
        offsets: list[str] = []
        if early_remind_months is not None and early_remind_months >= 2:
            n = early_remind_months
            # Walk N months down to 1.5 months in half-month steps:
            # N, N-0.5, N-1, ..., 1.5
            two_x = 2 * n
            while two_x >= 3:  # stop at 1.5 months (== 3 half-months)
                if two_x % 2 == 0:
                    offsets.append(str(two_x // 2))
                else:
                    offsets.append(f"{two_x // 2}.5")
                two_x -= 1
        standard = ["30", "21", "14", "7", "4", "0"]
        return offsets + standard

    def point_dates(self, rec: EventRecord, offsets: list[str]) -> dict[str, date]:
        expires_on = rec.expires_on_date
        if expires_on is None:
            return {}
        return {o: offset_to_date(o, expires_on) for o in offsets}

    def due_points(
        self,
        rec: EventRecord,
        sent: set[tuple],
        first_seen: set[tuple],
        known_ids: set[str],
    ) -> tuple[list[DueInfo], list[str]]:
        """Return (due_infos, voided_offsets) for this row today.

        - First ever appearance of this (id, expires_on):
          - Renewal (id already tracked with a previous date): every cycle
            point at or before today is voided silently (no notification at
            the moment of change); future points fire on their natural dates.
          - Brand-new row: threshold = N months if early_remind_months=N,
            else 30 days. Past points are voided; if days-to-expiry <=
            threshold -> ONE immediate notice; otherwise silent until the
            nearest future point.
        - Established row: points due today plus any missed-but-unsent points
          collapse into ONE DueInfo (kind "catch-up" if anything was missed).

        sent: set of (id, expires_on, early, offset) tuples already sent.
        """
        expires_on = rec.expires_on_date
        if expires_on is None:
            return [], []
        days_to_expiry = (expires_on - self.today).days
        early = rec.early_remind_months_value
        offsets = self.point_offsets(early)
        dates = self.point_dates(rec, offsets)
        key_base = (rec.id, rec.expires_on, rec.early_remind_months)
        first_key = (rec.id, rec.expires_on)

        if first_key not in first_seen:
            # Points at or before today never surface as catch-up later:
            # the caller marks them sent (voided).
            voided = [o for o in offsets if dates[o] <= self.today]
            if rec.id in known_ids:
                # Renewal: silent at the moment of change; the fresh cycle's
                # future points fire on their natural dates.
                return [], voided
            if early is not None:
                threshold_days = (
                    expires_on - offset_to_date(str(early), expires_on)
                ).days
            else:
                threshold_days = 30
            if days_to_expiry < 0 or days_to_expiry > threshold_days:
                return [], voided
            return [DueInfo("immediate", [], days_to_expiry, expires_on)], voided

        if self.today > expires_on:
            return [], []

        due_today = [
            o for o in offsets
            if dates[o] == self.today and key_base + (o,) not in sent
        ]
        missed = [
            o for o in offsets
            if dates[o] < self.today and key_base + (o,) not in sent
        ]
        if missed or due_today:
            if missed:
                return [DueInfo("catch-up", missed + due_today, days_to_expiry, expires_on)], []
            kind = "standard" if all(o in VALID_OFFSETS for o in due_today) else "early"
            return [DueInfo(kind, due_today, days_to_expiry, expires_on)], []
        return [], []


# ---------------------------------------------------------------------------
# StateStore
# ---------------------------------------------------------------------------

class StateStore:
    def __init__(self, path: str):
        self.path = path
        self.data: dict = {"sent": [], "first_seen": []}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self.data = {"sent": [], "first_seen": []}
        self.data.setdefault("sent", [])
        self.data.setdefault("first_seen", [])

    def save(self) -> None:
        d = os.path.dirname(self.path) or "."
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".state_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # --- API ---

    def is_first_seen(self, id_: str, expires_on: str) -> bool:
        return (id_, expires_on) not in self.data["first_seen"]

    def mark_first_seen(self, id_: str, expires_on: str) -> None:
        if (id_, expires_on) not in self.data["first_seen"]:
            self.data["first_seen"].append([id_, expires_on])

    def already_sent(self, key: tuple) -> bool:
        return list(key) in self.data["sent"]

    def mark_sent(self, key: tuple) -> None:
        lst = list(key)
        if lst not in self.data["sent"]:
            self.data["sent"].append(lst)

    def first_seen_set(self) -> set[tuple]:
        return {tuple(x) for x in self.data["first_seen"]}

    def sent_set(self) -> set[tuple]:
        return {tuple(x) for x in self.data["sent"]}

    def active_keys(self, ids_expiring: set[tuple]) -> None:
        """Prune sent keys whose (id, expires_on, early) base is no longer
        present & active in the CSV (row deleted, or expires_on /
        early_remind_months changed — old cycle voided). Keeps state.json
        bounded."""
        keep = [
            e for e in self.data["sent"]
            if len(e) >= 3 and tuple(e[:3]) in ids_expiring
        ]
        self.data["sent"] = keep


# ---------------------------------------------------------------------------
# MessageBuilder
# ---------------------------------------------------------------------------

# Urgency colours (4-level): 🔴 today · 🟠 ≤4 days · 🟡 ≤30 days · 🔵 >30 days
_URGENCE = [
    # (emoji, badge hex, badge text colour)
    ("🔴", "#e53935", "#ffffff"),
    ("🟠", "#fb8c00", "#ffffff"),
    ("🟡", "#fdd835", "#212121"),
    ("🔵", "#1e88e5", "#ffffff"),
]


def urgency_for_days(days_to_expiry: int) -> tuple[str, str, str]:
    """Pick (emoji, bg_hex, fg_hex) for a remaining-days count."""
    if days_to_expiry <= 0:
        return _URGENCE[0]
    if days_to_expiry <= 4:
        return _URGENCE[1]
    if days_to_expiry <= 30:
        return _URGENCE[2]
    return _URGENCE[3]


class MessageBuilder:
    @staticmethod
    def _entry(rec: EventRecord, check_date: date, catchup: bool) -> tuple[str, str]:
        """Return (plain line without emoji, html block) for one due row."""
        days = (rec.expires_on_date - check_date).days if rec.expires_on_date else 0
        due = "今日到期" if days == 0 else f"即將{days}日後到期"
        line = (
            f"ID: {rec.id} | {rec.people} | 事件: {rec.event} | "
            f"到期日: {rec.expires_on} | {due}"
        )
        note = rec.note.strip()

        # HTML entry: 事件 line fully bold (incl. label), ID plain,
        # 到期日 line bold, N-day count bold + underlined + light-yellow
        # highlight (#ffe082) for every urgency band.
        if days == 0:
            due_html = "<b>今日到期</b>"
        else:
            due_html = (
                f"即將<span style=\"background:#ffe082; padding:0 2px;\">"
                f"<u><b>{days}日</b></u></span>後到期"
            )
        parts = [
            f"ID: {escape(rec.id)} | {escape(rec.people)} | "
            f"<b>事件: {escape(rec.event)}</b>",
            f"<b>到期日: {escape(rec.expires_on)}</b> · {due_html}",
        ]
        if catchup:
            parts.append(' <b style="color:#e53935;">⚠️ 補發</b>')
        html_block = "<br>".join(parts)
        if note:
            html_block += (
                f'<br><span style="color:#80868b; font-size:12px;">'
                f"{escape(note)}</span>"
            )
        return line, html_block

    @staticmethod
    def _entries(infos: list[tuple[str, EventRecord, DueInfo]], check_date: date):
        out = []
        for _rid, rec, info in infos:
            days = (rec.expires_on_date - check_date).days if rec.expires_on_date else 0
            emoji, bg, fg = urgency_for_days(days)
            line, html_block = MessageBuilder._entry(
                rec, check_date, info.kind == "catch-up")
            if info.kind == "catch-up":
                line += " | 補發"
            if rec.note.strip():
                line += f" | {rec.note.strip()}"
            out.append((emoji, bg, fg, days, line, html_block))
        return out

    @staticmethod
    def build(check_date: date, infos: list[tuple[str, EventRecord, DueInfo]]):
        """infos: list of (row_id, record, due_info) in id order.
        Returns (telegram_text, email_subject, (plain_body, html_body))."""
        header = f"事件提醒 — {check_date.isoformat()}"
        entries = MessageBuilder._entries(infos, check_date)

        # Plain text: header line + one line per row, blank line between rows
        # (same convention for Telegram and the email text fallback).
        text_lines = [header]
        for emoji, _bg, _fg, _days, line, _html in entries:
            text_lines.append("")
            text_lines.append(f"{emoji} {line}")
        plain = "\n".join(text_lines)

        subject = header

        # HTML: title + one 2-column row per entry (vertically-centred badge
        # | details), with a full-width dark divider row between entries
        # (none after the last).
        parts = [
            '<p style="margin:0 0 10px; font-size:15px;">'
            f"<b>{escape(header)}</b></p>",
            '<table style="border-collapse:collapse; width:100%;">',
        ]
        for i, (emoji, _bg, _fg, days, _line, html_block) in enumerate(entries):
            if i > 0:
                parts.append(
                    '<tr><td colspan="2" style="height:0; padding:0; '
                    'border-top:2px solid #3c4043; '
                    "border-bottom:none;\"></td></tr>"
                )
            badge = (
                '<span style="background:#1f1f1f; color:#ffffff; '
                f'border-radius:4px; padding:2px 8px; font-size:12px; '
                f'display:inline-block;">{emoji} {days}日</span>'
            )
            parts.append(
                "<tr>"
                '<td style="vertical-align:middle; padding:2px 10px 2px 0; '
                f'white-space:nowrap;">{badge}</td>'
                f'<td style="vertical-align:top; line-height:1.35;">{html_block}</td>'
                "</tr>"
            )
        parts.append("</table>")
        html_body = (
            '<div style="background:#ffffff; max-width:560px; margin:0; padding:16px; '
            'color:#202124; font-size:14px; line-height:1.35; '
            "font-family:-apple-system, 'Noto Sans SC', 'Noto Sans CJK SC', "
            'sans-serif;">' + "".join(parts) + "</div>"
        )

        return plain, subject, (plain, html_body)


# ---------------------------------------------------------------------------
# Senders
# ---------------------------------------------------------------------------

class TelegramSender:
    def __init__(self, token: str, chat_ids: str):
        self.token = token
        # chat_ids may list several chat ids, comma-separated
        # (e.g. "123456789,987654321").
        self.chat_ids = [c.strip() for c in chat_ids.split(",") if c.strip()]

    def send(self, text: str) -> bool:
        """Send to every chat. Returns True only if ALL chats received it;
        one failing chat does not stop the others."""
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        all_ok = True
        for chat_id in self.chat_ids:
            data = json.dumps(
                {"chat_id": chat_id, "text": text}
            ).encode("utf-8")
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    body = json.loads(resp.read().decode("utf-8", "replace"))
                    ok = bool(body.get("ok"))
            except Exception as exc:  # noqa: BLE001
                print(f"[telegram] SEND FAILED (chat {chat_id}): {exc}",
                      file=sys.stderr)
                ok = False
            all_ok = all_ok and ok
        return all_ok


class EmailSender:
    def __init__(self, host: str, port: int, user: str, password: str,
                 sender_email: str, to_email: str, use_ssl: bool = False):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.sender_email = sender_email
        # to_email may list several recipients, comma-separated.
        self.recipients = [e.strip() for e in to_email.split(",") if e.strip()]
        self.to_email = ", ".join(self.recipients)
        # use_ssl=True  -> implicit TLS over the whole connection (port 465)
        # use_ssl=False -> plain connection + explicit STARTTLS (port 587)
        self.use_ssl = use_ssl

    def _login_and_send(self, server, msg) -> None:
        if not self.use_ssl:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        server.login(self.user, self.password)
        # Send the encoded bytes, not str: a raw Python str gets ascii-
        # encoded by smtplib and crashes on Chinese text.
        server.sendmail(self.sender_email, self.recipients, msg.as_bytes())

    def send(self, subject: str, body: tuple[str, str]) -> bool:
        """Send a multipart/alternative email: text/plain part first (fallback
        for clients that ignore HTML) + text/html coloured part."""
        plain, html = body
        try:
            # Multipart messages must be sent as raw bytes — a composed str
            # would get ascii-encoded and crash on the Chinese text.
            msg = MIMEMultipart("alternative")
            msg["From"] = self.sender_email
            msg["To"] = self.to_email
            msg["Subject"] = subject
            msg.attach(MIMEText(plain, "plain", "utf-8"))
            msg.attach(MIMEText(html, "html", "utf-8"))
            if self.use_ssl:
                server = smtplib.SMTP_SSL(self.host, self.port, timeout=30)
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=30)
            with server:
                self._login_and_send(server, msg)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[email] SEND FAILED: {exc}", file=sys.stderr)
            return False


# ---------------------------------------------------------------------------
# CsvStore
# ---------------------------------------------------------------------------

class CsvStore:
    def __init__(self, path: str):
        self.path = path
        self.bak_path = path + ".bak"

    def read(self) -> list[EventRecord]:
        with open(self.path, "r", encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        if not rows:
            return []
        if rows[0] != HEADER:
            raise ValueError(f"unexpected header in {self.path}: {rows[0]}")
        records = []
        for r in rows[1:]:
            if not any(c.strip() for c in r):
                continue  # skip blank lines
            records.append(EventRecord.from_row(r))
        return records

    def sort_key(self, rec: EventRecord) -> tuple:
        is_active = 0 if rec.status.strip().lower() == "active" else 1
        try:
            expires = rec.expires_on_date or date.max
        except Exception:
            expires = date.max
        return (is_active, expires, rec.id)

    def write_sorted(self, records: list[EventRecord]) -> None:
        """Backup -> build sorted CSV -> verify row count -> atomic replace."""
        # 1. Backup
        if os.path.exists(self.path):
            with open(self.path, "rb") as src, open(self.bak_path, "wb") as dst:
                dst.write(src.read())

        # 2. Build sorted rows (byte-identical cell values)
        sorted_recs = sorted(records, key=self.sort_key)

        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(HEADER)
        for rec in sorted_recs:
            writer.writerow(rec.to_row())
        new_text = buf.getvalue()

        # 3. Verify: parse the new text back — header, row count, and
        # byte-identical cell contents (only reordering is allowed)
        parsed = list(csv.reader(io.StringIO(new_text)))
        if not parsed or parsed[0] != HEADER:
            raise ValueError("header mismatch after build")
        data_rows = [list(r) for r in parsed[1:]]
        expected = sorted(rec.to_row() for rec in records)
        if len(data_rows) != len(records) or sorted(data_rows) != expected:
            raise ValueError("row count/content mismatch after build")

        # 4. Atomic replace
        d = os.path.dirname(self.path) or "."
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".event_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(new_text)
            os.replace(tmp, self.path)
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
