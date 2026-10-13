"""Offline tests for the Workday paging fix, light scan and change-marker logic (no network)."""
import unittest
from unittest import mock

from collectors import workday
from scheduler import run_collectors as rc


def page(total, start, n, title="Role"):
    """A fake Workday search page. Like the real API, `total` is only reported on the first page."""
    jobs = [{"title": f"{title} {start + i}", "externalPath": f"/job/{start + i}", "locationsText": "X", "postedOn": "Posted Today"} for i in range(n)]
    return {"total": total if start == 0 else 0, "jobPostings": jobs}


class FakeWorkday:
    """Serves `n` results for any search, 20 per page, total on page one only."""
    def __init__(self, n):
        self.n, self.calls = n, []

    def __call__(self, method, url, json=None, **kw):
        off = json["offset"]
        self.calls.append((json["searchText"], off, json["limit"]))
        count = min(max(self.n - off, 0), json["limit"])
        m = mock.Mock(); m.json.return_value = page(self.n, off, count); return m


class Paging(unittest.TestCase):
    def test_fetch_light_pages_through_everything_despite_total_zero_on_later_pages(self):
        fake = FakeWorkday(95)
        with mock.patch.object(workday, "request_with_retries", fake):
            postings = workday.fetch_light("acme.wd1/Careers")
        self.assertEqual(len(postings), 95)  # before the fix this stopped at 40
        design_calls = [c for c in fake.calls if c[0] == "design"]
        self.assertEqual([c[1] for c in design_calls], [0, 20, 40, 60, 80])

    def test_fetch_stops_at_the_reported_total(self):
        fake = FakeWorkday(20)
        with mock.patch.object(workday, "request_with_retries", fake):
            workday.fetch_light("acme.wd1/Careers")
        self.assertEqual(len([c for c in fake.calls if c[0] == "design"]), 1)

    def test_probe_marker_is_one_tiny_request(self):
        fake = FakeWorkday(451)
        with mock.patch.object(workday, "request_with_retries", fake):
            self.assertEqual(workday.probe_marker("acme.wd1/Careers"), "design:451")
        self.assertEqual(fake.calls, [("design", 0, 1)])


def tenant(**kw):
    base = {"id": 1, "name": "Acme", "ats_type": "workday", "ats_identifier": "acme.wd1/Careers",
            "full_ingest_at": "2026-10-01", "tier": "hot", "scan_marker": "design:451"}
    base.update(kw)
    return base


class ChangeMarker(unittest.TestCase):
    def setUp(self):
        self.full_scans = 0
        def fake_light(ident):
            self.full_scans += 1
            return [{"ats_posting_id": "/job/1", "title": "Senior Product Designer", "url": "http://x/1", "location": "Remote - USA", "remote_type": None}]
        self.patch = [mock.patch.object(rc.workday, "fetch_light", fake_light)]
        for p in self.patch:
            p.start()
        # slot that never matches the tenant's id, so only the marker decides
        self.slot = mock.patch.object(rc, "_tail_slot", lambda now=None: 7)
        self.slot.start()

    def tearDown(self):
        for p in self.patch:
            p.stop()
        self.slot.stop()

    def test_unchanged_marker_skips_the_full_scan(self):
        with mock.patch.object(rc.workday, "probe_marker", lambda ident: "design:451"):
            res, err = rc._fetch_tail(tenant(id=1))
        self.assertIsNone(err)
        self.assertTrue(res["unchanged"])
        self.assertEqual(self.full_scans, 0)

    def test_changed_marker_triggers_a_scan_and_reports_the_new_marker(self):
        with mock.patch.object(rc.workday, "probe_marker", lambda ident: "design:452"):
            res, err = rc._fetch_tail(tenant(id=1))
        self.assertFalse(res.get("unchanged"))
        self.assertEqual(self.full_scans, 1)
        self.assertEqual(res["marker"], "design:452")
        self.assertEqual([p["title"] for p in res["design"]], ["Senior Product Designer"])

    def test_first_scan_always_scans_even_if_marker_matches(self):
        with mock.patch.object(rc.workday, "probe_marker", lambda ident: "design:451"):
            res, _ = rc._fetch_tail(tenant(id=1, full_ingest_at=None))
        self.assertEqual(self.full_scans, 1)

    def test_forced_rotation_scans_even_when_unchanged(self):
        with mock.patch.object(rc.workday, "probe_marker", lambda ident: "design:451"):
            res, _ = rc._fetch_tail(tenant(id=7))  # 7 % 16 == the patched slot
        self.assertEqual(self.full_scans, 1)

    def test_probe_failure_is_returned_not_raised(self):
        def boom(ident):
            raise RuntimeError("tenant down")
        with mock.patch.object(rc.workday, "probe_marker", boom):
            res, err = rc._fetch_tail(tenant(id=1))
        self.assertIsNone(res)
        self.assertIsInstance(err, RuntimeError)


if __name__ == "__main__":
    unittest.main()
