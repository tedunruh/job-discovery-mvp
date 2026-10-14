"""A failure in the discovered-board scan must not lose or delay alerts for core companies,
and must not fail the run."""
import unittest
from unittest import mock

from scheduler import run_collectors as rc


class Conn:
    def commit(self): pass
    def rollback(self): pass
    def close(self): pass
    def cursor(self):
        c = mock.MagicMock(); c.__enter__.return_value = c; return c


CORE = {"id": 1, "name": "CoreCo", "ats_type": "greenhouse", "ats_identifier": "coreco",
        "full_ingest_at": "2026-10-01", "tier": "core", "scan_marker": None, "last_checked_at": None}
TAIL = {"id": 2, "name": "TailCo", "ats_type": "workday", "ats_identifier": "t.wd1/x",
        "full_ingest_at": None, "tier": "hot", "scan_marker": None, "last_checked_at": None}
USER = {"id": 7, "ntfy_topic": "topic-7", "work_types": []}


class RunIsolation(unittest.TestCase):
    def run_it(self, tail_raises):
        sent, alerts, events = [], [], []
        posting = {"ats_posting_id": "p1", "title": "Senior Product Designer", "url": "http://x/1",
                   "location": "Remote - United States", "remote_type": "remote", "raw_json": {}, "is_design": True}
        patches = {
            "get_conn": lambda: Conn(),
            "start_scheduler_run": lambda c: (1, None),
            "get_all_user_role_categories": lambda c: {},
            "get_all_user_keywords": lambda c: {},
            "get_all_users": lambda c: [USER],
            "get_companies_to_scan": lambda c: [CORE, TAIL],
            "_fetch_company": lambda company: ([dict(posting)], None),
            "get_source_health": lambda c, i: {"status": "healthy"},
            "record_fetch_success": lambda *a, **k: None,
            "upsert_postings": lambda c, cid, p, backfill=False: {"p1": 55},
            "mark_notified": lambda *a, **k: events.append("marked_sent"),
            "mark_stale_postings_closed": lambda *a, **k: None,
            "finish_scheduler_run": lambda *a, **k: events.append("run_finished"),
            "notify_new_postings": lambda postings, topic, **k: (events.append("alert_delivered"), sent.append((topic, postings)), True)[2],
            "notify_health_alerts": lambda a: alerts.extend(a) or True,
            "ping_success": lambda: events.append("ping_ok"),
            "ping_failure": lambda: events.append("ping_fail"),
        }

        def tail_scan(conn, tail, health_alerts, fan_out):
            events.append("tail_started")
            if tail_raises:
                raise RuntimeError("SSL connection has been closed unexpectedly")
            return conn
        patches["_scan_tail"] = tail_scan
        started = [mock.patch.object(rc, k, v) for k, v in patches.items()]
        for p in started:
            p.start()
        try:
            rc.run()
        finally:
            for p in started:
                p.stop()
        return sent, alerts, events

    def test_core_alert_goes_out_before_the_tail_scan_even_starts(self):
        sent, alerts, events = self.run_it(tail_raises=False)
        self.assertLess(events.index("alert_delivered"), events.index("tail_started"))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "topic-7")

    def test_a_tail_crash_does_not_lose_alerts_or_fail_the_run(self):
        sent, alerts, events = self.run_it(tail_raises=True)
        self.assertEqual(len(sent), 1, "the core alert must still be delivered")
        self.assertIn("run_finished", events)
        self.assertIn("ping_ok", events)
        self.assertNotIn("ping_fail", events)
        self.assertTrue(any("Discovered-board scan failed" in a for a in alerts), alerts)

    def test_alert_is_not_delivered_twice(self):
        sent, _, _ = self.run_it(tail_raises=False)
        self.assertEqual(len(sent), 1)


if __name__ == "__main__":
    unittest.main()
