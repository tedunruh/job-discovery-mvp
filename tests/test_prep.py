"""SC-72 slice 1: Prep, what the panel beside the list says about one job. Made only from stored requirements and the
resume, with no model call; every state keeps Apply possible."""
import unittest
from unittest import mock

from dashboard import app as dapp
from dashboard import fit, prep


def reqs(*pairs):
    return [{"label": label, "terms": terms} for label, terms in pairs]


REQS = reqs(("Design systems experience", ["design system"]), ("Figma", ["figma"]), ("Mentoring", ["mentor"]),
            ("Healthcare products", ["healthcare"]))
POSTING = {"id": 5, "title": "Senior Product Designer", "company_name": "Co", "ats_type": "ashby", "location": "Remote",
           "remote_type": "remote", "url": "u", "first_seen_at": None, "is_design": True, "applied": False}


class Line(unittest.TestCase):
    def test_scouts_line_counts_what_the_resume_covers(self):
        self.assertEqual(prep.say([True, True, True]), "You cover everything they list.")
        self.assertEqual(prep.say([True, True, False]), "You cover 2 of the 3 things they list.")
        self.assertEqual(prep.say([True, False, False, False]),
                         "You cover 1 of the 4 things they list. Worth a look before you apply.")

    def test_flags_use_the_same_test_as_the_score(self):
        text = "built a design system in figma"
        self.assertEqual(fit.covered_flags(REQS, text), [True, True, False, False])
        self.assertEqual(sum(fit.covered_flags(REQS, text)), fit.covered(REQS, text))


class Build(unittest.TestCase):
    def setUp(self):
        self.prep_row, self.has_resume, self.resume_text = (True, REQS), True, "design systems and Figma work"
        self.profile = {"titles": ["designer"], "skills": [], "level": None}
        self.queued, self.fit_calls = [], []

        def fake_fit(conn, uid, profile, postings, shown):
            self.fit_calls.append(profile)
            postings[0]["fit"] = 4
            return 1, []

        for target, value in {
            "get_postings_by_ids": lambda c, u, ids: [dict(POSTING)],
            "get_posting_prep": lambda c, pid: self.prep_row,
            "get_resume_text": lambda c, u: (self.has_resume, self.resume_text),
            "resume_text_for": lambda c, u: self.resume_text,
            "get_user_profile": lambda c, u: self.profile,
            "queue_reads": lambda ids: self.queued.append(ids),
            "apply_fit": fake_fit,
        }.items():
            p = mock.patch.object(prep, target, value)
            p.start()
            self.addCleanup(p.stop)

    def build(self):
        return prep.build(mock.MagicMock(), 7, 5)

    def test_a_job_with_a_resume_marks_each_requirement_and_leads_with_what_is_covered(self):
        out = self.build()
        self.assertEqual(out["state"], "ok")
        self.assertEqual((out["company"], out["title"], out["mono"], out["source"]), ("Co", "Senior Product Designer", "CO", "Ashby"))
        self.assertEqual(out["say"], "You cover 2 of the 4 things they list. Worth a look before you apply.")
        self.assertEqual([(r["text"], r["mark"], r["state"]) for r in out["requirements"]], [
            ("Design systems experience", "yes", "In your resume"), ("Figma", "yes", "In your resume"),
            ("Mentoring", "no", "Not in your resume"), ("Healthcare products", "no", "Not in your resume")])
        self.assertEqual(out["lead"], ["Design systems experience. They list it, and your resume mentions it.",
                                       "Figma. They list it, and your resume mentions it."])
        self.assertEqual(out["fit"], {"pips": 4, "label": "Great fit"})
        self.assertEqual(self.queued, [])

    def test_skills_and_titles_on_the_profile_count_as_evidence_like_they_do_for_the_score(self):
        self.resume_text = "figma only"
        self.profile["skills"] = ["Mentor"]
        marks = {r["text"]: r["mark"] for r in self.build()["requirements"]}
        self.assertEqual(marks["Mentoring"], "yes")

    def test_lead_with_is_at_most_three_things(self):
        self.prep_row = (True, reqs(*[(f"Skill {n}", [f"skill{n}"]) for n in range(5)]))
        self.resume_text = "skill0 skill1 skill2 skill3 skill4"
        out = self.build()
        self.assertEqual(len(out["lead"]), 3)
        self.assertEqual(out["say"], "You cover everything they list.")

    def test_nothing_covered_leads_with_nothing(self):
        self.resume_text = "unrelated"
        self.assertEqual(self.build()["lead"], [])

    def test_no_resume_shows_the_requirements_without_marks_and_no_fit(self):
        self.has_resume = False
        out = self.build()
        self.assertEqual(out["state"], "no_resume")
        self.assertEqual(out["message"], "I need your resume to check your fit and tailor it. It takes about a minute.")
        self.assertEqual({r["mark"] for r in out["requirements"]}, {"unknown"})
        self.assertEqual([r["state"] for r in out["requirements"]], [""] * 4)
        self.assertIsNone(out["fit"])
        self.assertEqual(out["lead"], [])
        self.assertEqual(self.fit_calls, [])

    def test_no_stored_text_or_nothing_usable_in_it_says_so_and_checks_nothing(self):
        for row in ((False, None), (True, [])):
            self.prep_row = row
            out = self.build()
            self.assertEqual(out["state"], "no_text")
            self.assertEqual(out["say"], "I couldn’t read this posting, so I can’t check your fit or tailor for it.")
            self.assertEqual((out["requirements"], out["lead"], out["fit"], out["message"]), ([], [], None, ""))

    def test_requirements_not_read_yet_queue_the_ordinary_read_and_say_to_come_back(self):
        self.prep_row = (True, None)
        out = self.build()
        self.assertEqual(out["state"], "reading")
        self.assertIn("still reading this posting", out["say"])
        self.assertEqual(self.queued, [[5]])

    def test_a_person_without_a_profile_still_gets_marks_but_no_fit(self):
        self.profile = None
        out = self.build()
        self.assertEqual(out["state"], "ok")
        self.assertIsNone(out["fit"])

    def test_a_job_that_is_not_open_is_not_found(self):
        self.prep_row = None
        self.assertIsNone(self.build())


def sample(**over):
    out = {"id": 5, "company": "Juniper <Labs>", "title": "Staff Designer", "mono": "JL", "tint": "sage", "source": "Ashby",
           "state": "ok", "say": "You cover everything they list.", "message": "",
           "fit": {"pips": 4, "label": "Great fit"},
           "requirements": [{"text": "Design systems <b>x</b>", "mark": "yes", "state": "In your resume"},
                            {"text": "React", "mark": "no", "state": "Not in your resume"}],
           "lead": ["Design systems. They list it, and your resume mentions it."]}
    out.update(over)
    return out


class Route(unittest.TestCase):
    def setUp(self):
        self.client = dapp.app.test_client()
        self.signed_in, self.body = True, sample()
        for target, value in {"get_conn": lambda: mock.MagicMock(),
                              "get_session_user": lambda c, t: (7, False) if self.signed_in else (None, False)}.items():
            p = mock.patch.object(dapp, target, value)
            p.start()
            self.addCleanup(p.stop)
        for target, value in {"get_conn": lambda: mock.MagicMock(), "build": lambda conn, uid, pid: self.body}.items():
            p = mock.patch.object(prep, target, value)
            p.start()
            self.addCleanup(p.stop)
        self.client.set_cookie(dapp.SESSION_COOKIE, "tok")

    def get(self):
        r = self.client.get("/prep/5")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        return r.get_data(as_text=True)

    def test_it_draws_the_panel_with_a_heading_a_close_button_and_apply(self):
        html = self.get()
        self.assertIn('<h2 class="fg-panel-title" id="panel-title" tabindex="-1"', html)
        self.assertIn('aria-label="Close"', html)
        self.assertIn('href="/open/5" target="_blank"', html)
        self.assertIn("Apply<span class=\"fg-visually-hidden\"> (opens the posting in a new tab)</span>", html)
        self.assertIn("You cover everything they list.", html)
        self.assertIn("My estimate from the posting and your resume, not the employer’s screen.", html)
        self.assertIn("Great fit", html)
        self.assertEqual(html.count('class="fg-req"'), 2)
        self.assertIn("In your resume", html)
        self.assertIn("Not in your resume", html)

    def test_text_from_a_posting_is_escaped(self):
        html = self.get()
        self.assertNotIn("<b>x</b>", html)
        self.assertIn("Design systems &lt;b&gt;x&lt;/b&gt;", html)
        self.assertIn("Juniper &lt;Labs&gt;", html)

    def test_no_resume_asks_for_one_and_keeps_apply(self):
        self.body = sample(state="no_resume", say="Here’s what they’re asking for.", fit=None, lead=[],
                           message="I need your resume to check your fit and tailor it. It takes about a minute.",
                           requirements=[{"text": "React", "mark": "unknown", "state": ""}])
        html = self.get()
        self.assertIn("I need your resume to check your fit and tailor it. It takes about a minute.", html)
        self.assertIn('href="/profile">Add your resume</a>', html)
        self.assertIn('href="/open/5"', html)
        self.assertNotIn("In your resume", html)
        self.assertNotIn("What I’d lead with", html)

    def test_no_text_has_only_the_line_and_apply(self):
        self.body = sample(state="no_text", say="I couldn’t read this posting, so I can’t check your fit or tailor for it.",
                           fit=None, lead=[], requirements=[])
        html = self.get()
        self.assertIn("I couldn’t read this posting", html)
        self.assertNotIn("What they’re asking for", html)
        self.assertIn('href="/open/5"', html)
        self.assertNotIn("Add your resume", html)

    def test_an_unknown_job_is_a_404(self):
        self.body = None
        self.assertEqual(self.client.get("/prep/5").status_code, 404)

    def test_it_needs_a_sign_in(self):
        self.signed_in = False
        r = self.client.get("/prep/5")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])


if __name__ == "__main__":
    unittest.main()
