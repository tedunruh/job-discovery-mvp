"""SC-58: jobs marked applied leave the main list; the Status filter brings them back; the job just marked stays in
place for one more page load so the click can be undone."""
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from dashboard import app as dapp


def row(pid, title, hours_ago, applied=False):
    return {"id": pid, "title": title, "company_name": "Co", "ats_type": "ashby", "location": "Remote - United States",
            "remote_type": "remote", "url": "u", "is_design": True, "applied": applied,
            "first_seen_at": datetime.now(timezone.utc) - timedelta(hours=hours_ago)}


class Base(unittest.TestCase):
    count = 4

    def setUp(self):
        self.client = dapp.app.test_client()
        self.client.set_cookie(dapp.SESSION_COOKIE, "tok")
        titles = ["Senior Product Designer", "Product Designer", "UX Designer", "Staff Product Designer"]
        self.applied = {}                                    # posting id -> bool, the database's user_postings
        self.rows = [(i + 1, titles[i % 4] + ("" if i < 4 else f" {i}"), i + 1) for i in range(self.count)]
        self.events = []
        profile = {"titles": ["designer"], "level": None, "one_level_up": False, "city": None, "skills": []}

        def open_postings(c, u, since=None, keywords=()):
            return [row(i, t, h, self.applied.get(i, False)) for i, t, h in self.rows]

        def set_applied(c, pid, flag, uid):
            self.applied[pid] = flag

        patches = [
            mock.patch.object(dapp, "get_conn", lambda: mock.MagicMock()),
            mock.patch.object(dapp, "get_session_user", lambda c, t: (7, False)),
            mock.patch.object(dapp, "is_onboarded", lambda c, u: True),
            mock.patch.object(dapp, "log_visit", lambda c, u: None),
            mock.patch.object(dapp, "get_user_role_categories", lambda c, u: set()),
            mock.patch.object(dapp, "get_user_keywords", lambda c, u: []),
            mock.patch.object(dapp, "get_user_profile", lambda c, u: dict(profile)),
            mock.patch.object(dapp, "get_user_work_types", lambda c, u: []),
            mock.patch.object(dapp, "get_open_postings", open_postings),
            mock.patch.object(dapp, "set_applied", set_applied),
            mock.patch.object(dapp, "log_event", lambda c, uid, kind, pid=None: self.events.append((kind, pid))),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def page(self, query="/", follow=True):
        r = self.client.get(query, follow_redirects=follow)
        self.assertEqual(r.status_code, 200)
        return r.get_data(as_text=True)

    def listed(self, html):
        """(title, is the 'just applied' kind) for each row, in order."""
        out = []
        for li in re.findall(r'<li class="posting[^"]*" id="posting-\d+">.*?</li>', html, re.S):
            title = re.search(r'class="posting-title">(?:<a[^>]*>)?([^<]+)', li).group(1).strip()
            out.append((title, "posting--done" in li.split(">")[0]))
        return out

    def mark(self, pid, flag=True, **extra):
        return self.client.post(f"/set_applied/{pid}", data=dict({"applied": "true" if flag else "false"}, **extra))


class Statuses(Base):
    def test_by_default_applied_jobs_are_not_in_the_list_or_the_count(self):
        self.applied = {2: True}
        html = self.page()
        self.assertEqual([t for t, _ in self.listed(html)], ["Senior Product Designer", "UX Designer", "Staff Product Designer"])
        self.assertIn("3 open roles", html)

    def test_the_applied_view_shows_only_applied_jobs_with_the_toggle_checked(self):
        self.applied = {2: True, 4: True}
        html = self.page("/?status=applied")
        self.assertEqual([t for t, _ in self.listed(html)], ["Product Designer", "Staff Product Designer"])
        self.assertIn("2 open roles", html)
        self.assertEqual(html.count('aria-pressed="true"'), 2)

    def test_all_shows_everything(self):
        self.applied = {2: True}
        self.assertEqual(len(self.listed(self.page("/?status=all"))), 4)

    def test_the_applied_view_with_nothing_applied_says_how_to_fill_it(self):
        html = self.page("/?status=applied")
        self.assertIn("haven't marked any of these roles as applied yet", html)
        self.assertEqual(self.listed(html), [])

    def test_the_filter_offers_the_three_views_with_the_current_one_checked(self):
        html = self.page()
        self.assertRegex(html, r'fg-filter-label">Status</span>\s*<span class="fg-filter-value">Not applied yet</span>')
        self.assertRegex(html, r'name="status" value="open" onchange="this.form.submit\(\)" checked')
        for key, label in (("open", "Not applied yet"), ("applied", "Applied"), ("all", "All")):
            self.assertRegex(html, rf'name="status" value="{key}"[^>]*>\s*{label}\s*</label>')
        self.assertRegex(self.page("/?status=all"), r'fg-filter-label">Status</span>\s*<span class="fg-filter-value">All</span>')

    def test_the_choice_is_remembered_like_the_other_filters(self):
        self.applied = {2: True}
        self.page("/?status=applied")
        self.assertEqual([t for t, _ in self.listed(self.page("/"))], ["Product Designer"])

    def test_an_unknown_status_changes_nothing(self):
        self.applied = {2: True}
        html = self.page("/?status=nonsense")
        self.assertEqual(len(self.listed(html)), 3)

    def test_an_unknown_status_does_not_undo_the_view_you_chose(self):
        self.applied = {2: True}
        self.page("/?status=applied")
        self.page("/?status=nonsense")
        self.assertEqual([t for t, _ in self.listed(self.page("/"))], ["Product Designer"])


class MarkingApplied(Base):
    def test_the_job_stays_in_place_checked_and_saying_so_for_one_page_load(self):
        r = self.mark(2)
        self.assertEqual(r.status_code, 302)
        self.assertIn("posting-2", r.headers["Location"])          # back to the same row
        html = self.page("/")
        rows = self.listed(html)
        self.assertEqual([t for t, _ in rows], ["Senior Product Designer", "Product Designer", "UX Designer", "Staff Product Designer"])
        self.assertEqual([done for _, done in rows], [False, True, False, False])
        self.assertIn("Applied just now", html)
        done_li = re.search(r'<li class="posting posting--done".*?</li>', html, re.S).group(0)
        self.assertIn('aria-pressed="true"', done_li)
        self.assertIn('name="applied" value="false"', done_li)       # clicking it again is the undo
        self.assertNotIn("<a ", done_li)                              # a kept row is not a link to the posting
        self.assertIn("3 open roles", html)                           # it is already out of the count

    def test_it_is_gone_on_the_next_refresh(self):
        self.mark(2)
        self.page("/")
        html = self.page("/")
        self.assertEqual([t for t, _ in self.listed(html)], ["Senior Product Designer", "UX Designer", "Staff Product Designer"])
        self.assertNotIn("Applied just now", html)

    def test_changing_a_filter_settles_it(self):
        self.mark(2)
        html = self.page("/?range=week")
        self.assertNotIn("Applied just now", html)
        self.assertEqual(len(self.listed(html)), 3)

    def test_unchecking_it_is_the_undo_and_the_job_is_back_in_its_place(self):
        self.mark(2)
        self.page("/")
        r = self.mark(2, flag=False)
        self.assertIn("posting-2", r.headers["Location"])
        html = self.page("/")
        self.assertEqual([t for t, _ in self.listed(html)], ["Senior Product Designer", "Product Designer", "UX Designer", "Staff Product Designer"])
        self.assertNotIn("Applied just now", html)
        self.assertFalse(self.applied[2])

    def test_a_new_answer_settles_the_previous_one(self):
        self.mark(2)
        self.mark(3)
        html = self.page("/")
        self.assertEqual([t for t, _ in self.listed(html)], ["Senior Product Designer", "UX Designer", "Staff Product Designer"])
        self.assertEqual([d for _, d in self.listed(html)], [False, True, False])

    def test_unmarking_a_different_job_also_settles_the_kept_row(self):
        self.applied = {4: True}                                    # an older application, seen in the Applied view
        self.mark(2)                                                # kept in place
        self.mark(4, flag=False)                                    # a new answer: unmarking another job
        html = self.page("/")
        self.assertEqual(self.listed(html), [("Senior Product Designer", False), ("UX Designer", False),
                                              ("Staff Product Designer", False)])   # job 2 left; job 4 is back, unmarked
        self.assertNotIn("Applied just now", html)

    def test_it_is_saved_for_the_person_and_logged(self):
        self.mark(3)
        self.assertTrue(self.applied[3])
        self.assertEqual(self.events, [("applied", 3)])
        self.mark(3, flag=False)
        self.assertEqual(self.events, [("applied", 3)])             # unmarking logs nothing new

    def test_marking_in_the_all_view_keeps_the_row_as_a_normal_checked_row(self):
        self.page("/?status=all")
        self.mark(2)
        html = self.page("/")
        self.assertEqual([d for _, d in self.listed(html)], [False] * 4)  # nothing is hiding, so nothing needs confirming
        self.assertEqual(len(self.listed(html)), 4)
        self.assertEqual(html.count('aria-pressed="true"'), 1)


class KeepingYourPlace(Base):
    count = 60

    def test_the_form_carries_how_many_you_were_looking_at_and_the_redirect_keeps_it(self):
        html = self.page("/?n=100")
        self.assertIn('name="n" value="100"', html)
        r = self.mark(2, n="100", skills="all")
        loc = r.headers["Location"]
        self.assertIn("n=100", loc)
        self.assertIn("skills=all", loc)
        self.assertTrue(loc.endswith("#posting-2"))

    def test_the_first_page_has_no_extra_field_and_the_limit_is_capped(self):
        self.assertNotIn('name="n"', self.page("/"))
        self.assertIn("n=1000", self.mark(2, n="999999").headers["Location"])

    def test_the_show_more_line_counts_the_roles_not_the_kept_row(self):
        self.mark(2)
        self.assertIn("Showing 50 of 59", self.page("/"))


if __name__ == "__main__":
    unittest.main()
