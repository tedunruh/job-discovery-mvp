"""SC-73 slice 1: where each application stands. The Applications tab is grouped Waiting to hear, Interviewing, Offer
and Closed (empty groups hidden); a row shows only its current status; Scout's line counts what is going."""
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from dashboard.elevate import (
    APPLICATION_GROUPS, application_group, application_status, applications_line, group_applications, short_date,
)
from db import db as _db
from tests.test_applications_tab import Base

NOW = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)


def app(pid, stage="waiting", days_ago=3, status="open", changed_days_ago=None, applied_ago="3 days ago"):
    when = NOW - timedelta(days=days_ago)
    changed = NOW - timedelta(days=changed_days_ago) if changed_days_ago is not None else None
    return {"id": pid, "title": f"Role {pid}", "company_name": "Co", "stage": stage, "posting_status": status,
            "applied_at": when, "stage_changed_at": changed, "activity_at": changed or when, "applied_ago": applied_ago}


class Groups(unittest.TestCase):
    def test_each_stage_has_its_group(self):
        self.assertEqual(application_group(app(1)), "Waiting to hear")
        self.assertEqual(application_group(app(1, "interviewing")), "Interviewing")
        self.assertEqual(application_group(app(1, "offer")), "Offer")
        self.assertEqual(application_group(app(1, "not")), "Closed")
        self.assertEqual(application_group(app(1, "mine")), "Closed")
        self.assertEqual(application_group({"id": 1}), "Waiting to hear")       # no stage stored: waiting

    def test_an_employer_closed_role_is_closed_unless_you_are_talking_to_them(self):
        self.assertEqual(application_group(app(1, "waiting", status="closed")), "Closed")
        self.assertEqual(application_group(app(1, "interviewing", status="closed")), "Interviewing")
        self.assertEqual(application_group(app(1, "offer", status="closed")), "Offer")

    def test_groups_come_in_order_empty_ones_hidden_most_recent_activity_first(self):
        rows = [app(1, days_ago=9), app(2, "not", changed_days_ago=2), app(3, days_ago=1), app(4, "interviewing", changed_days_ago=1)]
        grouped = group_applications(rows)
        self.assertEqual([(label, count, [r["id"] for r in rs]) for label, count, rs in grouped],
                         [("Waiting to hear", 2, [3, 1]), ("Interviewing", 1, [4]), ("Closed", 1, [2])])
        self.assertEqual(APPLICATION_GROUPS, ("Waiting to hear", "Interviewing", "Offer", "Closed"))
        self.assertEqual(group_applications([]), [])

    def test_a_row_without_a_date_sorts_last(self):
        old = app(1)
        old["applied_at"] = old["activity_at"] = None
        self.assertEqual([r["id"] for r in group_applications([old, app(2, days_ago=40)])[0][2]], [2, 1])

    def test_closed_rows_are_marked_to_dim(self):
        closed, live = app(1, "not"), app(2)
        group_applications([closed, live])
        self.assertTrue(closed["closed"])
        self.assertFalse(live["closed"])


class Status(unittest.TestCase):
    def test_one_current_status_per_row(self):
        self.assertEqual(application_status(app(1)), ("Waiting to hear", "Applied 3 days ago"))
        self.assertEqual(application_status(app(1, applied_ago="")), ("Waiting to hear", "Applied"))
        self.assertEqual(application_status(app(1, "interviewing", changed_days_ago=16)), ("Interviewing", "Since Nov 4"))
        self.assertEqual(application_status(app(1, "offer", changed_days_ago=16)), ("Offer", "Since Nov 4"))
        self.assertEqual(application_status(app(1, "not", changed_days_ago=21)), ("Not moving forward", "Oct 30"))
        self.assertEqual(application_status(app(1, "mine", changed_days_ago=21)), ("Closed by you", "Oct 30"))
        self.assertEqual(application_status(app(1, status="closed")), ("The employer closed this role", ""))

    def test_short_date_is_the_persons_calendar_day(self):
        self.assertEqual(short_date(datetime(2026, 11, 5, 2, 0, tzinfo=timezone.utc)), "Nov 4")    # 8 PM Nov 4 in Chicago


class Line(unittest.TestCase):
    def line(self, rows):
        return applications_line(group_applications(rows))

    def test_counts_what_is_going(self):
        rows = [app(1, "interviewing", changed_days_ago=1)] + [app(i) for i in (2, 3, 4)]
        self.assertEqual(self.line(rows), "1 interview going. 3 still waiting to hear.")
        self.assertEqual(self.line([app(1, "interviewing"), app(2, "interviewing"), app(3, "offer")]), "1 offer to answer. 2 interviews going.")

    def test_nothing_going_says_nothing(self):
        self.assertEqual(self.line([]), "")
        self.assertEqual(self.line([app(1, "not"), app(2, "mine")]), "")


class Page(Base):
    def test_the_tab_is_grouped_and_the_line_counts_what_is_going(self):
        self.applications = [
            {**app(1, applied_ago="3 days ago"), "location": "Remote", "remote_type": "remote", "ats_type": "lever"},
            {**app(2, "interviewing", changed_days_ago=16), "location": "Remote", "remote_type": "remote", "ats_type": "lever"},
            {**app(3, "not", changed_days_ago=21), "location": "Remote", "remote_type": "remote", "ats_type": "lever"},
            {**app(4, status="closed"), "location": "Remote", "remote_type": "remote", "ats_type": "lever"},
        ]
        html = self.get("/?tab=applications")
        self.assertEqual(re.findall(r'<h2 class="fg-day-label">(.*?) <span', html), ["Waiting to hear", "Interviewing", "Closed"])
        self.assertIn("1 interview going. 1 still waiting to hear.", html)
        self.assertIn("<p data-state-top>Interviewing</p>", html)
        self.assertIn("data-state-when>Since Nov 4</p>", html)
        self.assertIn("<p data-state-top>Not moving forward</p>", html)
        self.assertIn("<p data-state-top>The employer closed this role</p>", html)
        self.assertEqual(len(re.findall(r'<li class="fg-job fg-job--closed"', html)), 2)
        self.assertEqual(len(re.findall(r'<li class="fg-job" ', html)), 2)
        self.assertNotIn("Newest first", html)

    def test_no_line_and_no_groups_without_applications(self):
        html = self.get("/?tab=applications")
        self.assertIn("No applications yet.", html)
        self.assertNotIn("fg-day-label", html)
        self.assertIn('<div class="voice-wrap" hidden>', html)   # the line's place is kept (a stage change can fill it) but empty

    def test_open_roles_keeps_its_own_line(self):
        self.assertIn("new since yesterday", self.get("/"))


class Storing(unittest.TestCase):
    def sql(self, applied):
        conn = mock.MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        _db.set_applied(conn, 5, applied, 7)
        return cur.execute.call_args_list[0].args

    def test_applying_starts_waiting_and_keeps_a_stage_it_already_has(self):
        sql, params = self.sql(True)
        self.assertEqual(params, (7, 5, True, True, True))
        self.assertIn("COALESCE(user_postings.stage, 'waiting')", sql)

    def test_undo_clears_the_stage(self):
        sql, params = self.sql(False)
        self.assertEqual(params, (7, 5, False, False, False))
        self.assertIn("stage = CASE WHEN EXCLUDED.applied THEN", sql)          # no ELSE: NULL when not applied
        self.assertIn("heard_at = CASE WHEN EXCLUDED.applied THEN user_postings.heard_at END", sql)   # SC-73 slice 3: answers go too
        self.assertIn("checkin_pushes = CASE WHEN EXCLUDED.applied THEN user_postings.checkin_pushes ELSE 0 END", sql)

    def test_undoing_applied_lets_go_of_the_history(self):
        conn = mock.MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        _db.set_applied(conn, 5, False, 7)
        sql, params = cur.execute.call_args.args
        self.assertIn("DELETE FROM application_stage_log", sql)
        self.assertEqual(params, (7, 5))


if __name__ == "__main__":
    unittest.main()
