"""SC-55: a tap on a push notification is counted. The alert links to Scout, which logs the open and forwards."""
import unittest
from datetime import datetime, timezone
from unittest import mock
from zoneinfo import ZoneInfo

import psycopg2

from dashboard import app as dapp
from db import db as dbmod
from scheduler import notify, run_collectors as rc

CHICAGO = ZoneInfo("America/Chicago")


def central(hour, minute=0):
    return datetime(2026, 10, 26, hour, minute, tzinfo=CHICAGO).astimezone(timezone.utc)


class Link(unittest.TestCase):
    def sent(self, postings):
        captured = []
        with mock.patch.object(notify, "_post", lambda payload, label: captured.append(payload) or True):
            notify.notify_new_postings(postings, "topic-x")
        return captured[0]

    def test_a_single_alert_taps_through_scout_when_it_has_a_link(self):
        p = self.sent([{"company": "Co", "title": "Product Designer", "url": "https://jobs.example/1",
                        "open_url": "https://scout.example/a/tok"}])
        self.assertEqual(p["click"], "https://scout.example/a/tok")

    def test_without_one_it_goes_straight_to_the_job_as_before(self):
        p = self.sent([{"company": "Co", "title": "Product Designer", "url": "https://jobs.example/1"}])
        self.assertEqual(p["click"], "https://jobs.example/1")

    def test_a_grouped_alert_still_has_no_single_link(self):
        p = self.sent([{"company": "A", "title": "T1", "url": "u1", "open_url": "o1"},
                       {"company": "B", "title": "T2", "url": "u2", "open_url": "o2"}])
        self.assertNotIn("click", p)

    def test_the_link_is_scouts_address_plus_the_token(self):
        self.assertEqual(notify.alert_link("abc"), f"{notify.DASHBOARD_URL}/a/abc")
        self.assertTrue(notify.DASHBOARD_URL.startswith("https://"))

    def test_only_while_scouts_site_is_awake(self):
        self.assertFalse(notify.tracking_allowed(central(0, 30)))
        self.assertFalse(notify.tracking_allowed(central(5, 59)))
        self.assertTrue(notify.tracking_allowed(central(6, 0)))
        self.assertTrue(notify.tracking_allowed(central(12)))
        self.assertTrue(notify.tracking_allowed(central(23, 59)))


class Route(unittest.TestCase):
    def setUp(self):
        self.client = dapp.app.test_client()          # no sign-in cookie on purpose
        self.events = []
        self.target = (7, 55, "https://jobs.example/role/55")
        patches = [
            mock.patch.object(dapp, "get_conn", lambda: mock.MagicMock()),
            mock.patch.object(dapp, "get_alert_target", lambda c, token: self.target if token == "good" else None),
            mock.patch.object(dapp, "log_event", lambda c, uid, kind, pid=None: self.events.append((uid, kind, pid))),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_a_valid_link_forwards_to_the_job_and_counts_the_open_with_no_sign_in(self):
        r = self.client.get("/a/good")
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "https://jobs.example/role/55"))
        self.assertEqual(self.events, [(7, "opened_alert", 55)])

    def test_a_link_preview_is_forwarded_but_not_counted(self):
        r = self.client.head("/a/good")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.events, [])

    def test_an_unknown_link_goes_to_the_dashboard_and_counts_nothing(self):
        r = self.client.get("/a/nope")
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("jobs.example", r.headers["Location"])
        self.assertEqual(self.events, [])

    def test_a_job_with_no_address_goes_to_the_dashboard(self):
        self.target = (7, 55, None)
        r = self.client.get("/a/good")
        self.assertNotIn("jobs.example", r.headers["Location"])


class Cursor:
    def __init__(self, conn): self.conn = conn
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None):
        self.conn.sql.append((sql, params))
        if "alert_token" in sql and not sql.startswith(("SAVEPOINT", "ROLLBACK", "RELEASE")) and self.conn.broken:
            raise psycopg2.errors.UndefinedColumn('column "alert_token" does not exist')
    def fetchone(self): return self.conn.row


class Conn:
    def __init__(self, broken=False, row=("kept",)): self.broken, self.row, self.sql = broken, row, []
    def cursor(self): return Cursor(self)


class Helpers(unittest.TestCase):
    def test_the_token_is_kept_once_made_and_comes_back_from_the_database(self):
        conn = Conn(row=("the-stored-one",))
        self.assertEqual(dbmod.ensure_alert_token(conn, 7, 55), "the-stored-one")
        sql, params = [q for q in conn.sql if q[0].startswith("UPDATE")][0]
        self.assertIn("COALESCE(alert_token", sql)          # an existing token is never replaced
        self.assertEqual(params[1:], (7, 55))
        self.assertGreaterEqual(len(params[0]), 20)         # a fresh random one is offered

    def test_two_made_tokens_differ(self):
        a, b = Conn(), Conn()
        dbmod.ensure_alert_token(a, 7, 55); dbmod.ensure_alert_token(b, 7, 55)
        self.assertNotEqual([q for q in a.sql if q[0].startswith("UPDATE")][0][1][0],
                            [q for q in b.sql if q[0].startswith("UPDATE")][0][1][0])

    def test_a_missing_column_gives_no_token_and_never_raises(self):
        conn = Conn(broken=True)
        self.assertIsNone(dbmod.ensure_alert_token(conn, 7, 55))
        self.assertTrue(any(q[0].startswith("ROLLBACK TO SAVEPOINT") for q in conn.sql))

    def test_no_row_means_no_token(self):
        self.assertIsNone(dbmod.ensure_alert_token(Conn(row=None), 7, 55))

    def test_an_empty_token_looks_nothing_up(self):
        conn = Conn()
        self.assertIsNone(dbmod.get_alert_target(conn, ""))
        self.assertIsNone(dbmod.get_alert_target(conn, None))
        self.assertEqual(conn.sql, [])

    def test_a_known_token_gives_person_job_and_address(self):
        self.assertEqual(dbmod.get_alert_target(Conn(row=[7, 55, "https://x"]), "good"), (7, 55, "https://x"))


class WholeRun(unittest.TestCase):
    """The collector hands the alert its private link, only while the site is awake and only when one could be made."""

    def alerts(self, token, awake):
        from tests.test_run_isolation import RunIsolation
        case = RunIsolation()
        case.alert_token = token
        with mock.patch.object(rc, "tracking_allowed", lambda now=None: awake):
            sent, _, _ = case.run_it(tail_raises=False)
        return sent[0][1]

    def test_with_a_token_and_the_site_awake_the_alert_carries_scouts_link(self):
        (alert,) = self.alerts("tok123", True)
        self.assertEqual(alert["open_url"], notify.alert_link("tok123"))
        self.assertEqual(alert["url"], "http://x/1")                  # and still knows the job's own address

    def test_overnight_the_alert_keeps_the_jobs_own_link(self):
        (alert,) = self.alerts("tok123", False)
        self.assertNotIn("open_url", alert)

    def test_if_no_token_could_be_made_the_alert_is_still_sent_with_the_jobs_own_link(self):
        (alert,) = self.alerts(None, True)
        self.assertNotIn("open_url", alert)

    def test_overnight_no_token_is_even_requested_and_by_day_exactly_one_is(self):
        from tests.test_run_isolation import RunIsolation
        for awake, expected in ((False, 0), (True, 1)):
            case = RunIsolation()
            case.alert_token = "tok"
            with mock.patch.object(rc, "tracking_allowed", lambda now=None, awake=awake: awake):
                case.run_it(tail_raises=False)
            self.assertEqual(len(case.token_calls), expected, f"awake={awake}")
            if expected:
                self.assertEqual(case.token_calls[0], (7, 55))        # this person, this job


class ActivitySummary(unittest.TestCase):
    def test_the_alert_column_counts_alert_opens_under_the_right_heading(self):
        import importlib.util, os, re
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "engagement.py")
        source = open(path).read()
        headings = re.search(r"EVENTS = \[(.*?)\]", source).group(1).replace('"', "").replace(" ", "").split(",")
        counted = re.findall(r"event_type = '(\w+)'", source)
        self.assertEqual(headings, ["sign_in", "visit", "opened", "from_alert", "applied"])
        self.assertEqual(counted, ["sign_in", "visit", "opened", "opened_alert", "applied"])   # same order, so same columns
        self.assertEqual(len(headings), len(counted))


if __name__ == "__main__":
    unittest.main()
