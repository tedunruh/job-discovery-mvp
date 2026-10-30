"""SC-63: coming back from a job. Which jobs to ask "Did you apply?" about (until answered), which rows say Viewed, the
Viewed list, and the routes behind them."""
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from dashboard import app as dapp
from dashboard.return_ask import choose_asks

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def ago(**kw):
    return NOW - timedelta(**kw)


class Rule(unittest.TestCase):
    def test_nothing_opened_asks_nothing(self):
        self.assertEqual(choose_asks({}, {}, set()), [])

    def test_the_one_opened_job_asks(self):
        self.assertEqual(choose_asks({7: ago(minutes=12)}, {}, set()), [7])

    def test_every_opened_job_asks_most_recent_first(self):
        opens = {1: ago(minutes=40), 2: ago(minutes=12), 3: ago(minutes=25)}
        self.assertEqual(choose_asks(opens, {}, set()), [2, 3, 1])

    def test_the_question_sticks_however_long_ago_the_job_was_opened(self):
        self.assertEqual(choose_asks({7: ago(days=80), 8: ago(days=2)}, {}, set()), [8, 7])

    def test_an_applied_or_hidden_job_is_not_asked_about_but_the_others_still_are(self):
        self.assertEqual(choose_asks({7: ago(minutes=5), 8: ago(minutes=50)}, {}, {7}), [8])

    def test_not_yet_answers_it_until_it_is_opened_again(self):
        opens = {7: ago(minutes=30)}
        self.assertEqual(choose_asks(opens, {7: ago(minutes=20)}, set()), [])                        # answered after opening
        self.assertEqual(choose_asks({7: ago(minutes=5)}, {7: ago(minutes=20)}, set()), [7])         # opened again since

    def test_after_not_yet_the_other_jobs_still_ask(self):
        opens = {1: ago(minutes=50), 2: ago(minutes=30)}
        self.assertEqual(choose_asks(opens, {2: ago(minutes=5)}, set()), [1])


def row(pid, title, hours_ago, applied=False):
    return {"id": pid, "title": title, "company_name": "Co", "ats_type": "ashby", "location": "Remote - United States",
            "remote_type": "remote", "url": "u", "is_design": True, "applied": applied,
            "first_seen_at": datetime.now(timezone.utc) - timedelta(hours=hours_ago)}


def hidden_row(pid, title, days_ago=1, reason=None):
    return {"id": pid, "title": title, "company_name": "Co", "ats_type": "ashby", "location": "Remote - United States",
            "remote_type": "remote", "hidden_at": datetime.now(timezone.utc) - timedelta(days=days_ago), "hidden_reason": reason}


class Base(unittest.TestCase):
    def setUp(self):
        self.client = dapp.app.test_client()
        self.client.set_cookie(dapp.SESSION_COOKIE, "tok")
        now = datetime.now(timezone.utc)
        self.now = now
        self.opens, self.dismissals, self.events, self.hidden = {}, {}, [], []
        self.rows = [row(1, "Senior Product Designer", 1), row(2, "Product Designer", 2), row(3, "UX Designer", 3)]
        self.exists = {1, 2, 3, 99}
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
            mock.patch.object(dapp, "get_applications", lambda c, u: []),
            mock.patch.object(dapp, "get_hidden", lambda c, u: [dict(h) for h in self.hidden]),
            mock.patch.object(dapp, "get_hidden_among", lambda c, u, ids: {h["id"] for h in self.hidden if h["id"] in ids}),
            mock.patch.object(dapp, "get_return_events", lambda c, u: (dict(self.opens), dict(self.dismissals))),
            mock.patch.object(dapp, "apply_fit", lambda conn, uid, profile, postings, shown: (0, [])),
            mock.patch.object(dapp, "get_postings_by_ids", lambda c, u, ids: [dict(r) for r in self.rows if r["id"] in ids]),
            mock.patch.object(dapp, "get_applied_among", lambda c, u, ids: {r["id"] for r in self.rows if r["applied"] and r["id"] in ids}),
            mock.patch.object(dapp, "get_posting_url", lambda c, pid: "https://jobs.example/x" if pid in self.exists else None),
            mock.patch.object(dapp, "log_event", lambda c, uid, kind, pid=None: self.events.append((kind, pid))),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def page(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        return r.get_data(as_text=True)

    def live(self):
        """The page as a person sees it: without the hidden <template> copies the script uses."""
        return re.sub(r"<template.*?</template>", "", self.page(), flags=re.S)

    def row_html(self, html, pid):
        return re.search(rf'<li class="fg-job" id="posting-{pid}">.*?</li>', html, re.S).group(0)


class Page(Base):
    def test_a_page_with_nothing_opened_shows_discovered_apply_and_hide_on_every_row(self):
        html = self.live()
        for pid in (1, 2, 3):
            r = self.row_html(html, pid)
            self.assertRegex(r, r'<p data-state-top>Discovered on Ashby</p>')
            self.assertRegex(r, r'<p class="fg-job-when" data-state-when>\d{1,2}:\d{2} [AP]M</p>')
            self.assertIn(f'href="/open/{pid}" target="_blank" rel="noopener" data-apply>Apply', r)
            self.assertIn(f'action="/hide/{pid}" data-hide', r)
            self.assertNotIn("Did you apply?", r)
            self.assertNotIn("data-viewed", r)

    def test_an_opened_job_asks_in_its_own_action_spot_without_hide(self):
        self.opens = {2: self.now - timedelta(minutes=12)}
        html = self.live()
        asking = self.row_html(html, 2)
        self.assertIn(">Did you apply?</p>", asking)
        self.assertIn('action="/set_applied/2"', asking)
        self.assertIn('name="applied" value="true"', asking)
        self.assertIn('action="/ask/dismiss/2"', asking)
        self.assertIn(">Yes</button>", asking)
        self.assertIn(">Not yet</button>", asking)
        self.assertIn("data-viewed", asking)
        self.assertNotIn("data-apply", asking)                       # the question replaces Apply and Hide in the same spot
        self.assertNotIn("data-hide", asking)                        # no Hide during the question
        for pid in (1, 3):
            self.assertNotIn("Did you apply?", self.row_html(html, pid))
            self.assertIn("data-apply", self.row_html(html, pid))

    def test_viewed_is_a_quiet_marker_after_the_discovery_time_with_no_date_of_its_own(self):
        self.opens = {3: self.now - timedelta(hours=5)}
        r = self.row_html(self.live(), 3)
        when = re.search(r'<p class="fg-job-when" data-state-when>(.*?)</p>', r, re.S).group(1)
        self.assertRegex(when, r"^\d{1,2}:\d{2} [AP]M<span class=\"fg-job-viewed\" data-viewed>")
        self.assertIn("Viewed</span>", when)
        self.assertNotIn("5 hours ago", when)                        # the discovery time, never the time it was viewed
        self.assertRegex(when, r'<svg(?![^>]*\shidden[\s>])[^>]*aria-hidden="true"')   # an icon plus the word, not colour alone

    def test_the_old_state_line_help_line_and_outline_are_gone(self):
        self.opens = {2: self.now - timedelta(minutes=12)}
        html = self.live()
        for gone in ("Welcome back", "If you did, mark it Applied", "posting-viewed", "row-ask", "posting--asking", "Viewed 12 minutes ago"):
            self.assertNotIn(gone, html)

    def test_every_opened_job_keeps_asking_until_it_is_answered(self):
        self.opens = {1: self.now - timedelta(minutes=40), 3: self.now - timedelta(minutes=25)}
        html = self.live()
        self.assertEqual(html.count(">Did you apply?</p>"), 2)
        for pid in (1, 3):
            self.assertIn("data-viewed", self.row_html(html, pid))
            self.assertNotIn("data-apply", self.row_html(html, pid))
        self.assertIn("data-apply", self.row_html(html, 2))               # the one never opened has no question
        self.assertNotIn("data-viewed", self.row_html(html, 2))

    def test_the_question_is_in_the_page_without_focus_or_blocking(self):
        self.opens = {2: self.now - timedelta(minutes=12)}
        html = self.live()
        asking = self.row_html(html, 2)
        for forbidden in ("autofocus", "tabindex", "role=\"dialog\"", "aria-modal"):
            self.assertNotIn(forbidden, asking)
        self.assertRegex(html, r'<p class="fg-visually-hidden" role="status" aria-live="polite" id="return-live"></p>')

    def test_the_question_is_still_there_days_later(self):
        self.opens = {2: self.now - timedelta(days=30)}
        html = self.live()
        self.assertIn("data-viewed", self.row_html(html, 2))
        self.assertIn(">Did you apply?</p>", self.row_html(html, 2))

    def test_not_yet_means_no_question_until_it_is_opened_again(self):
        self.opens = {2: self.now - timedelta(minutes=30)}
        self.dismissals = {2: self.now - timedelta(minutes=20)}
        html = self.live()
        self.assertNotIn("Did you apply?", html)
        self.assertIn("data-apply", self.row_html(html, 2))                  # Apply (and Hide) come back
        self.assertIn("data-hide", self.row_html(html, 2))
        self.assertIn("data-viewed", self.row_html(html, 2))                 # and the row still says Viewed
        self.opens = {2: self.now - timedelta(minutes=2)}
        self.assertIn("Did you apply?", self.live())

    def test_a_job_off_this_page_cannot_be_asked_about(self):
        self.opens = {50: self.now - timedelta(minutes=5)}           # opened, but not among the rows shown
        self.assertNotIn("Did you apply?", self.live())

    def test_an_applied_job_is_not_asked_about_and_is_not_in_open_roles(self):
        self.rows[1]["applied"] = True
        self.opens = {2: self.now - timedelta(minutes=5)}
        html = self.live()
        self.assertNotIn("Did you apply?", html)
        self.assertNotIn('id="posting-2"', html)

    def test_a_hidden_job_is_not_asked_about_and_is_not_in_open_roles(self):
        self.hidden = [hidden_row(2, "Product Designer", days_ago=2, reason="junior")]
        self.opens = {2: self.now - timedelta(minutes=5)}
        html = self.live()
        self.assertNotIn("Did you apply?", html)
        self.assertNotIn('id="posting-2"', html)

    def test_the_templates_the_script_copies_are_in_the_page(self):
        html = self.page()
        self.assertRegex(html, r'<template id="return-ask-template">\s*<p class="fg-voice fg-job-question">Did you apply\?</p>')
        self.assertIn('action="/ask/dismiss/__id__"', html)
        self.assertIn('action="/set_applied/__id__"', html)
        self.assertRegex(html, r'<template id="return-apply-template">\s*<span class="fg-job-group"><a class="fg-button fg-button--secondary" href="/open/__id__"')
        self.assertIn('action="/hide/__id__" data-hide', html)
        self.assertRegex(html, r'<template id="return-viewed-template">\s*<span class="fg-job-viewed" data-viewed>')
        self.assertIn('data-return-url="/return.json"', html)
        for script in ("toast.js", "job.js", "return.js"):
            self.assertIn(f'<script src="/field-guide/js/{script}" defer></script>', html)


class ViewedList(Base):
    def viewed_page(self):
        r = self.client.get("/?view=viewed")
        self.assertEqual(r.status_code, 200)
        return re.sub(r"<template.*?</template>", "", r.get_data(as_text=True), flags=re.S)

    def test_open_roles_links_to_it(self):
        self.assertIn('href="/?view=viewed" data-viewed-link>Viewed jobs</a>', self.live())

    def test_it_lists_only_opened_jobs_last_opened_first(self):
        self.opens = {1: self.now - timedelta(days=2), 3: self.now - timedelta(minutes=5)}
        html = self.viewed_page()
        self.assertIn("Jobs you viewed", html)
        self.assertEqual(re.findall(r'<li class="fg-job" id="posting-(\d+)">', html), ["3", "1"])
        self.assertIn("Opened 5 minutes ago", html)
        self.assertIn("Opened 2 days ago", html)
        self.assertNotIn("data-viewed>", html)                        # the line says when instead of the Viewed marker

    def test_it_ignores_the_posted_and_work_type_filters(self):
        self.rows[0]["first_seen_at"] = self.now - timedelta(days=40)  # long outside "Past 24 hours"
        self.rows[0]["location"], self.rows[0]["remote_type"] = "Austin, TX", "onsite"
        self.client.get("/?range=day")
        self.client.get("/?work_set=1&work=remote")
        self.opens = {1: self.now - timedelta(hours=1)}
        self.assertIn('id="posting-1"', self.viewed_page())

    def test_the_question_sticks_here_too_and_an_answered_job_has_apply(self):
        self.opens = {1: self.now - timedelta(minutes=40), 2: self.now - timedelta(minutes=10)}
        self.dismissals = {2: self.now - timedelta(minutes=5)}
        html = self.viewed_page()
        self.assertIn(">Did you apply?</p>", self.row_html(html, 1))
        self.assertIn("data-apply", self.row_html(html, 2))
        self.assertNotIn("Did you apply?", self.row_html(html, 2))

    def test_applied_and_hidden_jobs_are_not_listed(self):
        self.rows[0]["applied"] = True
        self.hidden = [hidden_row(2, "Product Designer")]
        self.opens = {1: self.now - timedelta(minutes=5), 2: self.now - timedelta(minutes=6), 3: self.now - timedelta(minutes=7)}
        html = self.viewed_page()
        self.assertEqual(re.findall(r'<li class="fg-job" id="posting-(\d+)">', html), ["3"])

    def test_a_job_that_closed_is_not_listed(self):
        self.opens = {3: self.now - timedelta(minutes=7), 77: self.now - timedelta(minutes=2)}
        self.assertEqual(re.findall(r'<li class="fg-job" id="posting-(\d+)">', self.viewed_page()), ["3"])

    def test_nothing_viewed_says_so(self):
        html = self.viewed_page()
        self.assertIn("Nothing viewed yet.", html)
        self.assertNotIn('class="fg-job"', html)

    def test_it_has_the_way_back_and_keeps_the_script_and_the_tabs(self):
        html = self.client.get("/?view=viewed").get_data(as_text=True)
        self.assertIn('<a class="fg-link" href="/">Back to open roles</a>', html)
        self.assertIn('data-return-url="/return.json"', html)
        self.assertRegex(html, r"Open roles[^<]*<[^>]*>\s*3")           # the tab still counts the open roles, not the viewed ones

    def test_the_applications_tab_ignores_the_view(self):
        self.assertNotIn("Jobs you viewed", self.client.get("/?tab=applications&view=viewed").get_data(as_text=True))


class State(Base):
    def test_it_reports_the_questions_and_what_was_viewed_as_json_that_is_never_cached(self):
        self.opens = {1: self.now - timedelta(minutes=40), 2: self.now - timedelta(minutes=12)}
        r = self.client.get("/return.json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        body = r.get_json()
        self.assertEqual(body["asks"], [{"id": 2}, {"id": 1}])
        self.assertEqual(body["viewed"], {"1": "40 minutes ago", "2": "12 minutes ago"})

    def test_nothing_to_ask_is_an_empty_list(self):
        self.assertEqual(self.client.get("/return.json").get_json(), {"asks": [], "viewed": {}})

    def test_applied_jobs_are_neither_asked_about_nor_listed_as_viewed(self):
        self.rows[1]["applied"] = True
        self.opens = {2: self.now - timedelta(minutes=5), 1: self.now - timedelta(minutes=50)}
        body = self.client.get("/return.json").get_json()
        self.assertEqual(body["asks"], [{"id": 1}])
        self.assertEqual(list(body["viewed"]), ["1"])

    def test_it_needs_a_sign_in(self):
        anonymous = dapp.app.test_client()
        with mock.patch.object(dapp, "get_conn", lambda: mock.MagicMock()), mock.patch.object(dapp, "get_session_user", lambda c, t: (None, False)):
            r = anonymous.get("/return.json")
        self.assertEqual(r.status_code, 302)


class NotYet(Base):
    def test_the_script_posts_and_gets_a_quiet_answer(self):
        r = self.client.post("/ask/dismiss/2", headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.events, [("ask_dismissed", 2)])

    def test_without_the_script_it_returns_to_the_same_row(self):
        r = self.client.post("/ask/dismiss/2")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("#posting-2"))
        self.assertEqual(self.events, [("ask_dismissed", 2)])

    def test_an_unknown_job_records_nothing(self):
        self.client.post("/ask/dismiss/12345", headers={"X-Requested-With": "fetch"})
        self.assertEqual(self.events, [])

    def test_a_plain_visit_to_that_address_records_nothing(self):
        self.client.get("/ask/dismiss/2")
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
