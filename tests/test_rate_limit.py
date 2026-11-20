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
    def test_blocked_boards_are_neither_failed_nor_checked_and_the_scan_itself_sends_no_alert(self):
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
        self.assertEqual(alerts, [])   # the scan stays quiet: settle_cooloffs decides when a block is worth a buzz


class CoreCompaniesAreGuardedToo(unittest.TestCase):
    def setUp(self):
        rc._rate_limited.clear()
        rc._succeeded.clear()
        self.old = rc.COLLECTORS["workday"]
        self.calls = []

        def always_429(ident):
            self.calls.append(ident)
            raise http_error(429)
        rc.COLLECTORS["workday"] = always_429

    def tearDown(self):
        rc.COLLECTORS["workday"] = self.old
        rc._rate_limited.clear()
        rc._succeeded.clear()

    def test_core_fetches_stop_after_the_trip_count_and_count_toward_the_pause(self):
        results = [rc._fetch_company(board(i, "workday", "core")) for i in range(1, 11)]
        self.assertEqual(len(self.calls), rc.RATE_LIMIT_TRIP)
        self.assertEqual(sum(isinstance(r[1], rc.AtsBlocked) for r in results), 10 - rc.RATE_LIMIT_TRIP)
        self.assertEqual(rc._rate_limited["workday"], rc.RATE_LIMIT_TRIP)

    def test_a_429_is_a_rate_limit_but_a_403_or_404_is_not(self):
        self.assertTrue(rc._is_rate_limit(http_error(429)))
        self.assertFalse(rc._is_rate_limit(http_error(403)))
        self.assertFalse(rc._is_rate_limit(http_error(404)))
        self.assertFalse(rc._is_rate_limit(ValueError("x")))


class CoolOff(unittest.TestCase):
    """settle_cooloffs: pause an ATS that tripped (longer each time in a row), clear one that answered, and say so on the
    phone only when it is worth it: the second refusal in a row, then once a day, and once when it answers again."""
    def setUp(self):
        rc._rate_limited.clear()
        rc._succeeded.clear()
        self.saved, self.cleared, self.alerts = [], [], []
        self.patches = [
            mock.patch.object(rc, "save_ats_cooloff",
                              lambda conn, ats, level, minutes, alerted=False: self.saved.append((ats, level, minutes, alerted))),
            mock.patch.object(rc, "clear_ats_cooloff", lambda conn, ats: self.cleared.append(ats)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        rc._rate_limited.clear()
        rc._succeeded.clear()

    def settle(self, before):
        return rc.settle_cooloffs(None, before, self.alerts)

    @staticmethod
    def state(level, active=False, alerted_hours=None):
        return {"active": active, "level": level, "minutes_left": 0, "alerted_hours": alerted_hours}

    def test_the_first_trip_pauses_it_an_hour_and_stays_quiet(self):
        rc._rate_limited["workday"] = rc.RATE_LIMIT_TRIP
        self.assertEqual(self.settle({}), {"workday": 60})
        self.assertEqual(self.saved, [("workday", 1, 60, False)])
        self.assertEqual(self.alerts, [])

    def test_each_trip_in_a_row_pauses_it_longer_up_to_six_hours(self):
        rc._rate_limited["workday"] = rc.RATE_LIMIT_TRIP
        got = []
        for level in (1, 2, 3, 4, 4):
            self.settle({"workday": self.state(level)})
            got.append(self.saved[-1][2])
        self.assertEqual(got, [120, 240, 360, 360, 360])

    def test_the_second_refusal_in_a_row_is_announced_once_then_only_a_day_later(self):
        rc._rate_limited["workday"] = rc.RATE_LIMIT_TRIP
        self.settle({"workday": self.state(1)})
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("Workday", self.alerts[0])
        self.assertTrue(self.saved[-1][3])                                   # recorded as announced
        for hours, level in ((1, 2), (6, 3), (23.5, 4)):                     # later refusals, same day: quiet
            self.settle({"workday": self.state(level, alerted_hours=hours)})
        self.assertEqual(len(self.alerts), 1)
        self.assertFalse(self.saved[-1][3])
        self.settle({"workday": self.state(4, alerted_hours=24.5)})          # a day on: one more
        self.assertEqual(len(self.alerts), 2)

    def test_a_few_refusals_below_the_trip_do_not_pause_it(self):
        rc._rate_limited["workday"] = rc.RATE_LIMIT_TRIP - 1
        self.assertEqual(self.settle({}), {})
        self.assertEqual(self.saved, [])
        self.assertEqual(self.alerts, [])

    def test_a_success_after_a_pause_clears_it_but_no_attempt_leaves_it(self):
        before = {"workday": self.state(2)}
        self.settle(before)
        self.assertEqual(self.cleared, [])                 # nothing was asked of it this run
        rc._succeeded["workday"] = 3
        self.settle(before)
        self.assertEqual(self.cleared, ["workday"])

    def test_answering_again_is_announced_only_if_the_block_was(self):
        rc._succeeded["workday"] = 3
        self.settle({"workday": self.state(1)})                              # a blip nobody was told about
        self.assertEqual(self.alerts, [])
        self.settle({"workday": self.state(3, alerted_hours=5)})             # a block we announced
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("answering again", self.alerts[0])

    def test_a_pause_still_running_is_never_cleared(self):
        rc._succeeded["workday"] = 3
        self.settle({"workday": self.state(1, active=True)})
        self.assertEqual(self.cleared, [])


class WorkableCadence(unittest.TestCase):
    def test_hot_workable_boards_are_checked_every_other_run_and_never_skipped_twice(self):
        boards = [board(i) for i in range(1, 41)]
        picks = [{c["id"] for c in rc.select_tail(boards, now=s * rc.RUN_INTERVAL_SECONDS)} for s in range(2)]
        self.assertEqual(picks[0] | picks[1], {c["id"] for c in boards})
        self.assertTrue(18 <= len(picks[0]) <= 22)


if __name__ == "__main__":
    unittest.main()
