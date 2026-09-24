"""Acceptance tests for event-reminder-bot (REQUIREMENTS.md §10).

Stdlib only. Fake dates, in-memory fake senders — no real tokens, no network.
Run from the project root:

    python3 -m unittest discover -s tests -v
"""

import csv
import io
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot as botmod  # noqa: E402
from event_reminder import (  # noqa: E402
    HEADER,
    CsvStore,
    StateStore,
    add_months,
)

TZ = ZoneInfo("Europe/London")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_row(rid, expires, status="active", early="", event="TestEvent", note=""):
    # id,people,status,expires_on,early_remind_months,event,type,amount,currency,paid_on,note
    return [rid, "本人", status, expires, early, event, "", "", "", "", note]


def write_csv(path, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(list(HEADER))
    for r in rows:
        w.writerow(r)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())


def read_rows(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.reader(f))


def seed_first_seen(state_path, pairs):
    """State as if the rows had been present (and first-seen) earlier."""
    st = StateStore(state_path)
    for rid, exp in pairs:
        st.mark_first_seen(rid, exp)
    st.save()


class FakeSender:
    """Duck-typed stand-in for TelegramSender/EmailSender."""

    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []  # list of arg tuples per attempted send

    def send(self, *args):
        self.sent.append(args)
        return self.ok


def run_day(d, csv_path, state_path, tg=None, em=None, dry=False):
    return botmod.run_pass(csv_path, state_path, d, tg, em, dry,
                           log=lambda *a: None)


def daily(d0, d1):
    while d0 <= d1:
        yield d0
        d0 += timedelta(days=1)


# ---------------------------------------------------------------------------
# §10.1 — standard points fire once each, no repeats
# ---------------------------------------------------------------------------

class TestStandardPoints(unittest.TestCase):
    def test_each_standard_point_fires_once_no_repeats(self):
        with tempfile.TemporaryDirectory() as tmp:
            csvp = os.path.join(tmp, "event.csv")
            stp = os.path.join(tmp, "state.json")
            exp = date(2026, 3, 19)
            # Established row: first-seen marked before the window, so the
            # 30-day point fires on its natural date (not as "immediate").
            write_csv(csvp, [make_row("R100", exp.isoformat())])
            seed_first_seen(stp, [("R100", exp.isoformat())])

            tg = FakeSender()
            fired = {}
            for d in daily(exp - timedelta(days=30), exp + timedelta(days=5)):
                n = run_day(d, csvp, stp, tg)
                if n == 1:
                    fired[d] = tg.sent.pop()
            self.assertEqual(tg.sent, [], "no extra/duplicate messages")
            expected_days = [exp - timedelta(days=x) for x in (30, 21, 14, 7, 4, 0)]
            self.assertEqual(sorted(fired.keys()), expected_days)
            texts = [t[0] for t in fired.values()]
            for label in ("30日後到期", "21日後到期", "14日後到期",
                          "7日後到期", "4日後到期", "今日到期"):
                self.assertIn(label, " || ".join(texts))
            self.assertNotIn("補發", " || ".join(texts))


# ---------------------------------------------------------------------------
# §10.2 — new-row threshold rule
# ---------------------------------------------------------------------------

class TestNewRow(unittest.TestCase):
    def test_far_new_row_silent_until_30day_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            csvp = os.path.join(tmp, "event.csv")
            stp = os.path.join(tmp, "state.json")
            exp = date(2026, 6, 15)             # 30-day point = 2026-05-16
            write_csv(csvp, [make_row("R101", exp.isoformat())])

            tg = FakeSender()
            # Day of appearance: 90 days to expiry > 30-day threshold -> silent
            self.assertEqual(run_day(date(2026, 3, 17), csvp, stp, tg), 0)
            self.assertEqual(tg.sent, [])

            fired = {}
            for d in daily(date(2026, 3, 18), date(2026, 6, 21)):
                n = run_day(d, csvp, stp, tg)
                if n == 1:
                    fired[d] = tg.sent.pop()
            self.assertEqual(tg.sent, [])
            self.assertEqual(
                sorted(fired.keys()),
                [exp - timedelta(days=x) for x in (30, 21, 14, 7, 4, 0)],
            )

    def test_near_new_row_immediate_then_remaining_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            csvp = os.path.join(tmp, "event.csv")
            stp = os.path.join(tmp, "state.json")
            exp = date(2026, 4, 10)             # 9 days out at first sight
            write_csv(csvp, [make_row("R102", exp.isoformat())])

            tg = FakeSender()
            fired = {}
            for d in daily(date(2026, 4, 1), date(2026, 4, 11)):
                n = run_day(d, csvp, stp, tg)
                if n == 1:
                    fired[d] = tg.sent.pop()
            self.assertEqual(tg.sent, [])
            # One immediate notice on day 1, then only the future standard
            # points (7d=04-03, 4d=04-06, 0=04-10). 30/21/14 were voided.
            self.assertEqual(sorted(fired.keys()), [date(2026, 4, 1),
                                                    date(2026, 4, 3),
                                                    date(2026, 4, 6),
                                                    date(2026, 4, 10)])
            first = fired[date(2026, 4, 1)][0]
            self.assertIn("9日後到期", first)
            self.assertIn("ID: R102 | 本人 | 事件: TestEvent | 到期日: 2026-04-10", first)
            self.assertIn("今日到期", fired[date(2026, 4, 10)][0])
            for d in (date(2026, 3, 11), date(2026, 3, 20), date(2026, 3, 27)):
                self.assertNotIn(d, fired)

    def test_new_row_already_expired_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            csvp = os.path.join(tmp, "event.csv")
            stp = os.path.join(tmp, "state.json")
            write_csv(csvp, [make_row("R103", "2026-03-01")])
            tg = FakeSender()
            self.assertEqual(run_day(date(2026, 4, 1), csvp, stp, tg), 0)
            self.assertEqual(tg.sent, [])
            # No catch-up can ever surface afterwards either.
            self.assertEqual(run_day(date(2026, 4, 2), csvp, stp, tg), 0)


# ---------------------------------------------------------------------------
# §10.2b — early_remind_months (N, N−0.5 … 1.5) and calendar-month math
# ---------------------------------------------------------------------------

class TestEarlyPoints(unittest.TestCase):
    def test_n2_spec_example(self):
        import event_reminder as er
        rec = er.EventRecord.from_row(make_row("R110", "2027-03-10", early="2"))
        planner = er.CyclePlanner(date(2026, 12, 31))
        offsets = planner.point_offsets(2)
        self.assertEqual(offsets, ["2", "1.5", "30", "21", "14", "7", "4", "0"])
        dates = planner.point_dates(rec, offsets)
        self.assertEqual(
            [dates[o] for o in offsets],
            [date(2027, 1, 10), date(2027, 1, 26), date(2027, 2, 8),
             date(2027, 2, 17), date(2027, 2, 24), date(2027, 3, 3),
             date(2027, 3, 6), date(2027, 3, 10)],
        )

    def test_n2_runtime_early_points_then_standard(self):
        import event_reminder as er
        with tempfile.TemporaryDirectory() as tmp:
            csvp = os.path.join(tmp, "event.csv")
            stp = os.path.join(tmp, "state.json")
            write_csv(csvp, [make_row("R111", "2027-03-10", early="2")])
            # First sight 2026-12-31: 70 days out > 59-day threshold -> silent
            self.assertEqual(run_day(date(2026, 12, 31), csvp, stp), 0)
            tg = FakeSender()
            fired = {}
            for d in daily(date(2027, 1, 1), date(2027, 3, 15)):
                n = run_day(d, csvp, stp, tg)
                if n == 1:
                    fired[d] = tg.sent.pop()
            got = {date(2027, 1, 10), date(2027, 1, 26), date(2027, 2, 8),
                   date(2027, 2, 17), date(2027, 2, 24), date(2027, 3, 3),
                   date(2027, 3, 6), date(2027, 3, 10)}
            self.assertEqual(set(fired.keys()), got)
            # New layout: remaining-days phrasing, e.g. 59/43/30日後到期.
            self.assertIn("59日後到期", fired[date(2027, 1, 10)][0])
            self.assertIn("43日後到期", fired[date(2027, 1, 26)][0])
            self.assertIn("30日後到期", fired[date(2027, 2, 8)][0])
            self.assertNotIn("補發", " || ".join(t[0] for t in fired.values()))

    def test_n3_offsets_and_dates(self):
        import event_reminder as er
        rec = er.EventRecord.from_row(make_row("R112", "2027-03-31", early="3"))
        planner = er.CyclePlanner(date(2027, 1, 1))
        offsets = planner.point_offsets(3)
        self.assertEqual(offsets, ["3", "2.5", "2", "1.5",
                                   "30", "21", "14", "7", "4", "0"])
        dates = planner.point_dates(rec, offsets)
        self.assertEqual(
            [dates[o] for o in offsets[:4]],
            # 3mo → 2026-12-31; 2.5mo → 2027-01-31 −15d; 2mo → 2027-01-31;
            # 1.5mo → 2027-02-28 (clamped) −15d
            [date(2026, 12, 31), date(2027, 1, 16),
             date(2027, 1, 31), date(2027, 2, 13)],
        )

    def test_add_months_end_of_month_clamp(self):
        self.assertEqual(add_months(date(2027, 3, 31), -1), date(2027, 2, 28))
        self.assertEqual(add_months(date(2027, 1, 31), -1), date(2026, 12, 31))
        self.assertEqual(add_months(date(2027, 3, 15), -2), date(2027, 1, 15))

    def test_blank_or_sub2_early_standard_only(self):
        import event_reminder as er
        planner = er.CyclePlanner(date(2027, 1, 1))
        self.assertEqual(planner.point_offsets(None), ["30", "21", "14", "7", "4", "0"])
        rec_blank = er.EventRecord.from_row(make_row("R113", "2027-03-10"))
        rec_1 = er.EventRecord.from_row(make_row("R114", "2027-03-10", early="1"))
        self.assertIsNone(rec_blank.early_remind_months_value)
        self.assertIsNone(rec_1.early_remind_months_value)


# ---------------------------------------------------------------------------
# §10.3 — catch-up collapses missed days into one message
# ---------------------------------------------------------------------------

class TestCatchUp(unittest.TestCase):
    def test_missed_days_collapse_into_single_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            csvp = os.path.join(tmp, "event.csv")
            stp = os.path.join(tmp, "state.json")
            exp = date(2026, 5, 31)   # 30d=05-01, 21d=05-10, 14d=05-17, 7d=05-24
            write_csv(csvp, [make_row("R120", exp.isoformat())])
            seed_first_seen(stp, [("R120", exp.isoformat())])

            tg = FakeSender()
            # Normal run for the 30-day point.
            self.assertEqual(run_day(date(2026, 5, 1), csvp, stp, tg), 1)
            self.assertIn("30日後到期", tg.sent.pop()[0])
            # 05-02 .. 05-17: bot did not run (NAS down).
            # Next run on 05-18: 21d and 14d both missed -> ONE catch-up.
            self.assertEqual(run_day(date(2026, 5, 18), csvp, stp, tg), 1)
            text = tg.sent.pop()[0]
            self.assertIn("補發", text)
            self.assertIn("13日後到期", text)   # 2026-05-31 - 2026-05-18
            self.assertTrue(text.rstrip().endswith("補發"))  # catch-up tail
            self.assertEqual(tg.sent, [])
            # Cadence resumes normally afterwards.
            self.assertEqual(run_day(date(2026, 5, 24), csvp, stp, tg), 1)
            self.assertIn("7日後到期", tg.sent.pop()[0])
            self.assertEqual(tg.sent, [])
            # No re-sends of the collapsed points.
            self.assertEqual(run_day(date(2026, 5, 25), csvp, stp, tg), 0)


# ---------------------------------------------------------------------------
# §10.4 — expires_on changed mid-cycle: silent, old cycle voided
# ---------------------------------------------------------------------------

class TestRenewal(unittest.TestCase):
    def test_expiry_change_silent_and_new_cycle_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            csvp = os.path.join(tmp, "event.csv")
            stp = os.path.join(tmp, "state.json")
            write_csv(csvp, [make_row("R130", "2026-06-01")])
            seed_first_seen(stp, [("R130", "2026-06-01")])

            tg = FakeSender()
            # Old cycle: 30-day point fires (21day = 05-11 comes later).
            self.assertEqual(run_day(date(2026, 5, 2), csvp, stp, tg), 1)
            self.assertIn("30日後到期", tg.sent.pop()[0])
            # User renews on 2026-05-11 (old 21-day point day): no notice,
            # old cycle voided, fresh cycle on 2026-09-01.
            write_csv(csvp, [make_row("R130", "2026-09-01")])
            self.assertEqual(run_day(date(2026, 5, 11), csvp, stp, tg), 0)
            self.assertEqual(tg.sent, [])
            # State pruned: old (id, old-date) base gone.
            st = StateStore(stp)
            bases = {tuple(e[:3]) for e in st.data["sent"]}
            self.assertNotIn(("R130", "2026-06-01", ""), bases)
            # New cycle fires only on the new dates; old remainder never fires.
            fired = {}
            for d in daily(date(2026, 5, 12), date(2026, 9, 5)):
                n = run_day(d, csvp, stp, tg)
                if n == 1:
                    fired[d] = tg.sent.pop()
            new_exp = date(2026, 9, 1)
            self.assertEqual(sorted(fired.keys()),
                             [new_exp - timedelta(days=x)
                              for x in (30, 21, 14, 7, 4, 0)])
            self.assertEqual(tg.sent, [])


# ---------------------------------------------------------------------------
# §10.5 — row deleted / inactive: silent, state pruned
# ---------------------------------------------------------------------------

class TestInactiveOrDeleted(unittest.TestCase):
    def test_inactive_row_silent_and_state_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            csvp = os.path.join(tmp, "event.csv")
            stp = os.path.join(tmp, "state.json")
            write_csv(csvp, [make_row("R140", "2026-06-10")])
            seed_first_seen(stp, [("R140", "2026-06-10")])
            tg = FakeSender()
            self.assertEqual(run_day(date(2026, 5, 11), csvp, stp, tg), 1)  # 30d
            tg.sent.pop()
            st = StateStore(stp)
            self.assertIn(list(("R140", "2026-06-10", "", "30")), st.data["sent"])
            # User flips status to inactive.
            write_csv(csvp, [make_row("R140", "2026-06-10", status="inactive")])
            self.assertEqual(run_day(date(2026, 5, 25), csvp, stp, tg), 0)
            self.assertEqual(tg.sent, [])
            st = StateStore(stp)
            self.assertEqual(
                [e for e in st.data["sent"] if e[0] == "R140"], [],
            )

    def test_deleted_row_silent_and_state_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            csvp = os.path.join(tmp, "event.csv")
            stp = os.path.join(tmp, "state.json")
            write_csv(csvp, [make_row("R141", "2026-06-10"),
                             make_row("R142", "2026-07-10")])
            seed_first_seen(stp, [("R141", "2026-06-10"), ("R142", "2026-07-10")])
            tg = FakeSender()
            # 2026-05-11: only R141 is due (30d). R142's 30d = 2026-06-10.
            self.assertEqual(run_day(date(2026, 5, 11), csvp, stp, tg), 1)
            text = tg.sent.pop()[0]
            self.assertIn("R141", text)
            self.assertNotIn("R142", text)
            # User deletes R141.
            write_csv(csvp, [make_row("R142", "2026-07-10")])
            self.assertEqual(run_day(date(2026, 5, 12), csvp, stp, tg), 0)
            st = StateStore(stp)
            self.assertFalse(any(e[0] == "R141" for e in st.data["sent"]))


# ---------------------------------------------------------------------------
# §10.6 — grouping: two rows due same run -> one Telegram + one email
# ---------------------------------------------------------------------------

class TestGrouping(unittest.TestCase):
    def test_two_rows_due_same_day_one_message_each_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            csvp = os.path.join(tmp, "event.csv")
            stp = os.path.join(tmp, "state.json")
            # R151 30-day point and R152 21-day point both fall on 2026-05-26.
            write_csv(csvp, [make_row("R151", "2026-06-25"),
                             make_row("R152", "2026-06-16")])
            seed_first_seen(stp, [("R151", "2026-06-25"), ("R152", "2026-06-16")])
            tg, em = FakeSender(), FakeSender()
            self.assertEqual(run_day(date(2026, 5, 26), csvp, stp, tg, em), 2)
            self.assertEqual(len(tg.sent), 1, "exactly one Telegram message")
            self.assertEqual(len(em.sent), 1, "exactly one email")
            text = tg.sent[0][0]
            self.assertIn("R151", text)
            self.assertIn("R152", text)
            self.assertLess(text.find("R151"), text.find("R152"), "id order")
            subject, body = em.sent[0]
            self.assertEqual(subject, "事件提醒 2026-05-26")
            self.assertEqual(body, text)


# ---------------------------------------------------------------------------
# §10.7 — CsvStore re-sort: order, .bak, byte-identical values
# ---------------------------------------------------------------------------

class TestCsvStoreReSort(unittest.TestCase):
    def test_sort_order_backup_and_byte_identical_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            csvp = os.path.join(tmp, "event.csv")
            rows = [
                make_row("R202", "2026-05-01", status="inactive"),
                make_row("R200", "2027-01-01"),
                make_row("R199", "2026-11-01", note="續約, 收錢, 記賬"),
                make_row("R203", "2026-12-01"),
                make_row("R201", "2027-01-01"),
            ]
            write_csv(csvp, rows)
            original_bytes = open(csvp, "rb").read()

            store = CsvStore(csvp)
            records = store.read()
            store.write_sorted(records)

            parsed = read_rows(csvp)
            self.assertEqual(parsed[0], list(HEADER))
            self.assertEqual([r[0] for r in parsed[1:]],
                             ["R199", "R203", "R200", "R201", "R202"])
            # .bak holds the pre-sort file exactly
            self.assertTrue(os.path.exists(csvp + ".bak"))
            self.assertEqual(open(csvp + ".bak", "rb").read(), original_bytes)
            # Cell contents preserved exactly (multiset), incl. the
            # comma-containing note.
            self.assertEqual(sorted(parsed[1:]), sorted(rows))
            r199 = next(r for r in parsed[1:] if r[0] == "R199")
            self.assertEqual(r199[10], "續約, 收錢, 記賬")
            # No temp litter.
            self.assertTrue(all(not n.startswith(".event_")
                                for n in os.listdir(tmp)))


# ---------------------------------------------------------------------------
# Sender failure independence (REQUIREMENTS §7)
# ---------------------------------------------------------------------------

class TestSenderIndependence(unittest.TestCase):
    def _one_point_row(self, tmp):
        csvp = os.path.join(tmp, "event.csv")
        stp = os.path.join(tmp, "state.json")
        write_csv(csvp, [make_row("R170", "2026-06-30")])
        seed_first_seen(stp, [("R170", "2026-06-30")])
        return csvp, stp  # 30-day point = 2026-05-31

    def test_email_still_sends_when_telegram_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            csvp, stp = self._one_point_row(tmp)
            tg, em = FakeSender(ok=False), FakeSender(ok=True)
            self.assertEqual(run_day(date(2026, 5, 31), csvp, stp, tg, em), 1)
            self.assertEqual(len(tg.sent), 1, "telegram was attempted")
            self.assertEqual(len(em.sent), 1, "email still sent")
            # Dedup must survive the channel failure: no re-send next run.
            self.assertEqual(run_day(date(2026, 6, 1), csvp, stp, tg, em), 0)
            self.assertEqual(len(em.sent), 1)

    def test_telegram_still_sends_when_email_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            csvp, stp = self._one_point_row(tmp)
            tg, em = FakeSender(ok=True), FakeSender(ok=False)
            self.assertEqual(run_day(date(2026, 5, 31), csvp, stp, tg, em), 1)
            self.assertEqual(len(tg.sent), 1)
            self.assertEqual(len(em.sent), 1)
            st = StateStore(stp)
            self.assertIn(list(("R170", "2026-06-30", "", "30")), st.data["sent"])


# ---------------------------------------------------------------------------
# Email recipients: MAIL_TO may list several recipients, comma-separated
# ---------------------------------------------------------------------------

class TestEmailRecipients(unittest.TestCase):
    def test_comma_separated_recipients(self):
        import event_reminder as er
        em = er.EmailSender(
            host="smtp.example.com", port=587, user="u", password="p",
            sender_email="from@example.com",
            to_email="dennis@example.com, wife@example.com",
        )
        self.assertEqual(em.recipients,
                         ["dennis@example.com", "wife@example.com"])
        self.assertEqual(em.to_email, "dennis@example.com, wife@example.com")

    def test_single_recipient_default_shape(self):
        import event_reminder as er
        em = er.EmailSender(
            host="h", port=1, user="u", password="p",
            sender_email="f@x.com", to_email="one@x.com",
        )
        self.assertEqual(em.recipients, ["one@x.com"])
        self.assertEqual(em.to_email, "one@x.com")

    def test_blank_entries_dropped(self):
        import event_reminder as er
        em = er.EmailSender(
            host="h", port=1, user="u", password="p",
            sender_email="f@x.com", to_email=" a@x.com , , b@x.com ",
        )
        self.assertEqual(em.recipients, ["a@x.com", "b@x.com"])

    def test_use_ssl_flag_maps_to_connection_mode(self):
        import event_reminder as er
        em587 = er.EmailSender(
            host="smtp.gmail.com", port=587, user="u", password="p",
            sender_email="f@x.com", to_email="to@x.com", use_ssl=False,
        )
        self.assertFalse(em587.use_ssl)
        em465 = er.EmailSender(
            host="smtp.gmail.com", port=465, user="u", password="p",
            sender_email="f@x.com", to_email="to@x.com", use_ssl=True,
        )
        self.assertTrue(em465.use_ssl)
        # Default is STARTTLS mode (Gmail 587 setup)
        em_default = er.EmailSender(
            host="h", port=587, user="u", password="p",
            sender_email="f@x.com", to_email="to@x.com",
        )
        self.assertFalse(em_default.use_ssl)

    def test_chinese_subject_and_body_wire_ascii_safe(self):
        # Regression: the real bot's messages are Chinese; the old raw-
        # string message made smtplib raise "ascii codec can't encode".
        # EmailMessage must encode them so the on-wire bytes are ASCII.
        import event_reminder as er
        captured = {}

        class FakeServer:
            def __init__(self, host, port, timeout=None):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def ehlo(self):
                pass

            def starttls(self, context=None):
                pass

            def login(self, user, password):
                pass

            def sendmail(self, from_addr, to_addrs, msg):
                captured["msg"] = msg
                captured["to"] = to_addrs

        import smtplib
        orig = smtplib.SMTP
        smtplib.SMTP = FakeServer
        try:
            em = er.EmailSender(
                host="smtp.gmail.com", port=587, user="u", password="p",
                sender_email="f@x.com", to_email="a@x.com, b@x.com",
            )
            ok = em.send("事件提醒 — 2026-09-24",
                         "ID: R001 | 本人 | 事件: OVO Energy | 到期日: 2026-12-19 | "
                         "將30日後到期")
        finally:
            smtplib.SMTP = orig
        self.assertTrue(ok)
        wire = captured["msg"]
        self.assertIsInstance(wire, (bytes, bytearray))  # bytes on the wire
        # Subject is RFC 2047 encoded; the (possibly transfer-encoded) body
        # must round-trip back to the exact Chinese text — base64 or plain.
        self.assertIn(b"=?utf-8?", wire)
        self.assertIn(b"a@x.com, b@x.com", wire)
        from email import message_from_bytes
        payload = message_from_bytes(wire).get_payload(decode=True)
        self.assertIn("ID: R001 | 本人 | 事件: OVO Energy",
                      payload.decode("utf-8"))


# ---------------------------------------------------------------------------
# Telegram chats: TELEGRAM_CHAT_ID may list several chats, comma-separated
# ---------------------------------------------------------------------------

class TestTelegramChats(unittest.TestCase):
    def test_single_chat(self):
        import event_reminder as er
        tg = er.TelegramSender("tok", "123456789")
        self.assertEqual(tg.chat_ids, ["123456789"])

    def test_comma_separated_chats(self):
        import event_reminder as er
        tg = er.TelegramSender("tok", "123456, 654231")
        self.assertEqual(tg.chat_ids, ["123456", "654231"])

    def test_blank_entries_dropped(self):
        import event_reminder as er
        tg = er.TelegramSender("tok", " 111 , , 222 ")
        self.assertEqual(tg.chat_ids, ["111", "222"])


# ---------------------------------------------------------------------------
# DST: 11:00 Europe/London across spring-forward + downtime catch-up
# ---------------------------------------------------------------------------

class TestDst(unittest.TestCase):
    def test_1100_resolved_correctly_across_spring_forward(self):
        # UK spring-forward 2026-03-29 (01:00 -> 02:00).
        # Day before is still GMT; the 11:00 run that day falls in BST.
        self.assertEqual(datetime(2026, 3, 28, 11, 0, tzinfo=TZ).utcoffset(),
                         timedelta(hours=0))
        self.assertEqual(datetime(2026, 3, 29, 11, 0, tzinfo=TZ).utcoffset(),
                         timedelta(hours=1))
        self.assertEqual(datetime(2026, 3, 8, 11, 0, tzinfo=TZ).utcoffset(),
                         timedelta(hours=0))
        self.assertEqual(datetime(2026, 10, 24, 11, 0, tzinfo=TZ).utcoffset(),
                         timedelta(hours=1))   # before autumn-back day
        self.assertEqual(datetime(2026, 10, 25, 11, 0, tzinfo=TZ).utcoffset(),
                         timedelta(hours=0))   # autumn-back day is GMT

    def test_run_missed_during_downtime_is_caught_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            csvp = os.path.join(tmp, "event.csv")
            stp = os.path.join(tmp, "state.json")
            # 21-day point lands on the DST day 2026-03-29.
            write_csv(csvp, [make_row("R160", "2026-04-19")])
            seed_first_seen(stp, [("R160", "2026-04-19")])
            tg = FakeSender()
            self.assertEqual(run_day(date(2026, 3, 20), csvp, stp, tg), 1)  # 30d
            tg.sent.pop()
            # 2026-03-21..03-29 the container did not run (incl. the DST day).
            self.assertEqual(run_day(date(2026, 3, 30), csvp, stp, tg), 1)
            text = tg.sent.pop()[0]
            self.assertIn("補發", text)
            self.assertIn("20日後到期", text)   # 2026-04-19 - 2026-03-30
            self.assertEqual(tg.sent, [])


class TestBotHelpers(unittest.TestCase):
    """bot.py helpers: on-demand trigger files + --once flag."""

    def test_consume_trigger_missing_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "run-now")
            self.assertFalse(botmod.consume_trigger(p))

    def test_consume_trigger_asks_once_then_consumes(self):
        import pathlib
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "run-now")
            pathlib.Path(p).touch()
            self.assertTrue(botmod.consume_trigger(p))
            self.assertFalse(os.path.exists(p))          # consumed
            self.assertFalse(botmod.consume_trigger(p))  # second ask: none

    def test_once_flag_default_false(self):
        argv_backup = sys.argv[:]
        try:
            sys.argv = ["bot.py"]
            self.assertFalse(botmod.once_flag())
            sys.argv = ["bot.py", "--once"]
            self.assertTrue(botmod.once_flag())
        finally:
            sys.argv = argv_backup
