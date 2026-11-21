"""SC-73 slice 2: the panel for one application. Where it stands (changeable, saved as it changes), a short history, and
what was sent; changing the stage answers with the toast's words and the change an Undo takes back."""
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from dashboard import applications as apps
from dashboard.elevate import STAGE_OPTIONS, application_history, stage_toast
from db import db as _db
from tests.test_applications_tab import Base

NOW = datetime.now(timezone.utc)


def stored(stage="waiting", status="open", applied_days=20):
    return {"id": 9, "company_name": "Northwind Health", "title": "Senior Product Designer", "stage": stage,
            "posting_status": status, "applied_at": NOW - timedelta(days=applied_days), "stage_changed_at": None}


class History(unittest.TestCase):
    def test_newest_first_ending_with_the_application_itself(self):
        lines = application_history([("interviewing", datetime(2026, 11, 4, 18, tzinfo=timezone.utc))],
                                    datetime(2026, 10, 28, 18, tzinfo=timezone.utc), tailored=True, letter=True)
        self.assertEqual(lines, [{"date": "Nov 4", "text": "Interviewing"},
                                 {"date": "Oct 28", "text": "Applied with a tailored resume and cover letter"}])

    def test_the_application_line_says_what_went_with_it(self):
        when = datetime(2026, 10, 28, 18, tzinfo=timezone.utc)
        self.assertEqual(application_history([], when, True, False)[0]["text"], "Applied with a tailored resume")
        self.assertEqual(application_history([], when)[0]["text"], "Applied")
        self.assertEqual(application_history([], None)[0], {"date": "", "text": "Applied"})   # no date kept: none invented

    def test_the_choices_and_the_toast_words(self):
        self.assertEqual([label for _, label in STAGE_OPTIONS],
                         ["Applied, waiting to hear", "Interviewing", "Offer", "Not moving forward", "Closed by me"])
        self.assertEqual(stage_toast("interviewing"), "Moved to Interviewing.")
        self.assertEqual(stage_toast("nonsense"), "")


class Panel(Base):
    def setUp(self):
        super().setUp()
        self.row, self.files, self.changes = stored(), None, []
        self.set_calls, self.undo_calls, self.next_log = [], [], 12
        patches = [
            mock.patch.object(apps, "get_conn", lambda: mock.MagicMock()),
            mock.patch.object(apps, "get_applications", lambda c, u, pid=None: [dict(self.row)] if self.row else []),
            mock.patch.object(apps, "applied_tailoring", lambda c, u, pid: self.files),
            mock.patch.object(apps, "get_stage_history", lambda c, u, pid: list(self.changes)),
            mock.patch.object(apps, "set_stage", self.fake_set),
            mock.patch.object(apps, "undo_stage", lambda c, u, pid, log: self.undo_calls.append((pid, log)) or True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def fake_set(self, conn, user_id, posting_id, stage):
        self.set_calls.append((posting_id, stage))
        if stage == self.row["stage"] or stage not in dict(STAGE_OPTIONS):
            return None
        self.row["stage"] = stage
        self.changes.insert(0, (stage, NOW))
        return self.next_log

    def panel(self, url="/application/9"):
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        return r.get_data(as_text=True)

    def test_shows_where_it_stands_history_and_what_was_sent(self):
        self.row["applied_at"] = datetime(2026, 10, 28, 18, tzinfo=timezone.utc)
        self.files = {"id": 41, "letter": "Hello there.\nI would love to talk.", "saved_at": datetime(2026, 10, 28, 18, tzinfo=timezone.utc)}
        html = self.panel()
        self.assertIn('<option value="waiting" selected>Applied, waiting to hear</option>', html)
        self.assertIn("Northwind Health", html)
        self.assertIn("Applied with a tailored resume and cover letter", html)
        self.assertIn("Resume, tailored for Northwind Health", html)
        self.assertIn("Saved Oct 28", html)
        self.assertIn('href="/tailor/41/resume.pdf" download>PDF', html)
        self.assertIn('href="/tailor/41/resume.docx" download>Word', html)
        self.assertIn("7 words", html)
        self.assertIn('data-copy-text="Hello there.\nI would love to talk."', html)
        self.assertIn('href="/open/9" target="_blank"', html)
        self.assertNotIn("data-toast", html)

    def test_an_application_without_tailored_files_says_so(self):
        html = self.panel()
        self.assertIn("You applied with your saved resume.", html)
        self.assertNotIn("/tailor/", html)
        self.assertNotIn("data-copy-text", html)

    def test_resume_without_a_letter_has_no_copy_button(self):
        self.files = {"id": 41, "letter": "", "saved_at": NOW}
        html = self.panel()
        self.assertIn("/tailor/41/resume.pdf", html)
        self.assertNotIn("Cover letter", html)

    def test_says_when_the_employer_closed_the_role(self):
        self.row = stored(status="closed")
        self.assertIn("The employer closed this role.", self.panel())
        self.row = stored()
        self.assertNotIn("The employer closed this role.", self.panel())

    def test_a_job_not_applied_to_has_no_panel(self):
        self.row = None
        self.assertEqual(self.client.get("/application/9").status_code, 404)

    def test_changing_the_stage_answers_with_the_toast_words_and_the_undo(self):
        r = self.client.post("/application/9/stage", data={"stage": "interviewing"}, headers={"X-Requested-With": "fetch"})
        html = r.get_data(as_text=True)
        self.assertEqual(self.set_calls, [(9, "interviewing")])
        self.assertIn('<option value="interviewing" selected>Interviewing</option>', html)
        self.assertIn('data-toast="Moved to Interviewing."', html)
        self.assertIn('data-undo-log="12"', html)
        self.assertIn('data-undo-url="/application/9/stage/undo"', html)
        self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_picking_the_same_stage_again_changes_nothing_and_says_nothing(self):
        html = self.client.post("/application/9/stage", data={"stage": "waiting"}).get_data(as_text=True)
        self.assertNotIn("data-toast", html)
        self.assertEqual(self.changes, [])

    def test_an_unknown_stage_is_refused_quietly(self):
        html = self.client.post("/application/9/stage", data={"stage": "hired"}).get_data(as_text=True)
        self.assertNotIn("data-toast", html)
        self.assertIn('selected>Applied, waiting to hear', html)

    def test_undo_takes_back_only_the_named_change(self):
        self.client.post("/application/9/stage/undo", data={"log": "12"})
        self.assertEqual(self.undo_calls, [(9, 12)])
        self.client.post("/application/9/stage/undo", data={"log": "not a number"})
        self.assertEqual(self.undo_calls[-1], (9, 0))

    def test_the_list_comes_on_its_own_with_scouts_line(self):
        self.applications = [dict(application(1), stage="interviewing", stage_changed_at=NOW, posting_status="open"),
                             dict(application(2), stage="waiting", posting_status="open")]
        html = self.client.get("/applications/list").get_data(as_text=True)
        self.assertIn('data-voice-text="1 interview going. 1 still waiting to hear."', html)
        self.assertEqual(re.findall(r'<h2 class="fg-day-label">(.*?) <span', html), ["Waiting to hear", "Interviewing"])


def application(pid):
    from tests.test_applications_tab import application as make
    return make(pid, f"Role {pid}", 3)


class Storing(unittest.TestCase):
    def conn(self, *fetches):
        conn = mock.MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = list(fetches)
        return conn, cur

    def test_a_change_is_stored_with_what_undo_will_put_back(self):
        before = NOW - timedelta(days=3)
        conn, cur = self.conn(("waiting", before), (12,))
        self.assertEqual(_db.set_stage(conn, 7, 9, "interviewing"), 12)
        statements = [c.args for c in cur.execute.call_args_list]
        self.assertIn("FOR UPDATE", statements[0][0])
        self.assertIn("stage_changed_at = now()", statements[1][0])
        self.assertEqual(statements[2][1], (7, 9, "interviewing", "waiting", before))

    def test_nothing_changes_for_the_same_stage_an_unapplied_job_or_an_unknown_stage(self):
        conn, cur = self.conn(("offer", None))
        self.assertIsNone(_db.set_stage(conn, 7, 9, "offer"))
        self.assertEqual(len(cur.execute.call_args_list), 1)         # looked, wrote nothing
        conn, cur = self.conn(None)
        self.assertIsNone(_db.set_stage(conn, 7, 9, "interviewing"))
        conn, cur = self.conn()
        self.assertIsNone(_db.set_stage(conn, 7, 9, "hired"))
        cur.execute.assert_not_called()

    def test_undo_restores_the_previous_stage_and_removes_the_row(self):
        before = NOW - timedelta(days=3)
        conn, cur = self.conn((12, "waiting", before))
        self.assertTrue(_db.undo_stage(conn, 7, 9, 12))
        statements = [c.args for c in cur.execute.call_args_list]
        self.assertEqual(statements[1][1], ("waiting", before, 7, 9))
        self.assertIn("DELETE FROM application_stage_log", statements[2][0])

    def test_a_late_undo_for_an_older_change_does_nothing(self):
        conn, cur = self.conn((13, "interviewing", NOW))              # a newer change exists
        self.assertFalse(_db.undo_stage(conn, 7, 9, 12))
        self.assertEqual(len(cur.execute.call_args_list), 1)
        conn, cur = self.conn(None)
        self.assertFalse(_db.undo_stage(conn, 7, 9, 12))


if __name__ == "__main__":
    unittest.main()
