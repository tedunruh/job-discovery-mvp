"""Offline tests for collectors/rippling.py (no network)."""
import unittest
from unittest import mock

from collectors import rippling
from dashboard.filters import work_type


def job(i, title, locations):
    return {"id": f"id-{i}", "name": title, "url": f"https://ats.rippling.com/acme/jobs/id-{i}", "department": {"name": "X"}, "locations": locations}


def loc(name, country="United States", code="US", wt="ON_SITE"):
    return {"name": name, "country": country, "countryCode": code, "workplaceType": wt}


class Normalize(unittest.TestCase):
    def test_fields(self):
        n = rippling._normalize(job(1, "  Senior Product Designer ", [loc("San Francisco, CA")]))
        self.assertEqual(n["ats_posting_id"], "id-1")
        self.assertEqual(n["title"], "Senior Product Designer")
        self.assertTrue(n["url"].endswith("/id-1"))
        self.assertIsNone(n["ats_posted_at"])  # the API gives no date

    def test_workplace_type_maps_to_remote_hybrid_or_onsite(self):
        self.assertEqual(rippling._normalize(job(1, "x", [loc("Remote", wt="REMOTE")]))["remote_type"], "remote")
        self.assertEqual(rippling._normalize(job(1, "x", [loc("NYC", wt="HYBRID")]))["remote_type"], "hybrid")
        self.assertIsNone(rippling._normalize(job(1, "x", [loc("NYC")]))["remote_type"])
        # any remote location makes the role remote-capable, even if another is on-site
        self.assertEqual(rippling._normalize(job(1, "x", [loc("NYC"), loc("Remote", wt="REMOTE")]))["remote_type"], "remote")

    def test_dashboard_work_types(self):
        f = lambda locs: (lambda n: work_type(n["location"], n["remote_type"]))(rippling._normalize(job(1, "x", locs)))
        self.assertEqual(f([loc("Remote", wt="REMOTE")]), "remote")                              # remote + US
        self.assertIsNone(f([loc("Remote", "Canada", "CA", "REMOTE")]))                          # remote, not US
        self.assertEqual(f([loc("Austin, TX", wt="HYBRID")]), "hybrid")
        self.assertEqual(f([loc("Chicago, IL")]), "onsite")

    def test_location_text_adds_country_without_duplicating_it(self):
        self.assertEqual(rippling._location_text([loc("Toronto", "Canada", "CA")]), "Toronto, Canada")
        self.assertEqual(rippling._location_text([loc("Austin, United States")]), "Austin, United States")
        self.assertEqual(rippling._location_text([loc("A, CA"), loc("B, NY")]), "A, CA, United States; B, NY, United States")
        self.assertIsNone(rippling._location_text([]))


class Fetch(unittest.TestCase):
    def test_pages_until_done_and_stops_on_empty(self):
        pages = {0: {"items": [job(1, "a", []), job(2, "b", [])], "totalPages": 3},
                 1: {"items": [job(3, "c", [])], "totalPages": 3},
                 2: {"items": [], "totalPages": 3}}
        calls = []

        def fake(method, url, params=None, **kw):
            calls.append(params["page"])
            m = mock.Mock(); m.json.return_value = pages[params["page"]]; return m

        with mock.patch.object(rippling, "request_with_retries", fake):
            out = rippling.fetch("acme")
        self.assertEqual([j["title"] for j in out], ["a", "b", "c"])
        self.assertEqual(calls, [0, 1, 2])

    def test_single_page_board(self):
        def fake(method, url, params=None, **kw):
            m = mock.Mock(); m.json.return_value = {"items": [job(1, "a", [])], "totalPages": 1}; return m
        with mock.patch.object(rippling, "request_with_retries", fake):
            self.assertEqual(len(rippling.fetch("acme")), 1)


if __name__ == "__main__":
    unittest.main()
