"""SC-70: the fit score. How a role scores against a person (plain matching), how requirements are read and
capped, and what the Open roles page does with the result."""
import re
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from dashboard import app as dapp
from dashboard import fit
from dashboard import requirements as rq
from dashboard import resume as rs
from dashboard.elevate import scout_line
from tests.test_hide_job import Base, row


def reqs(*names):
    return [{"label": n, "terms": [n]} for n in names]


A, B, C = "figma", "research", "storytelling"


class Matching(unittest.TestCase):
    def test_a_requirement_is_covered_when_any_of_its_terms_appears_as_a_whole_phrase(self):
        r = [{"label": "Design systems", "terms": ["design system", "component library"]},
             {"label": "Java", "terms": ["java"]}]
        self.assertEqual(fit.covered(r, "built a component library; wrote javascript"), 1)   # javascript is not java
        self.assertEqual(fit.covered(r, "design systems and java"), 2)        # a plural counts
        self.assertEqual(fit.covered(r, "nothing here"), 0)
        self.assertEqual(fit.covered([{"label": "x", "terms": ["a"]}], "a"), 0)   # a one-letter term would match stray letters
        self.assertEqual(fit.covered([{"label": "x", "terms": ["design"]}], "a redesign"), 0)   # not inside a longer word

    def test_the_evidence_is_the_resume_plus_saved_skills_and_titles(self):
        text = fit.evidence("Led Figma work", ["Prototyping (Figma)"], ["Product Designer"])
        for word in ("led figma work", "prototyping", "product designer"):
            self.assertIn(word, text)

    def test_level_is_two_for_exact_one_for_a_step_off_and_nothing_if_either_side_states_none(self):
        self.assertEqual(fit.level_points("Senior Product Designer", "senior"), 2)
        self.assertEqual(fit.level_points("Staff Product Designer", "senior"), 1)
        self.assertEqual(fit.level_points("Principal Product Designer", "senior"), 0)
        self.assertEqual(fit.level_points("Junior Designer", "mid"), 0)
        self.assertIsNone(fit.level_points("Product Designer", "senior"))
        self.assertIsNone(fit.level_points("Senior Product Designer", None))


class Scoring(unittest.TestCase):
    def test_all_three_parts_make_ten(self):
        self.assertEqual(fit.score(reqs(A), A, 2, 2, "Senior Designer", "senior"), 10)
        self.assertEqual(fit.score(reqs(A), "nothing", 2, 0, "Principal Designer", "senior"), 0)

    def test_the_weights_are_six_two_and_two(self):
        self.assertEqual(fit.score(reqs(A, B), A, 0, 0, "Designer", None), 5)                  # half of the requirements
        self.assertEqual(fit.score(reqs(A), "nothing", 2, 2, "Product Designer", None), 2)     # 2 of 8: requirements are 6
        self.assertEqual(fit.score(reqs(A), A, 4, 2, "Product Designer", None), 9)             # 6 + 1 of 8 possible
        self.assertEqual(fit.score(reqs(A, B, C), A, 2, 2, "Staff Designer", "senior"), 5)     # 2 + 2 + 1 of 10

    def test_what_cannot_be_judged_is_left_out_and_the_rest_scaled_to_ten(self):
        self.assertEqual(fit.score(reqs(A, B), A, 0, 0, "Designer", None), 5)                  # only requirements
        self.assertEqual(fit.score([], A, 2, 1, "Designer", None), 5)                          # none found: skills only
        self.assertEqual(fit.score(reqs(A), "   ", 0, 0, "Designer", None), None)              # nothing to check against

    def test_no_requirements_read_yet_means_no_score(self):
        self.assertIsNone(fit.score(None, A, 3, 3, "Senior Designer", "senior"))

    def test_pips(self):
        self.assertEqual([fit.pips(n) for n in range(11)], [1, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5])
        self.assertEqual(fit.pips(None), 0)

    def test_close_phrase_and_line(self):
        self.assertEqual(fit.close_phrase(0), "")
        self.assertEqual(fit.close_phrase(1), "One looks close to what you’re after.")
        self.assertEqual(fit.close_phrase(2), "Two look close to what you’re after.")
        self.assertEqual(fit.close_phrase(12), "12 look close to what you’re after.")
        self.assertEqual(scout_line(3, 12, False, fit.close_phrase(2)), "3 new since yesterday. Two look close to what you’re after.")
        self.assertEqual(scout_line(0, 12, False, fit.close_phrase(1)), "Nothing new since yesterday. One looks close to what you’re after.")
        self.assertEqual(scout_line(0, 0, True, fit.close_phrase(2)), "You’re caught up. I’ll keep looking.")


class Normalizing(unittest.TestCase):
    def test_the_models_list_is_cut_down_to_a_safe_shape(self):
        raw = {"requirements": [{"label": "  Design   systems ", "terms": ["Design System", "design system", "x" * 80, 5, "Tokens (Figma)"],
                                 "ignore": "this"}, {"label": "", "terms": ["a"]}, {"label": "No terms", "terms": []}, "junk"] + [
            {"label": f"R{i}", "terms": [f"t{i}"]} for i in range(12)]}
        out = rq.normalize(raw)
        self.assertEqual(out[0], {"label": "Design systems", "terms": ["design system", "tokens"]})
        self.assertEqual(len(out), 8)
        self.assertTrue(all(set(r) == {"label", "terms"} for r in out))

    def test_anything_else_is_nothing(self):
        for raw in (None, [], "x", {"requirements": "x"}, {}):
            self.assertEqual(rq.normalize(raw), [])


class Reading(unittest.TestCase):
    def setUp(self):
        self.logged, self.stored, self.spend = [], {}, [0.0]
        self.pending = [(1, "text one"), (2, "text two"), (3, "text three")]
        self.commits = lambda: self.conn.commit.call_count
        self.conn = mock.Mock()
        patches = [
            mock.patch.object(rq, "get_conn", lambda: self.conn),
            mock.patch.object(rq, "get_unread_postings", lambda c, ids, n: list(self.pending)),
            mock.patch.object(rq, "posting_read_spend", lambda c: self.spend[0]),
            mock.patch.object(rq, "log_posting_read", lambda c, pid, **kw: self.logged.append((pid, kw))),
            mock.patch.object(rq, "set_requirements", lambda c, pid, r: self.stored.__setitem__(pid, r)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(setattr, rq, "PROVIDER", None)

    def provider(self, text):
        return rs.ReadResult({"requirements": [{"label": "Figma", "terms": ["Figma"]}]}, 2000, 300, request_id="req_9")

    def test_every_call_is_logged_with_tokens_and_cost_and_the_requirements_are_stored(self):
        rq.PROVIDER = self.provider
        self.assertEqual(rq.read_pending([1, 2, 3]), 3)
        pid, kw = self.logged[0]
        self.assertEqual((pid, kw["status"], kw["input_tokens"], kw["output_tokens"], kw["request_id"]), (1, "ok", 2000, 300, "req_9"))
        self.assertAlmostEqual(float(kw["cost_usd"]), 0.00035)
        self.assertEqual((kw["model"], kw["price_version"]), (rs.MODEL, rs.PRICE_VERSION))
        self.assertEqual(self.stored[1], [{"label": "Figma", "terms": ["figma"]}])
        self.assertEqual(sorted(self.stored), [1, 2, 3])

    def test_a_role_with_nothing_usable_is_stored_empty_so_it_is_not_asked_again(self):
        rq.PROVIDER = lambda text: rs.ReadResult({"requirements": []}, 100, 10)
        rq.read_pending([1])
        self.assertEqual(self.stored[1], [])

    def test_reading_stops_at_the_monthly_cap(self):
        rq.PROVIDER = self.provider
        self.spend[0] = rq.monthly_cap_usd()
        self.assertEqual(rq.read_pending([1, 2, 3]), 0)
        self.assertEqual((self.logged, self.stored), ([], {}))

    def test_the_cap_is_checked_before_each_call(self):
        calls = []
        def provider(text):
            calls.append(text)
            self.spend[0] = rq.monthly_cap_usd()      # the first call used up the month
            return rs.ReadResult({"requirements": []}, 10, 10)
        rq.PROVIDER = provider
        self.assertEqual(rq.read_pending([1, 2, 3]), 1)
        self.assertEqual(len(calls), 1)

    def test_the_cap_can_be_set_by_the_environment(self):
        with mock.patch.dict("os.environ", {"POSTING_MONTHLY_CAP_USD": "1.5"}):
            self.assertEqual(rq.monthly_cap_usd(), 1.5)
        with mock.patch.dict("os.environ", {"POSTING_MONTHLY_CAP_USD": "lots"}):
            self.assertEqual(rq.monthly_cap_usd(), rq.DEFAULT_MONTHLY_CAP_USD)

    def test_a_failed_call_is_logged_and_the_role_stays_unread(self):
        def sometimes(text):
            if text == "text two":
                raise rs.ResumeError("unreadable")
            return self.provider(text)
        rq.PROVIDER = sometimes
        self.assertEqual(rq.read_pending([1, 2, 3]), 2)
        self.assertEqual(sorted(self.stored), [1, 3])
        self.assertEqual([(p, kw["status"], kw["error_kind"]) for p, kw in self.logged if kw["status"] == "error"], [(2, "error", "unreadable")])

    def test_an_unavailable_service_stops_the_rest(self):
        def down(text):
            raise rs.ResumeError("unavailable")
        rq.PROVIDER = down
        self.assertEqual(rq.read_pending([1, 2, 3]), 0)
        self.assertEqual(len(self.logged), 1)

    def test_no_transaction_is_held_open_while_the_model_runs(self):
        self.pending = [(1, "text one")]
        seen = []
        def provider(text):
            seen.append(self.commits())
            return self.provider(text)
        rq.PROVIDER = provider
        rq.read_pending([1])
        self.assertGreaterEqual(seen[0], 2)      # the list's transaction and the one before the call were both ended

    def test_only_the_start_of_a_long_posting_is_sent(self):
        sent = []
        class Client:
            def __init__(self, **kw):
                self.messages = self
            def create(self, **kw):
                sent.append(kw["messages"][0]["content"][0]["text"])
                return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text='{"requirements": []}')],
                                       usage=SimpleNamespace(input_tokens=10, output_tokens=2), model=rs.MODEL)
        with mock.patch.dict("sys.modules", {"anthropic": SimpleNamespace(Anthropic=Client)}):
            rq.read_requirements("x" * 50000)
        self.assertEqual(len(sent[0]), rq.MAX_TEXT)

    def test_nothing_starts_without_a_key_or_roles(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(rq.queue([1, 2]))
        rq.PROVIDER = self.provider
        self.assertFalse(rq.queue([]))

    def test_only_one_reader_runs_at_a_time_and_the_next_can_start_after(self):
        rq.PROVIDER = self.provider
        gate, entered = threading.Event(), threading.Event()
        def slow(ids):
            entered.set()
            gate.wait(5)
        with mock.patch.object(rq, "read_pending", slow):
            self.assertTrue(rq.queue([1]))
            entered.wait(5)
            self.assertFalse(rq.queue([2]))     # one is already running
            gate.set()
            for _ in range(100):
                if rq._lock.acquire(blocking=False):
                    rq._lock.release()
                    break
                threading.Event().wait(0.02)
            self.assertTrue(rq.queue([3]))
            threading.Event().wait(0.1)


class ThePage(Base):
    def setUp(self):
        super().setUp()
        self.queued, self.stored_reqs = [], {1: reqs("figma", "research"), 2: reqs("figma")}
        self.resume = (True, "I use figma every day")
        profile = {"titles": ["designer"], "level": "senior", "one_level_up": False, "city": None, "skills": ["figma"]}
        patches = [
            mock.patch.object(dapp, "get_user_profile", lambda c, u: dict(profile)),
            mock.patch.object(dapp, "get_user_skill_cutoff", lambda c, u: None),
            mock.patch.object(fit, "get_requirements", lambda c, ids: {i: self.stored_reqs[i] for i in ids if i in self.stored_reqs}),
            mock.patch.object(fit, "get_resume_text", lambda c, u: self.resume),
            mock.patch.object(fit, "get_skill_matches", lambda c, ids, skills: {i: ["figma"] for i in ids}),
            mock.patch.object(fit, "get_unread_postings", lambda c, ids, n: [(i, "text") for i in ids]),
            mock.patch.object(dapp, "queue_reads", lambda ids: self.queued.append(list(ids))),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def fit_of(self, html, pid):
        c = self.card(html, pid)
        return re.findall(r'class="fg-pip fg-pip--on"', c), re.search(r"</span>\s*([A-Z]\w+ fit)</span>", c)

    def test_a_scored_role_shows_pips_and_a_word_and_an_unread_one_shows_none(self):
        html = self.get()
        on, word = self.fit_of(html, 1)
        self.assertEqual(len(on), 4)                      # 3 of 6 + 2 of 2 + 2 of 2 = 8 of 10
        self.assertEqual(word.group(1), "Great fit")
        self.assertNotIn("fg-job-fit", self.card(html, 3))

    def test_scouts_line_counts_the_jobs_that_look_close(self):
        self.assertIn("Two look close to what you’re after.", self.get())     # roles 1 and 2 are both 4 or 5 pips

    def test_a_role_nobody_has_seen_is_never_queued(self):
        self.stored_reqs = {}
        with mock.patch.object(dapp, "PAGE_SIZE", 2):
            self.get()
        self.assertEqual(self.queued, [[1, 2]])         # the third role is on the next page: not read, not paid for

    def test_a_role_beyond_the_page_that_was_already_read_still_counts_for_scouts_line(self):
        self.stored_reqs = {3: reqs(A)}
        with mock.patch.object(dapp, "PAGE_SIZE", 2):
            html = self.get()
        self.assertIn("One looks close", html)

    def test_three_pips_is_not_close(self):
        self.stored_reqs = {1: reqs(A, B, C), 2: reqs(A, B, C)}
        html = self.get()
        self.assertEqual(len(self.fit_of(html, 1)[0]), 3)
        self.assertNotIn("close to what you", html)

    def test_the_applications_tab_shows_no_pips(self):
        app = dict(id=1, title="Senior Product Designer", company_name="Northwind Health", ats_type="greenhouse",
                   location="Remote - United States", remote_type="remote", applied_at=None)
        with mock.patch.object(dapp, "get_applications", lambda c, u: [dict(app)]):
            html = self.get("/?tab=applications")
        self.assertIn("Senior Product Designer", html)
        self.assertNotIn("fg-job-fit", html)

    def test_the_roles_still_waiting_are_queued_to_be_read(self):
        self.get()
        self.assertEqual(self.queued, [[3]])

    def test_applications_and_hidden_jobs_show_no_pips(self):
        self.hidden = [dict(id=9, title="Designer", company_name="Copperline", ats_type="lever", location="Austin, TX",
                            remote_type=None, hidden_at=self.rows[0]["first_seen_at"], hidden_reason=None)]
        self.assertNotIn("fg-job-fit", self.get("/?view=hidden").split("fg-job-list")[1])

    def test_the_page_works_when_the_score_fails(self):
        with mock.patch.object(fit, "get_requirements", side_effect=RuntimeError("boom")):
            html = self.get()
        self.assertNotIn("fg-job-fit", html)
        self.assertIn("Product Designer", html)

    def test_a_person_without_a_profile_gets_no_score(self):
        with mock.patch.object(dapp, "get_user_profile", lambda c, u: None):
            html = self.get()
        self.assertNotIn("fg-job-fit", html)
        self.assertEqual(self.queued, [])

    def test_a_changed_resume_changes_the_score_on_the_next_load(self):
        self.stored_reqs = {1: reqs("research", "storytelling")}
        before = len(self.fit_of(self.get(), 1)[0])
        self.resume = (True, "research and storytelling")
        after = len(self.fit_of(self.get(), 1)[0])
        self.assertGreater(after, before)


class Upload(unittest.TestCase):
    def test_the_text_of_a_resume_is_kept_with_the_file(self):
        saved = []
        conn = mock.Mock()
        pdf = b"%PDF-1.4 fake"
        with mock.patch.object(rs, "reading_state", return_value=("ok", None)), \
             mock.patch.object(rs, "log_resume_parse", return_value=1), \
             mock.patch.object(rs, "pdf_text", return_value="the resume text"), \
             mock.patch.object(rs, "save_resume", side_effect=lambda c, u, n, content, text=None: saved.append(text)):
            rs.PROVIDER = lambda b: rs.ReadResult({"titles": [], "level": "unknown", "level_reason": "", "skills": []}, 1, 1)
            self.addCleanup(setattr, rs, "PROVIDER", None)
            rs.process_upload(conn, 7, "cv.pdf", pdf)
        self.assertEqual(saved, ["the resume text"])

    def test_a_pdf_that_cannot_be_read_gives_empty_text_not_an_error(self):
        self.assertEqual(fit.pdf_text(b"%PDF-1.4 not really a pdf"), "")
        self.assertEqual(fit.pdf_text(b""), "")

    def test_text_is_taken_from_the_pdf_the_first_time_a_score_needs_it(self):
        conn, saved = mock.Mock(), []
        with mock.patch.object(fit, "get_resume_text", return_value=(True, None)), \
             mock.patch.object(fit, "get_resume_content", return_value=b"%PDF"), \
             mock.patch.object(fit, "pdf_text", return_value="taken"), \
             mock.patch.object(fit, "set_resume_text", lambda c, u, t: saved.append((u, t))):
            self.assertEqual(fit.resume_text_for(conn, 5), "taken")
        self.assertEqual(saved, [(5, "taken")])

    def test_no_resume_means_no_text(self):
        with mock.patch.object(fit, "get_resume_text", return_value=(False, None)):
            self.assertEqual(fit.resume_text_for(mock.Mock(), 5), "")


if __name__ == "__main__":
    unittest.main()
