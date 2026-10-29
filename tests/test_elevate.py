"""SC-69: the Open roles view with Scout's character: a line in Scout's voice, jobs grouped by day with short times,
company monograms, and a warmer caught-up state."""
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from dashboard import app as dapp
from dashboard.elevate import DAY_LABELS, day_label, group_by_day, monogram, rows_by_day, scout_line, short_time, tint

# Wednesday 28 Oct 2026, 1:30 PM in US Central (18:30 UTC)
NOW = datetime(2026, 10, 28, 18, 30, tzinfo=timezone.utc)


def at(**kw):
    return NOW - timedelta(**kw)


class Days(unittest.TestCase):
    def test_labels_follow_the_calendar_day_in_central_not_the_hours_since(self):
        self.assertEqual(day_label(at(hours=1), NOW), "Today")
        self.assertEqual(day_label(datetime(2026, 10, 28, 4, 59, tzinfo=timezone.utc), NOW), "Yesterday")   # 11:59 PM Tue Central
        self.assertEqual(day_label(datetime(2026, 10, 28, 5, 1, tzinfo=timezone.utc), NOW), "Today")        # 12:01 AM Wed Central
        self.assertEqual(day_label(at(days=1, hours=2), NOW), "Yesterday")
        # NOW is Wednesday 28 Oct 2026: this week began Sunday 25 Oct, last week was 18 to 24 Oct
        self.assertEqual(day_label(at(days=2), NOW), "Earlier this week")       # Mon 26
        self.assertEqual(day_label(at(days=3), NOW), "Earlier this week")       # Sun 25, the first day of this week
        self.assertEqual(day_label(at(days=4), NOW), "Last week")               # Sat 24, the last day of last week
        self.assertEqual(day_label(at(days=10), NOW), "Last week")              # Sun 18, the first day of last week
        self.assertEqual(day_label(at(days=11), NOW), "Earlier")                # Sat 17
        self.assertEqual(day_label(at(days=40), NOW), "Earlier")

    def test_a_sunday_and_a_monday_start_the_week_in_the_right_place(self):
        monday = datetime(2026, 10, 26, 18, 30, tzinfo=timezone.utc)            # Monday: yesterday was Sunday
        self.assertEqual(day_label(monday - timedelta(days=1), monday), "Yesterday")
        self.assertEqual(day_label(monday - timedelta(days=2), monday), "Last week")     # Saturday belongs to last week
        sunday = datetime(2026, 10, 25, 18, 30, tzinfo=timezone.utc)
        self.assertEqual(day_label(sunday - timedelta(days=1), sunday), "Yesterday")
        self.assertEqual(day_label(sunday - timedelta(days=2), sunday), "Last week")

    def test_a_job_found_late_in_the_evening_is_still_today_in_the_evening(self):
        late = datetime(2026, 10, 29, 3, 30, tzinfo=timezone.utc)   # 10:30 PM Wed Central, already Thursday in UTC
        self.assertEqual(day_label(late, datetime(2026, 10, 29, 4, 0, tzinfo=timezone.utc)), "Today")

    def test_short_times(self):
        self.assertEqual(short_time(datetime(2026, 10, 28, 14, 40, tzinfo=timezone.utc), NOW), "9:40 AM")
        self.assertEqual(short_time(datetime(2026, 10, 27, 21, 30, tzinfo=timezone.utc), NOW), "4:30 PM")     # yesterday: a time
        self.assertEqual(short_time(datetime(2026, 10, 26, 18, 0, tzinfo=timezone.utc), NOW), "Mon")          # earlier this week: the day
        self.assertEqual(short_time(datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc), NOW), "Wed")          # last week: the day
        self.assertEqual(short_time(datetime(2026, 10, 14, 18, 0, tzinfo=timezone.utc), NOW), "Oct 14")       # older: the date

    def test_groups_count_every_matching_job_not_just_the_page(self):
        every = [{"first_seen_at": at(hours=1)}] * 5 + [{"first_seen_at": at(days=1, hours=3)}] * 2
        counts = group_by_day(every, NOW)
        self.assertEqual(counts, {"Today": 5, "Yesterday": 2})
        page = every[:3]
        self.assertEqual([(l, c, len(r)) for l, c, r in rows_by_day(page, counts, NOW)], [("Today", 5, 3)])

    def test_groups_come_in_the_order_of_the_headings(self):
        rows = [{"first_seen_at": at(days=20)}, {"first_seen_at": at(days=7)}, {"first_seen_at": at(hours=1)}, {"first_seen_at": at(days=3)}, {"first_seen_at": at(days=1, hours=2)}]
        rows.sort(key=lambda r: r["first_seen_at"], reverse=True)
        counts = group_by_day(rows, NOW)
        self.assertEqual([g[0] for g in rows_by_day(rows, counts, NOW)], list(DAY_LABELS))


class Monogram(unittest.TestCase):
    def test_initials(self):
        for name, want in [("Juniper Labs", "JL"), ("Northwind Health", "NH"), ("Docker", "DO"), ("Harbor & Co.", "HA"),
                           ("The Trade Desk", "TD"), ("ACLU - Internships", "AI"), ("3M", "3M"), ("", "")]:
            self.assertEqual(monogram(name), want, name)

    def test_the_same_tint_every_time_for_a_company_whatever_its_case(self):
        self.assertEqual(tint("Juniper Labs"), tint("Juniper Labs"))
        for name in [f"Company {i} Labs" for i in range(30)]:    # many names, so a chance match cannot hide a case-sensitive tint
            self.assertEqual(tint(name), tint("  " + name.upper() + " "), name)
        self.assertIn(tint("Anything"), ("sage", "gold", "clay", "forest"))

    def test_all_four_tints_are_used(self):
        names = [f"Company {i}" for i in range(60)]
        self.assertEqual({tint(n) for n in names}, {"sage", "gold", "clay", "forest"})


class Line(unittest.TestCase):
    def test_scouts_line_is_built_from_real_counts(self):
        self.assertEqual(scout_line(3, 12, False), "3 new since yesterday.")
        self.assertEqual(scout_line(1, 12, False), "1 new since yesterday.")
        self.assertEqual(scout_line(0, 12, False), "Nothing new since yesterday.")
        self.assertEqual(scout_line(0, 0, True), "You’re caught up. I’ll keep looking.")

    def test_a_filter_that_empties_the_list_has_no_line_to_say(self):
        self.assertEqual(scout_line(0, 0, False), "")


def row(pid, title, when, company="Northwind Health"):
    return {"id": pid, "title": title, "company_name": company, "ats_type": "ashby", "location": "Remote - United States",
            "remote_type": "remote", "url": "u", "is_design": True, "applied": False, "first_seen_at": when}


class Fixed(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)


class Page(unittest.TestCase):
    def setUp(self):
        self.client = dapp.app.test_client()
        self.client.set_cookie(dapp.SESSION_COOKIE, "tok")
        self.rows = [row(1, "Senior Product Designer", at(hours=2), "Juniper Labs"), row(2, "Product Designer", at(hours=3), "Northwind Health"),
                     row(3, "UX Designer", at(days=1, hours=1), "Fieldstone"), row(4, "Lead Designer", at(days=3), "Brightline"),
                     row(5, "Principal Designer", at(days=12), "Larkspur")]
        self.work, self.hidden = [], []
        profile = {"titles": ["designer"], "level": None, "one_level_up": False, "city": None, "skills": []}
        patches = [
            mock.patch.object(dapp, "datetime", Fixed),
            mock.patch.object(dapp, "get_conn", lambda: mock.MagicMock()),
            mock.patch.object(dapp, "get_session_user", lambda c, t: (7, False)),
            mock.patch.object(dapp, "is_onboarded", lambda c, u: True),
            mock.patch.object(dapp, "log_visit", lambda c, u: None),
            mock.patch.object(dapp, "get_user_role_categories", lambda c, u: set()),
            mock.patch.object(dapp, "get_user_keywords", lambda c, u: []),
            mock.patch.object(dapp, "get_user_profile", lambda c, u: dict(profile)),
            mock.patch.object(dapp, "get_user_work_types", lambda c, u: self.work),
            mock.patch.object(dapp, "get_open_postings", lambda c, u, since=None, keywords=(): [dict(r) for r in self.rows]),
            mock.patch.object(dapp, "get_applications", lambda c, u: []),
            mock.patch.object(dapp, "get_hidden", lambda c, u: [dict(h) for h in self.hidden]),
            mock.patch.object(dapp, "get_return_events", lambda c, u: ({}, {})),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def get(self, url="/"):
        r = self.client.get(url, follow_redirects=True)     # /?range=all sets the filter, then lands on /
        self.assertEqual(r.status_code, 200)
        return re.sub(r"<template.*?</template>", "", r.get_data(as_text=True), flags=re.S)

    def test_scouts_line_sits_above_the_board_in_the_serif_voice(self):
        html = self.get("/?range=all")
        self.assertRegex(html, r'<p class="fg-voice voice-intro" data-voice>2 new since yesterday\.</p>')
        self.assertLess(html.index("data-voice"), html.index('<section class="board"'))

    def test_the_line_is_only_on_open_roles(self):
        self.assertNotIn("data-voice", self.get("/?tab=applications"))
        self.assertNotIn("data-voice", self.get("/?view=hidden"))

    def test_jobs_are_grouped_by_day_with_a_true_count_and_a_hairline(self):
        html = self.get("/?range=all")
        heads = re.findall(r'<h2 class="fg-day-label">([^<]+)<span class="fg-day-count">· (\d+)</span></h2>', html.replace("</h2>", "</h2>"))
        self.assertEqual([(h.strip(), c) for h, c in heads], [("Today", "2"), ("Yesterday", "1"), ("Earlier this week", "1"), ("Earlier", "1")])
        self.assertEqual(html.count('class="fg-day-rule" aria-hidden="true"'), 4)
        self.assertEqual(html.count('<section class="fg-day"'), 4)

    def test_last_week_has_its_own_group(self):
        self.rows = [row(1, "Lead Designer", at(days=5)), row(2, "Product Designer", at(days=10)), row(3, "UX Designer", at(days=11))]
        heads = re.findall(r'<h2 class="fg-day-label">([^<]+)<span class="fg-day-count">· (\d+)</span></h2>', self.get("/?range=all"))
        self.assertEqual([(h.strip(), c) for h, c in heads], [("Last week", "2"), ("Earlier", "1")])

    def test_each_group_holds_its_own_jobs_in_the_order_found(self):
        html = self.get("/?range=all")
        today = re.search(r'<section class="fg-day" aria-label="Today">(.*?)</section>', html, re.S).group(1)
        self.assertEqual(re.findall(r'<a class="fg-job-link"[^>]*>([^<]+)</a>', today), ["Senior Product Designer", "Product Designer"])

    def test_times_are_short(self):
        html = self.get("/?range=all")
        times = re.findall(r'<p class="fg-job-when" data-state-when>([^<]+)', html)
        self.assertEqual(times, ["11:30 AM", "10:30 AM", "12:30 PM", "Sun", "Oct 16"])

    def test_every_card_has_a_decorative_monogram_with_a_steady_tint(self):
        html = self.get("/?range=all")
        marks = re.findall(r'<span class="fg-mono fg-mono--(\w+)" aria-hidden="true">(\w+)</span>', html)
        self.assertEqual([m for _, m in marks], ["JL", "NH", "FI", "BR", "LA"])
        self.assertEqual(marks[0][0], tint("Juniper Labs"))

    def test_no_fit_line_on_rows_until_the_score_exists(self):
        html = self.get("/?range=all")
        self.assertNotIn("fg-fit", html.split("</style>")[-1])
        self.assertNotIn("fg-job-fit", html.split("</style>")[-1])

    def test_applications_and_the_hidden_list_get_monograms_too(self):
        self.hidden = [{"id": 9, "title": "Old", "company_name": "Copperline", "ats_type": "lever", "location": "Austin, TX", "remote_type": None,
                        "hidden_at": at(days=2), "hidden_reason": "pay"}]
        self.assertIn('<span class="fg-mono fg-mono--', self.get("/?view=hidden"))

    def test_caught_up_says_so_with_a_contour_and_scouts_line(self):
        self.rows = []
        self.work = ["remote", "hybrid", "onsite"]          # every work type: nothing is filtered out
        html = self.get("/?range=all")
        self.assertIn("You’re caught up. I’ll keep looking.", html)
        self.assertIn("You've been through everything new.", html)
        self.assertIn("I keep checking the boards, and I'll put anything that fits right here.", html)
        self.assertRegex(html, r'<span class="contour" aria-hidden="true">\s*<svg')

    def test_an_empty_list_caused_by_a_filter_is_not_called_caught_up(self):
        self.rows = []
        html = self.get("/")                                 # the default range is a week: a narrower view, not "everything"
        self.assertNotIn("caught up", html.lower())
        self.assertNotIn("data-caught-up", html)
        self.assertNotIn("data-voice", html)
        self.assertIn("No roles discovered", html)

    def test_a_quiet_day_says_so(self):
        self.rows = [row(5, "Lead Designer", at(days=3))]
        self.assertIn("Nothing new since yesterday.", self.get("/?range=all"))


class Tokens(unittest.TestCase):
    def test_the_title_and_monogram_sizes_are_tokens_not_loose_pixels(self):
        css = open("field-guide/dist/tokens.css").read()
        for token, value in (("--fg-font-size-title", "18px"), ("--fg-size-mono", "40px"), ("--fg-size-mono-phone", "36px")):
            self.assertIn(f"{token}: {value};", css)
        job = open("field-guide/css/components.css").read()
        job = job[job.index("---------- Job card"):]
        self.assertNotRegex(job, r"font-size:\s*\d+px")


if __name__ == "__main__":
    unittest.main()
