"""Offline tests for the Neon quota warning (SC-65): says so before the plan runs out, once a day, says so when it
cannot see, and never stops a run."""
import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from scheduler import quota_warning as qw
from scheduler import run_collectors as rc

GB = 1024 ** 3
SLOT = datetime(2026, 10, 30, 14, 1, tzinfo=timezone.utc)      # the run that starts in the alert quarter hour
LATER = datetime(2026, 10, 30, 14, 31, tzinfo=timezone.utc)    # same hour, later run
EVENING = datetime(2026, 10, 30, 20, 1, tzinfo=timezone.utc)


def history(*periods, project_id="p1"):
    """A usage-history answer; each period is a list of days, each day {metric: value}."""
    return {"projects": [{"project_id": project_id, "periods": [
        {"period_id": str(i), "consumption": [
            {"metrics": [{"metric_name": k, "value": v} for k, v in day.items()]} for day in days]}
        for i, days in enumerate(periods)]}]}


def day(hours=0.0, transfer_gb=0.0):
    return {"compute_unit_seconds": hours * 3600, "public_network_transfer_bytes": transfer_gb * GB}


class Usage(unittest.TestCase):
    def test_sums_the_days_of_the_current_period(self):
        used = qw.usage(history([day(10, 1), day(20, 2)]), "p1")
        self.assertAlmostEqual(used["compute"], 30)
        self.assertAlmostEqual(used["transfer"], 3)

    def test_only_the_last_period_counts(self):
        """A plan change starts a new period with a new allowance; the old one's use is not this month's."""
        used = qw.usage(history([day(200, 20)], [day(5, 1)]), "p1")
        self.assertAlmostEqual(used["compute"], 5)
        self.assertAlmostEqual(used["transfer"], 1)

    def test_other_metrics_are_ignored_and_missing_ones_are_zero(self):
        data = history([{"root_branch_bytes_month": 10 ** 12}])
        self.assertEqual(qw.usage(data, "p1"), {"compute": 0.0, "transfer": 0.0})

    def test_nothing_for_the_project_is_unknown_not_zero(self):
        self.assertIsNone(qw.usage(history([day(1)], project_id="other"), "p1"))
        self.assertIsNone(qw.usage({"projects": []}, "p1"))
        self.assertIsNone(qw.usage({}, "p1"))
        self.assertIsNone(qw.usage(None, "p1"))
        self.assertIsNone(qw.usage({"projects": [{"project_id": "p1", "periods": []}]}, "p1"))


class Warnings(unittest.TestCase):
    def test_all_well_is_silent(self):
        self.assertEqual(qw.warnings({"compute": 100, "transfer": 5}), [])

    def test_compute_at_eighty_percent_warns(self):
        lines = qw.warnings({"compute": 200, "transfer": 0})
        self.assertEqual(len(lines), 1)
        self.assertIn("compute is at 80%", lines[0])
        self.assertIn("200.0 of 250 hours", lines[0])

    def test_just_under_is_silent(self):
        self.assertEqual(qw.warnings({"compute": 199.9, "transfer": 19.9}), [])

    def test_transfer_counts_too(self):
        self.assertIn("network transfer is at 100%", qw.warnings({"compute": 0, "transfer": 25})[0])

    def test_each_allowance_over_gets_its_own_line(self):
        self.assertEqual(len(qw.warnings({"compute": 230, "transfer": 24})), 2)

    def test_not_being_able_to_see_is_said_not_assumed_fine(self):
        lines = qw.warnings(None)
        self.assertEqual(len(lines), 1)
        self.assertIn("blind", lines[0])


class Check(unittest.TestCase):
    PROJECT = {"id": "p1", "org_id": "org-1"}

    def run_check(self, now, answer=None, env=None, project=None, status=200, raises=None):
        env = {"NEON_API_KEY": "k"} if env is None else env
        project = self.PROJECT if project is None else project
        usage_resp = mock.Mock(ok=status == 200, status_code=status, text="denied")
        usage_resp.json.return_value = answer if answer is not None else history([day(10)])
        list_resp = mock.Mock()
        list_resp.json.return_value = {"projects": [project], "project": project}

        def get(url, **kwargs):
            if raises:
                raise raises
            return usage_resp if "consumption_history" in url else list_resp
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(qw.requests, "get", side_effect=get) as g:
            return qw.check(now), g

    def test_no_key_does_nothing_and_asks_nothing(self):
        lines, get = self.run_check(SLOT, env={})
        self.assertEqual(lines, [])
        get.assert_not_called()

    def test_warns_only_in_the_one_daily_run(self):
        answer = history([day(220)])
        self.assertEqual(len(self.run_check(SLOT, answer)[0]), 1)
        self.assertEqual(self.run_check(LATER, answer)[0], [])
        self.assertEqual(self.run_check(EVENING, answer)[0], [])

    def test_all_well_in_the_daily_run_is_silent(self):
        self.assertEqual(self.run_check(SLOT, history([day(10)]))[0], [])

    def test_asks_for_this_project_and_org_with_the_key(self):
        _, get = self.run_check(SLOT)
        call = [c for c in get.call_args_list if "consumption_history/v2/projects" in c.args[0]][0]
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer k")
        self.assertEqual(call.kwargs["params"]["org_id"], "org-1")
        self.assertEqual(call.kwargs["params"]["project_ids"], "p1")
        self.assertEqual(call.kwargs["params"]["granularity"], "daily")
        self.assertIn("compute_unit_seconds", call.kwargs["params"]["metrics"])
        self.assertIn("public_network_transfer_bytes", call.kwargs["params"]["metrics"])
        self.assertTrue(all(c.kwargs["timeout"] for c in get.call_args_list))

    def test_the_window_covers_today(self):
        _, get = self.run_check(SLOT)
        params = [c for c in get.call_args_list if "consumption_history" in c.args[0]][0].kwargs["params"]
        self.assertEqual(params["to"], "2026-10-31T00:00:00Z")
        self.assertLess(params["from"], "2026-10-01T00:00:00Z")

    def test_names_the_project_when_told_to(self):
        _, get = self.run_check(SLOT, env={"NEON_API_KEY": "k", "NEON_PROJECT_ID": "p1"})
        self.assertTrue(get.call_args_list[0].args[0].endswith("/projects/p1"))

    def test_several_projects_without_a_name_is_blind_in_the_daily_run(self):
        listing = mock.Mock()
        listing.json.return_value = {"projects": [self.PROJECT, {"id": "p2", "org_id": "org-1"}]}
        usage_resp = mock.Mock(ok=True)
        usage_resp.json.return_value = history([day(10)])  # healthy, so a guess of "the first one" would look fine

        def get(url, **kwargs):
            return usage_resp if "consumption_history" in url else listing
        with mock.patch.dict(os.environ, {"NEON_API_KEY": "k"}, clear=True), mock.patch.object(qw.requests, "get", side_effect=get):
            self.assertIn("blind", qw.check(SLOT)[0])

    def test_neon_refusing_is_said_in_the_daily_run_and_never_raises(self):
        lines, _ = self.run_check(SLOT, status=403)
        self.assertEqual(len(lines), 1)
        self.assertIn("blind", lines[0])
        self.assertEqual(self.run_check(EVENING, status=403)[0], [])

    def test_neon_unreachable_never_raises(self):
        self.assertIn("blind", self.run_check(SLOT, raises=RuntimeError("503"))[0][0])
        self.assertEqual(self.run_check(EVENING, raises=RuntimeError("503"))[0], [])

    def test_an_answer_without_our_project_is_blind(self):
        self.assertIn("blind", self.run_check(SLOT, {"projects": []})[0][0])

    def test_the_numbers_are_logged_every_run(self):
        with mock.patch("builtins.print") as out:
            self.run_check(EVENING, history([day(12.5)]))
        self.assertIn("compute 12.50 hours", " ".join(str(c.args[0]) for c in out.call_args_list))

    def test_the_failure_is_logged_with_its_reason(self):
        with mock.patch("builtins.print") as out:
            self.run_check(EVENING, status=403)
        text = " ".join(str(c.args[0]) for c in out.call_args_list)
        self.assertIn("HTTP 403", text)
        self.assertNotIn("Bearer", text)


class Wiring(unittest.TestCase):
    def test_a_warning_goes_out_with_the_run_alerts(self):
        with mock.patch.object(rc, "check_neon_quota", return_value=["Neon compute is at 85%"]), \
                mock.patch.object(rc, "_run") as run, mock.patch.object(rc, "previous_streak", return_value=None), \
                mock.patch.object(rc, "notify_health_alerts") as notify, mock.patch.object(rc, "ping_success"):
            rc.run()
        self.assertEqual(run.call_args.args[0], ["Neon compute is at 85%"])
        notify.assert_called_once_with(["Neon compute is at 85%"])


class Workflow(unittest.TestCase):
    def test_the_collect_step_gets_the_key(self):
        path = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows", "collect.yml")
        step = open(path).read().split("- id: collect", 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("NEON_API_KEY: ${{ secrets.NEON_API_KEY }}", step)


if __name__ == "__main__":
    unittest.main()
