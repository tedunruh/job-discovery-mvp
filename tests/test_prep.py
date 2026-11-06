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
            "expire_stale_tailorings": lambda c, u: None,
            "latest_tailoring": lambda c, u, p: None,
            "get_resume": lambda c, u: {"uploaded_at": "t0"},
        }.items():
            p = mock.patch.object(prep, target, value)
            p.start()
            self.addCleanup(p.stop)

    def build(self):
        with mock.patch.object(prep.tailor, "availability", lambda conn, uid: ("ok", 3)), \
                mock.patch.object(prep.letter, "available", lambda conn, uid: True):
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
        for target, value in {"get_conn": lambda: mock.MagicMock(), "build": lambda conn, uid, pid, notice=None: self.body}.items():
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



def change(cid, **over):
    c = {"id": cid, "kind": "reword", "target": "summary", "label": "Summary, reworded", "where": "",
         "before": "Old <b>text</b>.", "after": "New text here.", "value": "New text here.", "why": "Because.", "kept": True}
    c.update(over)
    return c


class Stages(unittest.TestCase):
    """Which stage the panel is in for a job, from the person's tailorings and resume."""

    def setUp(self):
        self.found, self.uploaded, self.avail, self.letters = None, "t0", ("ok", 3), True
        for target, value in {"expire_stale_tailorings": lambda c, u: None, "latest_tailoring": lambda c, u, p: self.found,
                              "get_resume": lambda c, u: {"uploaded_at": self.uploaded}}.items():
            p = mock.patch.object(prep, target, value)
            p.start()
            self.addCleanup(p.stop)
        for module, name, value in ((prep.tailor, "availability", lambda conn, uid: self.avail),
                                    (prep.letter, "available", lambda conn, uid: self.letters)):
            p = mock.patch.object(module, name, value)
            p.start()
            self.addCleanup(p.stop)

    def stage(self, notice=None):
        out = {"company": "Co", "stage": "prep", "tid": None, "tailor": None, "review": None, "steps": [], "notice": None, "say": "x"}
        prep.stage_for(mock.MagicMock(), 7, 5, out, notice)
        return out

    def ready(self, changes=None, **over):
        row = {"id": 9, "status": "ready", "resume_uploaded_at": "t0", "not_added": ["React"], "ready_at": None,
               "want_letter": False, "letter": None, "letter_state": None,
               "changes": [change("c1"), change("c2", kept=False)] if changes is None else changes}
        row.update(over)
        return row

    def test_no_tailoring_is_prep_with_the_button_and_the_months_count(self):
        out = self.stage()
        self.assertEqual((out["stage"], out["tailor"]["state"], out["tailor"]["used"], out["tailor"]["limit"]), ("prep", "ok", 3, 10))

    def test_a_running_tailoring_is_the_working_stage(self):
        self.found = {"id": 9, "status": "working"}
        out = self.stage()
        self.assertEqual((out["stage"], out["tid"], out["say"]), ("working", 9, "I’m tailoring your resume for Co."))
        self.assertEqual(out["steps"][0], "Reading your resume…")

    def test_a_finished_tailoring_is_the_review_stage_with_a_name_for_each_button(self):
        self.found = self.ready()
        out = self.stage()
        self.assertEqual((out["stage"], out["say"]), ("review", "Here’s what I changed."))
        r = out["review"]
        self.assertEqual((r["total"], r["kept"], r["line"]), (2, 1, "1 of 2 changes kept"))
        self.assertEqual(r["changes"][0]["name"], "Summary, reworded: New text here.")
        self.assertTrue(r["not_added"].startswith("Not added: React. The posting asks for it and your resume doesn’t mention it, so I left it out."))

    def test_a_tailoring_made_from_an_older_resume_file_is_not_shown(self):
        self.found, self.uploaded = self.ready(), "t1"
        self.assertEqual(self.stage()["stage"], "prep")   # they uploaded a new one: tailor again

    def test_a_review_with_nothing_to_change_says_so(self):
        self.found = self.ready(changes=[])
        out = self.stage()
        self.assertEqual((out["stage"], out["say"], out["review"]["total"]), ("review", "I didn’t find anything worth changing.", 0))

    def test_looks_good_makes_it_the_ready_stage_and_the_letter_follows_what_is_allowed(self):
        self.found = self.ready(ready_at="t", letter_state="ready", letter="Hello")
        out = self.stage()
        self.assertEqual((out["stage"], out["say"]), ("ready", "You’re ready. I saved these with the job."))
        self.assertEqual((out["letter"]["state"], out["letter"]["text"], out["letter"]["can_write"]), ("ready", "Hello", False))
        self.found = self.ready(letter_state="error")
        out = self.stage()
        self.assertEqual((out["stage"], out["letter"]["state"], out["letter"]["can_write"]), ("review", "error", True))
        self.assertIn("didn’t count toward your 10", out["letter"]["message"])
        self.letters = False
        out = self.stage()
        self.assertEqual((out["letter"]["can_write"], out["letter"]["unavailable"]), (False, False))   # error text, no button
        self.found = self.ready()
        out = self.stage()
        self.assertEqual((out["letter"]["state"], out["letter"]["can_write"], out["letter"]["unavailable"]), ("none", False, True))

    def test_nothing_changed_means_no_ready_stage_and_no_letter(self):
        self.found = self.ready(changes=[], ready_at="t")
        out = self.stage()
        self.assertEqual((out["stage"], out["letter"]), ("review", None))

    def test_the_tailor_offer_includes_the_letter_only_when_they_can_have_one(self):
        self.assertTrue(self.stage()["tailor"]["letter"])
        self.letters = False
        self.assertFalse(self.stage()["tailor"]["letter"])
        self.letters, self.avail = True, ("limit", 10)
        self.assertFalse(self.stage()["tailor"]["letter"])   # no tailoring left, so no letter offer beside it

    def test_the_limit_and_an_unavailable_service_replace_the_button_with_a_message(self):
        self.avail = ("limit", 10)
        out = self.stage()
        self.assertEqual(out["tailor"]["state"], "limit")
        self.assertEqual(out["tailor"]["message"], f"You’ve tailored 10 resumes this month, the most for now. It starts over on {prep.tailor.next_reset()}.")
        self.avail = ("unavailable", 2)
        self.assertEqual(self.stage()["tailor"]["message"], "Tailoring isn’t available right now. You can still apply.")

    def test_a_notice_from_a_failed_attempt_rides_along(self):
        self.assertEqual(self.stage("It failed.")["notice"], "It failed.")

    def test_not_added_wording(self):
        self.assertIsNone(prep.not_added_text([]))
        self.assertEqual(prep.not_added_text(["React"]), "Not added: React. The posting asks for it and your resume doesn’t mention it, so I left it out. If you’ve used it, add it to your resume and tailor again.")
        self.assertEqual(prep.not_added_text(["React", "GraphQL"]), "Not added: React, GraphQL. The posting asks for them and your resume doesn’t mention them, so I left them out. If you’ve used them, add them to your resume and tailor again.")
        self.assertIn("A, B, C, D and 2 more.", prep.not_added_text(list("ABCDEF")))


class TailorRoutes(unittest.TestCase):
    def setUp(self):
        self.client = dapp.app.test_client()
        self.signed_in, self.body, self.row, self.found_change, self.started, self.cancelled = True, sample(stage="prep", tailor={"state": "ok", "used": 3, "limit": 10, "message": ""}), \
            {"id": 9, "posting_id": 5, "status": "working", "error_kind": None, "letter_state": None}, True, [], []
        self.builds, self.readied, self.saved_letters, self.letter_started, self.letters = [], [], [], [], True

        def build(conn, uid, pid, notice=None):
            self.builds.append(notice)
            return self.body

        for module, targets in {
            dapp: {"get_conn": lambda: mock.MagicMock(), "get_session_user": lambda c, t: (7, False) if self.signed_in else (None, False)},
            prep: {"get_conn": lambda: mock.MagicMock(), "build": build, "expire_stale_tailorings": lambda c, u: None,
                   "get_tailoring": lambda c, u, tid: self.row, "cancel_tailoring": lambda c, u, tid: self.cancelled.append(tid) or 5,
                   "set_change_kept": lambda c, u, tid, cid, kept: self.found_change and self.cancelled.append((cid, kept)) or self.found_change,
                   "set_tailoring_ready": lambda c, u, tid, ready: self.readied.append((tid, ready)) or (5 if self.found_change else None),
                   "save_letter": lambda c, u, tid, text: self.saved_letters.append((tid, text)) or self.found_change,
                   "get_company_name": lambda c, pid: "Juniper <Labs>"},
            prep.letter: {"available": lambda c, u: self.letters, "start": lambda c, u, tid: self.letter_started.append(tid) or True},
            prep.tailor: {"start": lambda c, u, p, want_letter=False: self.started.append(p) or 9},
        }.items():
            for target, value in targets.items():
                p = mock.patch.object(module, target, value)
                p.start()
                self.addCleanup(p.stop)
        self.client.set_cookie(dapp.SESSION_COOKIE, "tok")

    def test_the_prep_stage_offers_tailoring_with_the_count_and_keeps_apply(self):
        html = self.client.get("/prep/5").get_data(as_text=True)
        self.assertIn('action="/tailor/start/5" data-panel-form><button class="fg-button fg-button--primary" type="submit">Tailor my resume</button>', html)
        self.assertIn("3 of 10 tailored this month.", html)
        self.assertIn("I send your resume and this posting to Claude", html)
        self.assertIn("Apply<span class=\"fg-visually-hidden\"> without tailoring (opens the posting in a new tab)</span>", html)
        self.assertIn('<li class="fg-step" aria-current="step"><span class="fg-step-num" aria-hidden="true">1</span>Prep', html)

    def test_at_the_limit_there_is_no_button_only_the_message_and_apply(self):
        self.body = sample(stage="prep", tailor={"state": "limit", "used": 10, "limit": 10, "message": "You’ve tailored 10 resumes this month, the most for now. It starts over on December 1."})
        html = self.client.get("/prep/5").get_data(as_text=True)
        self.assertNotIn("Tailor my resume", html)
        self.assertIn("It starts over on December 1.", html)
        self.assertIn('href="/open/5"', html)

    def test_the_working_stage_shows_progress_cancel_and_the_poll_address(self):
        self.body = sample(stage="working", tid=9, say="I’m tailoring your resume for Juniper.", steps=["Reading the posting…", "Making the changes…"])
        html = self.client.get("/prep/5").get_data(as_text=True)
        self.assertIn('data-poll="/tailor/9"', html)
        self.assertIn("I’m tailoring your resume for Juniper.", html)
        self.assertIn('role="status" aria-live="polite" data-steps=', html)
        self.assertIn('action="/tailor/9/cancel" data-panel-form><button class="fg-button fg-button--secondary" type="submit">Cancel</button>', html)
        self.assertIn('aria-current="step"><span class="fg-step-num" aria-hidden="true">2</span>Tailor', html)
        self.assertNotIn("Tailor my resume", html)

    def test_the_review_stage_shows_every_change_with_undo_or_redo_and_the_count(self):
        changes = [dict(change("c1"), name="Summary, reworded: New text here.", lines=False),
                   dict(change("c2", kept=False, label="Skills, reordered"), name="Skills, reordered", lines=False),
                   dict(change("c3", label="Bullets, reordered", where="Lead, Co", before=["a", "b"], after=["b", "a"]), name="Bullets, reordered, Lead, Co: b", lines=True)]
        self.body = sample(stage="review", tid=9, say="Here’s what I changed.", review={
            "tid": 9, "total": 3, "kept": 2, "line": "2 of 3 changes kept", "changes": changes, "empty": "",
            "not_added": "Not added: React. The posting asks for it and your resume doesn’t mention it, so I left it out. If you’ve used it, add it to your resume and tailor again."})
        html = self.client.get("/prep/5").get_data(as_text=True)
        self.assertEqual(html.count('class="fg-change"') + html.count('class="fg-change is-undone"'), 3)
        self.assertEqual(html.count("is-undone"), 1)
        self.assertIn(' · Undone</span>', html)
        self.assertIn('Undo<span class="fg-visually-hidden"> Summary, reworded: New text here.</span>', html)
        self.assertIn('Redo<span class="fg-visually-hidden"> Skills, reordered</span>', html)
        self.assertIn('<input type="hidden" name="kept" value="0">', html)   # Undo sends kept=0; Redo sends 1
        self.assertIn('<input type="hidden" name="kept" value="1">', html)
        self.assertEqual(html.count("data-kept-line"), 2)
        self.assertIn("2 of 3 changes kept", html)
        self.assertIn("Old &lt;b&gt;text&lt;/b&gt;.", html)    # the model's and the resume's words are escaped
        self.assertIn("<ol><li>a</li><li>b</li></ol>", html)
        self.assertIn("Not added: React.", html)
        self.assertIn("I only reworded and reordered what’s already in your resume. Undo anything you don’t want.", html)
        self.assertIn('href="/open/5"', html)
        self.assertNotIn("Tailor my resume", html)

    def test_a_review_with_no_changes_says_so_and_has_no_list(self):
        self.body = sample(stage="review", tid=9, say="I didn’t find anything worth changing.", review={
            "tid": 9, "total": 0, "kept": 0, "line": "", "changes": [], "not_added": None,
            "empty": "I didn’t find anything worth changing in your resume for this job, so it didn’t count toward your 10."})
        html = self.client.get("/prep/5").get_data(as_text=True)
        self.assertIn("so it didn’t count toward your 10.", html)
        self.assertNotIn("fg-changes", html)

    def test_start_runs_a_tailoring_only_when_the_job_is_in_prep_and_allowed(self):
        self.assertEqual(self.client.post("/tailor/start/5").status_code, 200)
        self.assertEqual(self.started, [5])
        self.started.clear()
        for body in (sample(stage="working", tid=9, steps=["Reading the posting…"]), sample(stage="review", tid=9, review={"total": 0}),
                     sample(stage="prep", tailor={"state": "limit", "used": 10, "limit": 10, "message": "m"}),
                     sample(stage="prep", tailor={"state": "unavailable", "used": 1, "limit": 10, "message": "m"}),
                     sample(state="no_resume", stage="prep", tailor=None)):
            self.body = body
            self.assertEqual(self.client.post("/tailor/start/5").status_code, 200)
        self.assertEqual(self.started, [])   # a double click, a limit or a missing resume starts nothing
        self.body = None
        self.assertEqual(self.client.post("/tailor/start/5").status_code, 404)

    def test_status_is_204_while_working_then_the_panel(self):
        self.assertEqual(self.client.get("/tailor/9").status_code, 204)
        self.row["status"] = "ready"
        r = self.client.get("/tailor/9")
        self.assertEqual((r.status_code, self.builds[-1]), (200, None))
        self.row.update(status="error", error_kind="failed")
        self.client.get("/tailor/9")
        self.assertIn("didn’t count toward your 10", self.builds[-1])
        self.row.update(status="cancelled", error_kind=None)
        self.client.get("/tailor/9")
        self.assertIsNone(self.builds[-1])
        self.row = None
        self.assertEqual(self.client.get("/tailor/9").status_code, 404)

    def test_cancel_stops_it_and_answers_with_the_panel(self):
        self.assertEqual(self.client.post("/tailor/9/cancel").status_code, 200)
        self.assertEqual(self.cancelled, [9])

    def test_undo_and_redo_set_one_change_and_reject_anything_else(self):
        self.assertEqual(self.client.post("/tailor/9/change", data={"change": "c2", "kept": "0"}).status_code, 200)
        self.assertEqual(self.client.post("/tailor/9/change", data={"change": "c2", "kept": "1"}).status_code, 200)
        self.assertEqual(self.cancelled, [("c2", False), ("c2", True)])
        self.assertEqual(self.client.post("/tailor/9/change", data={"change": "c2", "kept": "maybe"}).status_code, 404)
        self.found_change = False
        self.assertEqual(self.client.post("/tailor/9/change", data={"change": "zzz", "kept": "0"}).status_code, 404)

    def test_every_tailor_route_needs_a_sign_in(self):
        self.signed_in = False
        for method, url in (("post", "/tailor/start/5"), ("get", "/tailor/9"), ("get", "/tailor/9/letter/status"), ("post", "/tailor/9/cancel"), ("post", "/tailor/9/change"),
                            ("post", "/tailor/9/ready"), ("post", "/tailor/9/letter"), ("post", "/tailor/9/letter/save"),
                            ("get", "/tailor/9/resume.pdf"), ("get", "/tailor/9/resume.docx"), ("get", "/tailor/9/letter.txt")):
            r = getattr(self.client, method)(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/login", r.headers["Location"])


if __name__ == "__main__":
    unittest.main()

    # --- slice 3: the letter, Ready and the files ---

    def test_the_prep_stage_offers_a_cover_letter_beside_tailoring_when_they_can_have_one(self):
        self.body = sample(stage="prep", tailor={"state": "ok", "used": 3, "limit": 10, "message": "", "letter": True})
        html = self.client.get("/prep/5").get_data(as_text=True)
        self.assertIn('<input class="fg-checkbox" type="checkbox" name="letter" value="1" form="tailor-start-5"> Write a cover letter too', html)
        self.assertIn('<form id="tailor-start-5" method="post" action="/tailor/start/5"', html)
        self.body = sample(stage="prep", tailor={"state": "ok", "used": 3, "limit": 10, "message": "", "letter": False})
        self.assertNotIn("cover letter", self.client.get("/prep/5").get_data(as_text=True))

    def test_the_letter_is_asked_for_only_when_ticked_and_allowed(self):
        asked = []
        self.body = sample(stage="prep", tailor={"state": "ok", "used": 3, "limit": 10, "message": "", "letter": True})
        with mock.patch.object(prep.tailor, "start", lambda c, u, p, want_letter=False: asked.append(want_letter) or 9):
            self.client.post("/tailor/start/5", data={"letter": "1"})
            self.client.post("/tailor/start/5")
            self.body = sample(stage="prep", tailor={"state": "ok", "used": 3, "limit": 10, "message": "", "letter": False})
            self.client.post("/tailor/start/5", data={"letter": "1"})
        self.assertEqual(asked, [True, False, False])

    def review_body(self, letter, **over):
        return sample(stage="review", tid=9, say="Here’s what I changed.", letter=dict({
            "tid": 9, "state": "none", "text": "", "can_write": True, "unavailable": False, "message": "", "chars": 3000}, **letter),
            review={"tid": 9, "total": 1, "kept": 1, "line": "1 of 1 changes kept", "changes": [dict(change("c1"), name="Summary", lines=False)],
                    "empty": "", "not_added": None}, **over)

    def test_review_has_looks_good_first_and_apply_second(self):
        self.body = self.review_body({})
        html = self.client.get("/prep/5").get_data(as_text=True)
        self.assertIn('action="/tailor/9/ready" data-panel-form><input type="hidden" name="ready" value="1"><button class="fg-button fg-button--primary" type="submit">Looks good</button>', html)
        self.assertLess(html.index("Looks good"), html.index('href="/open/5"'))
        self.assertIn("Most applications don’t need one.", html)
        self.assertIn("It doesn’t count toward your 10 this month.", html)
        self.assertIn('action="/tailor/9/letter" data-panel-form="letter"><button class="fg-button fg-button--secondary" type="submit">Write a cover letter</button>', html)

    def test_the_letter_section_in_each_state(self):
        self.body = self.review_body({"state": "writing"})
        html = self.client.get("/prep/5").get_data(as_text=True)
        self.assertIn('data-poll="/tailor/9/letter/status" data-poll-swap="[data-letter]"', html)
        self.assertIn("Writing your cover letter…", html)
        self.assertNotIn("Write a cover letter", html)
        self.body = self.review_body({"state": "ready", "text": "Hello <b>team</b>,\n\n[Your name]", "can_write": False})
        html = self.client.get("/prep/5").get_data(as_text=True)
        self.assertIn('aria-labelledby="letter-title-9"', html)                      # the box is named by its heading
        self.assertIn("Hello &lt;b&gt;team&lt;/b&gt;,", html)                         # the model's words are escaped
        self.assertIn('data-announce="Your cover letter is ready."', html)
        self.assertIn('<button class="fg-button fg-button--secondary" type="button" data-copy-letter>Copy<span class="fg-visually-hidden"> cover letter</span></button>', html)
        self.assertIn("Fill in the bracketed parts; I don’t guess things I don’t know.", html)
        self.assertNotIn("data-poll", html)
        self.assertNotIn("Write a cover letter", html)                                # written once: no second button
        self.body = self.review_body({"state": "error", "message": "I couldn’t write it.", "can_write": True})
        html = self.client.get("/prep/5").get_data(as_text=True)
        self.assertIn('role="alert" tabindex="-1" data-letter-focus>I couldn’t write it.', html)
        self.assertIn(">Try again</button>", html)
        self.body = self.review_body({"unavailable": True, "can_write": False, "message": "Cover letters aren’t available right now."})
        html = self.client.get("/prep/5").get_data(as_text=True)
        self.assertIn("Cover letters aren’t available right now.", html)
        self.assertNotIn("Write a cover letter</button>", html)
        self.assertIn('href="/open/5"', html)                                         # apply is always there

    def ready_body(self, **letter):
        return sample(stage="ready", tid=9, say="You’re ready. I saved these with the job.", letter={
            "tid": 9, "state": "none", "text": "", "can_write": True, "unavailable": False, "message": "", "chars": 3000, **letter},
            review={"tid": 9, "total": 1, "kept": 1, "line": "", "changes": [], "empty": "", "not_added": None})

    def test_ready_has_both_files_named_for_the_company_the_letter_and_the_way_back(self):
        self.body = self.ready_body()
        html = self.client.get("/prep/5").get_data(as_text=True)
        self.assertIn("You’re ready. I saved these with the job.", html)
        self.assertIn('<a class="fg-button fg-button--secondary" href="/tailor/9/resume.pdf" download>Download<span class="fg-visually-hidden"> the PDF of your resume tailored for Juniper &lt;Labs&gt;</span></a>', html)
        self.assertIn('href="/tailor/9/resume.docx" download>Download<span class="fg-visually-hidden"> the Word file of your resume tailored for Juniper &lt;Labs&gt;</span>', html)
        self.assertIn('action="/tailor/9/ready" data-panel-form><input type="hidden" name="ready" value="0"><button class="fg-button fg-button--secondary" type="submit">Back to the changes</button>', html)
        self.assertIn("The posting opens on Ashby in a new tab. When you’re back, I’ll ask if you applied.", html)
        self.assertIn("data-tailored", html)                                          # the page marks the row Tailored
        self.assertIn('<li class="fg-step is-done"><span class="fg-step-num" aria-hidden="true">1</span>Prep', html)
        self.assertIn('aria-current="step"><span class="fg-step-num" aria-hidden="true">3</span>Apply', html)
        self.assertIn("Write a cover letter</button>", html)                          # a letter can still be asked for here
        self.assertNotIn("Looks good", html)

    def test_looks_good_and_back_to_the_changes(self):
        self.assertEqual(self.client.post("/tailor/9/ready", data={"ready": "1"}).status_code, 200)
        self.assertEqual(self.client.post("/tailor/9/ready", data={"ready": "0"}).status_code, 200)
        self.assertEqual(self.readied, [(9, True), (9, False)])
        self.assertEqual(self.client.post("/tailor/9/ready", data={"ready": "x"}).status_code, 404)
        self.found_change = False
        self.assertEqual(self.client.post("/tailor/9/ready", data={"ready": "1"}).status_code, 404)

    def test_a_letter_is_started_once_when_allowed(self):
        self.row["status"] = "ready"
        self.assertEqual(self.client.post("/tailor/9/letter").status_code, 200)
        self.assertEqual(self.letter_started, [9])
        self.letters = False
        self.assertEqual(self.client.post("/tailor/9/letter").status_code, 200)
        self.assertEqual(self.letter_started, [9])    # the month's spend is used up: nothing starts, the panel says so
        self.row = None
        self.assertEqual(self.client.post("/tailor/9/letter").status_code, 404)

    def test_the_tailoring_status_does_not_wait_for_a_letter_but_the_letter_status_does(self):
        self.row.update(status="ready", letter_state="writing")
        self.assertEqual(self.client.get("/tailor/9").status_code, 200)          # Review opens while the letter is still coming
        self.assertEqual(self.client.get("/tailor/9/letter/status").status_code, 204)
        self.row.update(letter_state="ready")
        self.assertEqual(self.client.get("/tailor/9/letter/status").status_code, 200)
        self.row = None
        self.assertEqual(self.client.get("/tailor/9/letter/status").status_code, 404)

    def test_edits_to_the_letter_are_saved_cleaned_and_only_for_the_owner(self):
        self.assertEqual(self.client.post("/tailor/9/letter/save", data={"letter": "Hi \x00 there\r\n\r\n\r\n\r\nBye"}).status_code, 204)
        self.assertEqual(self.saved_letters, [(9, "Hi  there\n\nBye")])
        self.assertEqual(self.client.post("/tailor/9/letter/save", data={"letter": "   "}).status_code, 404)   # never saves an empty letter
        self.found_change = False
        self.assertEqual(self.client.post("/tailor/9/letter/save", data={"letter": "Hello"}).status_code, 404)

    def test_files_come_only_from_a_ready_tailoring_the_person_said_looks_good_to(self):
        structure = {"header": ["Ada Lovelace"], "summary": "Designer.", "experience": [], "skills": [], "education": [], "other": []}
        self.row.update(status="ready", source=structure, changes=[], posting_id=5, ready_at="t", applied_at=None, letter="Hello,\n[Your name]")
        pdf = self.client.get("/tailor/9/resume.pdf")
        self.assertEqual((pdf.status_code, pdf.mimetype, pdf.data[:4]), (200, "application/pdf", b"%PDF"))
        self.assertEqual(pdf.headers["Content-Disposition"], 'attachment; filename="ada-lovelace-resume-juniper-labs.pdf"')
        self.assertEqual(pdf.headers["Cache-Control"], "no-store")
        word = self.client.get("/tailor/9/resume.docx")
        self.assertEqual((word.status_code, word.data[:2]), (200, b"PK"))
        self.assertTrue(word.headers["Content-Disposition"].endswith('.docx"'))
        text = self.client.get("/tailor/9/letter.txt")
        self.assertEqual((text.status_code, text.get_data(as_text=True)), (200, "Hello,\n[Your name]\n"))
        self.row.update(ready_at=None)                       # back to the changes: nothing to download
        for url in ("/tailor/9/resume.pdf", "/tailor/9/resume.docx", "/tailor/9/letter.txt"):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.row.update(applied_at="t")                      # but once it went with an application, it stays
        self.assertEqual(self.client.get("/tailor/9/resume.pdf").status_code, 200)
        self.row.update(status="cancelled")
        self.assertEqual(self.client.get("/tailor/9/resume.pdf").status_code, 404)
        self.row = None
        self.assertEqual(self.client.get("/tailor/9/resume.pdf").status_code, 404)
