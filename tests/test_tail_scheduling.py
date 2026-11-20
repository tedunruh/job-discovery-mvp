"""Offline tests for discovered-board scheduling and work-type matching (no network/DB)."""
import unittest

from role_categories import matches_work_type
from scheduler import run_collectors as rc


def board(i, tier):
    return {"id": i, "name": f"b{i}", "ats_type": "greenhouse", "ats_identifier": f"b{i}", "full_ingest_at": None, "tier": tier}


class SelectTail(unittest.TestCase):
    def setUp(self):
        self.companies = [board(i, "hot") for i in range(1, 51)] + [board(i, "cold") for i in range(1000, 1500)]

    def test_hot_boards_scanned_every_run(self):
        for slot in range(rc.COLD_SLICES):
            picked = rc.select_tail(self.companies, now=slot * rc.RUN_INTERVAL_SECONDS)
            self.assertEqual({c["id"] for c in picked if c["tier"] == "hot"}, set(range(1, 51)))

    def test_every_cold_board_scanned_exactly_once_per_rotation(self):
        seen = {}
        for slot in range(rc.COLD_SLICES):
            for c in rc.select_tail(self.companies, now=slot * rc.RUN_INTERVAL_SECONDS):
                if c["tier"] == "cold":
                    seen[c["id"]] = seen.get(c["id"], 0) + 1
        self.assertEqual(set(seen), set(range(1000, 1500)))
        self.assertEqual(set(seen.values()), {1})

    def test_slices_are_balanced(self):
        sizes = [len([c for c in rc.select_tail(self.companies, now=s * rc.RUN_INTERVAL_SECONDS) if c["tier"] == "cold"])
                 for s in range(rc.COLD_SLICES)]
        self.assertLessEqual(max(sizes) - min(sizes), 2)

    def test_off_and_core_never_in_tail(self):
        mixed = self.companies + [board(9001, "off"), board(9002, "core")]
        for slot in range(rc.COLD_SLICES):
            ids = {c["id"] for c in rc.select_tail(mixed, now=slot * rc.RUN_INTERVAL_SECONDS)}
            self.assertNotIn(9001, ids)
            self.assertNotIn(9002, ids)

    def test_rotation_follows_wall_clock_in_15_minute_steps(self):
        a = rc.select_tail(self.companies, now=0)
        b = rc.select_tail(self.companies, now=rc.RUN_INTERVAL_SECONDS - 1)   # same 15-min window
        c = rc.select_tail(self.companies, now=rc.RUN_INTERVAL_SECONDS)       # next window
        self.assertEqual([x["id"] for x in a], [x["id"] for x in b])
        self.assertNotEqual([x["id"] for x in a], [x["id"] for x in c])


class WorkdayCadence(unittest.TestCase):
    def test_hot_workday_tenants_are_checked_hourly_and_each_one_regularly(self):
        wd = [dict(board(i, "hot"), ats_type="workday") for i in range(1, 41)]
        other = [board(i, "hot") for i in range(100, 110)]
        picked_by_slot = [{c["id"] for c in rc.select_tail(wd + other, now=s * rc.RUN_INTERVAL_SECONDS)} for s in range(8)]
        for p in picked_by_slot:
            self.assertEqual({c["id"] for c in other}, p & {c["id"] for c in other})  # everything else: every run
        counts = [len(p & {c["id"] for c in wd}) for p in picked_by_slot]
        self.assertTrue(all(8 <= n <= 12 for n in counts), counts)  # about a quarter each run
        for t in range(1, 41):  # no tenant goes more than four runs (an hour) unchecked
            for s in range(4):
                self.assertTrue(any(t in picked_by_slot[s + k] for k in range(4)))

    def test_core_workday_companies_come_up_hourly_and_everything_else_core_every_run(self):
        core = [dict(board(i, "core"), ats_type="workday") for i in range(1, 9)] + [board(i, "core", ) for i in range(100, 104)]
        picks = [{c["id"] for c in rc.select_core(core, now=s * rc.RUN_INTERVAL_SECONDS)} for s in range(4)]
        for p in picks:
            self.assertEqual({100, 101, 102, 103}, p & {100, 101, 102, 103})
        self.assertEqual(set().union(*[p & set(range(1, 9)) for p in picks]), set(range(1, 9)))   # each one, once an hour
        self.assertTrue(all(len(p & set(range(1, 9))) == 2 for p in picks))


class BigWorkdayTenants(unittest.TestCase):
    def test_huge_tenants_are_checked_every_two_hours_and_small_ones_hourly(self):
        small = [dict(board(i, "hot"), ats_type="workday", scan_marker="design:30") for i in range(1, 41)]
        big = [dict(board(i, "hot"), ats_type="workday", scan_marker="design:1800") for i in range(101, 141)]
        picks = [{c["id"] for c in rc.select_tail(small + big, now=s * rc.RUN_INTERVAL_SECONDS)} for s in range(16)]
        for s_ in range(16):
            self.assertTrue(8 <= len(picks[s_] & {c["id"] for c in small}) <= 12)   # ~a quarter of the small ones each run
            self.assertTrue(3 <= len(picks[s_] & {c["id"] for c in big}) <= 7)      # ~an eighth of the big ones
        for t in range(101, 141):  # every big tenant is still checked at least once in 8 runs
            self.assertTrue(any(t in picks[s_] for s_ in range(8)))

    def test_a_tenant_with_no_marker_yet_counts_as_small(self):
        self.assertEqual(rc._marker_total({"scan_marker": None}), 0)
        self.assertEqual(rc._marker_total({"scan_marker": "design:451"}), 451)
        self.assertEqual(rc._marker_total({"scan_marker": "design:451|product manager:70"}), 451)
        self.assertEqual(rc._marker_total({"scan_marker": "rescan"}), 0)


class WorkTypeMatching(unittest.TestCase):
    def test_default_when_never_chosen_is_remote(self):
        self.assertTrue(matches_work_type("remote", None))
        self.assertFalse(matches_work_type("onsite", None))
        self.assertFalse(matches_work_type("hybrid", None))
        self.assertFalse(matches_work_type(None, None))  # remote outside the US only shows under Any

    def test_any(self):
        for prefs in ([], ["remote", "hybrid", "onsite"]):
            for wt in ("remote", "hybrid", "onsite", None):
                self.assertTrue(matches_work_type(wt, prefs), (wt, prefs))

    def test_specific_choices(self):
        self.assertTrue(matches_work_type("hybrid", ["remote", "hybrid"]))
        self.assertFalse(matches_work_type("onsite", ["remote", "hybrid"]))


class TailFetch(unittest.TestCase):
    def test_keeps_only_storable_design_postings(self):
        postings = [
            {"ats_posting_id": "1", "title": "Senior Product Designer", "url": "http://x/1"},
            {"ats_posting_id": "2", "title": "Account Executive", "url": "http://x/2"},
            {"ats_posting_id": "3", "title": "UX Researcher", "url": None},          # no URL: unusable
            {"ats_posting_id": None, "title": "Brand Designer", "url": "http://x/4"},  # no id: unusable
        ]
        rc.TAIL_COLLECTORS["greenhouse"] = lambda ident: postings
        res, err = rc._fetch_tail(board(1, "hot"))
        self.assertIsNone(err)
        self.assertEqual([p["title"] for p in res["design"]], ["Senior Product Designer"])
        self.assertTrue(res["alive"])

    def test_empty_board_is_not_alive_and_errors_are_returned_not_raised(self):
        rc.TAIL_COLLECTORS["greenhouse"] = lambda ident: []
        res, err = rc._fetch_tail(board(1, "hot"))
        self.assertFalse(res["alive"])

        def boom(ident):
            raise RuntimeError("down")
        rc.TAIL_COLLECTORS["greenhouse"] = boom
        res, err = rc._fetch_tail(board(1, "hot"))
        self.assertIsNone(res)
        self.assertIsInstance(err, RuntimeError)


if __name__ == "__main__":
    unittest.main()


class Fairness(unittest.TestCase):
    def test_least_recently_checked_first_and_never_checked_leads(self):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        boards = [dict(board(1, "hot"), last_checked_at=now - timedelta(minutes=5)),
                  dict(board(2, "hot"), last_checked_at=None),
                  dict(board(3, "hot"), last_checked_at=now - timedelta(hours=3)),
                  dict(board(4, "hot"), last_checked_at=now - timedelta(minutes=20))]
        self.assertEqual([c["id"] for c in rc.order_for_fairness(boards)], [2, 3, 4, 1])

    def test_a_cut_off_board_goes_first_next_time(self):
        # a run scans only the first two; the other two keep their old timestamps and so lead next run
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        boards = [dict(board(i, "hot"), last_checked_at=now - timedelta(hours=i)) for i in range(1, 5)]
        order = [c["id"] for c in rc.order_for_fairness(boards)]
        self.assertEqual(order, [4, 3, 2, 1])
