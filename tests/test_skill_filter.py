"""SC-51 slice 4: the skills cutoff on the dashboard list, the profile preview and the saved setting."""
import unittest
from datetime import datetime, timezone
from unittest import mock

import psycopg2

from dashboard import app as dapp
from dashboard import profile as pf
from dashboard import skill_filter as sf
from db import db as dbmod

SKILLS = ["Figma", "Design systems", "CSS"]


def post(pid, title="Product Designer", **kw):
    return dict({"id": pid, "title": title, "company_name": "Co", "ats_type": "ashby", "location": "Remote - United States",
                 "remote_type": "remote", "url": "u", "first_seen_at": datetime.now(timezone.utc), "is_design": True,
                 "applied": False}, **kw)


def matches(table):
    return mock.patch.object(sf, "get_skill_matches", lambda conn, ids, skills: {i: table[i] for i in ids if i in table})


class Apply(unittest.TestCase):
    table = {1: ["figma", "design systems", "css"], 2: ["figma"], 3: [], 4: ["figma", "css"]}   # 5 has no text

    def run_it(self, cutoff, show_all=False, skills=SKILLS):
        rows = [post(i) for i in (1, 2, 3, 4, 5)]
        with matches(self.table):
            return sf.apply_skill_cutoff(object(), rows, skills, cutoff, show_all)

    def test_roles_under_the_minimum_are_dropped_and_counted(self):
        kept, under = self.run_it(2)
        self.assertEqual([p["id"] for p in kept], [1, 4, 5])      # 2 and 3 mention fewer than two skills
        self.assertEqual(under, 2)

    def test_a_role_with_no_stored_text_is_never_hidden_and_says_so(self):
        kept, _ = self.run_it(3)
        unread = [p for p in kept if p["id"] == 5][0]
        self.assertEqual(unread["skill_line"], sf.NOT_SCORED)
        self.assertEqual([p["id"] for p in kept], [1, 5])

    def test_matched_skills_are_named_as_the_person_wrote_them(self):
        kept, _ = self.run_it(1)
        self.assertEqual([p for p in kept if p["id"] == 1][0]["skill_line"], "Mentions Figma, Design systems, CSS")
        self.assertEqual([p for p in kept if p["id"] == 2][0]["skill_line"], "Mentions Figma")

    def test_a_bracketed_part_of_a_skill_is_not_shown_because_it_was_not_matched(self):
        with matches({1: ["accessibility", "figma"]}):
            kept, _ = sf.apply_skill_cutoff(object(), [post(1)], ["Accessibility (WCAG 2.2)", "Figma"], 1)
        self.assertEqual(kept[0]["skill_line"], "Mentions Accessibility, Figma")

    def test_show_them_keeps_the_roles_under_the_minimum_with_an_honest_line(self):
        kept, under = self.run_it(2, show_all=True)
        self.assertEqual([p["id"] for p in kept], [1, 2, 3, 4, 5])
        self.assertEqual(under, 2)                                  # the same count either way
        lines = {p["id"]: p["skill_line"] for p in kept}
        self.assertEqual(lines[3], sf.NONE_MENTIONED)
        self.assertEqual(lines[2], "Mentions Figma")

    def test_the_minimum_can_never_exceed_the_number_of_skills(self):
        kept, _ = self.run_it(9, skills=["Figma", "CSS"])           # capped to 2
        self.assertEqual([p["id"] for p in kept], [1, 4, 5])

    def test_no_minimum_or_no_skills_changes_nothing_and_looks_nothing_up(self):
        rows = [post(1), post(2)]
        with mock.patch.object(sf, "get_skill_matches", side_effect=AssertionError("no lookup expected")):
            for cutoff, skills in ((None, SKILLS), (0, SKILLS), (3, []), (3, None), (3, ["A", "(x)"])):
                kept, under = sf.apply_skill_cutoff(object(), rows, skills, cutoff)
                self.assertEqual((kept, under), (rows, 0))
                self.assertNotIn("skill_line", rows[0])


class Dashboard(unittest.TestCase):
    def setUp(self):
        self.client = dapp.app.test_client()
        self.client.set_cookie(dapp.SESSION_COOKIE, "tok")
        self.lookups = []
        self.cutoff = 2
        rows = [post(1, "Senior Product Designer"), post(2, "Product Designer"), post(3, "Design Manager"),
                post(4, "Staff Designer")]
        profile = {"titles": ["designer", "design manager"], "level": None, "one_level_up": False, "city": None,
                   "skills": SKILLS}
        table = {1: ["figma", "css"], 2: ["figma"], 3: []}          # 4 has no text

        def lookup(conn, ids, skills):
            self.lookups.append(list(ids))
            return {i: table[i] for i in ids if i in table}

        patches = [
            mock.patch.object(dapp, "get_conn", lambda: mock.MagicMock()),
            mock.patch.object(dapp, "get_session_user", lambda c, t: (7, False)),
            mock.patch.object(dapp, "is_onboarded", lambda c, u: True),
            mock.patch.object(dapp, "log_visit", lambda c, u: None),
            mock.patch.object(dapp, "get_user_role_categories", lambda c, u: set()),
            mock.patch.object(dapp, "get_user_keywords", lambda c, u: []),
            mock.patch.object(dapp, "get_user_profile", lambda c, u: dict(profile)),
            mock.patch.object(dapp, "get_user_work_types", lambda c, u: []),
            mock.patch.object(dapp, "get_open_postings", lambda c, u, since=None, keywords=(): [dict(r) for r in rows]),
            mock.patch.object(dapp, "get_user_skill_cutoff", lambda c, u: self.cutoff),
            mock.patch.object(sf, "get_skill_matches", lookup),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def page(self, query=""):
        r = self.client.get("/" + query)
        self.assertEqual(r.status_code, 200)
        return r.get_data(as_text=True)

    def test_roles_under_the_minimum_are_hidden_with_a_line_saying_so(self):
        html = self.page()
        self.assertIn("Senior Product Designer", html)
        self.assertIn("Staff Designer", html)                        # no stored text: shown
        self.assertNotIn(">Product Designer<", html)                 # mentions only one skill
        self.assertNotIn(">Design Manager<", html)
        self.assertIn("2 roles are hidden by your skills cutoff.", html)
        self.assertIn('href="/?skills=all">Show them</a>', html)
        self.assertIn("Mentions Figma, CSS", html)
        self.assertIn("Not scored for your skills", html)
        self.assertIn("2 open roles", html)                          # the count follows the cutoff

    def test_show_them_lists_everything_and_offers_to_hide_again(self):
        html = self.page("?skills=all")
        for title in ("Senior Product Designer", "Product Designer", "Design Manager", "Staff Designer"):
            self.assertIn(title, html)
        self.assertIn("Showing 2 roles under your skills cutoff.", html)
        self.assertIn('href="/">Hide them</a>', html)
        self.assertIn("Mentions none of your skills", html)

    def test_without_a_minimum_the_page_is_as_before_and_nothing_is_looked_up(self):
        self.cutoff = None
        html = self.page()
        self.assertEqual(self.lookups, [])
        for title in ("Senior Product Designer", "Product Designer", "Design Manager", "Staff Designer"):
            self.assertIn(title, html)
        self.assertNotIn("hidden by your skills cutoff", html)
        self.assertNotIn("Mentions", html)

    def test_the_lookup_is_one_query_for_the_roles_that_passed_the_other_filters(self):
        self.page()
        self.assertEqual(len(self.lookups), 1)


class Paging(unittest.TestCase):
    def test_the_show_more_link_keeps_show_them_on(self):
        client = dapp.app.test_client()
        client.set_cookie(dapp.SESSION_COOKIE, "tok")
        rows = [post(i, f"Designer {i}") for i in range(1, 61)]                     # more than one page of 50
        profile = {"titles": ["designer"], "level": None, "one_level_up": False, "city": None, "skills": SKILLS}
        with mock.patch.object(dapp, "get_conn", lambda: mock.MagicMock()), \
             mock.patch.object(dapp, "get_session_user", lambda c, t: (7, False)), \
             mock.patch.object(dapp, "is_onboarded", lambda c, u: True), \
             mock.patch.object(dapp, "log_visit", lambda c, u: None), \
             mock.patch.object(dapp, "get_user_role_categories", lambda c, u: set()), \
             mock.patch.object(dapp, "get_user_keywords", lambda c, u: []), \
             mock.patch.object(dapp, "get_user_profile", lambda c, u: dict(profile)), \
             mock.patch.object(dapp, "get_user_work_types", lambda c, u: []), \
             mock.patch.object(dapp, "get_open_postings", lambda c, u, since=None, keywords=(): [dict(r) for r in rows]), \
             mock.patch.object(dapp, "get_user_skill_cutoff", lambda c, u: 1), \
             matches({i: ["figma"] for i in range(1, 61)}):
            plain = client.get("/").get_data(as_text=True)
            every = client.get("/?skills=all").get_data(as_text=True)
        self.assertIn('href="/?n=100">Show 50 more</a>', plain)
        self.assertIn('href="/?n=100&amp;skills=all">Show 50 more</a>', every)


class ProfilePage(unittest.TestCase):
    """GET /profile reads the saved minimum for the form and the select shows it."""

    def setUp(self):
        self.client = dapp.app.test_client()
        self.client.set_cookie(dapp.SESSION_COOKIE, "tok")
        profile = {"titles": ["product designer"], "skills": ["Figma"], "level": None, "level_note": None,
                   "one_level_up": False, "city": None, "alerts_started_at": datetime(2026, 10, 6), "alerts_paused_at": None}
        from dashboard import resume as resume_mod
        patches = [
            mock.patch.object(dapp, "get_session_user", lambda c, t: (7, False)),
            mock.patch.object(dapp, "get_conn", lambda: mock.MagicMock()),
            mock.patch.object(pf, "get_conn", lambda: mock.MagicMock()),
            mock.patch.object(pf, "is_onboarded", lambda c, u: True),
            mock.patch.object(pf, "get_user_profile", lambda c, u: dict(profile)),
            mock.patch.object(pf, "get_user_work_types", lambda c, u: None),
            mock.patch.object(pf, "get_user_skill_cutoff", lambda c, u: 4),
            mock.patch.object(pf, "preview_for", lambda c, u, d, days=7: []),
            mock.patch.object(pf, "get_week_stats", lambda c, u: {"reached": 0, "opened": 0, "applied": 0}),
            mock.patch.object(pf, "get_resume", lambda c, u: None),
            mock.patch.object(resume_mod, "reading_state", lambda c, u: ("ok", None)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_the_saved_minimum_is_read_and_shown_selected(self):
        r = self.client.get("/profile")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn('<option value="4" selected>4 of my skills</option>', html)
        self.assertIn('"cutoff": "4"', html)                       # and the page script's saved snapshot agrees

    def test_saved_profile_carries_the_minimum(self):
        model = pf.saved_profile(mock.MagicMock(), 7, True)
        self.assertEqual(model["skill_cutoff"], 4)


class Preview(unittest.TestCase):
    def test_the_preview_count_follows_the_minimum(self):
        rows = [post(1), post(2), post(3)]
        table = {1: ["figma", "css"], 2: ["figma"], 3: ["figma", "design systems", "css"]}
        draft = {"titles": ["product designer"], "skills": SKILLS, "level": None, "one_level_up": False, "city": None,
                 "work_types": None, "skill_cutoff": 2}
        with mock.patch.object(pf, "get_open_postings", lambda c, u, since=None, keywords=(): [dict(r) for r in rows]), \
             matches(table):
            self.assertEqual([p["id"] for p in pf.preview_for(object(), 7, draft)], [1, 3])
            self.assertEqual([p["id"] for p in pf.preview_for(object(), 7, dict(draft, skill_cutoff=None))], [1, 2, 3])


class SaveRoute(unittest.TestCase):
    def test_saving_stores_the_minimum_after_the_profile(self):
        order = []
        client = dapp.app.test_client()
        client.set_cookie(dapp.SESSION_COOKIE, "tok")
        saved = dict(titles=["product designer"], skills=[], level=None, one_level_up=False, city=None, work_types=None,
                     skill_cutoff=None, alerts_started_at=None, alerts_paused_at=None)
        with mock.patch.object(dapp, "get_session_user", lambda c, t: (7, False)), \
             mock.patch.object(dapp, "get_conn", lambda: mock.MagicMock()), \
             mock.patch.object(pf, "get_conn", lambda: mock.MagicMock()), \
             mock.patch.object(pf, "is_onboarded", lambda c, u: True), \
             mock.patch.object(pf, "saved_profile", lambda c, u, o: dict(saved)), \
             mock.patch.object(pf, "save_profile", lambda c, u, **kw: order.append(("profile", sorted(kw)))), \
             mock.patch.object(pf, "set_user_skill_cutoff", lambda c, u, n: order.append(("cutoff", n))), \
             mock.patch.object(pf, "ask_for_catch_up", lambda c: None):
            r = client.post("/profile", data={"titles": ["product designer"], "skills": ["Figma"], "level": "Any level",
                                              "work": ["Remote"], "skill_cutoff": "4"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual([o[0] for o in order], ["profile", "cutoff"])
        self.assertEqual(order[1], ("cutoff", 4))
        self.assertNotIn("skill_cutoff", order[0][1])   # save_profile does not know the field


class Cursor:
    def __init__(self, conn): self.conn = conn
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None):
        self.conn.sql.append(sql)
        if "skill_cutoff" in sql and not sql.startswith(("SAVEPOINT", "ROLLBACK", "RELEASE")) and self.conn.broken:
            raise psycopg2.errors.UndefinedColumn('column "skill_cutoff" does not exist')
    def fetchone(self): return (3,)


class Conn:
    def __init__(self, broken): self.broken, self.sql = broken, []
    def cursor(self): return Cursor(self)


class OptionalColumn(unittest.TestCase):
    def test_a_missing_column_gives_no_minimum_and_never_raises(self):
        conn = Conn(broken=True)
        self.assertIsNone(dbmod.get_user_skill_cutoff(conn, 7))
        self.assertFalse(dbmod.set_user_skill_cutoff(conn, 7, 3))
        self.assertTrue(any(q.startswith("ROLLBACK TO SAVEPOINT") for q in conn.sql))   # only this step is undone
        self.assertFalse(any(q == "ROLLBACK" for q in conn.sql))

    def test_with_the_column_it_reads_and_writes(self):
        conn = Conn(broken=False)
        self.assertEqual(dbmod.get_user_skill_cutoff(conn, 7), 3)
        self.assertTrue(dbmod.set_user_skill_cutoff(conn, 7, 3))
        self.assertTrue(any(q.startswith("RELEASE SAVEPOINT") for q in conn.sql))


if __name__ == "__main__":
    unittest.main()
