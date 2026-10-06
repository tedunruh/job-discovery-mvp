"""Offline tests for collectors/detect.py (no network): python -m unittest discover tests"""
import unittest
from unittest import mock

from collectors import detect


class SlugCandidates(unittest.TestCase):
    def test_variants_most_likely_first(self):
        slugs = detect.slug_candidates("Scale AI")
        self.assertEqual(slugs[0], "scaleai")
        self.assertIn("scale-ai", slugs)

    def test_company_suffixes_dropped(self):
        self.assertEqual(detect.slug_candidates("Acme Labs Inc")[0], "acme")

    def test_hostile_text_never_becomes_a_slug(self):
        for bad in ["../../etc/passwd", "evil.com/x?y=z", "a b\nc", "<script>", "💥"]:
            for slug in detect.slug_candidates(bad):
                self.assertRegex(slug, r"^[a-z0-9][a-z0-9-]*$")

    def test_capped(self):
        self.assertLessEqual(len(detect.slug_candidates("a b c d e f g h")), detect.MAX_SLUGS)


class UrlParsing(unittest.TestCase):
    def test_known_hosts(self):
        cases = {
            "https://boards.greenhouse.io/figma/jobs/123": ("greenhouse", "figma"),
            "https://job-boards.greenhouse.io/Anthropic": ("greenhouse", "anthropic"),
            "https://jobs.lever.co/palantir/abc-def": ("lever", "palantir"),
            "https://jobs.ashbyhq.com/Workhuman/uuid": ("ashby", "Workhuman"),  # Ashby slugs keep case
            "https://apply.workable.com/airhelp/": ("workable", "airhelp"),
            "https://zendesk.wd1.myworkdayjobs.com/en-US/zendesk/jobs": ("workday", "zendesk.wd1/zendesk"),
        }
        for url, expected in cases.items():
            self.assertEqual(detect.parse_ats_url(url), expected, url)

    def test_non_ats_urls_ignored(self):
        for text in ["https://example.com/careers", "figma", "https://evil.com/careers?x=1"]:
            self.assertIsNone(detect.parse_ats_url(text))


class IdentifierValidation(unittest.TestCase):
    def test_accepts_real_identifiers(self):
        for ats, ident in [("greenhouse", "figma"), ("lever", "palantir"), ("ashby", "Workhuman"),
                           ("workable", "airhelp"), ("workday", "workiva.wd503/careers")]:
            self.assertTrue(detect.valid_identifier(ats, ident), (ats, ident))

    def test_rejects_path_and_host_tricks(self):
        for ats, ident in [("greenhouse", "../admin"), ("greenhouse", "a/b"), ("lever", "x?y=1"), ("lever", "a@evil.com"),
                           ("ashby", "a/../b"), ("workable", ""), ("workable", "-x"), ("workday", "evil.com/x"),
                           ("workday", "a.wd1/../x"), ("nope", "figma"), ("greenhouse", "x" * 80)]:
            self.assertFalse(detect.valid_identifier(ats, ident), (ats, ident))

    def test_verify_makes_no_request_for_invalid_identifier(self):
        with mock.patch.object(detect, "_get") as get:
            self.assertIsNone(detect.verify("greenhouse", "../admin"))
            get.assert_not_called()


class Scoring(unittest.TestCase):
    def cand(self, ats, **kw):
        base = detect._candidate(ats, "x", kw.get("name"), ["Engineer"], kw.get("count", 10), text=kw.get("text", ""))
        return base

    def test_board_name_match_is_high(self):
        c = detect._score(self.cand("greenhouse", name="Figma"), "Figma", 0)
        self.assertEqual(c["confidence"], "high")

    def test_other_companys_board_is_low(self):
        c = detect._score(self.cand("greenhouse", name="Fin"), "Intercom", 0)
        self.assertEqual(c["confidence"], "low")

    def test_name_in_postings_is_high(self):
        c = detect._score(self.cand("ashby", text="Join Notion and build tools"), "Notion", 0)
        self.assertEqual(c["confidence"], "high")

    def test_unconfirmable_exact_slug_is_medium_guess_is_low(self):
        self.assertEqual(detect._score(self.cand("lever", text="hello"), "Linear", 0)["confidence"], "medium")
        self.assertEqual(detect._score(self.cand("lever", text="hello"), "Linear", 3)["confidence"], "low")

    def test_huge_workable_board_flagged_as_agency(self):
        c = detect._score(self.cand("workable", name="Figma", count=2500), "Figma", 0)
        self.assertEqual(c["confidence"], "low")


class DetectFlow(unittest.TestCase):
    def test_pasted_link_never_probes_slugs(self):
        good = detect._candidate("lever", "palantir", None, ["Eng"], 5)
        with mock.patch.object(detect, "probe", return_value=good) as probe:
            res = detect.detect("https://jobs.lever.co/palantir")
        self.assertEqual(probe.call_count, 1)
        self.assertEqual((res[0]["confidence"], res[0]["reason"]), ("high", "taken from the link you pasted"))

    def test_unverifiable_link_returns_nothing(self):
        with mock.patch.object(detect, "probe", return_value=None):
            self.assertEqual(detect.detect("https://jobs.lever.co/doesnotexist"), [])

    def test_domain_becomes_name(self):
        with mock.patch.object(detect, "probe", return_value=None) as probe:
            detect.detect("careers.figma.com")
        self.assertIn("figma", {c.args[1] for c in probe.call_args_list})

    def test_empty_query(self):
        self.assertEqual(detect.detect("   "), [])


if __name__ == "__main__":
    unittest.main()
