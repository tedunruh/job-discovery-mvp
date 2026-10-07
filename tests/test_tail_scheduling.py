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
