"""SC-51 slice 3: when a person's settings let in roles that discovered boards did not store, the collector is
asked for a quiet catch-up. Narrowing, or changing only skills, asks for nothing."""
import unittest
from unittest import mock

from dashboard import app as dashboard_app
from dashboard import profile as profile_screen
from role_categories import gate_widened, level_widened, work_types_widened


def p(titles=("product designer",), level=None, up=False, city=None, work=None, skills=()):
    return {"titles": list(titles), "skills": list(skills), "level": level, "one_level_up": up, "city": city,
            "work_types": work}


class Rules(unittest.TestCase):
    def test_a_new_title_widens_and_removing_or_retyping_one_does_not(self):
        self.assertTrue(gate_widened(p(), p(titles=["product designer", "product manager"])))
        self.assertFalse(gate_widened(p(titles=["a b", "c d"]), p(titles=["c d"])))
        self.assertFalse(gate_widened(p(titles=["Product  Designer"]), p(titles=["product designer"])))

    def test_skills_alone_never_widen(self):
        self.assertFalse(gate_widened(p(), p(skills=["figma", "research"])))

    def test_levels(self):
        self.assertTrue(level_widened("senior", False, None, False))        # any level now
        self.assertTrue(level_widened("senior", False, "senior", True))     # one level up added
        self.assertTrue(level_widened("senior", False, "staff", False))     # a level it did not admit
        self.assertFalse(level_widened("senior", True, "senior", False))    # narrower
        self.assertFalse(level_widened(None, False, "senior", False))       # from everything to something
        self.assertFalse(level_widened("staff", True, "staff", True))

    def test_work_types(self):
        self.assertTrue(work_types_widened(None, ["remote", "hybrid"]))     # never chose = Remote
        self.assertTrue(work_types_widened(["remote"], []))                 # [] = Any
        self.assertTrue(work_types_widened(["remote"], ["onsite"]))
        self.assertFalse(work_types_widened(["remote", "hybrid"], ["hybrid"]))
        self.assertFalse(work_types_widened(["remote", "hybrid", "onsite"], []))
        self.assertFalse(work_types_widened(None, ["remote"]))

    def test_a_city_widens_only_when_removed_or_changed(self):
        self.assertTrue(gate_widened(p(city="Chicago"), p(city=None)))
        self.assertTrue(gate_widened(p(city="Chicago"), p(city="Austin")))
        self.assertFalse(gate_widened(p(city=None), p(city="Chicago")))     # a city narrows
        self.assertFalse(gate_widened(p(city="Chicago"), p(city=" chicago ")))

    def test_someone_with_nothing_saved_who_adds_titles_widens(self):
        self.assertTrue(gate_widened(p(titles=[]), p()))


class Ask(unittest.TestCase):
    def test_a_failed_request_never_raises_into_the_save(self):
        conn = mock.MagicMock()
        with mock.patch.object(profile_screen, "request_wide_catch_up", side_effect=RuntimeError("no column")):
            self.assertFalse(profile_screen.ask_for_catch_up(conn))
        conn.rollback.assert_called_once()

    def test_a_good_request_is_committed(self):
        conn = mock.MagicMock()
        with mock.patch.object(profile_screen, "request_wide_catch_up", return_value=3000):
            self.assertTrue(profile_screen.ask_for_catch_up(conn))
        conn.commit.assert_called_once()


class Request(unittest.TestCase):
    def test_the_request_includes_workday_boards(self):
        from db import db as dbmod
        conn = mock.MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.rowcount = 3
        self.assertEqual(dbmod.request_wide_catch_up(conn), 3)
        sql = cur.execute.call_args[0][0].lower()
        self.assertNotIn("workday", sql)
        self.assertIn("wide_scan_at = null", sql)


class Routes(unittest.TestCase):
    def setUp(self):
        self.client = dashboard_app.app.test_client()
        self.client.set_cookie(dashboard_app.SESSION_COOKIE, "tok")
        self.asked = []
        self.conn = mock.MagicMock()
        saved = dict(p(titles=["product designer"]), alerts_started_at=None, alerts_paused_at=None)
        self.saved_writes = []
        patches = [
            mock.patch.object(dashboard_app, "get_conn", lambda: self.conn),
            mock.patch.object(dashboard_app, "get_session_user", lambda c, t: (7, False)),
            mock.patch.object(dashboard_app, "ask_for_catch_up", lambda c: self.asked.append("work type")),
            mock.patch.object(profile_screen, "get_conn", lambda: self.conn),
            mock.patch.object(profile_screen, "is_onboarded", lambda c, u: True),
            mock.patch.object(profile_screen, "saved_profile", lambda c, u, o: dict(saved)),
            mock.patch.object(profile_screen, "save_profile", lambda c, u, **kw: self.saved_writes.append(kw)),
            mock.patch.object(profile_screen, "ask_for_catch_up", lambda c: self.asked.append("profile")),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def post_profile(self, **form):
        data = {"titles": ["product designer"], "level": "Any level", "work": ["Remote"]}
        data.update(form)
        return self.client.post("/profile", data=data)

    def test_saving_a_new_title_saves_then_asks(self):
        r = self.post_profile(titles=["product designer", "product manager"])
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(self.saved_writes), 1)
        self.assertEqual(self.asked, ["profile"])

    def test_saving_a_narrower_or_unchanged_profile_asks_for_nothing(self):
        self.post_profile()
        self.post_profile(level="Senior")
        self.assertEqual(len(self.saved_writes), 2)
        self.assertEqual(self.asked, [])

    def test_widening_the_work_type_filter_on_the_dashboard_asks(self):
        with mock.patch.object(dashboard_app, "get_user_work_types", lambda c, u: ["remote"]), \
             mock.patch.object(dashboard_app, "set_user_work_types", lambda c, u, w: None):
            self.client.get("/?work_set=1&work=remote&work=hybrid")
            self.assertEqual(self.asked, ["work type"])
            self.client.get("/?work_set=1&work=remote")
            self.assertEqual(self.asked, ["work type"])  # same as before: nothing more


if __name__ == "__main__":
    unittest.main()
