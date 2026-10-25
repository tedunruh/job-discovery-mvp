"""SC-53: the strict reading of "a design role". The loose gate decides what is stored; this decides what a
person who picked nothing is shown. The titles below are real ones from the stored roles."""
import unittest

from collectors.filters import is_design_role, is_strict_design_role
from role_categories import matches_profile

REAL_DESIGN_ROLES = [
    "Senior Product Designer", "Staff Product Designer, Connected Experiences", "Product Designer (UX & UI) - Mid to Senior",
    "Founding Product Designer", "Principal UX designer - Monopoly GO!", "Lead UX Designer", "Senior UI/UX Designer II",
    "UI Designer - AI Neobank App", "Senior Visual Designer", "Brand Designer, AI & Ops", "Senior Graphic Designer",
    "Motion Graphic Designer", "Lead Brand Designer", "UX Researcher", "Senior User Researcher", "Design Director",
    "Associate Design Director", "Product Design Manager", "Design Manager", "Head of Design", "VP of Design",
    "Creative Director", "Art Director, Licensed Product Design", "Content Designer | LATAM", "Interaction Design Lead",
    "Experience Design Lead", "Design Systems Designer", "Senior Digital Designer", "Intern, Product Design",
    "Graphics Designer", "Founding Designer", "Senior Designer, Brand", "Email Designer",
    # a team name is not the job
    "Lead Product Designer | Platform Engineering", "Senior Product Designer, Design Systems",
    # genuine UX engineering stays
    "UX Engineer", "User Experience Engineer", "Design Technologist",
]

NOT_DESIGN_ROLES = [
    # engineering and hardware with "design" in the title (most of what the loose gate lets through)
    "Analog IC Design Engineer", "Civil Design Engineer", "Propulsion Design Engineer II", "Senior Electrical Designer",
    "Physical Design Engineer, PnR", "Staff Electrical Design Engineer", "Principal Mechanical Design Engineer",
    "Senior Mixed Signal Design Engineer", "ASIC Package Design Project Manager", "Design Release Engineer - Exterior Trim",
    "Lead Design Engineer", "Design Engineer", "Product Design Engineer", "Product Design Engineering Intern",
    "Senior Design Systems Engineer II", "Staff Frontend Engineer - Design Systems", "Senior Software Engineer - UI",
    "Software Engineering Manager - Global UX - Applications - Platform", "Digital Design Engineer",
    "Design Engineering Manager", "Senior Piping Designer", "Senior Technical Designer",
    # other kinds of design
    "Interior Designer II", "Jr. Interior Designer - ROAM", "Expert Instructional Designer", "Senior Learning Designer",
    "Associate Designer, Apparel", "Design Intern - Knitwear", "Sr Technical Designer, Men's Woven Shirts",
    "Bakery Cake Designer", "Business Process Designer", "Solution Designer Digital Channels", "Architectural Designer",
    "Production Designer - Target India", "Cafe Assistant Manager (Design District)", "Window Treatment Design Consultant",
    # "ux" and "ui" as words in other work
    "Product/UX Professionals - AI Training - Seattle, USA",
    # not design at all
    "Accountant", "Recruiter", "Product Manager", "Senior Data Scientist",
]


class Classifier(unittest.TestCase):
    def test_real_design_roles_pass(self):
        for title in REAL_DESIGN_ROLES:
            self.assertTrue(is_strict_design_role(title), title)

    def test_engineering_hardware_and_other_kinds_of_design_do_not(self):
        for title in NOT_DESIGN_ROLES:
            self.assertFalse(is_strict_design_role(title), title)

    def test_empty_and_missing_titles_are_not_design_roles(self):
        self.assertFalse(is_strict_design_role(""))
        self.assertFalse(is_strict_design_role(None))


class WhoIsAffected(unittest.TestCase):
    """matches_profile is the old rule, kept for people with no profile yet."""

    def test_nobody_who_picked_nothing_sees_engineering_roles_any_more(self):
        for title in NOT_DESIGN_ROLES:
            self.assertFalse(matches_profile(title, is_design_role(title), set(), []), title)
        for title in ("Senior Product Designer", "UX Researcher", "Design Director"):
            self.assertTrue(matches_profile(title, True, set(), []), title)

    def test_someone_who_picked_categories_is_matched_exactly_as_before(self):
        self.assertTrue(matches_profile("Senior Product Designer", True, {"product_design"}, []))
        self.assertFalse(matches_profile("Brand Designer", True, {"product_design"}, []))
        self.assertTrue(matches_profile("UX Engineer", True, {"ux_engineering"}, []))                # a pick wins over the strict rule
        self.assertTrue(matches_profile("Design Engineer", True, {"ux_engineering"}, []))

    def test_keywords_still_admit_any_role_by_title(self):
        self.assertTrue(matches_profile("Civil Design Engineer", True, set(), ["civil design engineer"]))
        self.assertTrue(matches_profile("Data Analyst", False, set(), ["data analyst"]))
        self.assertFalse(matches_profile("Data Analyst", False, set(), []))


if __name__ == "__main__":
    unittest.main()
