"""SC-72 slice 3: the cover letter (what may be shown, how it is written and stored, what it costs), and the resume as PDF
and Word files. The model is faked (letter.PROVIDER) and the database replaced, so no test spends money."""
import copy
import io
import unittest
import zipfile
from unittest import mock

from dashboard import letter as lt
from dashboard import resume as rs
from dashboard import resume_files as rf
from dashboard import tailor as t

STRUCT = {
    "header": ["Ada Lovelace", "ada@example.com · 555-0100 · Chicago, IL"],
    "summary": "Product designer with 10 years of experience across B2B and consumer products.",
    "experience": [
        {"company": "Northwind Health", "title": "Design Lead", "location": "Remote", "dates": "2022 to now",
         "bullets": ["Ran research and shipped a redesign that cut support calls by 20%.",
                     "Owned the design system in Figma and hired two designers."]},
        {"company": "Fieldstone", "title": "Senior Product Designer", "location": "", "dates": "2018 to 2022",
         "bullets": ["Designed onboarding and billing flows for a B2B platform."]},
    ],
    "skills": ["Figma", "Prototyping", "User research"],
    "education": ["BFA, Design, 2012"], "other": [{"heading": "Awards", "lines": ["AIGA Award 2020"]}],
}
REQS = [{"label": "Design systems", "terms": ["design system"]}, {"label": "Working knowledge of React", "terms": ["react"]}]
GOOD = (
    "Hello Juniper team,\n\n"
    "I design products people use every day, and I have spent 10 years doing it across B2B and consumer products. At "
    "Northwind Health I owned the design system in Figma and hired two designers, and I ran the research behind a "
    "redesign that cut support calls by 20%.\n\n"
    "Your Senior Product Designer role asks for someone who can set a direction and then build it with developers. "
    "That is the work I enjoy most. Before Northwind Health, at Fieldstone, I designed onboarding and billing flows "
    "for a B2B platform. [One sentence on why you want to work at Juniper.]\n\n"
    "I would love to talk about the role. [Link to your portfolio]\n\nThank you,\n[Your name]"
)


def plain():
    return lt.resume_text(STRUCT)


def patterns():
    return t.missing_requirements(REQS, plain().lower())[1]


class CheckLetter(unittest.TestCase):
    def check(self, text):
        return lt.check_letter(text, plain(), "Juniper Labs", "Senior Product Designer", patterns())

    def test_a_letter_that_sticks_to_the_resume_passes(self):
        self.assertEqual(self.check(GOOD), (True, ""))

    def test_a_number_the_resume_does_not_have_is_refused(self):
        ok, why = self.check(GOOD.replace("10 years", "12 years"))
        self.assertFalse(ok)
        self.assertIn("number", why)

    def test_a_name_the_resume_does_not_have_is_refused(self):
        ok, why = self.check(GOOD.replace("in Figma", "in Sketch and Photoshop"))
        self.assertFalse(ok)
        self.assertIn("sketch", why)

    def test_a_requirement_the_resume_lacks_may_not_be_claimed(self):
        ok, why = self.check(GOOD.replace("with developers", "with developers in react"))
        self.assertFalse(ok)
        self.assertIn("posting asks for", why)

    def test_a_number_said_in_words_is_refused_unless_the_resume_says_it_that_way(self):
        ok, why = self.check(GOOD.replace("cut support calls by 20%", "cut support calls by a fifth"))
        self.assertFalse(ok)
        self.assertIn("in words", why)
        ok, _ = lt.check_letter(GOOD.replace("by 20%", "by a fifth"), plain() + "\ncut calls by a fifth", "Juniper Labs",
                                "Senior Product Designer", patterns())
        self.assertTrue(ok)   # the resume itself says it that way

    def test_the_first_word_of_a_paragraph_is_not_read_as_a_name(self):
        self.assertEqual(lt.check_letter(GOOD.replace("Hello Juniper team,\n\n", "Hello Juniper team,\n\nYour posting caught my eye. ", 1),
                                         plain(), "Juniper Labs", "Senior Product Designer", patterns()), (True, ""))

    def test_the_prompt_asks_for_a_through_line_and_for_how_and_result(self):
        for phrase in ("through line", "how and result", "bare claim", "skills list"):
            self.assertIn(phrase, lt.PROMPT.replace("how and its result", "how and result"))

    def test_the_company_and_role_are_not_made_up_facts(self):
        self.assertTrue(self.check(GOOD.replace("Hello Juniper team", "Hello Juniper Labs team"))[0])

    def test_what_is_in_brackets_is_for_the_person_and_is_not_checked(self):
        self.assertTrue(self.check(GOOD.replace("[Link to your portfolio]", "[Link to Ada's 2031 Behance page]"))[0])

    def test_too_short_or_too_long_is_refused(self):
        self.assertFalse(self.check("Hello Juniper team,\n\nHi.\n\nThank you,\n[Your name]")[0])
        self.assertFalse(self.check(GOOD + " " + "word " * 400)[0])

    def test_clean_letter_is_plain_bounded_text(self):
        self.assertEqual(lt.clean_letter("```\r\nHi\x00 there  \r\n\r\n\r\n\r\nBye\n```"), "Hi there\n\nBye")
        self.assertEqual(len(lt.clean_letter("x" * 9000)), lt.MAX_CHARS)


class Draft(unittest.TestCase):
    def setUp(self):
        self.drafts, self.calls, self.notes = [GOOD], [], []

        def provider(structure, posting, company, title, missing, note):
            self.notes.append((missing, note))
            return rs.ReadResult({"letter": self.drafts.pop(0)}, 4000, 400, "req_9", "claude-sonnet-5-5")

        for target, value in {"PROVIDER": provider}.items():
            p = mock.patch.object(lt, target, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(t, "log_tailor_call", lambda c, u, **kw: self.calls.append(kw))
        p.start()
        self.addCleanup(p.stop)

    def draft(self):
        return lt.draft(mock.MagicMock(), 7, STRUCT, ("The posting text", REQS), "Juniper Labs", "Senior Product Designer",
                        {"skills": [], "titles": []}, plain())

    def test_a_good_letter_is_returned_and_its_call_logged_as_a_letter(self):
        self.assertEqual(self.draft(), GOOD)
        self.assertEqual([(c["kind"], c["status"], c["cost_usd"]) for c in self.calls],
                         [("letter", "ok", t.cost_usd(4000, 400, "letter"))])
        self.assertEqual(self.notes[0][0], ["Working knowledge of React"])   # what the resume lacks is told to the model
        self.assertEqual(t.cost_usd(1000, 1000, "letter"), t.cost_usd(1000, 1000, "tailor"))   # Sonnet rates

    def test_a_rejected_draft_is_written_once_more_with_the_reason(self):
        self.drafts = [GOOD.replace("10 years", "12 years"), GOOD]
        self.assertEqual(self.draft(), GOOD)
        self.assertEqual([c["status"] for c in self.calls], ["error", "ok"])
        self.assertEqual(self.calls[0]["error_kind"], "rejected")
        self.assertIn("number", self.notes[1][1])

    def test_two_rejections_give_nothing(self):
        bad = GOOD.replace("10 years", "12 years")
        self.drafts = [bad, bad]
        self.assertIsNone(self.draft())
        self.assertEqual([c["status"] for c in self.calls], ["error", "error"])

    def test_a_failed_call_gives_nothing_and_does_not_count(self):
        def down(*a):
            raise t.TailorError("unavailable")
        with mock.patch.object(lt, "PROVIDER", down):
            self.assertIsNone(self.draft())
        self.assertEqual([(c["kind"], c["status"]) for c in self.calls], [("letter", "error")])


class RunLetter(unittest.TestCase):
    """The background job: only a ready tailoring whose letter is marked as being written is touched."""

    def setUp(self):
        self.row = {"status": "ready", "letter_state": "writing", "posting_id": 5, "source": STRUCT,
                    "changes": [{"id": "c1", "kind": "reword", "target": "summary", "value": "Designer with 10 years of B2B and consumer products.", "kept": True}]}
        self.finished, self.seen = [], []

        def provider(structure, posting, company, title, missing, note):
            self.seen.append(structure["summary"])
            return rs.ReadResult({"letter": GOOD}, 4000, 400, "r", "m")

        for target, value in {
            "get_tailoring": lambda c, u, tid: self.row,
            "get_posting_text": lambda c, p: ("text", REQS),
            "get_postings_by_ids": lambda c, u, ids: [{"company_name": "Juniper Labs", "title": "Senior Product Designer"}],
            "get_user_profile": lambda c, u: {"skills": [], "titles": []},
            "resume_text_for": lambda c, u: plain(),
            "finish_letter": lambda c, tid, text, state: self.finished.append((tid, text, state)),
            "PROVIDER": provider,
        }.items():
            p = mock.patch.object(lt, target, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(t, "log_tailor_call", lambda c, u, **kw: None)
        p.start()
        self.addCleanup(p.stop)

    def test_the_letter_is_written_from_the_resume_as_the_kept_changes_leave_it(self):
        lt._run(mock.MagicMock(), 9, 7)
        self.assertEqual(self.finished, [(9, GOOD, "ready")])
        self.assertEqual(self.seen, ["Designer with 10 years of B2B and consumer products."])

    def test_an_undone_change_is_not_in_the_letters_resume(self):
        self.row["changes"][0]["kept"] = False
        lt._run(mock.MagicMock(), 9, 7)
        self.assertEqual(self.seen, [STRUCT["summary"]])

    def test_nothing_is_written_unless_it_was_asked_for(self):
        for over in ({"letter_state": None}, {"letter_state": "ready"}, {"status": "working"}):
            self.row = dict(self.row, **over)
            lt._run(mock.MagicMock(), 9, 7)
        self.assertEqual((self.finished, self.seen), ([], []))

    def test_a_letter_that_cannot_be_trusted_is_an_error_not_text(self):
        with mock.patch.object(lt, "draft", lambda *a: None):
            lt._run(mock.MagicMock(), 9, 7)
        self.assertEqual(self.finished, [(9, None, "error")])

    def test_a_crash_marks_the_letter_as_an_error_and_never_raises(self):
        conn = mock.MagicMock()
        with mock.patch.object(lt, "get_conn", lambda: conn), mock.patch.object(lt, "_run", side_effect=RuntimeError("boom")):
            lt.run(9, 7)
        self.assertEqual(self.finished, [(9, None, "error")])
        conn.close.assert_called_once()

    def test_start_marks_it_writing_then_writes_in_the_background_but_only_once(self):
        ran = []
        with mock.patch.object(lt, "begin_letter", lambda c, u, tid: True), mock.patch.object(t, "spawn", lambda fn: ran.append(fn)), \
                mock.patch.object(lt, "run", lambda *a: ran.append(a)):
            self.assertTrue(lt.start(mock.MagicMock(), 7, 9))
            ran[0]()
        self.assertEqual(ran[1], (9, 7))
        with mock.patch.object(lt, "begin_letter", lambda c, u, tid: False), mock.patch.object(t, "spawn", lambda fn: self.fail("again")):
            self.assertFalse(lt.start(mock.MagicMock(), 7, 9))   # already written or being written

    def test_a_letter_does_not_use_up_one_of_the_ten_but_the_dollar_cap_applies(self):
        with mock.patch.object(lt, "tailor_usage", lambda c, u: (10, 0.5)):
            self.assertTrue(lt.available(mock.MagicMock(), 7))   # all ten used: still allowed
        with mock.patch.object(lt, "tailor_usage", lambda c, u: (2, t.monthly_cap_usd())):
            self.assertFalse(lt.available(mock.MagicMock(), 7))   # Scout's month is spent


class Files(unittest.TestCase):
    def test_the_pdf_is_a_real_pdf_with_the_resume_as_text(self):
        import pypdf
        pdf = rf.to_pdf(STRUCT)
        self.assertTrue(pdf.startswith(b"%PDF"))
        text = "\n".join(page.extract_text() for page in pypdf.PdfReader(io.BytesIO(pdf)).pages)
        for line in ("Ada Lovelace", "SUMMARY", "EXPERIENCE", "Design Lead, Northwind Health", "cut support calls by 20%.",
                     "Figma, Prototyping, User research", "BFA, Design, 2012", "AWARDS", "AIGA Award 2020"):
            self.assertIn(line, text)

    def test_the_word_file_opens_with_headings_and_real_bullets(self):
        import docx
        document = docx.Document(io.BytesIO(rf.to_docx(STRUCT)))
        by_style = [(p.style.name, p.text) for p in document.paragraphs]
        self.assertEqual(by_style[0], ("Title", "Ada Lovelace"))
        self.assertIn(("Heading 1", "Experience"), by_style)
        self.assertIn(("Heading 2", "Design Lead, Northwind Health"), by_style)
        self.assertIn(("List Paragraph", "Owned the design system in Figma and hired two designers."), by_style)
        self.assertEqual(zipfile.ZipFile(io.BytesIO(rf.to_docx(STRUCT))).testzip(), None)

    def test_odd_characters_never_break_either_file(self):
        odd = copy.deepcopy(STRUCT)
        odd["summary"] = "Designer “quoted” — with • bullets, café, 日本, <tags> & \x01 control"
        odd["experience"][0]["bullets"][0] = "R&D <b>bold</b> ‘x’"
        self.assertTrue(rf.to_pdf(odd).startswith(b"%PDF"))
        import docx
        text = "\n".join(p.text for p in docx.Document(io.BytesIO(rf.to_docx(odd))).paragraphs)
        self.assertIn("R&D <b>bold</b>", text)    # written as text, never as markup
        self.assertNotIn("\x01", text)

    def test_a_resume_with_nothing_but_a_summary_still_makes_files(self):
        small = {"header": [], "summary": "Designer.", "experience": [], "skills": [], "education": [], "other": []}
        self.assertTrue(rf.to_pdf(small).startswith(b"%PDF"))
        self.assertTrue(rf.to_docx(small).startswith(b"PK"))
        self.assertTrue(rf.to_pdf({"summary": "", "experience": [], "skills": [], "education": [], "other": []}).startswith(b"%PDF"))   # an older read has no header

    def test_file_names_are_safe_and_say_who_and_where(self):
        self.assertEqual(rf.file_stem(STRUCT, "Northwind Health"), "ada-lovelace-resume-northwind-health")
        self.assertEqual(rf.file_stem({"header": ["ada@example.com"]}, "A/B <c>"), "resume-a-b-c")
        self.assertEqual(rf.file_stem({}, ""), "resume")

    def test_the_pdf_is_laid_out_like_the_word_file(self):
        """Same page count in both: the PDF's lines are spaced as Word spaces them (Arial's 1.15 x 1.1 here), not looser.
        It was once 1.35, so a resume that fit two pages in Word came out as three in the PDF."""
        import pypdf
        bullet = "Led the redesign of the onboarding flow for a product with two million monthly users " * 2
        long = {"summary": "", "skills": [], "education": [], "other": [], "header": [],
                "experience": [{"title": "Designer", "company": "Acme", "location": "", "dates": "", "bullets": [bullet] * 6}]}
        ys = []
        page = pypdf.PdfReader(io.BytesIO(rf.to_pdf(long))).pages[0]
        page.extract_text(visitor_text=lambda text, cm, tm, fd, fs: ys.append(round(tm[5] * cm[3] + cm[5], 1)) if text.strip() else None)
        ys = sorted(set(ys), reverse=True)
        pitches = [round(a - b, 1) for a, b in zip(ys, ys[1:])]
        self.assertIn(round(10 * rf.LINE, 1), pitches)   # a wrapped line sits 12.65 pt below the one above
        self.assertTrue(all(p < 14.5 for p in pitches[2:]), pitches)   # after the heading and role lines

    def test_the_files_are_made_from_the_kept_changes_only(self):
        changes = [{"id": "c1", "kind": "reword", "target": "summary", "value": "Brand new summary.", "kept": True},
                   {"id": "c2", "kind": "skills", "target": "skills", "value": ["User research", "Figma", "Prototyping"], "kept": False}]
        made = t.apply_changes(STRUCT, changes)
        self.assertEqual((made["summary"], made["skills"]), ("Brand new summary.", STRUCT["skills"]))
        import pypdf
        text = pypdf.PdfReader(io.BytesIO(rf.to_pdf(made))).pages[0].extract_text()
        self.assertIn("Brand new summary.", text)
        self.assertNotIn("Product designer with 10 years", text)


class Header(unittest.TestCase):
    def test_a_header_line_that_is_not_in_the_resume_is_dropped(self):
        structure = t.clean_structure({"header": ["Ada Lovelace", "Made-up Title at Nowhere Inc"], "summary": "", "experience": [],
                                       "skills": [], "education": [], "other": []})
        kept = t.drop_unseen_header(structure, "Ada Lovelace ada@example.com Designer")
        self.assertEqual(kept["header"], ["Ada Lovelace"])
        self.assertEqual(t.clean_structure({})["header"], [])
