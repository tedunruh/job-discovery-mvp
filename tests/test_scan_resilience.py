"""The tail scan must survive the database dropping an idle connection, and save as it goes."""
import unittest
from unittest import mock

import psycopg2

from db import db as dbmod
from scheduler import run_collectors as rc


class FakeCursor:
    def __init__(self, conn): self.conn = conn
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None):
        if self.conn.dead:
            raise psycopg2.OperationalError("SSL connection has been closed unexpectedly")


class FakeConn:
    def __init__(self, name, dead=False):
        self.name, self.dead, self.commits, self.closed_called = name, dead, 0, False
    def cursor(self): return FakeCursor(self)
    def commit(self):
        if self.dead:
            raise psycopg2.OperationalError("SSL connection has been closed unexpectedly")
        self.commits += 1
    def close(self): self.closed_called = True


class EnsureConn(unittest.TestCase):
    def test_returns_the_same_connection_when_alive(self):
        c = FakeConn("a")
        self.assertIs(dbmod.ensure_conn(c), c)

    def test_replaces_a_dropped_connection(self):
        dead, fresh = FakeConn("dead", dead=True), FakeConn("fresh")
        with mock.patch.object(dbmod, "get_conn", return_value=fresh):
            out = dbmod.ensure_conn(dead)
        self.assertIs(out, fresh)
        self.assertTrue(dead.closed_called)


def board(i):
    return {"id": i, "name": f"b{i}", "ats_type": "greenhouse", "ats_identifier": f"b{i}",
            "full_ingest_at": "2026-10-01", "tier": "hot", "scan_marker": None, "last_checked_at": None}


class ScanTailBatches(unittest.TestCase):
    def setUp(self):
        self.old_chunk = rc.TAIL_CHUNK
        rc.TAIL_CHUNK = 3
        self.saved = []      # (ok_ids, markers) per flush
        self.upserts = []    # (connection name, company id)
        self.first = FakeConn("first")
        self.second = FakeConn("second")

        def fake_fetch(company):
            return {"design": [{"ats_posting_id": f"p{company['id']}", "title": "Product Designer", "url": "http://x",
                                "is_design": True}], "alive": True, "marker": f"m{company['id']}"}, None

        def fake_upsert(conn, cid, design, backfill=False):
            self.upserts.append((conn.name, cid)); return {}

        patches = [
            mock.patch.object(rc, "_fetch_tail", fake_fetch),
            mock.patch.object(rc, "get_open_posting_hashes", lambda c, ids: {}),
            mock.patch.object(rc, "upsert_postings", fake_upsert),
            mock.patch.object(rc, "mark_stale_postings_closed", lambda *a, **k: None),
            mock.patch.object(rc, "record_tail_scan_results", lambda conn, ok, failed, first, empty=(): self.saved.append(("ok", list(ok)))),
            mock.patch.object(rc, "set_scan_markers", lambda conn, m: self.saved.append(("markers", dict(m)))),
            mock.patch.object(rc, "promote_hot_boards", lambda conn: 0),
        ]
        for p in patches:
            p.start()
        self.patches = patches

    def tearDown(self):
        rc.TAIL_CHUNK = self.old_chunk
        for p in self.patches:
            p.stop()

    def test_progress_is_saved_after_every_batch(self):
        rc._scan_tail(self.first, [board(i) for i in range(1, 8)], [], lambda *a: None)
        oks = [v for k, v in self.saved if k == "ok"]
        self.assertEqual(oks, [[1, 2, 3], [4, 5, 6], [7]])   # 7 boards, batches of 3, saved each time
        self.assertEqual([m for k, m in self.saved if k == "markers"][0], {1: "m1", 2: "m2", 3: "m3"})

    def test_a_dropped_connection_is_replaced_and_later_writes_use_the_new_one(self):
        # the first connection "drops" while the first batch is being fetched
        original_fetch = rc._fetch_tail

        def fetch_and_drop(company):
            if company["id"] == 1:
                self.first.dead = True
            return original_fetch(company)

        rc._fetch_tail = fetch_and_drop
        try:
            with mock.patch.object(dbmod, "get_conn", return_value=self.second), \
                 mock.patch.object(rc, "ensure_conn", dbmod.ensure_conn):
                # commit() before the batch is on the live connection; the drop happens during fetch
                out = rc._scan_tail(self.first, [board(i) for i in range(1, 4)], [], lambda *a: None)
        finally:
            rc._fetch_tail = original_fetch
        self.assertIs(out, self.second)
        self.assertTrue(all(name == "second" for name, _ in self.upserts))
        self.assertEqual(len(self.upserts), 3)


if __name__ == "__main__":
    unittest.main()
