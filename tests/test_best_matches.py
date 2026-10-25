"""SC-53: the roles a person's titles match, closest to what they wrote first. Ranking never hides a role."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from dashboard import app as dapp
from role_categories import best_matches_first, title_match_rank

MINE = ["Principal Product Designer", "Senior UX Designer", "Design Systems Lead", "product designer", "ux designer"]


def post(pid, title, hours_ago=0, **kw):
    return dict({"id": pid, "title": title, "company_name": "Co", "ats_type": "ashby", "location": "Remote - United States",
                 "remote_type": "remote", "url": "u", "is_design": True, "applied": False,
                 "first_seen_at": datetime.now(timezone.utc) - timedelta(hours=hours_ago)}, **kw)


class Rank(unittest.TestCase):
    def test_a_title_naming_a_seniority_the_person_wrote_beats_exact_generic_beats_generic(self):
        senior = title_match_rank("Principal Product Designer, Growth", MINE)      # contains a title with a level word
        exact_senior = title_match_rank("Senior UX Designer", MINE)                # is one, word for word
        exact_generic = title_match_rank("Product Designer", MINE)
        generic = title_match_rank("Associate Product Designer, Payments", MINE)  # only "product designer"
        self.assertGreater(exact_senior, senior)
        self.assertGreater(senior, exact_generic)
        self.assertGreater(exact_generic, generic)
        self.assertGreater(generic, (0, 0, 0))

    def test_no_match_ranks_last(self):
        self.assertEqual(title_match_rank("Accountant", MINE), (0, 0, 0))
        self.assertEqual(title_match_rank("Accountant", []), (0, 0, 0))
        self.assertEqual(title_match_rank("Accountant", None), (0, 0, 0))

    def test_case_spacing_and_punctuation_do_not_matter(self):
        self.assertEqual(title_match_rank("  senior   UX designer ", MINE), title_match_rank("Senior UX Designer", MINE))
        self.assertEqual(title_match_rank("Senior UX Designer!", MINE)[:2], (1, 1))

    def test_it_must_be_a_whole_phrase_not_part_of_a_word(self):
        self.assertEqual(title_match_rank("Product Designers Anonymous", ["product designer"]), (0, 0, 0))
        self.assertEqual(title_match_rank("Subproduct Designer", ["product designer"]), (0, 0, 0))

    def test_the_longer_phrase_wins_when_both_match(self):
        self.assertGreater(title_match_rank("Design Systems Lead, Platform", ["design systems lead", "design"]),
                           title_match_rank("Design Lead", ["design systems lead", "design"]))

    def test_the_best_of_several_matching_titles_counts(self):
        both = title_match_rank("Senior UX Designer", ["ux designer", "senior ux designer"])
        self.assertEqual(both, (1, 1, 3))


class Order(unittest.TestCase):
    def test_closest_first_newest_first_among_equals_and_nothing_removed(self):
        rows = [post(1, "Product Designer", 1), post(2, "Senior UX Designer", 5), post(3, "UX Designer, Growth", 2),
                post(4, "Senior UX Designer, Payments", 9), post(5, "Product Designer", 7), post(6, "Staff UX Designer", 3)]
        out = best_matches_first(rows, ["Senior UX Designer", "product designer", "ux designer"])
        self.assertEqual([p["id"] for p in out], [2, 4, 1, 5, 3, 6])      # seniority exact, seniority, exact generic (newest first), generic
        self.assertEqual(sorted(p["id"] for p in out), [1, 2, 3, 4, 5, 6])

    def test_without_titles_the_order_is_untouched(self):
        rows = [post(i, f"Role {i}") for i in (3, 1, 2)]
        self.assertEqual([p["id"] for p in best_matches_first(rows, [])], [3, 1, 2])


class Dashboard(unittest.TestCase):
    def setUp(self):
        self.client = dapp.app.test_client()
        self.client.set_cookie(dapp.SESSION_COOKIE, "tok")
        # the database hands rows over newest first
        self.rows = [post(1, "Product Designer", 1), post(2, "Product Designer, Payments", 2), post(3, "Senior UX Designer", 3),
                     post(4, "Principal Product Designer", 4)]
        self.profile = {"titles": ["Principal Product Designer", "Senior UX Designer", "product designer"], "level": None,
                        "one_level_up": False, "city": None, "skills": []}
        patches = [
            mock.patch.object(dapp, "get_conn", lambda: mock.MagicMock()),
            mock.patch.object(dapp, "get_session_user", lambda c, t: (7, False)),
            mock.patch.object(dapp, "is_onboarded", lambda c, u: True),
            mock.patch.object(dapp, "log_visit", lambda c, u: None),
            mock.patch.object(dapp, "get_user_role_categories", lambda c, u: set()),
            mock.patch.object(dapp, "get_user_keywords", lambda c, u: []),
            mock.patch.object(dapp, "get_user_profile", lambda c, u: self.profile),
            mock.patch.object(dapp, "get_user_work_types", lambda c, u: []),
            mock.patch.object(dapp, "get_open_postings", lambda c, u, since=None, keywords=(): [dict(r) for r in self.rows]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def titles_in_order(self):
        import re
        html = self.client.get("/").get_data(as_text=True)
        return re.findall(r'class="posting-link"[^>]*>([^<]+)</a>', html)

    def test_the_dashboard_lists_the_closest_titles_first(self):
        self.assertEqual(self.titles_in_order(),
                         ["Senior UX Designer", "Principal Product Designer", "Product Designer", "Product Designer, Payments"])

    def test_someone_without_a_profile_keeps_newest_first(self):
        self.profile = None
        self.assertEqual(self.titles_in_order(), ["Product Designer", "Product Designer, Payments", "Senior UX Designer",
                                                  "Principal Product Designer"])


if __name__ == "__main__":
    unittest.main()
