"""The dashboard lists matching roles newest first and nothing else (SC-53: a ranking was tried and removed)."""
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from dashboard import app as dapp


def post(pid, title, hours_ago):
    return {"id": pid, "title": title, "company_name": "Co", "ats_type": "ashby", "location": "Remote - United States",
            "remote_type": "remote", "url": "u", "is_design": True, "applied": False,
            "first_seen_at": datetime.now(timezone.utc) - timedelta(hours=hours_ago)}


class Order(unittest.TestCase):
    def test_a_profile_user_sees_newest_first_whatever_the_titles(self):
        client = dapp.app.test_client()
        client.set_cookie(dapp.SESSION_COOKIE, "tok")
        # the database hands rows over newest first; the exact title the person wrote is the OLDEST
        rows = [post(1, "Associate Product Designer, Payments", 1), post(2, "Product Designer", 2),
                post(3, "Senior UX Designer", 3)]
        profile = {"titles": ["Senior UX Designer", "product designer"], "level": None, "one_level_up": False, "city": None,
                   "skills": []}
        with mock.patch.object(dapp, "get_conn", lambda: mock.MagicMock()), \
             mock.patch.object(dapp, "get_session_user", lambda c, t: (7, False)), \
             mock.patch.object(dapp, "is_onboarded", lambda c, u: True), \
             mock.patch.object(dapp, "log_visit", lambda c, u: None), \
             mock.patch.object(dapp, "get_user_role_categories", lambda c, u: set()), \
             mock.patch.object(dapp, "get_user_keywords", lambda c, u: []), \
             mock.patch.object(dapp, "get_user_profile", lambda c, u: profile), \
             mock.patch.object(dapp, "get_user_work_types", lambda c, u: []), \
             mock.patch.object(dapp, "get_open_postings", lambda c, u, since=None, keywords=(): [dict(r) for r in rows]):
            html = client.get("/").get_data(as_text=True)
        self.assertEqual(re.findall(r'class="fg-job-link"[^>]*>([^<]+)</a>', html),
                         ["Associate Product Designer, Payments", "Product Designer", "Senior UX Designer"])


if __name__ == "__main__":
    unittest.main()
