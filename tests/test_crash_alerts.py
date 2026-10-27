"""Offline tests for crash alerts that do not repeat (SC-65): one alert when the collector starts crashing, a
reminder now and then, one message when it recovers, and no database address in any of them."""
import os
import time
import unittest
from unittest import mock

import requests

from scheduler import crash_alerts as ca
from scheduler import run_collectors as rc


def runs_response(*runs):
    """A GitHub 'list workflow runs' answer, newest first: each run is (id, conclusion). Run n started at 03:45
    UTC minus n minutes, so lower ids are newer."""
    resp = mock.Mock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"workflow_runs": [{"id": i, "conclusion": c, "created_at": started(i)} for i, c in runs]}
    return resp


def started(run_id):
    minutes = 24 * 60 + 45 - (100 - run_id)  # run 100 is 03:45 on the day after BASE; each id before it is a minute earlier
    return f"2026-10-27T{(minutes // 60) % 24:02d}:{minutes % 60:02d}:00Z"


ENV = {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_TOKEN": "t", "GITHUB_RUN_ID": "100"}


class Streak(unittest.TestCase):
    def streak(self, *runs, env=ENV):
        with mock.patch.dict("os.environ", env, clear=True), \
                mock.patch.object(ca.requests, "get", return_value=runs_response(*runs)):
            return ca.previous_streak()

    def crashed(self, *runs):
        return self.streak(*runs).crashed

    def test_last_run_fine_is_zero(self):
        self.assertEqual(self.crashed((99, "success"), (98, "failure")), 0)
        self.assertIsNone(self.streak((99, "success"), (98, "failure")).latest)

    def test_counts_crashes_in_a_row(self):
        self.assertEqual(self.crashed((99, "failure"), (98, "failure"), (97, "timed_out"), (96, "success"), (95, "failure")), 3)

    def test_remembers_when_the_newest_crash_started(self):
        streak = self.streak((99, "failure"), (98, "failure"))
        self.assertEqual(streak.latest, ca._started({"created_at": started(99)}))
        self.assertGreater(streak.latest, ca._started({"created_at": started(98)}))

    def test_the_current_run_is_not_counted(self):
        self.assertEqual(self.crashed((100, "failure"), (99, "success")), 0)

    def test_cancelled_runs_say_nothing(self):
        self.assertEqual(self.crashed((99, "cancelled"), (98, "failure"), (97, "skipped"), (96, "success")), 1)
        self.assertEqual(self.crashed((99, "cancelled"), (98, "success")), 0)

    def test_cannot_ask_without_a_token_or_repository(self):
        self.assertIsNone(self.streak((99, "failure"), env={"GITHUB_REPOSITORY": "owner/repo"}))
        self.assertIsNone(self.streak((99, "failure"), env={"GITHUB_TOKEN": "t"}))

    def test_github_unreachable_is_unknown_not_an_error(self):
        for error in (requests.ConnectionError("down"), requests.HTTPError("403"), ValueError("not json")):
            with mock.patch.dict("os.environ", ENV, clear=True), mock.patch.object(ca.requests, "get", side_effect=error):
                self.assertIsNone(ca.previous_streak())

    def test_an_unexpected_answer_is_unknown_not_an_error(self):
        resp = mock.Mock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = ["not", "a", "dict"]
        with mock.patch.dict("os.environ", ENV, clear=True), mock.patch.object(ca.requests, "get", return_value=resp):
            self.assertIsNone(ca.previous_streak())

    def test_asks_for_this_workflow_with_the_token(self):
        with mock.patch.dict("os.environ", ENV, clear=True), \
                mock.patch.object(ca.requests, "get", return_value=runs_response()) as get:
            ca.previous_streak()
        url = get.call_args.args[0]
        self.assertIn("/repos/owner/repo/actions/workflows/collect.yml/runs", url)
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer t")
        self.assertEqual(get.call_args.kwargs["params"]["status"], "completed")
        self.assertEqual(get.call_args.kwargs["params"]["per_page"], ca.PAGE)
        self.assertTrue(get.call_args.kwargs["timeout"])


HOUR = 3600
T0 = ca._started({"created_at": "2026-10-27T02:00:00Z"})  # the start of a two-hour slot (slots start on even hours, UTC)


class WhenToAlert(unittest.TestCase):
    def test_first_crash_alerts(self):
        self.assertTrue(ca.should_alert_crash(ca.Streak(0, None)))

    def test_unknown_alerts(self):
        self.assertTrue(ca.should_alert_crash(None))

    def test_quiet_inside_the_same_slot(self):
        streak = ca.Streak(3, T0 + 5 * 60)
        self.assertFalse(ca.should_alert_crash(streak, now=T0 + 20 * 60))
        self.assertFalse(ca.should_alert_crash(streak, now=T0 + 2 * HOUR - 60 - 5 * 60))

    def test_one_reminder_when_the_next_slot_begins(self):
        streak = ca.Streak(8, T0 + 2 * HOUR - 10 * 60)
        self.assertTrue(ca.should_alert_crash(streak, now=T0 + 2 * HOUR + 5 * 60))

    def test_a_long_outage_keeps_reminding(self):
        """The run count stops growing at one page of GitHub's answer; the reminders must not stop with it."""
        sent = 0
        for step in range(0, 30 * HOUR, 15 * 60):  # 30 hours of runs, each 15 minutes apart
            now = T0 + step
            if ca.should_alert_crash(ca.Streak(ca.PAGE, now - 15 * 60), now=now):
                sent += 1
        self.assertEqual(sent, 15)

    def test_reminder_gap_is_two_hours(self):
        self.assertEqual(ca.REMIND_HOURS, 2)


class Words(unittest.TestCase):
    def test_first_crash_is_the_plain_message(self):
        self.assertEqual(ca.crash_message(RuntimeError("boom"), ca.Streak(0, None)), "Collector run crashed: RuntimeError: boom")
        self.assertEqual(ca.crash_message(RuntimeError("boom"), None), "Collector run crashed: RuntimeError: boom")

    def test_reminder_says_how_many(self):
        text = ca.crash_message(RuntimeError("boom"), ca.Streak(7, T0))
        self.assertIn("8 runs in a row", text)
        self.assertIn("RuntimeError: boom", text)

    def test_a_count_at_the_page_limit_says_so(self):
        self.assertIn("100+ runs", ca.crash_message(RuntimeError("boom"), ca.Streak(ca.PAGE, T0)))
        self.assertIn("100+ runs", ca.recovered_message(ca.Streak(ca.PAGE, T0)))

    def test_recovery_message_counts_and_pluralises(self):
        self.assertIn("crashed 1 run in a row", ca.recovered_message(ca.Streak(1, T0)))
        self.assertIn("crashed 5 runs in a row", ca.recovered_message(ca.Streak(5, T0)))
        self.assertTrue(ca.recovered_message(ca.Streak(2, T0)).startswith("Collector is back"))

    def test_no_database_address_in_an_alert(self):
        error = RuntimeError('connection to server at "ep-little-king-b4u40dtt-pooler.c-2.us-east-2.aws.neon.tech" '
                             "(10.1.2.3), port 5432 failed: postgresql://user:secret@ep-x-1.neon.tech/db?sslmode=require")
        for streak in (ca.Streak(0, None), ca.Streak(7, T0)):
            text = ca.crash_message(error, streak)
            for leaked in ("neon.tech", "ep-little", "10.1.2.3", "secret", "user:"):
                self.assertNotIn(leaked, text)

    def test_long_errors_are_trimmed_and_flattened(self):
        text = ca.scrub("line one\n\nline   two " + "x" * 1000)
        self.assertLessEqual(len(text), 300)
        self.assertNotIn("\n", text)


class Wiring(unittest.TestCase):
    """What run() actually sends."""

    def run_with(self, streak, crash):
        sent = []
        patches = [
            mock.patch.object(rc, "_run", side_effect=RuntimeError("boom") if crash else None),
            mock.patch.object(rc, "previous_streak", return_value=streak),
            mock.patch.object(rc, "notify_health_alerts", side_effect=lambda alerts: sent.append(list(alerts))),
            mock.patch.object(rc, "ping_failure"),
            mock.patch.object(rc, "ping_success"),
        ]
        with patches[0], patches[1], patches[2], patches[3] as failure, patches[4] as success:
            if crash:
                with self.assertRaises(RuntimeError):
                    rc.run()
            else:
                rc.run()
        return sent, failure, success

    def test_first_crash_sends_one_alert_and_still_fails_the_run(self):
        sent, failure, success = self.run_with(ca.Streak(0, None), crash=True)
        self.assertEqual(sent, [["Collector run crashed: RuntimeError: boom"]])
        failure.assert_called_once()
        success.assert_not_called()

    def test_the_crash_alert_is_the_scrubbed_one(self):
        error = RuntimeError('could not connect to ep-abc-123.us-east-2.aws.neon.tech (10.0.0.1)')
        with mock.patch.object(rc, "_run", side_effect=error), \
                mock.patch.object(rc, "previous_streak", return_value=ca.Streak(0, None)), \
                mock.patch.object(rc, "notify_health_alerts") as notify, \
                mock.patch.object(rc, "ping_failure"):
            with self.assertRaises(RuntimeError):
                rc.run()
        text = "\n".join(notify.call_args.args[0])
        self.assertIn("could not connect", text)
        self.assertNotIn("neon.tech", text)
        self.assertNotIn("10.0.0.1", text)

    def test_health_alerts_gathered_before_the_crash_go_out_with_it(self):
        def partial(health_alerts):
            health_alerts.append("Board X failed")
            raise RuntimeError("boom")
        with mock.patch.object(rc, "_run", side_effect=partial), \
                mock.patch.object(rc, "previous_streak", return_value=ca.Streak(0, None)), \
                mock.patch.object(rc, "notify_health_alerts") as notify, \
                mock.patch.object(rc, "ping_failure"):
            with self.assertRaises(RuntimeError):
                rc.run()
        self.assertEqual(notify.call_args.args[0], ["Board X failed", "Collector run crashed: RuntimeError: boom"])

    def test_repeat_crash_is_quiet_but_still_pings_failure(self):
        sent, failure, _ = self.run_with(ca.Streak(3, time.time() - 60), crash=True)
        self.assertEqual(sent, [])
        failure.assert_called_once()

    def test_unknown_history_still_alerts(self):
        sent, _, _ = self.run_with(None, crash=True)
        self.assertEqual(len(sent), 1)

    def test_first_success_after_crashes_says_so_once(self):
        sent, _, success = self.run_with(ca.Streak(4, T0), crash=False)
        self.assertEqual(len(sent), 1)
        self.assertIn("Collector is back", sent[0][0])
        success.assert_called_once()

    def test_ordinary_success_is_silent(self):
        for streak in (ca.Streak(0, None), None):
            sent, _, success = self.run_with(streak, crash=False)
            self.assertEqual(sent, [])
            success.assert_called_once()


class Workflow(unittest.TestCase):
    """The collector can only ask GitHub about earlier runs if the workflow gives it a token that may read them."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows", "collect.yml")) as f:
            cls.text = f.read()

    def test_read_only_access_to_actions(self):
        block = self.text.split("\npermissions:\n", 1)[1].split("\n\n", 1)[0]
        self.assertIn("actions: read", block)
        self.assertNotIn("write", block)

    def test_the_collect_step_gets_the_token(self):
        step = self.text.split("- id: collect", 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("python scheduler/run_collectors.py", step)
        self.assertIn("GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}", step)


if __name__ == "__main__":
    unittest.main()
