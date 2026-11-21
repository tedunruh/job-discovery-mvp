"""SC-73 slice 3: check-ins on applications that are still waiting (SC-64's design): the question a row asks, the push the
collector sends, and the answers that come back."""
import re
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from checkins import MAX_PUSHES, push_level, question
from dashboard import applications as apps
from dashboard.elevate import application_history, group_applications
from db import db as _db
from scheduler import checkin_push
from scheduler.notify import checkin_link, notify_checkins
from tests.test_applications_tab import Base
from tests.test_applications_tab import application as base_application

# 10 AM Central on a Tuesday (CST: UTC-6)
NOW = datetime(2026, 11, 24, 16, 0, tzinfo=timezone.utc)


def app(days, **extra):
    a = {"stage": "waiting", "posting_status": "open", "applied_at": NOW - timedelta(days=days), "heard_at": None,
         "heard_not_yet_at": None, "close_not_yet_at": None, "checkin_pushes": 0}
    a.update(extra)
    return a


class Question(unittest.TestCase):
    def test_nothing_before_day_five(self):
        self.assertIsNone(question(app(0), NOW))
        self.assertIsNone(question(app(4), NOW))

    def test_did_you_hear_back_from_day_five(self):
        self.assertEqual(question(app(5), NOW), "heard")
        self.assertEqual(question(app(11), NOW), "heard")

    def test_not_yet_waits_until_day_twelve_and_asks_again(self):
        early = NOW - timedelta(days=2)                      # said Not yet on day 8
        self.assertIsNone(question(app(10, heard_not_yet_at=early), NOW))
        self.assertEqual(question(app(12, heard_not_yet_at=NOW - timedelta(days=4)), NOW), "heard")
        self.assertEqual(question(app(15, heard_not_yet_at=NOW - timedelta(days=7)), NOW), "heard")

    def test_not_yet_on_the_second_ask_waits_for_day_twenty_one(self):
        self.assertIsNone(question(app(15, heard_not_yet_at=NOW - timedelta(days=1)), NOW))
        self.assertIsNone(question(app(20, heard_not_yet_at=NOW - timedelta(days=2)), NOW))

    def test_should_i_close_it_from_day_twenty_one_and_not_yet_ends_it(self):
        self.assertEqual(question(app(21), NOW), "close")
        self.assertEqual(question(app(40, heard_not_yet_at=NOW - timedelta(days=20)), NOW), "close")
        self.assertIsNone(question(app(25, close_not_yet_at=NOW - timedelta(days=1)), NOW))

    def test_yes_asks_what_they_said_whatever_the_day(self):
        self.assertEqual(question(app(6, heard_at=NOW), NOW), "say")
        self.assertEqual(question(app(30, heard_at=NOW), NOW), "say")

    def test_no_question_once_it_has_moved_on_or_the_employer_closed_it(self):
        for stage in ("interviewing", "offer", "not", "mine"):
            self.assertIsNone(question(app(8, stage=stage), NOW), stage)
        self.assertIsNone(question(app(8, posting_status="closed"), NOW))
        self.assertIsNone(question(app(8, applied_at=None), NOW))        # applied before dates were kept: no age to go by


class Level(unittest.TestCase):
    def test_one_push_for_each_of_the_three_days_and_no_more(self):
        self.assertEqual([push_level(app(d), NOW) for d in (4, 5, 11)], [0, 1, 1])
        self.assertEqual(push_level(app(12, checkin_pushes=1), NOW), 2)
        self.assertEqual(push_level(app(21, checkin_pushes=2), NOW), 3)
        self.assertEqual(push_level(app(30, checkin_pushes=3), NOW), 0)
        self.assertEqual(MAX_PUSHES, 3)

    def test_a_push_already_sent_for_the_day_is_not_sent_again(self):
        self.assertEqual(push_level(app(6, checkin_pushes=1), NOW), 0)
        self.assertEqual(push_level(app(14, checkin_pushes=2), NOW), 0)

    def test_a_job_that_was_quiet_gets_the_push_for_where_it_is_now(self):
        self.assertEqual(push_level(app(25), NOW), 3)                      # none sent: not a catch-up of days 5 and 12

    def test_no_push_when_the_question_is_not_asking(self):
        self.assertEqual(push_level(app(6, heard_not_yet_at=NOW), NOW), 0)
        self.assertEqual(push_level(app(6, heard_at=NOW), NOW), 0)
        self.assertEqual(push_level(app(25, close_not_yet_at=NOW), NOW), 0)
        self.assertEqual(push_level(app(8, posting_status="closed"), NOW), 0)
        self.assertEqual(push_level(app(8, stage="interviewing"), NOW), 0)


def cand(uid, pid, days, **extra):
    c = dict(app(days), user_id=uid, posting_id=pid, ntfy_topic=f"t{uid}", checkin_pushed_on=None,
             company_name=f"Co{pid}", title=f"Role {pid}")
    c.update(extra)
    return c


ACTIVE = {"alerts_started_at": NOW, "alerts_paused_at": None}


class Plan(unittest.TestCase):
    def test_jobs_due_together_share_one_push(self):
        pushes, today = checkin_push.plan([cand(1, 10, 6), cand(1, 11, 7), cand(2, 12, 6)], {1: ACTIVE, 2: ACTIVE}, NOW)
        self.assertEqual(today, date(2026, 11, 24))
        self.assertEqual({u: [i["posting_id"] for i in e["items"]] for u, e in pushes.items()}, {1: [10, 11], 2: [12]})
        self.assertEqual(pushes[1]["levels"], {10: 1, 11: 1})

    def test_nothing_before_nine_central(self):
        early = datetime(2026, 11, 24, 14, 59, tzinfo=timezone.utc)      # 8:59 AM Central
        self.assertEqual(checkin_push.plan([cand(1, 10, 6)], {1: ACTIVE}, early)[0], {})
        on_time = datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc)     # 9:00 AM
        self.assertEqual(list(checkin_push.plan([cand(1, 10, 6)], {1: ACTIVE}, on_time)[0]), [1])

    def test_one_a_day_per_person(self):
        pushes, _ = checkin_push.plan([cand(1, 10, 6, checkin_pushed_on=date(2026, 11, 24)), cand(2, 11, 6)], {}, NOW)
        self.assertEqual(list(pushes), [2])
        pushes, _ = checkin_push.plan([cand(1, 10, 6, checkin_pushed_on=date(2026, 11, 23))], {}, NOW)
        self.assertEqual(list(pushes), [1])                                  # yesterday's does not count

    def test_none_while_alerts_are_paused_or_not_started_but_a_person_without_a_profile_is_asked(self):
        paused = dict(ACTIVE, alerts_paused_at=NOW)
        not_started = {"alerts_started_at": None, "alerts_paused_at": None}
        pushes, _ = checkin_push.plan([cand(1, 10, 6), cand(2, 11, 6), cand(3, 12, 6)], {1: paused, 2: not_started}, NOW)
        self.assertEqual(list(pushes), [3])

    def test_none_without_a_topic(self):
        self.assertEqual(checkin_push.plan([cand(1, 10, 6, ntfy_topic=None)], {}, NOW)[0], {})


class Sending(unittest.TestCase):
    def run_send(self, ok=True, candidates=None):
        conn, alerts, sent = mock.MagicMock(), [], []
        with mock.patch.object(checkin_push, "get_checkin_candidates", lambda c: candidates if candidates is not None else [cand(1, 10, 6), cand(1, 11, 21, checkin_pushes=2)]), \
             mock.patch.object(checkin_push, "ensure_alert_token", lambda c, u, p: "tok"), \
             mock.patch.object(checkin_push, "tracking_allowed", lambda now: True), \
             mock.patch.object(checkin_push, "notify_checkins", lambda items, topic, click: sent.append((items, topic, click)) or ok), \
             mock.patch.object(checkin_push, "mark_checkins_pushed", lambda c, u, levels, today: sent.append(("marked", u, levels, today))):
            checkin_push.send(conn, {1: ACTIVE}, alerts, NOW)
        return sent, alerts

    def test_sends_one_grouped_push_and_counts_it(self):
        sent, alerts = self.run_send()
        self.assertEqual(len(sent[0][0]), 2)
        self.assertEqual(sent[0][2], checkin_link("tok", grouped=True))
        self.assertEqual(sent[1], ("marked", 1, {10: 1, 11: 3}, date(2026, 11, 24)))
        self.assertEqual(alerts, [])

    def test_a_failed_push_is_not_counted_and_is_reported(self):
        sent, alerts = self.run_send(ok=False)
        self.assertEqual(len(sent), 1)                                       # no "marked"
        self.assertIn("tried again next run", alerts[0])

    def test_a_crash_never_takes_the_run_down(self):
        conn, alerts = mock.MagicMock(), []
        with mock.patch.object(checkin_push, "get_checkin_candidates", side_effect=RuntimeError("boom")):
            checkin_push.send(conn, {}, alerts, NOW)
        self.assertIn("Check-in pushes failed", alerts[0])
        conn.rollback.assert_called()


class Messages(unittest.TestCase):
    def post(self, items, click="https://x/y"):
        with mock.patch("scheduler.notify._post", lambda payload, label: payload):
            return notify_checkins(items, "topic", click)

    def test_one_job_asks_in_scouts_voice(self):
        p = self.post([{"company": "Northwind Health", "title": "Senior Designer", "level": 1, "days": 5}])
        self.assertEqual(p["title"], "Did you hear back?")
        self.assertIn("Northwind Health: Senior Designer", p["message"])
        self.assertIn("You applied 5 days ago.", p["message"])
        self.assertEqual(p["click"], "https://x/y")

    def test_day_twenty_one_asks_to_close(self):
        p = self.post([{"company": "Co", "title": "Role", "level": 3, "days": 21}])
        self.assertEqual(p["title"], "Should I close it?")
        self.assertIn("no word", p["message"])

    def test_several_share_one_push_with_no_buttons(self):
        p = self.post([{"company": f"Co{i}", "title": "Role", "level": 1, "days": 6} for i in range(3)])
        self.assertEqual(p["title"], "Any word on 3 applications?")
        self.assertNotIn("actions", p)

    def test_nothing_to_send_is_a_success(self):
        self.assertTrue(notify_checkins([], "topic", "x"))
        self.assertTrue(notify_checkins([{"company": "a", "title": "b", "level": 1, "days": 5}], None, "x"))

    def test_link_goes_through_scout_to_the_tab(self):
        self.assertTrue(checkin_link("tok", False).endswith("/a/tok/checkin"))
        self.assertTrue(checkin_link("tok", True).endswith("/a/tok/checkin?all=1"))
        self.assertTrue(checkin_link(None, False).endswith("/?tab=applications"))


class Storing(unittest.TestCase):
    def conn(self, *fetches):
        conn = mock.MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = list(fetches)
        return conn, cur

    def test_answers_are_recorded_only_for_a_waiting_application(self):
        conn, cur = self.conn()
        cur.rowcount = 1
        self.assertTrue(_db.record_checkin(conn, 7, 9, "heard_no"))
        sql, params = cur.execute.call_args.args
        self.assertIn("SET heard_not_yet_at = now()", sql)
        self.assertIn("COALESCE(stage, 'waiting') = 'waiting'", sql)
        self.assertEqual(params, (7, 9))
        for answer, column in (("heard_yes", "heard_at"), ("close_no", "close_not_yet_at")):
            _db.record_checkin(conn, 7, 9, answer)
            self.assertIn(f"SET {column} = now()", cur.execute.call_args.args[0])

    def test_an_unknown_answer_writes_nothing(self):
        conn, cur = self.conn()
        self.assertFalse(_db.record_checkin(conn, 7, 9, "drop table"))
        cur.execute.assert_not_called()

    def test_going_back_to_waiting_starts_the_check_ins_over_but_other_stages_keep_them(self):
        conn, cur = self.conn(("interviewing", None), (1,))
        _db.set_stage(conn, 7, 9, "waiting")
        self.assertIn("heard_at = NULL", cur.execute.call_args_list[1].args[0])
        conn, cur = self.conn(("waiting", None), (1,))
        _db.set_stage(conn, 7, 9, "interviewing", "checkin")
        self.assertNotIn("heard_at = NULL", cur.execute.call_args_list[1].args[0])
        self.assertEqual(cur.execute.call_args_list[2].args[1][-1], "checkin")

    def test_pushes_are_counted_and_the_days_push_is_used(self):
        conn, cur = self.conn()
        _db.mark_checkins_pushed(conn, 7, {10: 1, 11: 3}, date(2026, 11, 24))
        calls = [c.args for c in cur.execute.call_args_list]
        self.assertEqual(calls[0][1], (1, 7, 10))
        self.assertIn("GREATEST(checkin_pushes", calls[0][0])
        self.assertEqual(calls[2], ("UPDATE users SET checkin_pushed_on = %s WHERE id = %s", (date(2026, 11, 24), 7)))


class History(unittest.TestCase):
    def test_an_answer_on_the_row_reads_as_hearing_back(self):
        when = datetime(2026, 11, 4, 18, tzinfo=timezone.utc)
        lines = application_history([("interviewing", when, "checkin"), ("offer", when, "panel")], None)
        self.assertEqual([l["text"] for l in lines], ["Heard back: interview", "Offer", "Applied"])
        self.assertEqual(application_history([("mine", when, "checkin")], None)[0]["text"], "Closed by me")


def waiting(pid, days, **extra):
    return dict(base_application(pid, f"Role {pid}"), stage="waiting", posting_status="open", applied_at=datetime.now(timezone.utc) - timedelta(days=days),
                heard_at=None, heard_not_yet_at=None, close_not_yet_at=None, checkin_pushes=0, **extra)


class Rows(Base):
    def test_a_row_asks_the_question_it_is_due_and_a_young_one_asks_nothing(self):
        self.applications = [waiting(1, 6), waiting(2, 2), waiting(3, 22), dict(waiting(4, 7), heard_at=datetime.now(timezone.utc))]
        html = self.get("/?tab=applications")
        row = lambda i: re.search(rf'<li class="fg-job" id="posting-{i}">.*?</li>', html, re.S).group(0)
        self.assertIn("Did you hear back?", row(1))
        self.assertIn('data-checkin="heard_yes"', row(1))
        self.assertIn('data-checkin="heard_no" data-note="I’ll check back in a week."', row(1))
        self.assertNotIn("data-action", row(2))
        self.assertIn("Should I close it?", row(3))
        self.assertIn('data-checkin="close_yes">Yes, close it', row(3))
        self.assertIn('data-note="No problem. I’ll leave it here."', row(3))
        self.assertIn('<label class="fg-voice fg-job-question" for="say-4">What did they say?</label>', row(4))
        self.assertIn('<option value="interviewing">Interview</option>', row(4))

    def test_the_templates_the_script_copies_are_in_the_list(self):
        self.applications = [waiting(1, 6)]
        html = self.client.get("/applications/list").get_data(as_text=True)
        self.assertIn('<template id="checkin-say-template">', html)
        self.assertIn('for="say-__id__"', html)
        self.assertIn('<template id="checkin-note-template">', html)

    def test_an_employer_closed_role_asks_nothing(self):
        self.applications = [dict(waiting(1, 9), posting_status="closed")]
        self.assertNotIn("Did you hear back?", self.get("/?tab=applications"))


class Routes(Base):
    def setUp(self):
        super().setUp()
        self.recorded = []
        patches = [
            mock.patch.object(apps, "get_conn", lambda: mock.MagicMock()),
            mock.patch.object(apps, "record_checkin", lambda c, u, pid, answer: self.recorded.append((pid, answer)) or True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_an_answer_is_recorded_and_nothing_comes_back(self):
        for answer in ("heard_yes", "heard_no", "close_no"):
            r = self.client.post("/application/9/checkin", data={"answer": answer})
            self.assertEqual(r.status_code, 204)
        self.assertEqual(self.recorded, [(9, "heard_yes"), (9, "heard_no"), (9, "close_no")])

    def test_an_unknown_answer_is_refused(self):
        self.assertEqual(self.client.post("/application/9/checkin", data={"answer": "close_yes"}).status_code, 400)
        self.assertEqual(self.recorded, [])


class Tap(Base):
    def setUp(self):
        super().setUp()
        self.events = []
        patches = [mock.patch.object(dapp_module(), "get_alert_target", lambda c, t: (7, 9, "https://ats/x") if t == "tok" else None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_a_tap_goes_to_the_row_and_is_counted_as_a_check_in_tap(self):
        r = self.client.get("/a/tok/checkin")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/?tab=applications#posting-9"))
        self.assertEqual(self.events, [("opened_checkin", 9)])

    def test_the_grouped_push_lands_on_the_tab(self):
        r = self.client.get("/a/tok/checkin?all=1")
        self.assertTrue(r.headers["Location"].endswith("/?tab=applications"))

    def test_a_link_preview_is_not_a_tap_and_an_unknown_link_goes_home(self):
        self.client.head("/a/tok/checkin")
        self.assertEqual(self.events, [])
        r = self.client.get("/a/nope/checkin")
        self.assertTrue(r.headers["Location"].endswith("/"))
        self.assertEqual(self.events, [])


def dapp_module():
    from dashboard import app as dapp
    return dapp


if __name__ == "__main__":
    unittest.main()
