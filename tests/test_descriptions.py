"""Offline tests for job descriptions (SC-51): HTML to text, each platform's shape, the fetch budget, and the
Retry-After handling in the shared retry helper. No network."""
import threading
import unittest
from unittest import mock

import requests

from collectors import descriptions as d
from collectors import http


class Response:
    def __init__(self, data=None, status=200, headers=None):
        self._data, self.status_code, self.headers = data, status, headers or {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)


def http_error(status):
    return requests.HTTPError(f"{status} from x", response=Response(status=status))


class HtmlToText(unittest.TestCase):
    def test_tags_become_readable_lines_and_list_items(self):
        text = d.html_to_text("<p>We design <b>things</b>.</p><ul><li>Figma</li><li>Research</li></ul><p>Apply.</p>")
        self.assertEqual(text, "We design things.\n\n- Figma\n- Research\n\nApply.")

    def test_a_list_item_that_wraps_its_text_in_a_paragraph_keeps_its_bullet(self):
        self.assertEqual(d.html_to_text("<ul><li><p>One</p></li><li><p>Two</p></li></ul>"), "- One\n- Two")

    def test_a_lone_dash_in_prose_is_left_alone(self):
        self.assertEqual(d.html_to_text("<p>Pay: $90k - $120k</p>"), "Pay: $90k - $120k")

    def test_entity_escaped_html_from_greenhouse_is_decoded_first(self):
        text = d.html_to_text("&lt;p&gt;Build &amp;amp; ship&lt;/p&gt;&lt;ul&gt;&lt;li&gt;React&lt;/li&gt;&lt;/ul&gt;")
        self.assertEqual(text, "Build & ship\n\n- React")

    def test_real_markup_is_not_double_decoded(self):
        self.assertEqual(d.html_to_text("<p>5 &lt; 6 and R&amp;D</p>"), "5 < 6 and R&D")

    def test_scripts_and_styles_are_dropped(self):
        self.assertEqual(d.html_to_text("<style>p{color:red}</style><p>Hello</p><script>alert(1)</script>"), "Hello")

    def test_whitespace_and_non_breaking_spaces_collapse(self):
        self.assertEqual(d.html_to_text("<p>a\xa0\xa0  b</p>\n\n\n<p>  c  </p>"), "a b\n\nc")

    def test_empty_and_none(self):
        for value in ("", None, "   ", "<p></p>"):
            self.assertEqual(d.html_to_text(value), "")

    def test_cap(self):
        self.assertEqual(len(d.html_to_text("<p>" + "x" * 50000 + "</p>")), d.MAX_CHARS)
        self.assertEqual(len(d.html_to_text("<p>" + "x" * 500 + "</p>", limit=100)), 100)


class InListPlatforms(unittest.TestCase):
    def test_ashby_uses_the_plain_text_and_falls_back_to_html(self):
        self.assertEqual(d.from_list_payload("ashby", {"descriptionPlain": "Plain role text"}), "Plain role text")
        self.assertEqual(d.from_list_payload("ashby", {"descriptionHtml": "<p>Html <b>text</b></p>"}), "Html text")
        self.assertIsNone(d.from_list_payload("ashby", {}))

    def test_lever_joins_the_description_its_lists_and_the_closing_text(self):
        raw = {"descriptionPlain": "About the role.",
               "lists": [{"text": "What you'll do", "content": "<li>Design flows</li><li>Run research</li>"}],
               "additionalPlain": "We are an equal opportunity employer."}
        text = d.from_list_payload("lever", raw)
        self.assertIn("About the role.", text)
        self.assertIn("What you'll do\n- Design flows\n- Run research", text)
        self.assertTrue(text.endswith("We are an equal opportunity employer."))

    def test_plain_text_from_the_platform_gets_runs_of_blank_lines_collapsed(self):
        # Found by the real-endpoint check: Ashby's descriptionPlain had three or more newlines in a row.
        messy = "Intro  \r\n\r\n\r\n\r\nWhat you'll do\n\n\n\n- Design flows  \n"
        self.assertEqual(d.from_list_payload("ashby", {"descriptionPlain": messy}),
                         "Intro\n\nWhat you'll do\n\n- Design flows")
        text = d.from_list_payload("lever", {"descriptionPlain": messy, "additionalPlain": "A\n\n\n\nB"})
        self.assertNotIn("\n\n\n", text)

    def test_other_platforms_and_junk_return_none(self):
        self.assertIsNone(d.from_list_payload("greenhouse", {"content": "x"}))
        self.assertIsNone(d.from_list_payload("lever", None))
        self.assertIsNone(d.from_list_payload("lever", "text"))

    def test_these_two_make_no_request(self):
        with mock.patch.object(d, "request_with_retries", side_effect=AssertionError("no request expected")):
            self.assertEqual(d.fetch_description("ashby", "acme", {"raw_json": {"descriptionPlain": "ok text"}}), "ok text")
            self.assertEqual(d.fetch_description("lever", "acme", {"raw_json": {"descriptionPlain": "ok"}}), "ok")
            self.assertIsNone(d.fetch_description("ashby", "acme", {"raw_json": None}))


class PerJobPlatforms(unittest.TestCase):
    def fetch(self, ats, ident, posting_id, data, budget=None):
        calls = []

        def fake(method, url, **kw):
            calls.append((method, url, kw))
            return Response(data)

        with mock.patch.object(d, "request_with_retries", fake):
            text = d.fetch_description(ats, ident, {"ats_posting_id": posting_id}, budget)
        return text, calls

    def test_greenhouse(self):
        text, calls = self.fetch("greenhouse", "acme", "123", {"content": "&lt;p&gt;Hello&lt;/p&gt;"})
        self.assertEqual(text, "Hello")
        self.assertEqual(calls[0][1], "https://boards-api.greenhouse.io/v1/boards/acme/jobs/123")
        self.assertEqual(calls[0][2]["max_attempts"], d.ATTEMPTS)

    def test_workable_joins_description_requirements_and_benefits(self):
        text, calls = self.fetch("workable", "acme", "AB12", {"description": "<p>Role</p>", "requirements": "<p>Skills</p>", "benefits": ""})
        self.assertEqual(text, "Role\n\nSkills")
        self.assertEqual(calls[0][1], "https://apply.workable.com/api/v2/accounts/acme/jobs/AB12")

    def test_rippling_puts_the_role_before_the_company(self):
        text, calls = self.fetch("rippling", "acme", "uuid-1", {"description": {"company": "<p>About us</p>", "role": "<p>The job</p>"}})
        self.assertEqual(text, "The job\n\nAbout us")
        self.assertEqual(calls[0][1], "https://ats.rippling.com/api/v2/board/acme/jobs/uuid-1")

    def test_workday_builds_its_url_from_the_tenant_site_and_path(self):
        text, calls = self.fetch("workday", "nvidia.wd5/NVIDIAExternalCareerSite", "/job/US-CA/Designer_JR1",
                                 {"jobPostingInfo": {"jobDescription": "<p>Design things</p>"}})
        self.assertEqual(text, "Design things")
        self.assertEqual(calls[0][1], "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/job/US-CA/Designer_JR1")

    def test_empty_and_unexpected_bodies_give_none_not_an_error(self):
        self.assertIsNone(self.fetch("greenhouse", "a", "1", {"content": ""})[0])
        self.assertIsNone(self.fetch("workable", "a", "1", {})[0])
        self.assertIsNone(self.fetch("greenhouse", "a", "1", ["not", "a", "dict"])[0])

    def test_unknown_platform_is_none(self):
        self.assertIsNone(d.fetch_description("myspace", "a", {"ats_posting_id": "1"}))


class Budget(unittest.TestCase):
    def failing(self, status):
        def fake(*a, **k):
            raise http_error(status)
        return fake

    def test_a_404_means_the_role_is_gone_and_is_not_a_failure(self):
        budget = d.FetchBudget()
        with mock.patch.object(d, "request_with_retries", self.failing(404)):
            self.assertIsNone(d.fetch_description("greenhouse", "a", {"ats_posting_id": "1"}, budget))
        self.assertEqual((budget.failures, budget.rate_limited), ({}, {}))

    def test_other_errors_are_counted_and_raised(self):
        budget = d.FetchBudget()
        with mock.patch.object(d, "request_with_retries", self.failing(500)):
            with self.assertRaises(requests.HTTPError):
                d.fetch_description("greenhouse", "a", {"ats_posting_id": "1"}, budget)
        self.assertEqual(budget.failures, {"greenhouse": 1})

    def test_a_platform_stops_being_asked_after_five_rate_limits(self):
        budget = d.FetchBudget(stop_after_rate_limits=5)
        calls = []

        def fake(*a, **k):
            calls.append(1)
            raise http_error(429)

        with mock.patch.object(d, "request_with_retries", fake):
            for _ in range(5):
                with self.assertRaises(requests.HTTPError):
                    d.fetch_description("workable", "a", {"ats_posting_id": "x"}, budget)
            with self.assertRaises(d.Skipped):
                d.fetch_description("workable", "a", {"ats_posting_id": "x"}, budget)
        self.assertEqual(len(calls), 5)  # the sixth was never sent
        self.assertEqual(budget.blocked(), ["workable"])

    def test_a_403_counts_as_a_rate_limit_too(self):
        budget = d.FetchBudget()
        with mock.patch.object(d, "request_with_retries", self.failing(403)):
            with self.assertRaises(requests.HTTPError):
                d.fetch_description("rippling", "a", {"ats_posting_id": "x"}, budget)
        self.assertEqual(budget.rate_limited, {"rippling": 1})

    def test_one_platform_being_blocked_does_not_stop_another(self):
        budget = d.FetchBudget(stop_after_rate_limits=1)
        budget.record_rate_limit("workable")
        with self.assertRaises(d.Skipped):
            budget.reserve("workable")
        budget.reserve("greenhouse")  # still fine

    def test_the_whole_run_has_a_request_cap(self):
        budget = d.FetchBudget(max_requests=3)
        for _ in range(3):
            budget.reserve("greenhouse")
        with self.assertRaises(d.Skipped):
            budget.reserve("workday")

    def test_the_cap_holds_across_threads(self):
        budget = d.FetchBudget(max_requests=10)
        passed, lock = [], threading.Lock()

        def worker():
            try:
                budget.reserve("greenhouse")
                with lock:
                    passed.append(1)
            except d.Skipped:
                pass

        threads = [threading.Thread(target=worker) for _ in range(40)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(passed), 10)

    def test_a_skipped_request_makes_no_call(self):
        budget = d.FetchBudget(max_requests=0)
        with mock.patch.object(d, "request_with_retries", side_effect=AssertionError("must not be called")):
            with self.assertRaises(d.Skipped):
                d.fetch_description("greenhouse", "a", {"ats_posting_id": "1"}, budget)


class RetryAfter(unittest.TestCase):
    def test_delay_uses_the_header_capped_else_backoff(self):
        r = lambda h: mock.Mock(headers=h)
        self.assertEqual(http.retry_delay(r({"Retry-After": "7"}), 0), 7.0)
        self.assertEqual(http.retry_delay(r({"Retry-After": "9999"}), 0), http.MAX_RETRY_AFTER_SECONDS)
        self.assertEqual(http.retry_delay(r({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), 2), 4.0)  # date form: backoff
        self.assertEqual(http.retry_delay(r({}), 1), 2.0)
        self.assertEqual(http.retry_delay(r({"Retry-After": "-5"}), 0), 0.0)

    def test_the_helper_waits_as_long_as_the_server_asks(self):
        slow = mock.Mock(status_code=429, headers={"Retry-After": "3"})
        slow.raise_for_status.side_effect = requests.HTTPError("429", response=slow)
        ok = mock.Mock(status_code=200, headers={})
        ok.raise_for_status.return_value = None
        with mock.patch.object(http.requests, "request", side_effect=[slow, ok]), mock.patch.object(http.time, "sleep") as sleep:
            self.assertIs(http.request_with_retries("GET", "https://x", max_attempts=2), ok)
        sleep.assert_called_once_with(3.0)

    def test_without_the_header_it_still_backs_off_as_before(self):
        bad = mock.Mock(status_code=503, headers={})
        bad.raise_for_status.side_effect = requests.HTTPError("503", response=bad)
        ok = mock.Mock(status_code=200, headers={})
        ok.raise_for_status.return_value = None
        with mock.patch.object(http.requests, "request", side_effect=[bad, ok]), mock.patch.object(http.time, "sleep") as sleep:
            http.request_with_retries("GET", "https://x", max_attempts=2)
        sleep.assert_called_once_with(http.BACKOFF_SECONDS)


if __name__ == "__main__":
    unittest.main()
