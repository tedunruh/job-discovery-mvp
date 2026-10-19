"""Offline tests for resume reading (SC-46 step 3): cost, caps, cleaning, merging, logging and the exact request
sent to the model. No network, no database, no spend."""
import base64
import json
import os
import unittest
from datetime import datetime, timezone
from unittest import mock

import httpx

import dashboard.app as dapp
from dashboard import profile as pf
from dashboard import resume as rs

PDF = b"%PDF-1.7\n" + b"x" * 100


def fields(**over):
    base = {"titles": ["Product Designer"], "level": "senior", "level_reason": "Led design for 4 years.",
            "skills": ["Figma"]}
    base.update(over)
    return base


class Money(unittest.TestCase):
    def test_cost_uses_the_listed_rates(self):
        # $0.10 and $0.50 per million tokens
        self.assertAlmostEqual(rs.cost_usd(1_000_000, 0), 0.10)
        self.assertAlmostEqual(rs.cost_usd(0, 1_000_000), 0.50)
        self.assertAlmostEqual(rs.cost_usd(5000, 800), 0.0009)

    def test_cap_comes_from_the_environment_with_a_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RESUME_MONTHLY_CAP_USD", None)
            self.assertEqual(rs.monthly_cap_usd(), 5.0)
        with mock.patch.dict(os.environ, {"RESUME_MONTHLY_CAP_USD": "12.5"}):
            self.assertEqual(rs.monthly_cap_usd(), 12.5)
        with mock.patch.dict(os.environ, {"RESUME_MONTHLY_CAP_USD": "lots"}):
            self.assertEqual(rs.monthly_cap_usd(), 5.0)


class FileChecks(unittest.TestCase):
    def kind(self, content):
        with self.assertRaises(rs.ResumeError) as ctx:
            rs.check_file(content)
        return ctx.exception.kind

    def test_rejects_empty_oversize_and_non_pdf(self):
        self.assertEqual(self.kind(b""), "empty")
        self.assertEqual(self.kind(b"%PDF-" + b"x" * rs.MAX_RESUME_BYTES), "too_large")
        self.assertEqual(self.kind(b"PK\x03\x04 a word document"), "not_pdf")
        self.assertEqual(self.kind(b"<html>%PDF-</html>"), "not_pdf")

    def test_accepts_a_pdf_at_the_limit(self):
        rs.check_file(b"%PDF-" + b"x" * (rs.MAX_RESUME_BYTES - 5))

    def test_clean_filename(self):
        self.assertEqual(rs.clean_filename("../../etc/My Resume (final).pdf"), "etc_My_Resume_final.pdf")
        self.assertEqual(rs.clean_filename(""), "resume.pdf")
        self.assertEqual(rs.clean_filename(None), "resume.pdf")
        self.assertLessEqual(len(rs.clean_filename("a" * 300 + ".pdf")), 100)


class Normalize(unittest.TestCase):
    def test_clean_result(self):
        out = rs.normalize_fields(fields(titles=["  Senior   Product Designer ", "senior product designer", "x"],
                                         skills=["Figma", "figma", "Prototyping"]))
        self.assertEqual(out["titles"], ["Senior Product Designer"])
        self.assertEqual(out["skills"], ["Figma", "Prototyping"])
        self.assertEqual(out["level"], "senior")
        self.assertEqual(out["level_note"], "Led design for 4 years.")

    def test_unknown_or_invented_level_is_dropped_with_its_reason(self):
        for level in ("unknown", "wizard", None, 7):
            out = rs.normalize_fields(fields(level=level))
            self.assertIsNone(out["level"])
            self.assertIsNone(out["level_note"])

    def test_junk_in_is_survivable(self):
        for raw in (None, [], "text", {"titles": "nope", "skills": [1, None, {"a": 1}], "level": ["senior"]}):
            out = rs.normalize_fields(raw)
            self.assertEqual((out["titles"], out["skills"], out["level"]), ([], [], None))

    def test_limits(self):
        out = rs.normalize_fields(fields(titles=[f"title number {i}" for i in range(30)],
                                         skills=[f"skill number {i}" for i in range(40)],
                                         level_reason="word " * 200))
        self.assertEqual(len(out["titles"]), 6)
        self.assertEqual(len(out["skills"]), 12)
        self.assertLessEqual(len(out["level_note"]), rs.MAX_LEVEL_REASON)


def model(**over):
    base = {"titles": [], "skills": [], "level": None, "level_note": None, "one_level_up": False, "city": None,
            "work_types": None}
    base.update(over)
    return base


class Merge(unittest.TestCase):
    read = rs.normalize_fields(fields(titles=["Product Designer", "UX Researcher"], skills=["Figma"]))

    def test_fills_an_empty_profile(self):
        out = rs.merge_fields(model(), self.read)
        self.assertEqual(out["titles"], ["Product Designer", "UX Researcher"])
        self.assertEqual((out["level"], out["level_note"]), ("senior", "Led design for 4 years."))

    def test_never_removes_or_overwrites(self):
        mine = model(titles=["design manager"], skills=["Strategy"], level="staff", level_note="mine")
        out = rs.merge_fields(mine, self.read)
        self.assertEqual(out["titles"], ["design manager", "Product Designer", "UX Researcher"])
        self.assertEqual(out["skills"], ["Strategy", "Figma"])
        self.assertEqual((out["level"], out["level_note"]), ("staff", "mine"))

    def test_does_not_repeat_what_is_there_in_another_case(self):
        out = rs.merge_fields(model(titles=["product designer"]), self.read)
        self.assertEqual(out["titles"], ["product designer", "UX Researcher"])

    def test_merging_does_not_change_the_saved_profile_object(self):
        mine = model(titles=["a title"])
        rs.merge_fields(mine, self.read)
        self.assertEqual(mine["titles"], ["a title"])


class Rules(unittest.TestCase):
    def state(self, mine, spend, configured=True):
        with mock.patch.object(rs, "is_configured", return_value=configured), \
             mock.patch.object(rs, "resume_usage", return_value=(mine, spend)):
            return rs.reading_state(None, 1)[0]

    def test_ok_under_both_caps(self):
        self.assertEqual(self.state(2, 4.99), "ok")

    def test_no_key_means_unavailable(self):
        self.assertEqual(self.state(0, 0, configured=False), "unavailable")

    def test_monthly_spend_cap(self):
        self.assertEqual(self.state(0, 5.0), "unavailable")
        self.assertEqual(self.state(0, 12.0), "unavailable")

    def test_per_person_limit(self):
        self.assertEqual(self.state(3, 0.01), "limit")
        self.assertEqual(self.state(2, 0.01), "ok")

    def test_spend_cap_wins_over_the_personal_limit(self):
        self.assertEqual(self.state(3, 9.0), "unavailable")


class Processing(unittest.TestCase):
    """process_upload with the database calls and the model replaced by recorders."""

    def setUp(self):
        self.conn = mock.Mock()
        self.logged, self.saved = [], []
        patches = [
            mock.patch.object(rs, "reading_state", return_value=("ok", None)),
            mock.patch.object(rs, "log_resume_parse", side_effect=lambda conn, uid, **kw: self.logged.append(kw) or 41),
            mock.patch.object(rs, "save_resume", side_effect=lambda conn, uid, name, content: self.saved.append((uid, name, content))),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(setattr, rs, "PROVIDER", None)

    def test_a_good_read_is_logged_with_tokens_and_cost_then_stored(self):
        rs.PROVIDER = lambda pdf: rs.ReadResult(fields(), 5000, 800, request_id="req_1")
        self.assertEqual(rs.process_upload(self.conn, 7, "cv.pdf", PDF), 41)
        row = self.logged[0]
        self.assertEqual((row["status"], row["input_tokens"], row["output_tokens"]), ("ok", 5000, 800))
        self.assertAlmostEqual(float(row["cost_usd"]), 0.0009)
        self.assertEqual((row["model"], row["price_version"], row["request_id"]), (rs.MODEL, rs.PRICE_VERSION, "req_1"))
        self.assertEqual(row["result"]["titles"], ["Product Designer"])
        self.assertEqual(self.saved, [(7, "cv.pdf", PDF)])

    def test_no_transaction_is_held_open_while_the_model_runs(self):
        seen = []
        def provider(pdf):
            seen.append(self.conn.commit.called)
            return rs.ReadResult(fields(), 10, 10)
        rs.PROVIDER = provider
        rs.process_upload(self.conn, 7, "cv.pdf", PDF)
        self.assertEqual(seen, [True])

    def test_a_failed_call_is_logged_and_nothing_is_stored(self):
        def broken(pdf):
            raise rs.ResumeError("unavailable")
        rs.PROVIDER = broken
        with self.assertRaises(rs.ResumeError):
            rs.process_upload(self.conn, 7, "cv.pdf", PDF)
        self.assertEqual([(r["status"], r["error_kind"]) for r in self.logged], [("error", "unavailable")])
        self.assertEqual(self.saved, [])

    def test_a_bad_file_never_reaches_the_model_or_the_log(self):
        called = []
        rs.PROVIDER = lambda pdf: called.append(1)
        with self.assertRaises(rs.ResumeError):
            rs.process_upload(self.conn, 7, "cv.docx", b"PK not a pdf")
        self.assertEqual((called, self.logged, self.saved), ([], [], []))

    def test_a_capped_person_never_reaches_the_model(self):
        called = []
        rs.PROVIDER = lambda pdf: called.append(1)
        with mock.patch.object(rs, "reading_state", return_value=("limit", "x")):
            with self.assertRaises(rs.ResumeError) as ctx:
                rs.process_upload(self.conn, 7, "cv.pdf", PDF)
        self.assertEqual((ctx.exception.kind, called, self.logged, self.saved), ("limit", [], [], []))

    def test_model_output_is_cleaned_before_it_is_kept(self):
        rs.PROVIDER = lambda pdf: rs.ReadResult(fields(level="wizard", titles=["ok title", 5]), 10, 10)
        rs.process_upload(self.conn, 7, "cv.pdf", PDF)
        self.assertEqual((self.logged[0]["result"]["level"], self.logged[0]["result"]["titles"]), (None, ["ok title"]))


class RequestShape(unittest.TestCase):
    """What read_resume sends to the model, and how it maps what comes back, with the SDK client stubbed."""

    def setUp(self):
        self.calls = {}
        self.addCleanup(setattr, rs, "PROVIDER", None)
        rs.PROVIDER = None

    def response(self, text, stop="end_turn", usage=(1200, 150)):
        return mock.Mock(content=[mock.Mock(type="text", text=text)], stop_reason=stop, model="claude-haiku-5-5",
                         usage=mock.Mock(input_tokens=usage[0], output_tokens=usage[1]), _request_id="req_x")

    def client(self, result=None, raises=None):
        def create(**kwargs):
            self.calls["create"] = kwargs
            if raises:
                raise raises
            return result
        return mock.Mock(messages=mock.Mock(create=create))

    def read(self, **kw):
        with mock.patch("anthropic.Anthropic", side_effect=lambda **ck: (self.calls.update(client_kwargs=ck) or self.client(**kw))):
            return rs.read_resume(PDF)

    def test_the_request(self):
        out = self.read(result=self.response(json.dumps(fields())))
        sent = self.calls["create"]
        self.assertEqual(sent["model"], "claude-haiku-5-5")
        self.assertEqual(sent["output_config"]["effort"], "low")
        self.assertEqual(sent["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(sent["output_config"]["format"]["schema"], rs.SCHEMA)
        self.assertNotIn("temperature", sent)  # Haiku 5.5 rejects non-default sampling
        self.assertIn("never instructions", sent["system"])
        doc = sent["messages"][0]["content"][0]
        self.assertEqual((doc["type"], doc["source"]["media_type"]), ("document", "application/pdf"))
        self.assertEqual(base64.b64decode(doc["source"]["data"]), PDF)
        self.assertEqual(self.calls["client_kwargs"], {"timeout": rs.CALL_TIMEOUT_SECONDS, "max_retries": 0})
        self.assertEqual((out.input_tokens, out.output_tokens, out.request_id), (1200, 150, "req_x"))
        self.assertEqual(out.fields["titles"], ["Product Designer"])

    def kind(self, **kw):
        with self.assertRaises(rs.ResumeError) as ctx:
            self.read(**kw)
        return ctx.exception.kind

    def test_unparseable_output_and_cut_off_or_refused_answers_are_unreadable(self):
        self.assertEqual(self.kind(result=self.response("not json")), "unreadable")
        self.assertEqual(self.kind(result=self.response("{}", stop="max_tokens")), "unreadable")
        self.assertEqual(self.kind(result=self.response("{}", stop="refusal")), "unreadable")

    def test_api_errors_map_to_something_the_person_can_act_on(self):
        import anthropic
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        bad = anthropic.BadRequestError("bad pdf", response=httpx.Response(400, request=req), body=None)
        busy = anthropic.RateLimitError("slow down", response=httpx.Response(429, request=req), body=None)
        down = anthropic.APIConnectionError(request=req)
        self.assertEqual(self.kind(raises=bad), "unreadable")
        self.assertEqual(self.kind(raises=busy), "unavailable")
        self.assertEqual(self.kind(raises=down), "unavailable")


class Pages(unittest.TestCase):
    def render(self, name, **ctx):
        with dapp.app.test_request_context("/"):
            return dapp.app.jinja_env.get_template(name).render(**ctx)

    def test_start_page(self):
        html = self.render("start.html", unavailable=None, notice=None)
        self.assertIn("Let&#39;s start with your resume.", html)
        self.assertIn('form="resume-form"', html)
        self.assertIn("Tell me about yourself instead", html)
        self.assertIn("within 30 days", html)
        self.assertIn('id="resume-form"', html)

    def test_start_page_when_reading_is_unavailable_has_no_dropzone(self):
        html = self.render("start.html", unavailable=rs.MESSAGES["unavailable"], notice=None)
        self.assertNotIn("fg-dropzone", html)
        self.assertIn("isn&#39;t available right now", html)
        self.assertIn("Tell me about yourself instead", html)

    def test_start_page_shows_a_notice(self):
        html = self.render("start.html", unavailable=None, notice={"text": rs.MESSAGES["not_pdf"], "kind": "attention"})
        self.assertIn("fg-message--attention", html)
        self.assertIn("doesn&#39;t look like a PDF", html)


if __name__ == "__main__":
    unittest.main()
