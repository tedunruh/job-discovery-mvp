"""Offline tests for the Neon quota warning (SC-65): says so before the plan runs out, once a day, says so when it
cannot see, and never stops a run."""
import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from scheduler import quota_warning as qw
from scheduler import run_collectors as rc

GB = 10 ** 9
SLOT = datetime(2026, 10, 30, 14, 1, tzinfo=timezone.utc)      # the run that starts in the alert quarter hour
LATER = datetime(2026, 10, 30, 14, 31, tzinfo=timezone.utc)    # same hour, later run
EVENING = datetime(2026, 10, 30, 20, 1, tzinfo=timezone.utc)


def record(hours=0.0, transfer_gb=0.0, project_id="p1"):
    """A Neon project record with this period's usage so far."""
    return {"id": project_id, "compute_time_seconds": hours * 3600, "data_transfer_bytes": transfer_gb * GB}


class Usage(unittest.TestCase):
    def test_converts_seconds_to_hours_and_bytes_to_decimal_gb(self):
        used = qw.usage(record(30, 3))
        self.assertAlmostEqual(used["compute"], 30)
        self.assertAlmostEqual(used["transfer"], 3)

    def test_zero_is_a_real_answer(self):
        self.assertEqual(qw.usage(record(0, 0)), {"compute": 0.0, "transfer": 0.0})

    def test_a_record_without_the_figures_is_unknown_not_zero(self):
        self.assertIsNone(qw.usage({"id": "p1"}))
        self.assertIsNone(qw.usage({"id": "p1", "compute_time_seconds": 5}))
        self.assertIsNone(qw.usage({"id": "p1", "compute_time_seconds": None, "data_transfer_bytes": None}))
        self.assertIsNone(qw.usage({"id": "p1", "compute_time_seconds": "5", "data_transfer_bytes": "6"}))
        self.assertIsNone(qw.usage(None))


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
    LISTED = {"id": "p1", "org_id": "org-1"}

    def run_check(self, now, detail=None, env=None, status=200, raises=None, listed=None):
        env = {"NEON_API_KEY": "k"} if env is None else env
        listed = [self.LISTED] if listed is None else listed
        detail_resp = mock.Mock(ok=status == 200, status_code=status, text="denied")
        detail_resp.json.return_value = {"project": detail if detail is not None else record(10, 1)}
        list_resp = mock.Mock()
        list_resp.json.return_value = {"projects": listed}

        def get(url, **kwargs):
            if raises:
                raise raises
            return list_resp if url.endswith("/projects") else detail_resp
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(qw.requests, "get", side_effect=get) as g:
            return qw.check(now), g

    def test_no_key_does_nothing_and_asks_nothing(self):
        lines, get = self.run_check(SLOT, env={})
        self.assertEqual(lines, [])
        get.assert_not_called()

    def test_warns_only_in_the_one_daily_run(self):
        project = record(220)
        self.assertEqual(len(self.run_check(SLOT, project)[0]), 1)
        self.assertEqual(self.run_check(LATER, project)[0], [])
        self.assertEqual(self.run_check(EVENING, project)[0], [])

    def test_all_well_in_the_daily_run_is_silent(self):
        self.assertEqual(self.run_check(SLOT, record(10, 1))[0], [])

    def test_reads_the_record_of_the_project_it_found_with_the_key(self):
        _, get = self.run_check(SLOT)
        urls = [c.args[0] for c in get.call_args_list]
        self.assertTrue(urls[-1].endswith("/projects/p1"))
        self.assertTrue(all(c.kwargs["headers"]["Authorization"] == "Bearer k" for c in get.call_args_list))
        self.assertTrue(all(c.kwargs["timeout"] for c in get.call_args_list))

    def test_names_the_project_when_told_to(self):
        _, get = self.run_check(SLOT, env={"NEON_API_KEY": "k", "NEON_PROJECT_ID": "p9"})
        self.assertTrue(get.call_args_list[0].args[0].endswith("/projects/p9"))

    def test_several_projects_without_a_name_is_blind_in_the_daily_run(self):
        lines, _ = self.run_check(SLOT, record(10), listed=[self.LISTED, {"id": "p2"}])
        self.assertIn("blind", lines[0])

    def test_neon_refusing_is_said_in_the_daily_run_and_never_raises(self):
        lines, _ = self.run_check(SLOT, status=403)
        self.assertEqual(len(lines), 1)
        self.assertIn("blind", lines[0])
        self.assertEqual(self.run_check(EVENING, status=403)[0], [])

    def test_neon_unreachable_never_raises(self):
        self.assertIn("blind", self.run_check(SLOT, raises=RuntimeError("503"))[0][0])
        self.assertEqual(self.run_check(EVENING, raises=RuntimeError("503"))[0], [])

    def test_a_record_without_the_figures_is_blind(self):
        self.assertIn("blind", self.run_check(SLOT, {"id": "p1"})[0][0])

    def test_the_numbers_are_logged_every_run(self):
        with mock.patch("builtins.print") as out:
            self.run_check(EVENING, record(12.5, 2))
        text = " ".join(str(c.args[0]) for c in out.call_args_list)
        self.assertIn("compute 12.50 hours", text)
        self.assertIn("network transfer 2.00 GB", text)

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
