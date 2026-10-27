"""Coming back from a job (SC-63): which job, if any, to ask "Did you apply?" about. A pure function over what the
event log already holds, so the rule can be tested without a database.

    opens:      {posting id: when this person last opened it}      ('opened' from the dashboard, 'opened_alert' from a push)
    dismissals: {posting id: when they last answered "Not yet"}
"""
from datetime import timedelta

ASK_WINDOW = timedelta(hours=24)  # a job opened longer ago than this is only marked Viewed, never asked about


def choose_ask(opens, dismissals, applied, now):
    """The id of the job to ask about, or None.

    Only the job opened most recently is ever asked about; the others are just Viewed. It is not asked about if it
    was opened outside the window, is already applied to, or was answered "Not yet" after that last open (opening it
    again asks again)."""
    if not opens:
        return None
    latest = max(opens, key=opens.get)
    if now - opens[latest] > ASK_WINDOW or latest in applied:
        return None
    answered = dismissals.get(latest)
    if answered is not None and answered >= opens[latest]:
        return None
    return latest
