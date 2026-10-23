"""SC-51 slice 2: which roles are wanted, and that fetching descriptions never hurts ingestion."""
import unittest
from unittest import mock

import psycopg2

from collectors import descriptions as d
from scheduler import describe, run_collectors as rc


def profile(titles, level=None, city=None, one_level_up=False):
    return {"titles": titles, "level": level, "city": city, "one_level_up": one_level_up}


def post(title="Senior Product Designer", location="Remote - United States", remote="remote", design=True, **kw):
    return dict({"ats_posting_id": "p1", "title": title, "location": location, "remote_type": remote,
                 "url": "http://x", "is_design": design}, **kw)


class Gate(unittest.TestCase):
    def gate(self, users, profiles=None, categories=None, keywords=None):
        return describe.build_gate(users, profiles or {}, categories or {}, keywords or {})

    def test_a_role_is_wanted_when_one_persons_title_level_and_work_type_fit(self):
        users = [{"id": 1, "work_types": None}, {"id": 2, "work_types": ["onsite"]}]
        profiles = {1: profile(["product designer"], "senior"), 2: profile(["ux researcher"])}
        g = self.gate(users, profiles)
        self.assertTrue(g(post()))
        self.assertFalse(g(post(title="Staff Product Designer")))        # wrong level for person 1
        self.assertFalse(g(post(title="Accountant", design=False)))      # nobody's title
        self.assertFalse(g(post(location="Chicago, IL", remote="onsite")))  # person 1 is remote-only

    def test_a_city_only_narrows_hybrid_and_onsite_roles(self):
        users = [{"id": 1, "work_types": ["hybrid"]}]
        g = self.gate(users, {1: profile(["product designer"], city="Chicago")})
        self.assertTrue(g(post(location="Chicago, IL", remote="hybrid")))
        self.assertFalse(g(post(location="Austin, TX", remote="hybrid")))

    def test_someone_without_a_profile_is_matched_on_their_old_categories_and_keywords(self):
        users = [{"id": 5, "work_types": None}]
        g = self.gate(users, categories={5: {"product_design"}}, keywords={5: ["accountant"]})
        self.assertTrue(g(post()))
        self.assertTrue(g(post(title="Staff Accountant", design=False)))
        self.assertFalse(g(post(title="Recruiter", design=False)))

    def test_paused_alerts_do_not_remove_a_role_from_the_gate(self):
        users = [{"id": 1, "work_types": None}]
        paused = dict(profile(["product designer"]), alerts_paused_at="2026-10-20")
        self.assertTrue(self.gate(users, {1: paused})(post()))

    def test_nobody_means_nothing_is_wanted(self):
        self.assertFalse(self.gate([])(post()))


class Conn:
    def __init__(self): self.commits, self.sql = 0, []
    def commit(self): self.commits += 1
    def rollback(self): pass
    def cursor(self):
        c = mock.MagicMock(); c.__enter__.return_value = c
        c.execute.side_effect = lambda sql, *a: self.sql.append(sql)
        return c


COMPANY = {"id": 3, "name": "Co", "ats_type": "workable", "ats_identifier": "co"}


class Store(unittest.TestCase):
    def step(self, wanted=lambda p: True, budget=None):
        return describe.DescriptionStep(wanted, budget)

    def run_store(self, step, postings, missing, fetch=None, conn=None):
        conn = conn or Conn()
        stored = []
        with mock.patch.object(describe._db, "get_postings_missing_description", lambda c, cid, ids: missing), \
             mock.patch.object(describe._db, "set_posting_description", lambda c, pid, text: stored.append((pid, text))), \
             mock.patch.object(describe._db, "ensure_conn", lambda c: c), \
             mock.patch.object(d, "fetch_description", fetch or (lambda *a, **k: "fetched text")):
            out = step.store(conn, COMPANY, postings)
        return out, stored, conn

    def test_prepare_tags_wanted_roles_and_keeps_in_list_text(self):
        step = self.step(lambda p: p["title"] == "Keep")
        a, b = post(title="Keep", raw_json={"descriptionPlain": "From the list"}), post(title="Skip")
        step.prepare({"ats_type": "ashby"}, a); step.prepare({"ats_type": "ashby"}, b)
        self.assertTrue(a["wanted"]); self.assertEqual(a["description"], "From the list")
        self.assertFalse(b["wanted"]); self.assertNotIn("description", b)

    def test_only_wanted_roles_missing_a_description_are_fetched_and_stored(self):
        step = self.step()
        have = post(ats_posting_id="have", wanted=True, description="in hand")
        need = post(ats_posting_id="need", wanted=True)
        nope = post(ats_posting_id="nope")  # not wanted
        calls = []
        _, stored, _ = self.run_store(step, [have, need, nope], {"have": 10, "need": 11, "nope": 12},
                                      fetch=lambda ats, ident, p, budget: calls.append(p["ats_posting_id"]) or "text")
        self.assertEqual(sorted(stored), [(10, "in hand"), (11, "text")])
        self.assertEqual(calls, ["need"])  # the one with text in hand costs no request

    def test_requests_happen_with_nothing_pending(self):
        step, order = self.step(), []
        conn = Conn()
        conn.commit = lambda: order.append("commit")
        self.run_store(step, [post(wanted=True)], {"p1": 1}, conn=conn,
                       fetch=lambda *a, **k: order.append("request") or "t")
        self.assertEqual(order, ["commit", "request"])

    def test_a_role_the_platform_has_no_text_for_is_noted_so_it_is_not_asked_again(self):
        _, stored, _ = self.run_store(self.step(), [post(wanted=True)], {"p1": 1}, fetch=lambda *a, **k: None)
        self.assertEqual(stored, [(1, "")])

    def test_budget_or_rate_limit_leaves_the_role_for_the_next_run(self):
        def skipped(*a, **k): raise d.Skipped("spent")
        step = self.step()
        _, stored, _ = self.run_store(step, [post(wanted=True)], {"p1": 1}, fetch=skipped)
        self.assertEqual(stored, []); self.assertEqual(step.waiting, 1)

    def test_a_failed_request_leaves_the_role_for_the_next_run_and_does_not_raise(self):
        def boom(*a, **k): raise RuntimeError("500")
        step = self.step()
        _, stored, _ = self.run_store(step, [post(wanted=True)], {"p1": 1}, fetch=boom)
        self.assertEqual(stored, []); self.assertEqual(step.failed, 1)

    def test_nothing_wanted_or_nothing_missing_makes_no_query_and_no_commit(self):
        conn = Conn()
        self.run_store(self.step(), [post()], {}, conn=conn)  # none wanted
        self.run_store(self.step(), [post(wanted=True)], {}, conn=conn)  # none missing
        self.assertEqual(conn.commits, 0)

    def test_a_database_error_switches_the_step_off_and_rolls_back_to_the_savepoint(self):
        conn, step = Conn(), self.step()

        def missing(c, cid, ids): raise psycopg2.errors.UndefinedColumn('column "description" does not exist')
        with mock.patch.object(describe._db, "get_postings_missing_description", missing):
            out = step.store(conn, COMPANY, [post(wanted=True)])
        self.assertIs(out, conn)
        self.assertIn("UndefinedColumn", step.broken)
        self.assertTrue(any("ROLLBACK TO SAVEPOINT" in q for q in conn.sql))
        self.assertFalse(any(q.startswith("ROLLBACK") and "SAVEPOINT" not in q for q in conn.sql))
        with mock.patch.object(describe._db, "get_postings_missing_description", side_effect=AssertionError("should not run")):
            step.store(conn, COMPANY, [post(wanted=True)])  # switched off: nothing more is attempted

    def test_lacking_lists_wanted_roles_without_a_description(self):
        step = self.step()
        ps = [post(ats_posting_id="a", wanted=True), post(ats_posting_id="b", wanted=True), post(ats_posting_id="c")]
        self.assertEqual([p["ats_posting_id"] for p in step.lacking(ps, {"a"})], ["b"])


class Wiring(unittest.TestCase):
    def test_workers_tag_roles_before_dropping_a_non_design_payload(self):
        step = describe.DescriptionStep(lambda p: True)
        board = {"id": 1, "ats_type": "ashby", "ats_identifier": "x"}
        postings = [{"ats_posting_id": "1", "title": "Accountant", "url": "u", "raw_json": {"descriptionPlain": "Books"}}]
        with mock.patch.object(rc, "_describer", step), \
             mock.patch.dict(rc.COLLECTORS, {"ashby": lambda ident: postings}):
            out, err = rc._fetch_company(board)
        p = out[0]
        self.assertIsNone(err)
        self.assertEqual(p["description"], "Books")
        self.assertIsNone(p["raw_json"])  # still dropped for storage

    def test_without_a_describer_the_workers_behave_as_before(self):
        board = {"id": 1, "ats_type": "ashby", "ats_identifier": "x"}
        postings = [{"ats_posting_id": "1", "title": "Accountant", "url": "u", "raw_json": {"a": 1}}]
        with mock.patch.dict(rc.COLLECTORS, {"ashby": lambda ident: postings}):
            out, _ = rc._fetch_company(board)
        self.assertNotIn("wanted", out[0]); self.assertNotIn("description", out[0])

    def test_hourly_cleanup_failure_does_not_take_the_run_down(self):
        conn = Conn()
        with mock.patch.object(rc, "_tail_slot", lambda now=None: 0), \
             mock.patch.object(rc, "clear_closed_descriptions", side_effect=psycopg2.Error("no column")), \
             mock.patch.object(rc, "ensure_conn", lambda c: c):
            self.assertIs(rc._drop_old_descriptions(conn), conn)
        with mock.patch.object(rc, "_tail_slot", lambda now=None: 1), \
             mock.patch.object(rc, "clear_closed_descriptions") as clear:
            rc._drop_old_descriptions(conn)
        clear.assert_not_called()  # only every fourth run (hourly)

    def test_report_alerts_when_broken_or_a_platform_is_blocked(self):
        step = describe.DescriptionStep(lambda p: True)
        step.broken = "UndefinedColumn: x"
        step.budget.rate_limited["workable"] = 5
        alerts = []
        with mock.patch.object(rc, "_tail_slot", lambda now=None: 0):
            rc._report_descriptions(step, alerts)
        self.assertEqual(len(alerts), 2)
        self.assertIn("Workable", alerts[1])


class ScanTailCalls(unittest.TestCase):
    """The discovered-board scan hands wanted roles to the step: when it writes, and when a board is
    otherwise unchanged but its stored roles have just become wanted."""

    BOARD = {"id": 9, "name": "b", "ats_type": "greenhouse", "ats_identifier": "b", "full_ingest_at": "2026-10-01",
             "tier": "hot", "scan_marker": None, "last_checked_at": None}

    def scan(self, hashes_match, described):
        role = {"ats_posting_id": "r1", "title": "Product Designer", "url": "u", "is_design": True, "wanted": True}
        stored = {"r1": rc.content_hash(role)} if hashes_match else {"r1": "old"}
        step, calls, upserts = describe.DescriptionStep(lambda p: True), [], []
        step.store = lambda conn, company, postings: calls.append([p["ats_posting_id"] for p in postings]) or conn

        with mock.patch.object(rc, "_describer", step), \
             mock.patch.object(rc, "_fetch_tail", lambda c: ({"design": [dict(role)], "alive": True, "marker": None}, None)), \
             mock.patch.object(rc, "get_open_posting_hashes", lambda c, ids: {9: stored}), \
             mock.patch.object(describe._db, "get_open_described_ids", lambda c, ids: {9: described}), \
             mock.patch.object(rc, "upsert_postings", lambda *a, **k: upserts.append(1) or {}), \
             mock.patch.object(rc, "mark_stale_postings_closed", lambda *a, **k: None), \
             mock.patch.object(rc, "record_tail_scan_results", lambda *a, **k: None), \
             mock.patch.object(rc, "set_scan_markers", lambda *a: None), \
             mock.patch.object(rc, "promote_hot_boards", lambda c: 0), \
             mock.patch.object(rc, "ensure_conn", lambda c: c):
            rc._scan_tail(Conn(), [dict(self.BOARD)], [], lambda *a: None)
        return calls, upserts

    def test_a_changed_board_stores_then_describes(self):
        calls, upserts = self.scan(hashes_match=False, described=set())
        self.assertEqual((calls, len(upserts)), ([["r1"]], 1))

    def test_an_unchanged_board_is_described_when_a_stored_role_lacks_one(self):
        calls, upserts = self.scan(hashes_match=True, described=set())
        self.assertEqual((calls, len(upserts)), ([["r1"]], 0))  # described, nothing rewritten

    def test_an_unchanged_board_whose_roles_are_all_described_costs_nothing(self):
        calls, upserts = self.scan(hashes_match=True, described={"r1"})
        self.assertEqual((calls, len(upserts)), ([], 0))

    def test_a_missing_column_switches_the_step_off_but_the_scan_still_runs(self):
        conn = Conn()
        step = describe.DescriptionStep(lambda p: True)
        with mock.patch.object(describe._db, "get_open_described_ids",
                               side_effect=psycopg2.errors.UndefinedColumn("no description")):
            self.assertEqual(step.described_ids(conn, [1, 2]), {})
        self.assertIn("UndefinedColumn", step.broken)
        self.assertTrue(any("ROLLBACK TO SAVEPOINT" in q for q in conn.sql))
        calls, upserts = [], []
        # the whole discovered-board scan, with the step broken: roles are still written, nothing is described
        role = {"ats_posting_id": "r1", "title": "Product Designer", "url": "u", "is_design": True, "wanted": True}

        with mock.patch.object(rc, "_describer", step), \
             mock.patch.object(rc, "_fetch_tail", lambda c: ({"design": [dict(role)], "alive": True, "marker": None}, None)), \
             mock.patch.object(rc, "get_open_posting_hashes", lambda c, ids: {9: {"r1": "old"}}), \
             mock.patch.object(rc, "upsert_postings", lambda *a, **k: upserts.append(1) or {}), \
             mock.patch.object(rc, "mark_stale_postings_closed", lambda *a, **k: None), \
             mock.patch.object(rc, "record_tail_scan_results", lambda *a, **k: None), \
             mock.patch.object(rc, "set_scan_markers", lambda *a: None), \
             mock.patch.object(rc, "promote_hot_boards", lambda c: 0), \
             mock.patch.object(rc, "ensure_conn", lambda c: c), \
             mock.patch.object(describe._db, "get_postings_missing_description", side_effect=AssertionError("off")):
            rc._scan_tail(Conn(), [dict(self.BOARD)], [], lambda *a: None)
        self.assertEqual(len(upserts), 1)


class DescriptionsComeAfterAlerts(unittest.TestCase):
    def test_core_descriptions_are_fetched_only_after_the_alerts_are_delivered(self):
        from tests.test_run_isolation import RunIsolation
        order = []
        real_deliver = rc._deliver

        def deliver(*a, **k):
            order.append("deliver")
            return real_deliver(*a, **k)

        def store(self, conn, company, postings):
            order.append("store")
            return conn

        with mock.patch.object(rc, "_deliver", deliver), \
             mock.patch.object(describe.DescriptionStep, "store", store):
            sent, _, _ = RunIsolation().run_it(tail_raises=False)
        self.assertEqual(len(sent), 1)
        self.assertIn("store", order)
        self.assertLess(order.index("deliver"), order.index("store"))


if __name__ == "__main__":
    unittest.main()
