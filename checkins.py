"""SC-73 slice 3: check-ins on applications that are still waiting (the design is SC-64's). Pure rules, shared by the
dashboard (the question a row asks) and the collector (the push), so the two can never disagree.

An application is asked about from day 5 after the person applied:
    day 5    "Did you hear back?"      Yes: "What did they say?" (and the check-ins end). Not yet: ask again on day 12.
    day 12   "Did you hear back?"      Not yet: ask nothing more until day 21.
    day 21   "Should I close it?"      Yes, close it: closed by the person. Not yet: no more check-ins for the job.
A push goes out at each of those three days (so at most 3 per job): one a day per person at most, jobs due together
share it, only after 9 AM Central, never while alerts are paused or off. A role the employer has closed, or one that has
moved on from "waiting", is not asked about at all. The question itself waits on the row whatever the alerts say."""
from datetime import timedelta

FIRST_DAY, SECOND_DAY, CLOSE_DAY = 5, 12, 21
MAX_PUSHES = 3
PUSH_FROM_HOUR = 9   # Central


def _waiting(a):
    return (a.get("stage") or "waiting") == "waiting" and a.get("posting_status") != "closed" and a.get("applied_at") is not None


def _days(a, now):
    return (now - a["applied_at"]).days


def question(a, now):
    """What this application's row asks right now: None, "heard" (Did you hear back?), "say" (What did they say?) or
    "close" (Should I close it?). `a` has stage, posting_status, applied_at, heard_at, heard_not_yet_at and close_not_yet_at."""
    if not _waiting(a):
        return None
    if a.get("heard_at"):
        return "say"
    days = _days(a, now)
    if days < FIRST_DAY:
        return None
    if days >= CLOSE_DAY:
        return None if a.get("close_not_yet_at") else "close"
    snoozed = a.get("heard_not_yet_at")
    if days >= SECOND_DAY:
        return None if snoozed and snoozed >= a["applied_at"] + timedelta(days=SECOND_DAY) else "heard"
    return None if snoozed else "heard"


def push_level(a, now):
    """Which of the three pushes (1, 2 or 3) this application is due for, or 0 for none. It is due when its question is
    asking ("say" never pushes: they have already answered) and fewer pushes than that level have gone out. A job that
    was quiet for a while (alerts paused) gets the push for where it is now, not a catch-up of the earlier ones."""
    q = question(a, now)
    if q not in ("heard", "close"):
        return 0
    days = _days(a, now)
    level = 3 if days >= CLOSE_DAY else 2 if days >= SECOND_DAY else 1
    return level if (a.get("checkin_pushes") or 0) < level else 0
