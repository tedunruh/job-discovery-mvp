"""Offline tests for the Neon quota warning (SC-65): says so before the plan runs out, once a day, and never
stops a run."""
import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from scheduler import quota_warning as qw
from scheduler import run_collectors as rc

GB = 1024 ** 3
NOON = datetime(2026, 10, 30, 14, 5, tzinfo=timezone.utc)   # inside the alert hour
EVENING = datetime(2026, 10, 30, 20, 5, tzinfo=timezone.utc)


def project(hours=0.0, storage_gb=0.0, transfer_gb=0.0, start="2026-10-01T00:00:00Z"):
    return {"compute_time_seconds": hours * 3600, "synthetic_storage_size": storage_gb * GB,
            "data_transfer_bytes": transfer_gb * GB, "consumption_period_start": start}


class Warnings(unittest.TestCase):
    def test_all_well_is_silent(self):
        self.assertEqual(qw.warnings(project(100, 1, 5), NOON), [])

    def test_compute_at_eighty_percent_warns(self):
        lines = qw.warnings(project(hours=200), NOON)
        self.assertEqual(len(lines), 1)
        self.assertIn("compute is at 80%", lines[0])
        self.assertIn("200.0 of 250 hours", lines[0])

    def test_just_under_is_silent(self):
        self.assertEqual(qw.warnings(project(hours=199.9), NOON), [])

    def test_storage_and_transfer_count_too(self):
        self.assertIn("storage is at 90%", qw.warnings(project(storage_gb=4.5), NOON)[0])
        self.assertIn("network transfer is at 100%", qw.warnings(project(transfer_gb=25), NOON)[0])

    def test_each_over_allowance_gets_its_own_line(self):
        self.assertEqual(len(qw.warnings(project(230, 4.5, 24), NOON)), 3)

    def test_no_usage_in_a_month_under_way_is_said_not_assumed_fine(self):
        lines = qw.warnings(project(0, 0, 0, start="2026-10-01T00:00:00Z"), NOON)
        self.assertEqual(len(lines), 1)
        self.assertIn("blind", lines[0])

    def test_no_usage_in_a_brand_new_month_is_not_alarming(self):
        self.assertEqual(qw.warnings(project(0, 0, 0, start="2026-10-30T01:00:00Z"), NOON), [])

    def test_a_record_without_usage_fields(self):
        self.assertEqual(len(qw.warnings({"consumption_period_start": "2026-10-01T00:00:00Z"}, NOON)), 1)
        self.assertEqual(qw.warnings({}, NOON), [])


class Check(unittest.TestCase):
    def check(self, body, now, env=None, status=None):
        env = {"NEON_API_KEY": "k"} if env is None else env
        resp = mock.Mock()
        resp.raise_for_status.side_effect = status
        resp.json.return_value = body
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(qw.requests, "get", return_value=resp) as get:
            return qw.check(now), get

    def test_no_key_does_nothing_and_asks_nothing(self):
        lines, get = self.check({}, NOON, env={})
        self.assertEqual(lines, [])
        get.assert_not_called()

    def test_warns_only_in_the_alert_hour(self):
        body = {"projects": [project(hours=220)]}
        self.assertEqual(len(self.check(body, NOON)[0]), 1)
        self.assertEqual(self.check(body, EVENING)[0], [])

    def test_sends_the_key_and_a_timeout(self):
        _, get = self.check({"projects": [project(1)]}, NOON)
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer k")
        self.assertTrue(get.call_args.kwargs["timeout"])

    def test_names_the_project_when_told_to(self):
        lines, get = self.check({"project": project(hours=220)}, NOON, env={"NEON_API_KEY": "k", "NEON_PROJECT_ID": "p1"})
        self.assertTrue(get.call_args.args[0].endswith("/projects/p1"))
        self.assertTrue(get.call_args.kwargs["timeout"])
        self.assertEqual(len(lines), 1)

    def test_several_projects_without_a_name_does_nothing(self):
        self.assertEqual(self.check({"projects": [project(220), project(220)]}, NOON)[0], [])

    def test_neon_unreachable_never_raises(self):
        self.assertEqual(self.check({}, NOON, status=RuntimeError("503"))[0], [])
        self.assertEqual(self.check({"unexpected": 1}, NOON)[0], [])

    def test_the_numbers_are_logged_every_run(self):
        with mock.patch("builtins.print") as out:
            self.check({"projects": [project(12.5)]}, EVENING)
        self.assertIn("compute 12.50 hours", " ".join(str(c.args[0]) for c in out.call_args_list))


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
