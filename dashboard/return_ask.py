"""Coming back from a job (SC-63): which job, if any, to ask "Did you apply for this one?" about, and which jobs to
mark Viewed. Pure functions over what the event log already holds, so the rule can be tested without a database.

    opens:      {posting id: when this person last opened it}      ('opened' from the dashboard, 'opened_alert' from a push)
    dismissals: {posting id: when they last answered "Not yet"}
"""
from datetime import timedelta

ASK_WINDOW = timedelta(hours=24)  # a job opened longer ago than this is only marked Viewed, never asked about


def choose_ask(opens, dismissals, applied, now):
    """(posting id to ask about or None, how many OTHER jobs were opened in the same window).

    Only the job opened most recently is ever asked about; the others are just Viewed. It is not asked about if it
    was opened outside the window, is already applied to, or was answered "Not yet" after that last open (opening it
    again asks again)."""
    if not opens:
        return None, 0
    latest = max(opens, key=opens.get)
    recent = [pid for pid, when in opens.items() if now - when <= ASK_WINDOW]
    others = len([pid for pid in recent if pid != latest])
    if latest not in recent or latest in applied:
        return None, 0
    answered = dismissals.get(latest)
    if answered is not None and answered >= opens[latest]:
        return None, 0
    return latest, others


def help_text(opened_ago, others):
    """The line under the question: what to do, when they opened it, and what else was marked Viewed."""
    text = f"If you did, mark it Applied. You opened it {opened_ago}."
    if others:
        text += f" You also looked at {others} other role{'' if others == 1 else 's'}; {'it is' if others == 1 else 'they are'} marked Viewed."
    return text
