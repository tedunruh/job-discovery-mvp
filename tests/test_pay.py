"""SC-71: pay ranges for postings. Read as data from the boards that give it (Ashby, Greenhouse, Lever), read from the
description text where they do not (conservatively: no pay beats the wrong pay), stored, and shown on the job card."""
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from collectors import ashby, greenhouse, pay
from dashboard import app as dapp
from dashboard.pay_line import pay_line
from db import db as _db
from scheduler import run_collectors as rc


class Make(unittest.TestCase):
    def test_a_range_is_cleaned_and_labelled(self):
        self.assertEqual(pay.make(150000.0, 188000, "usd", "year", "board"),
                         {"min": 150000, "max": 188000, "currency": "USD", "period": "year", "source": "board"})

    def test_it_refuses_what_is_not_believable(self):
        for args in [(None, None, "USD", "year"), (200000, 100000, "USD", "year"), (0, 10, "USD", "hour"), (150, 160, "USD", "year"),
                     (150000, 160000, "USD", "decade"), (150000, 160000, "", "year"), (150000, 160000, "DOLLARS", "year"),
                     (1, 2, "USD", "hour"), (500000, 900000, "USD", "day"), (300000, 400000, "USD", "week")]:
            self.assertIsNone(pay.make(*args, "board"), args)

    def test_from_or_up_to_is_allowed(self):
        self.assertEqual(pay.make(120000, None, "USD", "year", "text")["max"], None)
        self.assertEqual(pay.make(None, 90000, "USD", "year", "text")["min"], None)


class Boards(unittest.TestCase):
    def test_ashby_takes_the_salary_not_the_bonus_or_equity(self):
        job = {"compensation": {"summaryComponents": [
            {"compensationType": "EquityPercentage", "interval": "NONE", "currencyCode": None, "minValue": None, "maxValue": None},
            {"compensationType": "Bonus", "interval": "1 YEAR", "currencyCode": "USD", "minValue": 10000, "maxValue": 20000},
            {"compensationType": "Salary", "interval": "1 YEAR", "currencyCode": "USD", "minValue": 211400, "maxValue": 290600}]}}
        self.assertEqual(pay.from_ashby(job), pay.make(211400, 290600, "USD", "year", "board"))

    def test_ashby_hourly_and_empty(self):
        job = {"compensation": {"summaryComponents": [{"compensationType": "Salary", "interval": "1 HOUR", "currencyCode": "USD", "minValue": 50, "maxValue": 100}]}}
        self.assertEqual(pay.from_ashby(job)["period"], "hour")
        self.assertIsNone(pay.from_ashby({"compensation": {"summaryComponents": []}}))
        self.assertIsNone(pay.from_ashby({}))
        self.assertIsNone(pay.from_ashby({"compensation": None}))

    def test_greenhouse_amounts_are_in_cents(self):
        job = {"pay_input_ranges": [{"min_cents": 16500000, "max_cents": 19000000, "currency_type": "USD", "title": "Annual Base Salary Range:"}]}
        self.assertEqual(pay.from_greenhouse(job), pay.make(165000, 190000, "USD", "year", "board"))

    def test_greenhouse_hourly_title(self):
        job = {"pay_input_ranges": [{"min_cents": 4500, "max_cents": 6000, "currency_type": "USD", "title": "Hourly rate"}]}
        self.assertEqual(pay.from_greenhouse(job)["period"], "hour")
        self.assertEqual(pay.from_greenhouse(job)["min"], 45)

    def test_greenhouse_with_no_ranges(self):
        self.assertIsNone(pay.from_greenhouse({"pay_input_ranges": []}))
        self.assertIsNone(pay.from_greenhouse({}))

    def test_lever_intervals(self):
        for interval, period in [("per-year-salary", "year"), ("per-hour-wage", "hour"), ("per-month-salary", "month")]:
            got = pay.from_lever({"salaryRange": {"min": 5000 if period != "hour" else 40, "max": 9000 if period != "hour" else 60, "currency": "USD", "interval": interval}})
            self.assertEqual(got["period"], period, interval)
        self.assertIsNone(pay.from_lever({"salaryRange": {"min": 3000, "max": 4000, "currency": "USD", "interval": "bi-week-salary"}}))   # not guessed
        self.assertIsNone(pay.from_lever({}))

    def test_other_boards_give_none(self):
        for ats in ("workable", "rippling", "workday", "nonsense"):
            self.assertIsNone(pay.from_board(ats, {"anything": 1}))
        self.assertIsNone(pay.from_board("ashby", None))

    def test_attach_puts_pay_on_each_posting_and_slims_greenhouse(self):
        postings = [{"raw_json": {"id": 1, "pay_input_ranges": [{"min_cents": 10000000, "max_cents": 12000000, "currency_type": "USD", "title": "x", "blurb": "<p>long html</p>"}]}},
                    {"raw_json": {"id": 2}}]
        pay.attach("greenhouse", postings)
        self.assertEqual(postings[0]["pay"]["min"], 100000)
        self.assertNotIn("blurb", postings[0]["raw_json"]["pay_input_ranges"][0])
        self.assertIsNone(postings[1]["pay"])

    def test_the_collectors_ask_for_pay(self):
        with mock.patch.object(ashby, "request_with_retries") as get:
            get.return_value.json.return_value = {"jobs": []}
            ashby.fetch("acme")
        self.assertEqual(get.call_args.kwargs["params"], {"includeCompensation": "true"})
        for light, want in [(True, {"pay_transparency": "true"}), (False, {"content": "true", "pay_transparency": "true"})]:
            with mock.patch.object(greenhouse, "request_with_retries") as get:
                get.return_value.json.return_value = {"jobs": []}
                greenhouse.fetch("acme", light=light)
            self.assertEqual(get.call_args.kwargs["params"], want)


class Text(unittest.TestCase):
    def read(self, text):
        found = pay.from_text(text)
        return None if found is None else (found["min"], found["max"], found["currency"], found["period"])

    def test_the_usual_wordings(self):
        for text, want in [
            ("The expected salary range for this role is $147,000 - $187,000. However, the starting base pay", (147000, 187000, "USD", "year")),
            ("Starting Pay / Salary Range $144,000.00 - $190,000.00 / year", (144000, 190000, "USD", "year")),
            ("Base Pay Range  $180,000—$195,000 USD", (180000, 195000, "USD", "year")),
            ("Compensation: $180K - $220K + Offers Equity", (180000, 220000, "USD", "year")),
            ("The base salary range is $93K–$210K annually, plus equity", (93000, 210000, "USD", "year")),
            ("Salary: £55,000 to £65,000 per annum", (55000, 65000, "GBP", "year")),
            ("Base salary: €85,000 to €160,000 annually", (85000, 160000, "EUR", "year")),
            ("pay range of CAD $90,000 - $110,000", (90000, 110000, "CAD", "year")),
            ("The salary range is $135,000-175,000 CAD / year.", (135000, 175000, "CAD", "year")),
            ("Compensation: $60 - $110 / hour  Commitment: 40 hours per week", (60, 110, "USD", "hour")),
            ("Salary: Ranges from $32-38/hour depending on experience.", (32, 38, "USD", "hour")),
            ("Compensation: €4,500–5,500/month gross, paid in USD", (4500, 5500, "EUR", "month")),
            ("Monthly Salary Range  $21,310—$25,380 USD  Roles that are based", (21310, 25380, "USD", "month")),
            ("GBR base pay range per year: £101,000 - 149,000", (101000, 149000, "GBP", "year")),
            ("The annual on target earnings (OTE) range for this role is $150,000-$200,000.", (150000, 200000, "USD", "year")),
        ]:
            self.assertEqual(self.read(text), want, text)

    def test_several_zones_span_all_of_them(self):
        text = ("Zone 1 Pay Range  $166,600—$229,150 USD  Zone 2 Pay Range  $158,300—$217,700 USD  "
                "Zone 3 Pay Range  $128,400—$179,700 USD")
        self.assertEqual(self.read(text), (128400, 229150, "USD", "year"))

    def test_the_next_zone_counts_even_with_no_pay_word_beside_it(self):
        filler = "Our offices are in many cities and we hire remotely across the whole country, with teams in every region. " * 2
        text = ("pay range: US employees in New York City or the Bay Area:  $75,900\u2014$85,400 USD  " + filler +
                "US employees outside of those metros:  $68,300\u2014$76,900 USD")
        self.assertEqual(self.read(text), (68300, 85400, "USD", "year"))

    def test_two_levels_in_a_row_span_both(self):
        text = "The annual salary range for this role is $117,000 CAD to $143,000 CAD; or $144,000 to $176,000, respectively."
        self.assertEqual(self.read(text), (117000, 176000, "CAD", "year"))

    def test_money_that_is_not_pay_is_not_read(self):
        for text in [
            "Posh has grown to a team of 70, secured $70m in venture funding, and facilitated over $400M in transactions.",
            "Backed by $81 million in funding from Smash Capital, salary to be discussed",
            "we have raised $20 - $30 million in funding",
            "Housing subsidy of $2,000 - $3,000 per month for those living within the city",
            "$1,000/year education reimbursement, and a flexible vacation policy",
            "a valuation of $4 billion and revenue between $100 - $200 million",
            "Equity: $80,000 - $100,000",
            "A $5,000 - $10,000 signing bonus is offered",
            "In 2024 we grew from 10 to 50 people and from $2 to $9 in value",
            "our plan costs $12 - $20 per month for members",
        ]:
            self.assertIsNone(self.read(text), text)

    def test_funding_beside_a_figure_is_not_pay(self):
        self.assertIsNone(self.read("The salary budget comes from funding of $120,000 - $150,000 raised from investors"))

    def test_a_period_word_far_from_the_range_is_not_its_period(self):
        text = "The base salary range is $120,000 - $150,000. Our company hosts events monthly and pays out bonuses quarterly."
        self.assertEqual(self.read(text), (120000, 150000, "USD", "year"))

    def test_a_middling_figure_with_no_period_is_not_read_as_a_year(self):
        self.assertIsNone(self.read("Salary: $5,000 - $8,000"))

    def test_zones_in_another_currency_are_not_merged_in(self):
        text = "Salary range $100,000 - $120,000 USD. In the UK the salary range is \u00a360,000 - \u00a370,000 GBP."
        self.assertEqual(self.read(text), (100000, 120000, "USD", "year"))

    def test_equity_next_to_base_is_left_out(self):
        text = "COMPENSATION & BENEFITS   - Equity: $80,000 - $100,000   - Base salary: $180,000 - $275,000   - Medical"
        self.assertEqual(self.read(text), (180000, 275000, "USD", "year"))

    def test_total_compensation_that_includes_equity_is_not_pay(self):
        self.assertIsNone(self.read("with total compensation ranging from £75k–£200k (base salary + equity)."))

    def test_an_ote_range_after_a_base_range_is_not_merged_into_it(self):
        text = ("the pay range at the start of employment is expected to be between CA$133,100 - CA$208,600/year, with an expected "
                "On Target Earnings (OTE) between CA$147,900 - CA$231,800/year")
        self.assertEqual(self.read(text), (133100, 208600, "CAD", "year"))

    def test_a_period_word_belongs_to_the_range_next_to_it(self):
        text = "Compensation & Benefits: Salary: $150,000 - $158,000  Benefits:  - Medical: Up to $950/month company contribution"
        self.assertEqual(self.read(text), (150000, 158000, "USD", "year"))

    def test_days_in_a_schedule_are_not_a_day_rate(self):
        self.assertEqual(self.read("Salary: $150,000-$250,000. YOUR FIRST 90 DAYS 1. Days 1–30: Learn the product"), (150000, 250000, "USD", "year"))

    def test_a_small_figure_with_no_period_is_not_a_years_pay(self):
        self.assertIsNone(self.read("The salary for this role is $45 - $60."))

    def test_no_pay_words_no_pay(self):
        self.assertIsNone(self.read("Our office is between $100,000 - $200,000 square feet"))
        self.assertIsNone(self.read(""))
        self.assertIsNone(self.read(None))


class Line(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(pay_line({"pay_min": 150000, "pay_max": 188000, "pay_currency": "USD", "pay_period": "year"}), "$150,000 - $188,000")
        self.assertEqual(pay_line({"pay_min": 45, "pay_max": 60, "pay_currency": "USD", "pay_period": "hour"}), "$45 - $60 / hour")
        self.assertEqual(pay_line({"pay_min": 55000, "pay_max": 65000, "pay_currency": "GBP", "pay_period": "year"}), "£55,000 - £65,000")
        self.assertEqual(pay_line({"pay_min": 4500, "pay_max": 5500, "pay_currency": "EUR", "pay_period": "month"}), "€4,500 - €5,500 / month")
        self.assertEqual(pay_line({"pay_min": 90000, "pay_max": 110000, "pay_currency": "CAD", "pay_period": "year"}), "CA$90,000 - CA$110,000")
        self.assertEqual(pay_line({"pay_min": 500000, "pay_max": 700000, "pay_currency": "SEK", "pay_period": "year"}), "SEK 500,000 - SEK 700,000")

    def test_one_sided_and_single_figures(self):
        self.assertEqual(pay_line({"pay_min": 120000, "pay_max": None, "pay_currency": "USD", "pay_period": "year"}), "$120,000+")
        self.assertEqual(pay_line({"pay_min": None, "pay_max": 90000, "pay_currency": "USD", "pay_period": "year"}), "Up to $90,000")
        self.assertEqual(pay_line({"pay_min": 25, "pay_max": 25, "pay_currency": "USD", "pay_period": "hour"}), "$25 / hour")
        self.assertEqual(pay_line({"pay_min": 45.5, "pay_max": 60.25, "pay_currency": "USD", "pay_period": "hour"}), "$45.50 - $60.25 / hour")

    def test_no_pay_is_an_empty_line_never_a_dash(self):
        for row in [{}, {"pay_min": None, "pay_max": None, "pay_currency": None, "pay_period": None}, {"pay_min": 100000, "pay_max": 120000, "pay_currency": None}]:
            self.assertEqual(pay_line(row), "")


class Stored(unittest.TestCase):
    def test_a_posting_without_pay_keeps_the_fingerprint_it_always_had(self):
        base = {"title": "Designer", "location": "Remote", "remote_type": None, "url": "u", "ats_posted_at": "2026-10-01", "is_design": True, "raw_json": {"id": 1}}
        before = _db.content_hash(base)
        import hashlib, json
        always = hashlib.sha256(json.dumps(["Designer", "Remote", None, "u", "2026-10-01", {"id": 1}], sort_keys=True, default=str).encode()).hexdigest()
        self.assertEqual(before, always)   # exactly what it was before pay existed: asking for pay rewrites nothing by itself
        self.assertEqual(_db.content_hash({**base, "pay": None}), before)
        self.assertEqual(_db.content_hash({**base, "raw_json": {"id": 1, "compensation": {"summaryComponents": []}, "pay_input_ranges": []}}), before)

    def test_found_pay_and_changed_pay_change_it(self):
        base = {"title": "Designer", "location": "Remote", "remote_type": None, "url": "u", "ats_posted_at": "2026-10-01", "is_design": True, "raw_json": {"id": 1}}
        a = _db.content_hash({**base, "pay": pay.make(100000, 120000, "USD", "year", "board")})
        b = _db.content_hash({**base, "pay": pay.make(100000, 130000, "USD", "year", "board")})
        self.assertNotEqual(a, _db.content_hash(base))
        self.assertNotEqual(a, b)

    def test_pay_columns(self):
        self.assertEqual(_db._pay_columns(None), (None, None, None, None, None))
        self.assertEqual(_db._pay_columns(pay.make(100000, 120000, "usd", "year", "text")), (100000, 120000, "USD", "year", "text"))

    def test_the_upsert_writes_pay_and_keeps_text_pay_when_the_board_gives_none(self):
        cur = mock.MagicMock()
        conn = mock.MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        posting = {"ats_posting_id": "1", "title": "Designer", "url": "u", "is_design": True, "raw_json": {"id": 1},
                   "pay": pay.make(100000, 120000, "USD", "year", "board")}
        with mock.patch.object(_db.psycopg2.extras, "execute_values", return_value=[]) as run:
            _db.upsert_postings(conn, 5, [posting])
        sql = run.call_args.args[1]
        rows = run.call_args.args[2]
        self.assertIn("pay_min, pay_max, pay_currency, pay_period, pay_source", sql)
        self.assertIn("CASE WHEN EXCLUDED.pay_source IS NOT NULL THEN EXCLUDED.pay_min ELSE postings.pay_min END", sql)
        self.assertEqual(rows[0][-5:], (100000, 120000, "USD", "year", "board"))
        self.assertEqual(run.call_args.kwargs["template"].count("%s"), len(rows[0]))   # one placeholder per value, in step

    def test_the_description_step_reads_pay_only_where_the_board_gave_none(self):
        conn = mock.MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        _db.set_posting_description(conn, 9, "The base salary range is $120,000 - $150,000.")
        sql, params = cur.execute.call_args.args
        self.assertEqual(sql.count("WHEN pay_source IS NULL THEN"), 5)
        self.assertEqual(params[1:6], (120000, 150000, "USD", "year", "text"))
        self.assertEqual(params[-1], 9)
        _db.set_posting_description(conn, 9, "No money talk here.")
        self.assertEqual(cur.execute.call_args.args[1][1:6], (None, None, None, None, None))

    def test_the_schema_adds_five_columns_and_nothing_else(self):
        schema = open("db/schema.sql").read()
        tail = schema[schema.index("-- SC-71"):]
        for col in ("pay_min NUMERIC(14,2)", "pay_max NUMERIC(14,2)", "pay_currency TEXT", "pay_period TEXT", "pay_source TEXT"):
            self.assertIn(f"ALTER TABLE postings ADD COLUMN IF NOT EXISTS {col};", tail)
        self.assertNotIn("DROP", tail)

    def test_queries_read_the_pay_columns(self):
        for fn in (_db.get_open_postings, _db.get_hidden, _db.get_applications):
            conn = mock.MagicMock()
            cur = conn.cursor.return_value.__enter__.return_value
            cur.fetchall.return_value = []
            fn(conn, 7) if fn is not _db.get_open_postings else fn(conn, 7)
            self.assertIn("p.pay_min, p.pay_max, p.pay_currency, p.pay_period", cur.execute.call_args.args[0], fn.__name__)


class Collection(unittest.TestCase):
    def test_a_fetched_board_has_its_pay_read_before_the_payload_is_dropped(self):
        raw = {"id": 3, "title": "Product Designer", "pay_input_ranges": [{"min_cents": 15000000, "max_cents": 18000000, "currency_type": "USD", "title": "Annual", "blurb": "x"}]}
        posting = {"ats_posting_id": "3", "title": "Account Executive", "location": None, "remote_type": None, "url": "u", "ats_posted_at": None, "raw_json": raw}
        company = {"id": 1, "ats_type": "greenhouse", "ats_identifier": "acme"}
        with mock.patch.dict(rc.COLLECTORS, {"greenhouse": lambda ident: [posting]}):
            postings, error = rc._fetch_company(company)
        self.assertIsNone(error)
        self.assertEqual(postings[0]["pay"]["min"], 150000)      # not a design role: its payload is dropped, but the pay stays
        self.assertIsNone(postings[0]["raw_json"])


class DiscoveredBoards(unittest.TestCase):
    def test_a_discovered_board_has_its_pay_read_too(self):
        raw = {"id": 4, "title": "Product Designer", "pay_input_ranges": [{"min_cents": 14000000, "max_cents": 17000000, "currency_type": "USD", "title": "Annual", "blurb": "x"}]}
        posting = {"ats_posting_id": "4", "title": "Product Designer", "location": "Remote", "remote_type": None, "url": "u", "ats_posted_at": None, "raw_json": raw}
        company = {"id": 2, "ats_type": "greenhouse", "ats_identifier": "acme", "scan_marker": None, "full_ingest_at": 1}
        with mock.patch.dict(rc.TAIL_COLLECTORS, {"greenhouse": lambda ident: [posting]}), mock.patch.object(rc, "_describer", None):
            result, error = rc._fetch_tail(company)
        self.assertIsNone(error)
        self.assertEqual(result["design"][0]["pay"]["min"], 140000)
        self.assertNotIn("blurb", result["design"][0]["raw_json"]["pay_input_ranges"][0])


def row(pid, title, **pay_cols):
    return {"id": pid, "title": title, "company_name": "Northwind Health", "ats_type": "greenhouse", "location": "Remote - United States",
            "remote_type": "remote", "url": "u", "is_design": True, "applied": False, "hidden_at": None,
            "first_seen_at": datetime.now(timezone.utc) - timedelta(hours=2), **pay_cols}


class Page(unittest.TestCase):
    def setUp(self):
        self.client = dapp.app.test_client()
        self.client.set_cookie(dapp.SESSION_COOKIE, "tok")
        self.rows = [row(1, "Senior Product Designer", pay_min=150000, pay_max=188000, pay_currency="USD", pay_period="year"),
                     row(2, "Product Designer"),
                     row(3, "UX Designer", pay_min=45, pay_max=60, pay_currency="USD", pay_period="hour")]
        profile = {"titles": ["designer"], "level": None, "one_level_up": False, "city": None, "skills": []}
        patches = [
            mock.patch.object(dapp, "get_conn", lambda: mock.MagicMock()),
            mock.patch.object(dapp, "get_session_user", lambda c, t: (7, False)),
            mock.patch.object(dapp, "is_onboarded", lambda c, u: True),
            mock.patch.object(dapp, "log_visit", lambda c, u: None),
            mock.patch.object(dapp, "get_user_role_categories", lambda c, u: set()),
            mock.patch.object(dapp, "get_user_keywords", lambda c, u: []),
            mock.patch.object(dapp, "get_user_profile", lambda c, u: dict(profile)),
            mock.patch.object(dapp, "get_user_work_types", lambda c, u: []),
            mock.patch.object(dapp, "get_open_postings", lambda c, u, since=None, keywords=(): [dict(r) for r in self.rows]),
            mock.patch.object(dapp, "get_applications", lambda c, u: []),
            mock.patch.object(dapp, "get_hidden", lambda c, u: []),
            mock.patch.object(dapp, "get_return_events", lambda c, u: ({}, {})),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_the_card_shows_the_range_and_a_blank_line_when_there_is_none(self):
        html = re.sub(r"<template.*?</template>", "", self.client.get("/", follow_redirects=True).get_data(as_text=True), flags=re.S)
        pays = re.findall(r'<p class="fg-job-pay">([^<]*)</p>', html)
        self.assertEqual(pays, ["$150,000 - $188,000", " ", "$45 - $60 / hour"])
        self.assertNotIn("--", " ".join(pays))
        self.assertNotIn("Pay not listed", html)


if __name__ == "__main__":
    unittest.main()
