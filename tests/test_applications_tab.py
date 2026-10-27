"""SC-63: the dashboard has two tabs. Open roles (the default) lists what is still open to you; Applications lists what
you have applied to, newest first, with nothing to click but the job. Marking a job applied moves it between them."""
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from dashboard import app as dapp
from db import db as _db


def row(pid, title, hours_ago, applied=False):
    return {"id": pid, "title": title, "company_name": "Co", "ats_type": "ashby", "location": "Remote - United States",
            "remote_type": "remote", "url": "u", "is_design": True, "applied": applied,
            "first_seen_at": datetime.now(timezone.utc) - timedelta(hours=hours_ago)}


def application(pid, title, days_ago=None):
    when = datetime.now(timezone.utc) - timedelta(days=days_ago) if days_ago is not None else None
    return {"id": pid, "title": title, "company_name": "Co", "ats_type": "lever", "location": "Remote", "applied_at": when}


class Base(unittest.TestCase):
    def setUp(self):
        self.client = dapp.app.test_client()
        self.client.set_cookie(dapp.SESSION_COOKIE, "tok")
        self.rows = [row(1, "Senior Product Designer", 1), row(2, "Product Designer", 2), row(3, "UX Designer", 3)]
        self.applications = []
        self.events, self.applied = [], {}
        profile = {"titles": ["designer"], "level": None, "one_level_up": False, "city": None, "skills": []}
        patches = [
            mock.patch.object(dapp, "get_conn", lambda: mock.MagicMock()),
            mock.patch.object(dapp, "get_session_user", lambda c, t: (7, False)),
            mock.patch.object(dapp, "is_onboarded", lambda c, u: True),
            mock.patch.object(dapp, "log_visit", lambda c, u: None),
            mock.patch.object(dapp, "get_user_role_categories", lambda c, u: set()),
            mock.patch.object(dapp, "get_user_keywords", lambda c, u: []),
            mock.patch.object(dapp, "get_user_profile", lambda c, u: dict(profile)),
            mock.patch.object(dapp, "get_user_work_types", lambda c, u: []),
            mock.patch.object(dapp, "get_open_postings", lambda c, u, since=None, keywords=(): [dict(r) for r in self.rows]),
            mock.patch.object(dapp, "get_applications", lambda c, u: [dict(a) for a in self.applications]),
            mock.patch.object(dapp, "get_return_events", lambda c, u: ({}, {})),
            mock.patch.object(dapp, "set_applied", lambda c, pid, flag, uid: self.applied.__setitem__(pid, flag)),
            mock.patch.object(dapp, "log_event", lambda c, uid, kind, pid=None: self.events.append((kind, pid))),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def get(self, url="/"):
        r = self.client.get(url, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        return re.sub(r"<template.*?</template>", "", r.get_data(as_text=True), flags=re.S)

    def titles(self, html):
        return re.findall(r'<a class="fg-job-link"[^>]*>([^<]+)</a>', html)


class Tabs(Base):
    def tabs(self, html):
        block = re.search(r'<div class="fg-tabs" role="tablist" aria-label="Your jobs">(.*?)</div>', html, re.S).group(1)
        return re.findall(r'<a class="fg-tab" role="tab" href="([^"]+)" aria-selected="(\w+)"[^>]*>(.*?)</a>', block, re.S)

    def test_two_tabs_with_counts_and_open_roles_is_the_default(self):
        self.applications = [application(9, "Design Lead", 3)]
        found = self.tabs(self.get())
        self.assertEqual([(h, s) for h, s, _ in found], [("/", "true"), ("/?tab=applications", "false")])
        self.assertRegex(found[0][2], r"Open roles\s*<span class=\"fg-tab-count\" data-tab-count>3</span>")
        self.assertRegex(found[1][2], r"Applications\s*<span class=\"fg-tab-count\" data-tab-count>1</span>")

    def test_the_applications_tab_is_the_selected_one_on_its_page(self):
        found = self.tabs(self.get("/?tab=applications"))
        self.assertEqual([s for _, s, _ in found], ["false", "true"])

    def test_an_unknown_tab_is_open_roles(self):
        self.assertEqual([s for _, s, _ in self.tabs(self.get("/?tab=nonsense"))], ["true", "false"])

    def test_applied_jobs_leave_open_roles_and_the_count(self):
        self.rows[1]["applied"] = True
        html = self.get()
        self.assertEqual(self.titles(html), ["Senior Product Designer", "UX Designer"])
        self.assertIn("2 open roles", html)
        self.assertEqual(self.tabs(html)[0][2].count(">2<"), 1)

    def test_the_status_filter_and_the_applied_toggle_are_gone(self):
        self.rows[1]["applied"] = True
        html = self.get()
        for gone in ("filter-status", "Not applied yet", "fg-toggle", "applied-toggle", "Mark as applied"):
            self.assertNotIn(gone, html)
        self.assertEqual(self.titles(self.get("/?status=applied")), ["Senior Product Designer", "UX Designer"])   # an old link is ignored

    def test_the_filters_are_on_open_roles_only(self):
        self.assertIn("Work type", self.get())
        self.assertIn("Posted", self.get())
        apps = self.get("/?tab=applications")
        self.assertNotIn("Work type", apps)
        self.assertNotIn("filter-posted", apps)


class Applications(Base):
    def test_lists_applied_jobs_in_the_order_given_with_when(self):
        self.applications = [application(9, "Design Lead", 1), application(8, "Staff Designer", 8), application(7, "Old role")]
        html = self.get("/?tab=applications")
        self.assertEqual(self.titles(html), ["Design Lead", "Staff Designer", "Old role"])
        self.assertIn("Applied 1 day ago", html)
        self.assertRegex(html, r'<p class="fg-job-state" data-state>Applied</p>')      # applied before the time was kept: no invented time
        self.assertIn("Newest first", html)
        self.assertIn("3 applications", html)

    def test_a_row_has_nothing_to_click_but_the_job(self):
        self.applications = [application(9, "Design Lead", 1)]
        li = re.search(r'<li class="fg-job" id="posting-9">.*?</li>', self.get("/?tab=applications"), re.S).group(0)
        for gone in ("<button", "<form", "data-apply", "Did you apply", "data-action"):
            self.assertNotIn(gone, li)
        self.assertIn('href="/open/9"', li)

    def test_open_roles_are_not_listed_there(self):
        self.applications = [application(9, "Design Lead", 1)]
        self.assertEqual(self.titles(self.get("/?tab=applications")), ["Design Lead"])

    def test_empty_says_what_will_happen_in_scouts_voice(self):
        html = self.get("/?tab=applications")
        self.assertIn("No applications yet.", html)
        self.assertIn("When you apply to a role from Open roles, it moves here.", html)
        self.assertIn("0 applications", html)

    def test_one_application_is_singular(self):
        self.applications = [application(9, "Design Lead", 1)]
        self.assertIn("1 application<", self.get("/?tab=applications").replace("</p>", "<"))

    def test_the_page_has_no_return_script_hook_on_applications(self):
        self.assertNotIn("data-return-url", self.get("/?tab=applications"))
        self.assertIn("data-return-url", self.get("/"))


class Marking(Base):
    def test_the_script_marks_applied_and_gets_a_quiet_answer(self):
        r = self.client.post("/set_applied/2", data={"applied": "true"}, headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.applied, {2: True})
        self.assertEqual(self.events, [("applied", 2)])

    def test_undo_marks_it_not_applied_and_logs_no_new_applied_event(self):
        self.client.post("/set_applied/2", data={"applied": "false"}, headers={"X-Requested-With": "fetch"})
        self.assertEqual(self.applied, {2: False})
        self.assertEqual(self.events, [])

    def test_without_the_script_applied_lands_on_applications_and_undo_on_open_roles(self):
        r = self.client.post("/set_applied/2", data={"applied": "true"})
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/?tab=applications"))
        r = self.client.post("/set_applied/2", data={"applied": "false"})
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/"))

    def test_it_needs_a_post(self):
        self.client.get("/set_applied/2")
        self.assertEqual((self.applied, self.events), ({}, []))


class Toast(Base):
    def test_the_toast_is_in_the_page_with_its_live_region_and_both_actions(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertRegex(html, r'<div class="fg-toast-region" role="status" aria-live="polite" id="toast-region"></div>')
        template = re.search(r'<template id="toast-template">(.*?)</template>', html, re.S).group(1)
        self.assertIn("You&#39;ve applied to this role. You can find it in Applications.", template)
        self.assertIn('<a class="fg-toast-action" href="/?tab=applications">View</a>', template)
        self.assertIn('data-toast-undo>Undo</button>', template)
        for forbidden in ("autofocus", "tabindex"):
            self.assertNotIn(forbidden, template)

    def test_the_live_region_starts_empty(self):
        self.assertIn('id="toast-region"></div>', self.get())


class Query(unittest.TestCase):
    def test_applications_come_from_the_applied_flag_newest_event_first(self):
        conn = mock.MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = ["x"]
        self.assertEqual(_db.get_applications(conn, 7), ["x"])
        sql, params = cur.execute.call_args.args
        self.assertEqual(params, (7,))
        self.assertIn("up.user_id = %s AND up.applied", sql)
        self.assertIn("event_type = 'applied'", sql)
        self.assertIn("ORDER BY applied_at DESC NULLS LAST", sql)
        self.assertNotIn("status = 'open'", sql)          # a role that has since closed is still something they applied to


if __name__ == "__main__":
    unittest.main()
