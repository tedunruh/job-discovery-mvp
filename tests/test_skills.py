"""SC-51 slice 4: a person's skills against a role's stored description. One plain rule, used by the dashboard
(through Postgres) and by these tests (through Python)."""
import unittest
from unittest import mock

from db import db as dbmod
from role_categories import (
    effective_cutoff,
    skill_phrase,
    skill_phrases,
    skill_regex,
    skill_status,
    skills_matched,
)


class Phrase(unittest.TestCase):
    def test_brackets_are_removed_and_whitespace_and_case_are_normalised(self):
        self.assertEqual(skill_phrase("Accessibility (WCAG 2.2)"), "accessibility")
        self.assertEqual(skill_phrase("  Design   Systems "), "design systems")
        self.assertEqual(skill_phrase("Figma (advanced) and Sketch"), "figma and sketch")

    def test_nothing_usable_left_means_empty(self):
        for raw in ("", None, "(WCAG)", "R", " x "):
            self.assertEqual(skill_phrase(raw), "", raw)

    def test_phrases_are_deduplicated_ignoring_case_and_brackets(self):
        got = skill_phrases(["Figma", "figma", "FIGMA (expert)", "CSS", "", "A"])
        self.assertEqual(got, ["figma", "css"])


class Matching(unittest.TestCase):
    def test_a_skill_matches_as_a_whole_word_in_any_case(self):
        self.assertEqual(skills_matched("We use FIGMA every day.", ["Figma"]), ["figma"])
        self.assertEqual(skills_matched("Figma", ["Figma"]), ["figma"])               # alone
        self.assertEqual(skills_matched("(Figma), sketch", ["Figma"]), ["figma"])     # punctuation around it

    def test_it_does_not_match_inside_a_longer_word(self):
        self.assertEqual(skills_matched("Figmatic and superfigma", ["Figma"]), [])
        self.assertEqual(skills_matched("css3 and scss", ["CSS"]), [])

    def test_phrases_and_symbols(self):
        self.assertEqual(skills_matched("experience with user research and c++.", ["User research", "C++"]),
                         ["user research", "c++"])
        self.assertEqual(skills_matched("built in node.js", ["Node.js"]), ["node.js"])
        self.assertEqual(skills_matched("nodexjs", ["Node.js"]), [])                   # the dot is a dot

    def test_no_synonyms(self):
        self.assertEqual(skills_matched("we do user research", ["UX research"]), [])

    def test_bracketed_part_of_a_skill_is_not_looked_for(self):
        self.assertEqual(skills_matched("Strong accessibility skills", ["Accessibility (WCAG 2.2)"]), ["accessibility"])

    def test_each_skill_counts_once_however_often_it_appears(self):
        self.assertEqual(skills_matched("figma figma Figma", ["Figma", "figma"]), ["figma"])

    def test_no_text_or_no_skills(self):
        self.assertEqual(skills_matched("", ["Figma"]), [])
        self.assertEqual(skills_matched(None, ["Figma"]), [])
        self.assertEqual(skills_matched("figma", []), [])

    def test_the_regex_is_the_same_text_postgres_gets(self):
        # db.get_skill_matches hands skill_regex(phrase) to Postgres with ~*; keep it free of Python-only syntax.
        r = skill_regex("node.js")
        self.assertEqual(r, r"(?<![a-z0-9])node\.js(?![a-z0-9])")


class Cutoff(unittest.TestCase):
    def test_no_cutoff_when_not_set_or_no_usable_skills(self):
        self.assertIsNone(effective_cutoff(None, ["Figma"]))
        self.assertIsNone(effective_cutoff(0, ["Figma"]))
        self.assertIsNone(effective_cutoff(3, []))
        self.assertIsNone(effective_cutoff(3, ["(x)", "A"]))

    def test_it_is_never_higher_than_the_number_of_skills(self):
        self.assertEqual(effective_cutoff(3, ["Figma", "CSS"]), 2)
        self.assertEqual(effective_cutoff(3, ["Figma", "figma"]), 1)                    # repeats are one skill
        self.assertEqual(effective_cutoff(2, ["Figma", "CSS", "HTML"]), 2)

    def test_status(self):
        self.assertEqual(skill_status(0, 3, has_text=False), "unscored")                # never hidden without text
        self.assertEqual(skill_status(2, 3, has_text=True), "hidden")
        self.assertEqual(skill_status(3, 3, has_text=True), "shown")
        self.assertEqual(skill_status(0, None, has_text=True), "shown")                 # no cutoff: nothing hidden


class FakeCursor:
    def __init__(self, rows): self.rows, self.sql, self.params = rows, None, None
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None): self.sql, self.params = sql, params
    def fetchall(self): return self.rows


class SqlForm(unittest.TestCase):
    def test_one_boolean_per_skill_and_only_roles_with_text(self):
        cur = FakeCursor([(10, True, False), (11, False, False)])
        conn = mock.Mock(); conn.cursor.return_value = cur
        got = dbmod.get_skill_matches(conn, [10, 11, 12], ["Figma", "CSS (basics)", "figma"])
        self.assertEqual(got, {10: ["figma"], 11: []})
        self.assertEqual(cur.sql.count("description ~* %s"), 2)                        # figma, css: repeats dropped
        self.assertIn("description <> ''", cur.sql)
        self.assertEqual(cur.params, [skill_regex("figma"), skill_regex("css"), [10, 11, 12]])

    def test_nothing_to_ask_means_no_query(self):
        conn = mock.Mock()
        self.assertEqual(dbmod.get_skill_matches(conn, [1], []), {})
        self.assertEqual(dbmod.get_skill_matches(conn, [], ["Figma"]), {})
        conn.cursor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
