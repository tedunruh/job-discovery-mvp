"""Offline tests for the per-run rate-limit breaker (SC-61): an ATS answering 429s is left alone
for the rest of the run instead of failing hundreds of boards."""
import unittest
from unittest import mock

import requests

from scheduler import run_collectors as rc


def board(i, ats="workable", tier="hot"):
    return {"id": i, "name": f"b{i}", "ats_type": ats, "ats_identifier": f"b{i}", "full_ingest_at": 1, "tier": tier}


def http_error(status):
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(f"{status} from x", response=resp)


class Breaker(unittest.TestCase):
    def setUp(self):
        rc._rate_limited.clear()
        self.old = rc.TAIL_COLLECTORS["workable"]
        self.calls = []

        def always_429(ident):
            self.calls.append(ident)
            raise http_error(429)
        rc.TAIL_COLLECTORS["workable"] = always_429

    def tearDown(self):
        rc.TAIL_COLLECTORS["workable"] = self.old
        rc._rate_limited.clear()

    def test_stops_requesting_after_the_trip_count(self):
        results = [rc._fetch_tail(board(i)) for i in range(1, 21)]
        self.assertEqual(len(self.calls), rc.RATE_LIMIT_TRIP)
        blocked = [r for r in results if isinstance(r[1], rc.AtsBlocked)]
        self.assertEqual(len(blocked), 20 - rc.RATE_LIMIT_TRIP)

    def test_other_error_statuses_do_not_trip_it(self):
        def not_found(ident):
            raise http_error(404)
        rc.TAIL_COLLECTORS["workable"] = not_found
        for i in range(1, 21):
            rc._fetch_tail(board(i))
        self.assertEqual(rc._rate_limited, {})

    def test_one_ats_blocked_does_not_stop_another(self):
        for i in range(1, 11):
            rc._fetch_tail(board(i))
        old = rc.TAIL_COLLECTORS["greenhouse"]
        rc.TAIL_COLLECTORS["greenhouse"] = lambda ident: []
        try:
            res, err = rc._fetch_tail(board(99, ats="greenhouse"))
        finally:
            rc.TAIL_COLLECTORS["greenhouse"] = old
        self.assertIsNone(err)


class ScanWithBlockedAts(unittest.TestCase):
    def test_blocked_boards_are_neither_failed_nor_checked_and_the_alert_names_the_ats(self):
        saved = []
        alerts = []

        class Conn:
            def commit(self): pass

        def fake_fetch(company):
            if company["id"] <= 2:
                return None, http_error(429)
            return None, rc.AtsBlocked("workable")

        with mock.patch.object(rc, "_fetch_tail", fake_fetch), \
             mock.patch.object(rc, "get_open_posting_hashes", lambda c, ids: {}), \
             mock.patch.object(rc, "ensure_conn", lambda c: c), \
             mock.patch.object(rc, "record_tail_scan_results",
                               lambda conn, ok, failed, first, empty=(): saved.append(list(failed))), \
             mock.patch.object(rc, "set_scan_markers", lambda conn, m: None), \
             mock.patch.object(rc, "promote_hot_boards", lambda conn: 0), \
             mock.patch.object(rc, "_tail_slot", lambda now=None: 0):
            rc._scan_tail(Conn(), [board(i) for i in range(1, 51)], alerts, lambda *a: None)

        self.assertEqual([cid for batch in saved for cid in batch], [1, 2])   # only the two that were refused
        self.assertEqual(len(alerts), 1)
        self.assertIn("Workable is rate-limiting", alerts[0])
        self.assertIn("48 board", alerts[0])


class WorkableCadence(unittest.TestCase):
    def test_hot_workable_boards_are_checked_every_other_run_and_never_skipped_twice(self):
        boards = [board(i) for i in range(1, 41)]
        picks = [{c["id"] for c in rc.select_tail(boards, now=s * rc.RUN_INTERVAL_SECONDS)} for s in range(2)]
        self.assertEqual(picks[0] | picks[1], {c["id"] for c in boards})
        self.assertTrue(18 <= len(picks[0]) <= 22)


if __name__ == "__main__":
    unittest.main()
