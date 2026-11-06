"""SC-72 slice 2: tailoring. The accuracy check (nothing new is ever added), the changes built from the model's edits,
applying the kept ones, the caps, and running one tailoring end to end with the database and both model calls faked,
so no test spends money."""
import copy
import datetime
import unittest
from unittest import mock

from dashboard import resume as rs
from dashboard import tailor as t

STRUCT = {
    "summary": "Product designer with 10 years of experience across B2B and consumer products.",
    "experience": [
        {"company": "Northwind Health", "title": "Design Lead", "location": "", "dates": "2022 to now",
         "bullets": ["Ran research and shipped a redesign that cut support calls by 20%.",
                     "Owned the design system in Figma and hired two designers.",
                     "Worked with developers to keep designs and code in sync."]},
        {"company": "Fieldstone", "title": "Senior Product Designer", "location": "", "dates": "2018 to 2022",
         "bullets": ["Designed onboarding and billing flows for a B2B platform.",
                     "Tested prototypes with customers every sprint."]},
    ],
    "skills": ["Figma", "Prototyping", "User research", "Accessibility"],
    "education": [], "other": [],
}
RESUME_TEXT = " ".join([STRUCT["summary"]] + [b for r in STRUCT["experience"] for b in r["bullets"]] +
                       STRUCT["skills"] + ["Northwind Health", "Fieldstone"])
REQS = [{"label": "Design systems", "terms": ["design system"]}, {"label": "Working knowledge of React", "terms": ["react"]},
        {"label": "Accessibility", "terms": ["accessibility"]}]


def patterns():
    return t.missing_requirements(REQS, RESUME_TEXT.lower())[1]


def words():
    return set(t.NAME_RE.findall(RESUME_TEXT.lower())) | set(t._words(RESUME_TEXT))


class Numbers(unittest.TestCase):
    def test_the_same_numbers_in_any_form_compare_equal(self):
        self.assertEqual(t.numbers("cut calls by 20% and hired two designers"),
                         t.numbers("Hired 2 designers; calls down 20 percent"))
        self.assertEqual(t.numbers("saved $1.2M over 3 years"), t.numbers("3 years, saving $1.2M"))

    def test_an_added_dropped_or_changed_number_is_different(self):
        base = t.numbers("cut calls by 20%")
        self.assertNotEqual(base, t.numbers("cut calls by 25%"))
        self.assertNotEqual(base, t.numbers("cut calls"))
        self.assertNotEqual(base, t.numbers("cut calls by 20% across 3 teams"))
        self.assertNotEqual(t.numbers("20%"), t.numbers("20"))   # a percentage is not a count


class Names(unittest.TestCase):
    def test_tools_and_products_are_names_but_an_opening_verb_is_not(self):
        self.assertEqual(t.names("Led a team using Figma and GitHub"), ["figma", "github"])
        self.assertEqual(t.names("Built UX research with iOS testers"), ["ux", "ios"])
        self.assertEqual(t.names("Led design. Built tools."), [])


class AcceptRewrite(unittest.TestCase):
    before = STRUCT["experience"][0]["bullets"][1]   # Owned the design system in Figma and hired two designers.

    def ok(self, after):
        return t.accept_rewrite(self.before, after, words(), patterns())

    def test_a_reworded_line_with_the_same_facts_is_accepted(self):
        self.assertTrue(self.ok("Led the design system in Figma and hired two designers."))

    def test_a_name_from_elsewhere_in_the_resume_is_fine(self):
        self.assertTrue(self.ok("Owned the design system, working in Figma, and hired two designers at Northwind Health."))

    def test_a_new_number_is_refused(self):
        self.assertFalse(self.ok("Owned the design system in Figma and hired three designers."))
        self.assertFalse(self.ok("Owned the design system in Figma and hired two designers across 4 teams."))

    def test_a_dropped_number_is_refused_too(self):
        self.assertFalse(self.ok("Owned the design system in Figma and hired designers."))

    def test_a_tool_or_company_the_resume_never_names_is_refused(self):
        self.assertFalse(self.ok("Owned the design system in Figma and Storybook and hired two designers."))
        self.assertFalse(self.ok("Owned the design system in Figma at Acme and hired two designers."))

    def test_a_requirement_the_resume_lacks_is_refused_even_in_lower_case(self):
        self.assertFalse(self.ok("Owned the design system in Figma, built with react, and hired two designers."))

    def test_an_unchanged_too_long_or_too_short_line_is_refused(self):
        self.assertFalse(self.ok(self.before))
        self.assertFalse(self.ok(self.before.upper()))
        self.assertFalse(self.ok(self.before + " " + self.before + " " + self.before))
        self.assertFalse(self.ok("Owned it."))
        self.assertFalse(self.ok(""))


class Structure(unittest.TestCase):
    def test_clean_structure_bounds_and_trims(self):
        raw = {"summary": "  A  summary ", "experience": [{"company": "Co", "title": "T", "location": None, "dates": "x",
                                                         "bullets": ["one  line", "", 5]}, "junk"],
               "skills": ["Figma", " ", "Sketch"], "education": "no", "other": [{"heading": "", "lines": ["x"]}]}
        out = t.clean_structure(raw)
        self.assertEqual(out["summary"], "A summary")
        self.assertEqual(out["experience"], [{"company": "Co", "title": "T", "location": "", "dates": "x", "bullets": ["one line"]}])
        self.assertEqual((out["skills"], out["education"], out["other"]), (["Figma", "Sketch"], [], []))
        self.assertEqual(t.clean_structure("nonsense")["experience"], [])

    def test_a_read_that_matches_the_resume_text_passes(self):
        self.assertTrue(t.check_structure(STRUCT, RESUME_TEXT))
        spaced = RESUME_TEXT.replace("design system", "design  sys- tem").replace("sync.", "ﬁx sync.")
        self.assertTrue(t.check_structure(STRUCT, " ".join(spaced.split())))   # layout noise does not matter

    def test_one_paraphrased_line_throws_the_whole_read_away(self):
        changed = copy.deepcopy(STRUCT)
        changed["experience"][1]["bullets"][0] = "Created onboarding and payment journeys for a business software product."
        self.assertFalse(t.check_structure(changed, RESUME_TEXT))

    def test_a_read_with_nothing_in_it_fails(self):
        self.assertFalse(t.check_structure(t.clean_structure({}), RESUME_TEXT))


def edit(**kw):
    out = {"rewrites": [], "bullet_order": [], "skills_order": {"skills": [], "why": ""}}
    out.update(kw)
    return out


class BuildChanges(unittest.TestCase):
    def build(self, raw):
        return t.build_changes(STRUCT, raw, RESUME_TEXT, patterns())

    def test_good_edits_become_changes_with_before_taken_from_the_resume_not_the_model(self):
        changes, rejected = self.build(edit(
            rewrites=[{"id": "e0b1", "after": "Led the design system in Figma and hired two designers.", "why": "Leads with design systems."},
                      {"id": "summary", "after": "Product designer with 10 years of experience, mostly B2B and consumer products.", "why": "Shorter."}],
            bullet_order=[{"role": "e0", "order": ["e0b1", "e0b0", "e0b2"], "why": "Design systems first."}],
            skills_order={"skills": ["accessibility", "Figma", "Prototyping", "User research"], "why": "They ask for it."}))
        self.assertEqual(rejected, 0)
        self.assertEqual([c["id"] for c in changes], ["c1", "c2", "c3", "c4"])
        self.assertEqual([c["label"] for c in changes], ["Summary, reworded", "Bullet, reworded", "Bullets, reordered", "Skills, reordered"])
        self.assertEqual(changes[0]["before"], STRUCT["summary"])
        self.assertEqual(changes[1]["before"], STRUCT["experience"][0]["bullets"][1])
        self.assertEqual(changes[1]["where"], "Design Lead, Northwind Health")
        self.assertEqual(changes[2]["value"], ["e0b1", "e0b0", "e0b2"])
        self.assertEqual(changes[3]["after"], "Accessibility, Figma, Prototyping, User research")   # the person's own spelling
        self.assertTrue(all(c["kept"] for c in changes))

    def test_edits_that_break_a_rule_are_dropped_and_counted(self):
        changes, rejected = self.build(edit(
            rewrites=[{"id": "e0b0", "after": "Ran research and shipped a redesign that cut support calls by 40%.", "why": ""},
                      {"id": "e9b9", "after": "Anything at all about something else entirely.", "why": ""},
                      {"id": "e1b1", "after": "Tested prototypes with React developers every sprint.", "why": ""},
                      "not an object"]))
        self.assertEqual((changes, rejected), ([], 4))

    def test_a_line_returned_unchanged_is_ignored_not_counted_as_a_mistake(self):
        line = STRUCT["experience"][0]["bullets"][1]
        self.assertEqual(self.build(edit(rewrites=[{"id": "e0b1", "after": line, "why": ""}])), ([], 0))
        self.assertEqual(self.build(edit(rewrites=[{"id": "e0b1", "after": line.upper() + " ", "why": ""}])), ([], 0))

    def test_an_id_is_changed_at_most_once(self):
        good = {"id": "e0b1", "after": "Led the design system in Figma and hired two designers.", "why": ""}
        changes, rejected = self.build(edit(rewrites=[good, dict(good, after="Ran the design system in Figma and hired two designers.")]))
        self.assertEqual((len(changes), rejected), (1, 1))

    def test_a_new_bullet_order_must_use_exactly_the_roles_bullets(self):
        for order in (["e0b1", "e0b0"], ["e0b1", "e0b0", "e0b2", "e0b3"], ["e0b1", "e0b0", "e1b0"], ["e0b0", "e0b1", "e0b2"]):
            changes, rejected = self.build(edit(bullet_order=[{"role": "e0", "order": order, "why": ""}]))
            self.assertEqual((changes, rejected), ([], 1), order)

    def test_skills_may_only_be_reordered(self):
        for skills in (["Figma", "Prototyping", "User research", "React"], ["Figma", "Prototyping"], ["Figma", "Figma", "Prototyping", "User research"]):
            changes, rejected = self.build(edit(skills_order={"skills": skills, "why": ""}))
            self.assertEqual((changes, rejected), ([], 1), skills)
        self.assertEqual(self.build(edit(skills_order={"skills": list(STRUCT["skills"]), "why": ""})), ([], 1))   # same order: nothing to do

    def test_an_empty_answer_is_no_changes(self):
        self.assertEqual(self.build(edit()), ([], 0))
        self.assertEqual(self.build("garbage"), ([], 0))

    def test_never_more_than_the_limit(self):
        big = copy.deepcopy(STRUCT)
        big["experience"] = [{"company": "C", "title": "T", "location": "", "dates": "", "bullets": [f"Built the thing number {n} for customers." for n in range(10)]}]
        raw = edit(rewrites=[{"id": f"e0b{n}", "after": f"Made the thing number {n} for customers.", "why": ""} for n in range(10)])
        changes, _ = t.build_changes(big, raw, " ".join(big["experience"][0]["bullets"]), [])
        self.assertEqual(len(changes), t.MAX_REWRITES)


class Apply(unittest.TestCase):
    def changes(self):
        return t.build_changes(STRUCT, edit(
            rewrites=[{"id": "e0b1", "after": "Led the design system in Figma and hired two designers.", "why": ""},
                      {"id": "summary", "after": "Product designer with 10 years of experience, mostly B2B and consumer products.", "why": ""}],
            bullet_order=[{"role": "e0", "order": ["e0b1", "e0b0", "e0b2"], "why": ""}],
            skills_order={"skills": ["Accessibility", "Figma", "Prototyping", "User research"], "why": ""}), RESUME_TEXT, patterns())[0]

    def test_kept_changes_are_made_and_the_source_is_left_alone(self):
        before = copy.deepcopy(STRUCT)
        out = t.apply_changes(STRUCT, self.changes())
        self.assertEqual(STRUCT, before)
        self.assertEqual(out["summary"], "Product designer with 10 years of experience, mostly B2B and consumer products.")
        self.assertEqual(out["experience"][0]["bullets"][0], "Led the design system in Figma and hired two designers.")   # moved up, reworded
        self.assertEqual(out["experience"][0]["bullets"][1:], [STRUCT["experience"][0]["bullets"][0], STRUCT["experience"][0]["bullets"][2]])
        self.assertEqual(out["skills"], ["Accessibility", "Figma", "Prototyping", "User research"])
        self.assertEqual(out["experience"][1], STRUCT["experience"][1])

    def test_an_undone_change_leaves_that_part_as_it_was(self):
        changes = self.changes()
        for c in changes:
            c["kept"] = False
        self.assertEqual(t.apply_changes(STRUCT, changes), STRUCT)
        changes[3]["kept"] = True   # only the skills
        out = t.apply_changes(STRUCT, changes)
        self.assertEqual(out["skills"][0], "Accessibility")
        self.assertEqual(out["summary"], STRUCT["summary"])
        self.assertEqual(t.kept_count(changes), 1)

    def test_nothing_is_added_whatever_is_kept(self):
        out = t.apply_changes(STRUCT, self.changes())
        self.assertEqual(sorted(out["skills"]), sorted(STRUCT["skills"]))
        for new, old in zip(out["experience"], STRUCT["experience"]):
            self.assertEqual(len(new["bullets"]), len(old["bullets"]))


class Requirements(unittest.TestCase):
    def test_what_the_resume_lacks_is_listed_and_patterned(self):
        labels, pats = t.missing_requirements(REQS, RESUME_TEXT.lower())
        self.assertEqual(labels, ["Working knowledge of React"])
        self.assertTrue(pats[0].search("builds in React"))
        self.assertFalse(pats[0].search("reactive design"))


class Caps(unittest.TestCase):
    def check(self, usage, configured=True, cap="5"):
        with mock.patch.object(t, "tailor_usage", lambda c, u: usage), \
                mock.patch.object(t, "is_configured", lambda: configured), \
                mock.patch.dict("os.environ", {"TAILOR_MONTHLY_CAP_USD": cap}):
            return t.availability(None, 1)

    def test_states(self):
        self.assertEqual(self.check((3, 0.4)), ("ok", 3))
        self.assertEqual(self.check((9, 0.4)), ("ok", 9))
        self.assertEqual(self.check((10, 0.4)), ("limit", 10))
        self.assertEqual(self.check((3, 5.0)), ("unavailable", 3))      # Scout's month is spent
        self.assertEqual(self.check((3, 0.4), configured=False), ("unavailable", 3))
        self.assertEqual(self.check((10, 6.0)), ("unavailable", 10))    # unavailable wins over limit
        self.assertEqual(self.check((3, 4.99), cap="oops")[0], "ok")    # a bad cap setting falls back to the default

    def test_reset_date_is_the_first_of_next_month(self):
        self.assertEqual(t.next_reset(datetime.date(2026, 11, 6)), "December 1")
        self.assertEqual(t.next_reset(datetime.date(2026, 12, 31)), "January 1")
        self.assertEqual(t.next_reset(datetime.date(2026, 1, 31)), "February 1")

    def test_cost_is_by_kind_of_call(self):
        self.assertEqual(t.cost_usd(10000, 2000, "tailor"), 0.04)   # Sonnet: $2 and $10 per million
        self.assertEqual(t.cost_usd(10000, 2000, "structure"), round(10000 * rs.PRICE_IN_PER_MTOK / 1e6 + 2000 * rs.PRICE_OUT_PER_MTOK / 1e6, 6))


class Run(unittest.TestCase):
    """_run with the database functions replaced by a small fake that records what was written."""

    def setUp(self):
        self.uploaded = "t0"
        self.cached, self.tailoring_status, self.finish_ok = None, "working", True
        self.finished, self.calls, self.saved, self.read_calls, self.tailor_calls = [], [], [], [], []
        self.raw_edit = edit(rewrites=[{"id": "e0b1", "after": "Led the design system in Figma and hired two designers.", "why": "Leads with design systems."}])

        def finish(conn, tid, **kw):
            self.finished.append(dict(kw, tid=tid))
            return self.finish_ok

        def read(pdf):
            self.read_calls.append(pdf)
            return rs.ReadResult(STRUCT, 3000, 900)

        def tailor(structure, text, requirements, missing):
            self.tailor_calls.append(missing)
            return rs.ReadResult(self.raw_edit, 6000, 700, "req_1", "claude-sonnet-5-5")

        patches = {
            "get_resume": lambda c, u, with_content=False: {"content": b"%PDF", "uploaded_at": self.uploaded},
            "get_posting_text": lambda c, p: ("The posting text", REQS),
            "resume_text_for": lambda c, u: RESUME_TEXT,
            "get_user_profile": lambda c, u: {"skills": [], "titles": []},
            "get_resume_structure": lambda c, u: self.cached,
            "save_resume_structure": lambda c, u, s, up: self.saved.append((s, up)),
            "get_tailoring": lambda c, u, tid: {"status": self.tailoring_status},
            "finish_tailoring": finish,
            "log_tailor_call": lambda c, u, **kw: self.calls.append(kw),
            "READ_PROVIDER": read,
            "PROVIDER": tailor,
        }
        for name, value in patches.items():
            p = mock.patch.object(t, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.conn = mock.MagicMock()

    def go(self):
        t._run(self.conn, 9, 7, 5)

    def test_first_tailoring_reads_the_resume_then_tailors_and_logs_both_calls(self):
        self.go()
        self.assertEqual(len(self.read_calls), 1)
        self.assertEqual(self.saved, [(t.clean_structure(STRUCT), "t0")])
        self.assertEqual(self.tailor_calls, [["Working knowledge of React"]])   # what the resume lacks is told to the model
        done = self.finished[-1]
        self.assertEqual((done["status"], len(done["changes"]), done["rejected"]), ("ready", 1, 0))
        self.assertEqual(done["not_added"], ["Working knowledge of React"])
        self.assertEqual(done["resume_uploaded_at"], "t0")
        self.assertEqual([(c["kind"], c["status"]) for c in self.calls], [("structure", "ok"), ("tailor", "ok")])
        self.assertEqual(self.calls[1]["cost_usd"], t.cost_usd(6000, 700, "tailor"))
        self.assertEqual(self.calls[1]["request_id"], "req_1")

    def test_a_resume_that_was_read_before_is_not_read_again(self):
        self.cached = (t.clean_structure(STRUCT), "t0")
        self.go()
        self.assertEqual((self.read_calls, self.saved), ([], []))
        self.assertEqual([c["kind"] for c in self.calls], ["tailor"])

    def test_a_read_that_does_not_match_the_resume_stops_before_the_main_call(self):
        bad = copy.deepcopy(STRUCT)
        bad["experience"][0]["bullets"][0] = "Invented a whole new accomplishment that is nowhere in this document."
        with mock.patch.object(t, "READ_PROVIDER", lambda pdf: rs.ReadResult(bad, 3000, 900)):
            self.go()
        self.assertEqual((self.saved, self.tailor_calls), ([], []))
        self.assertEqual((self.finished[-1]["status"], self.finished[-1]["error_kind"]), ("error", "unreadable_resume"))
        self.assertEqual([(c["kind"], c["status"]) for c in self.calls], [("structure", "error")])

    def test_a_failed_main_call_is_an_error_that_does_not_count(self):
        def down(*a):
            raise t.TailorError("unavailable")
        with mock.patch.object(t, "PROVIDER", down):
            self.go()
        self.assertEqual((self.finished[-1]["status"], self.finished[-1]["error_kind"]), ("error", "unavailable"))
        self.assertEqual(self.calls[-1]["status"], "error")

    def test_cancelled_before_the_main_call_spends_nothing_more(self):
        self.tailoring_status = "cancelled"
        self.go()
        self.assertEqual((self.tailor_calls, self.finished), ([], []))

    def test_cancelled_while_the_call_ran_is_paid_for_but_not_counted(self):
        self.finish_ok = False   # the row is no longer working when the answer comes back
        self.go()
        self.assertEqual(self.calls[-1]["status"], "cancelled")
        self.assertGreater(self.calls[-1]["cost_usd"], 0)

    def test_nothing_worth_changing_is_shown_but_not_counted(self):
        self.raw_edit = edit()
        self.go()
        self.assertEqual((self.finished[-1]["status"], self.finished[-1]["changes"]), ("ready", []))
        self.assertEqual((self.calls[-1]["status"], self.calls[-1]["error_kind"]), ("error", "nothing"))

    def test_edits_that_break_the_rules_never_reach_the_person(self):
        self.raw_edit = edit(rewrites=[{"id": "e0b1", "after": "Owned the design system in Figma and React and hired two designers.", "why": ""}])
        self.go()
        self.assertEqual((self.finished[-1]["changes"], self.finished[-1]["rejected"]), ([], 1))

    def test_a_posting_without_requirements_cannot_be_tailored(self):
        with mock.patch.object(t, "get_posting_text", lambda c, p: ("text", None)):
            self.go()
        self.assertEqual((self.read_calls, self.tailor_calls), ([], []))
        self.assertEqual(self.finished[-1]["status"], "error")

    def test_run_turns_a_crash_into_an_error_and_never_raises(self):
        conn = mock.MagicMock()
        with mock.patch.object(t, "get_conn", lambda: conn), mock.patch.object(t, "_run", side_effect=RuntimeError("boom")), \
                self.assertLogs("dashboard.tailor", "ERROR"):
            t.run(9, 7, 5)
        self.assertEqual((self.finished[-1]["status"], self.finished[-1]["error_kind"]), ("error", "failed"))
        conn.close.assert_called_once()

    def test_start_creates_one_tailoring_and_runs_it_in_the_background(self):
        ran = []
        with mock.patch.object(t, "create_tailoring", lambda c, u, p: 9), mock.patch.object(t, "spawn", lambda fn: ran.append(fn)), \
                mock.patch.object(t, "run", lambda *a: ran.append(a)):
            self.assertEqual(t.start(self.conn, 7, 5), 9)
            ran[0]()
        self.assertEqual(ran[1], (9, 7, 5))
        with mock.patch.object(t, "create_tailoring", lambda c, u, p: None), mock.patch.object(t, "spawn", lambda fn: self.fail("started twice")):
            self.assertIsNone(t.start(self.conn, 7, 5))   # one at a time per person

    def test_messages_say_what_happened(self):
        self.assertIn("didn’t count toward your 10", t.message("failed"))
        self.assertIn("didn’t count toward your 10", t.message("nothing"))
        self.assertEqual(t.message("something new"), t.message("failed"))


if __name__ == "__main__":
    unittest.main()
