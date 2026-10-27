"""SC-63: coming back from a job. Which job to ask "Did you apply?" about, which rows say Viewed, and the routes behind
them."""
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from dashboard import app as dapp
from dashboard.return_ask import ASK_WINDOW, choose_ask

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def ago(**kw):
    return NOW - timedelta(**kw)


class Rule(unittest.TestCase):
    def test_nothing_opened_asks_nothing(self):
        self.assertIsNone(choose_ask({}, {}, set(), NOW))

    def test_the_one_opened_job_asks(self):
        self.assertEqual(choose_ask({7: ago(minutes=12)}, {}, set(), NOW), 7)

    def test_only_the_latest_opened_job_asks(self):
        opens = {1: ago(minutes=40), 2: ago(minutes=12), 3: ago(minutes=25)}
        self.assertEqual(choose_ask(opens, {}, set(), NOW), 2)

    def test_the_window_edge(self):
        self.assertIsNone(choose_ask({7: NOW - ASK_WINDOW - timedelta(minutes=1)}, {}, set(), NOW))
        self.assertEqual(choose_ask({7: NOW - ASK_WINDOW + timedelta(minutes=1)}, {}, set(), NOW), 7)

    def test_an_old_job_does_not_step_in_when_the_latest_is_too_old(self):
        opens = {1: ago(days=3), 2: ago(days=2)}
        self.assertIsNone(choose_ask(opens, {}, set(), NOW))

    def test_an_applied_job_is_not_asked_about_and_the_next_one_does_not_step_in(self):
        self.assertIsNone(choose_ask({7: ago(minutes=5)}, {}, {7}, NOW))
        self.assertIsNone(choose_ask({7: ago(minutes=5), 8: ago(minutes=50)}, {}, {7}, NOW))

    def test_not_yet_holds_until_it_is_opened_again(self):
        opens = {7: ago(minutes=30)}
        self.assertIsNone(choose_ask(opens, {7: ago(minutes=20)}, set(), NOW))                       # answered after opening
        self.assertEqual(choose_ask({7: ago(minutes=5)}, {7: ago(minutes=20)}, set(), NOW), 7)       # opened again since

    def test_after_not_yet_the_next_older_job_does_not_step_in(self):
        opens = {1: ago(minutes=50), 2: ago(minutes=30)}
        self.assertIsNone(choose_ask(opens, {2: ago(minutes=5)}, set(), NOW))


def row(pid, title, hours_ago, applied=False):
    return {"id": pid, "title": title, "company_name": "Co", "ats_type": "ashby", "location": "Remote - United States",
            "remote_type": "remote", "url": "u", "is_design": True, "applied": applied,
            "first_seen_at": datetime.now(timezone.utc) - timedelta(hours=hours_ago)}


class Base(unittest.TestCase):
    def setUp(self):
        self.client = dapp.app.test_client()
        self.client.set_cookie(dapp.SESSION_COOKIE, "tok")
        now = datetime.now(timezone.utc)
        self.now = now
        self.opens, self.dismissals, self.events = {}, {}, []
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
            mock.patch.object(dapp, "get_return_events", lambda c, u: (dict(self.opens), dict(self.dismissals))),
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
    def test_a_page_with_nothing_opened_shows_discovered_and_apply_on_every_row(self):
        html = self.live()
        for pid in (1, 2, 3):
            r = self.row_html(html, pid)
            self.assertRegex(r, r'<p class="fg-job-state" data-state>Discovered \d+ hours? ago</p>')
            self.assertIn(f'href="/open/{pid}" target="_blank" rel="noopener" data-apply>Apply', r)
            self.assertNotIn("Did you apply?", r)

    def test_the_job_opened_last_asks_in_its_own_action_spot_and_says_viewed(self):
        self.opens = {2: self.now - timedelta(minutes=12)}
        html = self.live()
        asking = self.row_html(html, 2)
        self.assertIn(">Did you apply?</p>", asking)
        self.assertIn('action="/set_applied/2"', asking)
        self.assertIn('name="applied" value="true"', asking)
        self.assertIn('action="/ask/dismiss/2"', asking)
        self.assertIn(">Yes</button>", asking)
        self.assertIn(">Not yet</button>", asking)
        self.assertIn('data-state>Viewed 12 minutes ago</p>', asking)
        self.assertNotIn("data-apply", asking)                       # the question replaces Apply in the same spot
        for pid in (1, 3):
            self.assertNotIn("Did you apply?", self.row_html(html, pid))
            self.assertIn("data-apply", self.row_html(html, pid))

    def test_the_old_welcome_sentence_help_line_and_viewed_icon_are_gone(self):
        self.opens = {2: self.now - timedelta(minutes=12)}
        html = self.live()
        for gone in ("Welcome back", "If you did, mark it Applied", "posting-viewed", "row-ask", "posting--asking"):
            self.assertNotIn(gone, html)

    def test_several_opened_only_the_latest_asks_and_the_rest_say_viewed(self):
        self.opens = {1: self.now - timedelta(minutes=40), 2: self.now - timedelta(minutes=12), 3: self.now - timedelta(minutes=25)}
        html = self.live()
        self.assertEqual(html.count(">Did you apply?</p>"), 1)
        self.assertIn("Viewed 40 minutes ago", self.row_html(html, 1))
        self.assertIn("Viewed 25 minutes ago", self.row_html(html, 3))
        for pid in (1, 3):
            self.assertIn("data-apply", self.row_html(html, pid))

    def test_the_question_is_in_the_page_without_focus_or_blocking(self):
        self.opens = {2: self.now - timedelta(minutes=12)}
        html = self.live()
        asking = self.row_html(html, 2)
        for forbidden in ("autofocus", "tabindex", "role=\"dialog\"", "aria-modal"):
            self.assertNotIn(forbidden, asking)
        self.assertRegex(html, r'<p class="fg-visually-hidden" role="status" aria-live="polite" id="return-live"></p>')

    def test_a_job_opened_long_ago_is_viewed_but_not_asked_about(self):
        self.opens = {2: self.now - timedelta(days=3)}
        html = self.live()
        self.assertIn("Viewed 3 days ago", self.row_html(html, 2))
        self.assertNotIn("Did you apply?", html)

    def test_not_yet_means_no_question_until_it_is_opened_again(self):
        self.opens = {2: self.now - timedelta(minutes=30)}
        self.dismissals = {2: self.now - timedelta(minutes=20)}
        html = self.live()
        self.assertNotIn("Did you apply?", html)
        self.assertIn("data-apply", self.row_html(html, 2))                  # Apply comes back
        self.assertIn("Viewed 30 minutes ago", self.row_html(html, 2))       # and the row still says Viewed
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

    def test_the_templates_the_script_copies_are_in_the_page(self):
        html = self.page()
        self.assertRegex(html, r'<template id="return-ask-template">\s*<p class="fg-voice fg-job-question">Did you apply\?</p>')
        self.assertIn('action="/ask/dismiss/__id__"', html)
        self.assertIn('action="/set_applied/__id__"', html)
        self.assertRegex(html, r'<template id="return-apply-template">\s*<a class="fg-button fg-button--secondary" href="/open/__id__"')
        self.assertIn('data-return-url="/return.json"', html)
        self.assertIn('<script src="/field-guide/js/return.js" defer></script>', html)
        self.assertIn('<script src="/field-guide/js/toast.js" defer></script>', html)


class State(Base):
    def test_it_reports_the_question_and_what_was_viewed_as_json_that_is_never_cached(self):
        self.opens = {1: self.now - timedelta(minutes=40), 2: self.now - timedelta(minutes=12)}
        r = self.client.get("/return.json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        body = r.get_json()
        self.assertEqual(body["ask"], {"id": 2})
        self.assertEqual(body["viewed"], {"1": "40 minutes ago", "2": "12 minutes ago"})

    def test_nothing_to_ask_is_null(self):
        self.assertEqual(self.client.get("/return.json").get_json(), {"ask": None, "viewed": {}})

    def test_applied_jobs_are_neither_asked_about_nor_listed_as_viewed(self):
        self.rows[1]["applied"] = True
        self.opens = {2: self.now - timedelta(minutes=5), 1: self.now - timedelta(minutes=50)}
        body = self.client.get("/return.json").get_json()
        self.assertIsNone(body["ask"])
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
