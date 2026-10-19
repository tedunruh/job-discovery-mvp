"""Offline tests for profile-based matching (SC-46): levels, target titles, conversion from the old
role categories and keywords, and the dual-read between profiles and the old rule (no network/DB)."""
import unittest

from collectors.filters import is_design_role
from role_categories import (
    ROLE_CATEGORIES,
    alerts_active,
    matches_level,
    matches_profile,
    matches_profile_titles,
    profile_from_legacy,
    title_level,
)
from scheduler.run_collectors import _wants_posting


class TitleLevel(unittest.TestCase):
    def test_stated_levels(self):
        cases = {
            "Senior Product Designer": "senior",
            "Sr. Product Designer": "senior",
            "Staff Product Designer": "staff",
            "Principal Designer": "principal",
            "Design Manager": "lead_manager",
            "Lead Product Designer": "lead_manager",
            "Head of Design": "lead_manager",
            "Director, Design": "lead_manager",
            "Junior Designer": "junior",
            "Associate Product Designer": "junior",
            "Mid-Level UX Designer": "mid",
        }
        for title, level in cases.items():
            self.assertEqual(title_level(title), level, title)

    def test_manager_beats_seniority_word(self):
        self.assertEqual(title_level("Senior Design Manager"), "lead_manager")
        self.assertEqual(title_level("Associate Director of Design"), "lead_manager")

    def test_no_stated_level(self):
        for title in ["Product Designer", "UX Researcher", "Brand Designer, Growth"]:
            self.assertIsNone(title_level(title), title)

    def test_words_inside_other_words_do_not_count(self):
        self.assertIsNone(title_level("Stafford Designer"))
        self.assertIsNone(title_level("Leader Experience Designer"))


class LevelMatching(unittest.TestCase):
    def test_no_level_chosen_means_every_level(self):
        for title in ["Junior Designer", "Staff Designer", "Design Manager", "Designer"]:
            self.assertTrue(matches_level(title, None), title)
            self.assertTrue(matches_level(title, ""), title)

    def test_chosen_level_filters_stated_levels(self):
        self.assertTrue(matches_level("Senior Product Designer", "senior"))
        self.assertFalse(matches_level("Staff Product Designer", "senior"))
        self.assertFalse(matches_level("Design Manager", "senior"))
        self.assertFalse(matches_level("Junior Designer", "senior"))

    def test_unstated_level_always_passes(self):
        for level in ["mid", "senior", "staff", "principal", "lead_manager"]:
            self.assertTrue(matches_level("Product Designer", level), level)

    def test_one_level_up(self):
        self.assertFalse(matches_level("Staff Product Designer", "senior"))
        self.assertTrue(matches_level("Staff Product Designer", "senior", one_level_up=True))
        self.assertFalse(matches_level("Principal Designer", "senior", one_level_up=True))
        self.assertTrue(matches_level("Design Manager", "principal", one_level_up=True))

    def test_one_level_up_from_the_top_adds_nothing(self):
        self.assertTrue(matches_level("Design Manager", "lead_manager", one_level_up=True))
        self.assertFalse(matches_level("Senior Designer", "lead_manager", one_level_up=True))


class TitleMatching(unittest.TestCase):
    def test_title_must_match_a_target_title_as_whole_words(self):
        self.assertTrue(matches_profile_titles("Senior Product Designer", ["product designer"]))
        self.assertFalse(matches_profile_titles("Product Design Lead", ["product designer"]))
        self.assertFalse(matches_profile_titles("Software Engineer", ["product designer"]))

    def test_no_titles_matches_nothing(self):
        self.assertFalse(matches_profile_titles("Product Designer", []))

    def test_non_design_titles_work_too(self):
        self.assertTrue(matches_profile_titles("Senior Data Analyst", ["data analyst"], level="senior"))

    def test_level_applies_on_top_of_titles(self):
        self.assertFalse(matches_profile_titles("Staff Product Designer", ["product designer"], level="senior"))
        self.assertTrue(matches_profile_titles("Staff Product Designer", ["product designer"], level="senior", one_level_up=True))


class AlertsActive(unittest.TestCase):
    def test_off_until_started_and_off_while_paused(self):
        self.assertFalse(alerts_active({"alerts_started_at": None, "alerts_paused_at": None}))
        self.assertTrue(alerts_active({"alerts_started_at": "2026-10-01", "alerts_paused_at": None}))
        self.assertFalse(alerts_active({"alerts_started_at": "2026-10-01", "alerts_paused_at": "2026-10-10"}))


SAMPLE_TITLES = [
    "Senior Product Designer", "Product Designer", "Staff Product Designer", "Product Design Lead",
    "UX Researcher", "Senior UX Researcher", "User Research Manager", "Researcher, Data Science",
    "Design Manager", "Head of Design", "VP of Design", "Design Director", "Design Lead, Platform",
    "UX Engineer", "Design Engineer", "Design Technologist", "Brand Designer", "Visual Designer",
    "Graphic Designer", "Interior Designer", "Software Engineer", "Account Executive",
    "Recruiter", "Data Analyst", "Senior Data Analyst", "Product Manager", "UI Developer",
    "Acquisition Marketing Manager", "Quality Assurance Engineer", "Experience Designer",
]


class ConversionKeepsTheSameRoles(unittest.TestCase):
    """Converting a person's old categories and keywords to titles must show them the same roles."""

    def check(self, categories, keywords):
        titles = profile_from_legacy(categories, keywords)
        for title in SAMPLE_TITLES:
            old = matches_profile(title, is_design_role(title), set(categories), keywords)
            new = matches_profile_titles(title, titles)
            self.assertEqual(old, new, f"{title!r} with {sorted(categories)} {keywords}")

    def test_every_single_category(self):
        for key in ROLE_CATEGORIES:
            if key == "ux_research":
                continue  # known, intended difference: see test_researcher_is_no_longer_gated_on_design
            self.check({key}, [])

    def test_categories_plus_keywords(self):
        self.check({"product_design"}, ["data analyst"])
        self.check({"design_management", "product_design"}, ["product manager", "recruiter"])

    def test_no_categories_means_every_design_role(self):
        self.check(set(), [])
        self.check(set(), ["data analyst"])

    def test_researcher_is_no_longer_gated_on_design(self):
        # The old UX research category matched "researcher" only in titles that also pass the design
        # gate. A target title is a target title, so a non-design "Researcher, Data Science" now matches.
        titles = profile_from_legacy({"ux_research"}, [])
        self.assertFalse(matches_profile("Researcher, Data Science", False, {"ux_research"}, []))
        self.assertTrue(matches_profile_titles("Researcher, Data Science", titles))

    def test_converted_titles_are_clean(self):
        titles = profile_from_legacy({"product_design"}, ["Data  Analyst", "data analyst", ""])
        self.assertEqual(titles, ["product designer", "product design", "data analyst"])


class DualRead(unittest.TestCase):
    posting = {"title": "Senior Product Designer", "is_design": True}

    def test_no_profile_uses_the_old_rule(self):
        self.assertTrue(_wants_posting(1, self.posting, {}, {1: {"product_design"}}, {}))
        self.assertFalse(_wants_posting(1, self.posting, {}, {1: {"ux_research"}}, {}))

    def test_a_profile_replaces_the_old_rule(self):
        profile = {"titles": ["data analyst"], "level": None, "one_level_up": False,
                   "alerts_started_at": "2026-10-01", "alerts_paused_at": None}
        # old categories would match this posting; the profile's titles don't
        self.assertFalse(_wants_posting(1, self.posting, {1: profile}, {1: {"product_design"}}, {}))

    def test_a_profile_with_alerts_off_gets_nothing(self):
        profile = {"titles": ["product designer"], "level": None, "one_level_up": False,
                   "alerts_started_at": None, "alerts_paused_at": None}
        self.assertFalse(_wants_posting(1, self.posting, {1: profile}, {}, {}))
        profile["alerts_started_at"] = "2026-10-01"
        self.assertTrue(_wants_posting(1, self.posting, {1: profile}, {}, {}))
        profile["alerts_paused_at"] = "2026-10-05"
        self.assertFalse(_wants_posting(1, self.posting, {1: profile}, {}, {}))


if __name__ == "__main__":
    unittest.main()
