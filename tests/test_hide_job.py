"""SC-59: hide a job from Open roles, and say why. The job leaves Open roles, shows in 'Jobs you hid' with when and why,
and can be unhidden; hidden jobs are not asked about; the job card shows company, title, work type and place, pay line."""
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from dashboard import app as dapp
from dashboard.filters import location_line
from db import db as _db


def row(pid, title, hours_ago, location="Remote - United States", remote_type="remote", company="Northwind Health"):
    return {"id": pid, "title": title, "company_name": company, "ats_type": "greenhouse", "location": location,
            "remote_type": remote_type, "url": "u", "is_design": True, "applied": False,
            "first_seen_at": datetime.now(timezone.utc) - timedelta(hours=hours_ago)}


def hidden(pid, title, days_ago, reason=None):
    return {"id": pid, "title": title, "company_name": "Copperline", "ats_type": "lever", "location": "Austin, TX",
            "remote_type": None, "hidden_at": datetime.now(timezone.utc) - timedelta(days=days_ago), "hidden_reason": reason}


class Base(unittest.TestCase):
    def setUp(self):
        self.client = dapp.app.test_client()
        self.client.set_cookie(dapp.SESSION_COOKIE, "tok")
        self.rows = [row(1, "Senior Product Designer", 1), row(2, "Product Designer", 2), row(3, "UX Designer", 3)]
        self.hidden, self.calls, self.events, self.opens = [], [], [], {}
        self.conn = mock.MagicMock()
        profile = {"titles": ["designer"], "level": None, "one_level_up": False, "city": None, "skills": []}
        patches = [
            mock.patch.object(dapp, "get_conn", lambda: self.conn),
            mock.patch.object(dapp, "get_session_user", lambda c, t: (7, False)),
            mock.patch.object(dapp, "is_onboarded", lambda c, u: True),
            mock.patch.object(dapp, "log_visit", lambda c, u: None),
            mock.patch.object(dapp, "get_user_role_categories", lambda c, u: set()),
            mock.patch.object(dapp, "get_user_keywords", lambda c, u: []),
            mock.patch.object(dapp, "get_user_profile", lambda c, u: dict(profile)),
            mock.patch.object(dapp, "get_user_work_types", lambda c, u: []),
            mock.patch.object(dapp, "get_open_postings", lambda c, u, since=None, keywords=(): [dict(r) for r in self.rows]),
            mock.patch.object(dapp, "get_applications", lambda c, u: []),
            mock.patch.object(dapp, "get_hidden", lambda c, u: [dict(h) for h in self.hidden]),
            mock.patch.object(dapp, "get_return_events", lambda c, u: (dict(self.opens), {})),
            mock.patch.object(dapp, "get_applied_among", lambda c, u, ids: set()),
            mock.patch.object(dapp, "get_hidden_among", lambda c, u, ids: {h["id"] for h in self.hidden if h["id"] in ids}),
            mock.patch.object(dapp, "get_posting_url", lambda c, pid: "https://jobs.example/x" if pid in (1, 2, 3, 99) else None),
            mock.patch.object(dapp, "set_hidden", lambda c, pid, uid, flag, reason=None: self.calls.append((pid, uid, flag, reason))),
            mock.patch.object(dapp, "log_event", lambda c, uid, kind, pid=None: self.events.append((kind, pid))),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def get(self, url="/"):
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        return re.sub(r"<template.*?</template>", "", r.get_data(as_text=True), flags=re.S)

    def card(self, html, pid):
        return re.search(rf'<li class="fg-job" id="posting-{pid}">.*?</li>', html, re.S).group(0)


class Card(Base):
    def test_the_job_column_is_company_title_place_and_a_pay_line(self):
        c = self.card(self.get(), 1)
        lead = re.search(r'<div class="fg-job-lead">(.*?)</div>', c, re.S).group(1)
        self.assertEqual(re.findall(r'<p class="(fg-job-\w+)"', lead), ["fg-job-company", "fg-job-title", "fg-job-subline", "fg-job-pay"])
        self.assertIn('<p class="fg-job-company">Northwind Health</p>', c)
        self.assertIn('<p class="fg-job-subline">Remote, US</p>', c)
        self.assertNotIn("Found on", c)
        self.assertNotIn("•", c)

    def test_pay_is_a_blank_line_for_now_so_every_card_is_the_same_height(self):
        self.assertIn('<p class="fg-job-pay"> </p>', self.card(self.get(), 1))

    def test_the_state_column_says_where_then_when(self):
        c = self.card(self.get(), 1)
        self.assertIn("<p data-state-top>Discovered on Greenhouse</p>", c)
        self.assertRegex(c, r'<p class="fg-job-when" data-state-when>\d{1,2}:\d{2} [AP]M</p>')   # the time of day, not "1 hour ago"

    def test_the_action_spot_is_apply_then_hide_with_the_job_named_for_screen_readers(self):
        c = self.card(self.get(), 2)
        self.assertLess(c.index("data-apply"), c.index("data-hide"))
        self.assertIn('<span class="fg-job-group">', c)
        self.assertIn('>Hide<span class="fg-visually-hidden"> Product Designer</span></button>', c)

    def test_the_board_has_a_default_border_and_no_shadow(self):
        html = self.client.get("/").get_data(as_text=True)
        board = re.search(r"\.board \{(.*?)\}", html, re.S).group(1)
        self.assertIn("border: 1px solid var(--fg-color-border-default)", board)
        self.assertNotIn("box-shadow", board)


class LocationLine(unittest.TestCase):
    def test_work_type_then_place(self):
        cases = [
            (("Remote - United States", None), "Remote, US"), (("United States", "remote"), "Remote, US"),
            (("Remote", None), "Remote"), (("San Francisco", "remote"), "Remote, San Francisco"),
            (("Hybrid - Denver, CO", None), "Hybrid, Denver, CO"), (("Austin, TX", None), "Onsite, Austin, TX"),
            (("Remote – US", None), "Remote, US"), (("London", "hybrid"), "Hybrid, London"),
            (("Philippines", "remote"), "Remote, Philippines"),
            (("San Jose, California, United States of America", None), "Onsite, San Jose, California, US"),
        ]
        for args, want in cases:
            self.assertEqual(location_line(*args), want, args)

    def test_nothing_is_made_up(self):
        self.assertEqual(location_line(None, None), "")
        self.assertEqual(location_line("", None), "")

    def test_a_companys_own_count_of_locations_is_not_called_onsite(self):
        self.assertEqual(location_line("2 Locations", None), "2 locations")
        self.assertEqual(location_line("3 Locations", "hybrid"), "Hybrid, 3 locations")


class OpenRoles(Base):
    def test_hidden_jobs_are_not_listed_and_not_counted(self):
        self.hidden = [hidden(2, "Product Designer", 1)]
        html = self.get()
        self.assertEqual(re.findall(r'<a class="fg-job-link"[^>]*>([^<]+)</a>', html), ["Senior Product Designer", "UX Designer"])
        self.assertIn("2 open roles", html)

    def test_no_hidden_link_until_something_is_hidden(self):
        html = self.get()
        self.assertRegex(html, r'<a class="fg-link list-foot-end" href="/\?view=hidden" data-hidden-link hidden>Show 0 hidden jobs</a>')
        self.assertRegex(html, r'<div class="list-foot" data-foot hidden>')

    def test_the_footer_has_the_hidden_link_right_and_pagination_left(self):
        self.hidden = [hidden(9, "Old", 1), hidden(8, "Older", 2)]
        html = self.get()
        self.assertRegex(html, r'<a class="fg-link list-foot-end" href="/\?view=hidden" data-hidden-link>Show 2 hidden jobs</a>')
        self.assertNotIn('data-foot hidden', html)
        self.hidden = self.hidden[:1]
        self.assertIn("Show 1 hidden job</a>", self.get())

    def test_pagination_stays_on_the_left_when_there_is_more(self):
        self.rows = [row(i, f"Designer {i}", i) for i in range(1, 60)]
        self.hidden = [hidden(900, "Old", 1)]
        html = self.get()
        foot = re.search(r'<div class="list-foot" data-foot>(.*?)</div>', html, re.S).group(1)
        self.assertLess(foot.index("Showing 50 of 59"), foot.index("data-hidden-link"))

    def test_the_reason_menu_and_skip_are_in_the_page_for_the_script(self):
        html = self.client.get("/").get_data(as_text=True)
        tpl = re.search(r'<template id="return-why-template">(.*?)</template>', html, re.S).group(1)
        self.assertIn('>Why are you hiding?</label>', tpl)
        options = re.findall(r'<option value="([^"]*)"', tpl)
        self.assertEqual(options, ["", "role", "junior", "senior", "location", "pay", "company", "applied", "other"])
        self.assertIn('data-skip>Skip</button>', tpl)
        self.assertIn('<option value="" disabled selected>Select</option>', tpl)

    def test_the_reasons_read_in_the_agreed_words_and_order(self):
        html = self.client.get("/").get_data(as_text=True)
        labels = re.findall(r"<option value=\"\w+\">([^<]+)</option>", re.search(r'<template id="return-why-template">(.*?)</template>', html, re.S).group(1))
        self.assertEqual(labels, ["Not my kind of role", "Too junior", "Too senior", "Location or remote setup", "Pay",
                                  "Not interested in this company", "Already applied", "Other"])

    def test_the_simple_toast_template_has_only_undo(self):
        html = self.client.get("/").get_data(as_text=True)
        tpl = re.search(r'<template id="toast-simple-template">(.*?)</template>', html, re.S).group(1)
        self.assertIn("__text__", tpl)
        self.assertIn('data-toast-undo>Undo</button>', tpl)
        self.assertNotIn(">View<", tpl)


class HiddenList(Base):
    def test_lists_hidden_jobs_newest_first_with_when_and_why(self):
        self.hidden = [hidden(5, "Product Designer II", 2, "junior"), hidden(6, "UX Designer, Contract", 5, None)]
        html = self.get("/?view=hidden")
        self.assertIn(">Jobs you hid</h2>", html)
        self.assertEqual(re.findall(r'<a class="fg-job-link"[^>]*>([^<]+)</a>', html), ["Product Designer II", "UX Designer, Contract"])
        first = self.card(html, 5)
        self.assertIn("<p data-state-top>Hidden 2 days ago</p>", first)
        self.assertRegex(first, r'<p class="fg-job-when" data-state-when>Too junior</p>')
        self.assertNotIn("data-state-when", self.card(html, 6))                   # nothing after Skip

    def test_each_hidden_job_has_one_unhide_button_and_nothing_else(self):
        self.hidden = [hidden(5, "Product Designer II", 2, "junior")]
        c = self.card(self.get("/?view=hidden"), 5)
        self.assertIn('action="/unhide/5" data-unhide data-reason="junior"', c)
        self.assertIn(">Unhide<span class=\"fg-visually-hidden\"> Product Designer II</span></button>", c)
        for gone in ("data-apply", "data-hide", "Did you apply"):
            self.assertNotIn(gone, c)

    def test_the_view_has_a_way_back_and_the_heading_takes_focus(self):
        html = self.get("/?view=hidden")
        self.assertIn('<a class="fg-link" href="/">Back to open roles</a>', html)
        self.assertIn('id="hidden-title" tabindex="-1" data-focus-on-load', html)

    def test_filters_are_not_shown_in_this_view(self):
        html = self.get("/?view=hidden")
        self.assertNotIn("Work type", html)
        self.assertNotIn("filter-posted", html)

    def test_empty_says_what_will_land_here(self):
        html = self.get("/?view=hidden")
        self.assertIn("Nothing hidden.", html)
        self.assertIn("Jobs you hide land here, in case you change your mind.", html)

    def test_open_roles_is_still_the_selected_tab(self):
        html = self.get("/?view=hidden")
        self.assertRegex(html, r'<a class="fg-tab" role="tab" href="/" aria-selected="true"')

    def test_the_applications_tab_ignores_the_hidden_view(self):
        html = self.get("/?tab=applications&view=hidden")
        self.assertNotIn("Jobs you hid", html)


class Routes(Base):
    def test_hide_with_a_reason_gets_a_quiet_answer_and_records_it(self):
        r = self.client.post("/hide/2", data={"reason": "junior"}, headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.calls, [(2, 7, True, "junior")])
        self.assertEqual(self.events, [("hidden", 2)])

    def test_skip_hides_with_no_reason(self):
        self.client.post("/hide/2", headers={"X-Requested-With": "fetch"})
        self.assertEqual(self.calls, [(2, 7, True, None)])

    def test_putting_it_back_after_undo_is_not_a_new_hide_event(self):
        self.client.post("/hide/2", data={"reason": "pay", "again": "1"}, headers={"X-Requested-With": "fetch"})
        self.assertEqual(self.calls, [(2, 7, True, "pay")])
        self.assertEqual(self.events, [])

    def test_without_the_script_hide_goes_back_to_open_roles(self):
        r = self.client.post("/hide/2")
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/"))

    def test_an_unknown_job_hides_nothing(self):
        self.client.post("/hide/12345", data={"reason": "pay"}, headers={"X-Requested-With": "fetch"})
        self.assertEqual((self.calls, self.events), ([], []))

    def test_unhide_clears_it(self):
        r = self.client.post("/unhide/2", headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.calls, [(2, 7, False, None)])

    def test_without_the_script_unhide_stays_on_the_hidden_list(self):
        r = self.client.post("/unhide/2")
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/?view=hidden"))

    def test_hide_and_unhide_are_committed(self):
        self.client.get("/return.json")                       # a request that writes nothing: whatever it commits is the baseline
        baseline = self.conn.commit.call_count
        self.conn.commit.reset_mock()
        self.client.post("/hide/2", headers={"X-Requested-With": "fetch"})
        self.assertEqual(self.conn.commit.call_count, baseline + 1)
        self.conn.commit.reset_mock()
        self.client.post("/unhide/2", headers={"X-Requested-With": "fetch"})
        self.assertEqual(self.conn.commit.call_count, baseline + 1)

    def test_they_need_a_post(self):
        self.client.get("/hide/2")
        self.client.get("/unhide/2")
        self.assertEqual((self.calls, self.events), ([], []))


class ComingBack(Base):
    def test_a_hidden_job_is_neither_asked_about_nor_marked_viewed_by_the_script_state(self):
        self.hidden = [hidden(2, "Product Designer", 1)]
        self.opens = {2: datetime.now(timezone.utc) - timedelta(minutes=5), 1: datetime.now(timezone.utc) - timedelta(minutes=50)}
        body = self.client.get("/return.json").get_json()
        self.assertIsNone(body["ask"])
        self.assertEqual(list(body["viewed"]), ["1"])


class Storage(unittest.TestCase):
    def conn(self):
        conn = mock.MagicMock()
        return conn, conn.cursor.return_value.__enter__.return_value

    def test_hiding_stores_the_time_and_a_known_reason(self):
        conn, cur = self.conn()
        _db.set_hidden(conn, 5, 7, True, "junior")
        sql, params = cur.execute.call_args.args
        self.assertIn("CASE WHEN %s THEN now() END", sql)
        self.assertEqual(params[0:3], (7, 5, True))
        self.assertIn("junior", params)

    def test_an_unknown_reason_is_stored_as_none(self):
        conn, cur = self.conn()
        _db.set_hidden(conn, 5, 7, True, "<script>")
        self.assertNotIn("<script>", cur.execute.call_args.args[1])

    def test_unhiding_clears_both_columns(self):
        conn, cur = self.conn()
        _db.set_hidden(conn, 5, 7, False)
        sql, params = cur.execute.call_args.args
        self.assertIn("hidden_at = CASE WHEN %s THEN COALESCE(user_postings.hidden_at, now()) END", sql)
        self.assertIn("hidden_reason = CASE WHEN %s THEN EXCLUDED.hidden_reason END", sql)
        self.assertEqual(params[5:], (False, False))

    def test_the_hidden_list_is_open_postings_only_newest_first(self):
        conn, cur = self.conn()
        cur.fetchall.return_value = ["x"]
        self.assertEqual(_db.get_hidden(conn, 7), ["x"])
        sql = cur.execute.call_args.args[0]
        self.assertIn("p.status = 'open'", sql)
        self.assertIn("up.user_id = %s", sql)
        self.assertIn("up.hidden_at IS NOT NULL", sql)
        self.assertIn("ORDER BY up.hidden_at DESC", sql)

    def test_the_schema_adds_both_columns_without_touching_anything_else(self):
        schema = open("db/schema.sql").read()
        tail = schema[schema.index("-- SC-59"):]
        self.assertIn("ALTER TABLE user_postings ADD COLUMN IF NOT EXISTS hidden_at TIMESTAMPTZ;", tail)
        self.assertIn("ALTER TABLE user_postings ADD COLUMN IF NOT EXISTS hidden_reason TEXT;", tail)
        self.assertNotIn("DROP", tail)


if __name__ == "__main__":
    unittest.main()
