"""Coming back from a job (SC-63): which jobs to ask "Did you apply?" about. A pure function over what the event log
already holds, so the rule can be tested without a database.

    opens:      {posting id: when this person last opened it}      ('opened' from the dashboard, 'opened_alert' from a push)
    dismissals: {posting id: when they last answered "Not yet"}
"""


def choose_asks(opens, dismissals, applied):
    """The ids of the jobs to ask about, most recently opened first.

    The question sticks to a job until it is answered, however long ago it was opened and however many other jobs
    were opened since. A job is answered when it is applied to (or hidden: pass those in `applied`) or when they said
    "Not yet" after the last time they opened it; opening it again asks again."""
    asks = []
    for pid in sorted(opens, key=opens.get, reverse=True):
        if pid in applied:
            continue
        answered = dismissals.get(pid)
        if answered is not None and answered >= opens[pid]:
            continue
        asks.append(pid)
    return asks
