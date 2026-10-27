"""Crash alerts that do not repeat (SC-65).

When the database was over its quota every run crashed, and every crash sent its own push alert, a new one every 15
minutes. This keeps the first alert, stays quiet while the same problem continues, reminds once every REMIND_HOURS,
and says once when a run succeeds again. "Crashed in a row" comes from GitHub's own record of this workflow's recent
runs (the job's own token, no new secret), because when the database is down Scout cannot keep that count itself.
If GitHub cannot be asked, the alert is simply sent, as before: a duplicate beats silence.
"""
import os
import re
import time
from datetime import datetime
from typing import NamedTuple, Optional

import requests

REMIND_HOURS = 2  # a crash that keeps going is repeated at most once per this many hours
WORKFLOW_FILE = "collect.yml"
PAGE = 100  # runs asked for: about a day at one run every 15 minutes (the most GitHub gives in one answer)
_FAILED = {"failure", "timed_out"}
_DONE = _FAILED | {"success"}  # cancelled and skipped runs say nothing about whether Scout is healthy


class Streak(NamedTuple):
    crashed: int                 # runs in a row, just before this one, that crashed (0 = the last one was fine)
    latest: Optional[float]      # when the newest of them started (epoch seconds), None when crashed is 0


def scrub(text, limit=300):
    """An alert's error text without anything that points at the database: its address, IP numbers and URLs."""
    text = re.sub(r"postgres(?:ql)?://\S+", "<database url>", str(text))
    text = re.sub(r"\b(?:ep|br)-[\w-]+(?:\.[\w-]+)+", "<database host>", text)
    text = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<ip>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _started(run):
    return datetime.fromisoformat(run["created_at"].replace("Z", "+00:00")).timestamp()


def previous_streak():
    """The runs just before this one that crashed in a row, or None if GitHub cannot be asked (not running in
    Actions, no token, or the request failed)."""
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        return None
    current = os.environ.get("GITHUB_RUN_ID", "")
    try:
        response = requests.get(
            f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs",
            params={"status": "completed", "per_page": PAGE},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        response.raise_for_status()
        crashed, latest = 0, None
        for run in response.json().get("workflow_runs", []):  # newest first
            if str(run.get("id")) == current or run.get("conclusion") not in _DONE:
                continue
            if run["conclusion"] not in _FAILED:
                break
            crashed += 1
            if latest is None:
                latest = _started(run)
        return Streak(crashed, latest)
    except Exception as e:  # noqa: BLE001 - this runs inside the crash handler: nothing here may raise
        print(f"Could not ask GitHub about earlier runs (non-fatal): {e}")
        return None


def _slot(epoch):
    return int(epoch // (REMIND_HOURS * 3600))


def should_alert_crash(streak, now=None):
    """Send this crash's alert? The first of a streak; then the first crash in each later REMIND_HOURS slot of the
    clock (so about one reminder per slot, however long the streak); always if we cannot tell."""
    if streak is None or streak.crashed == 0:
        return True
    return _slot(time.time() if now is None else now) != _slot(streak.latest)


def crash_message(error, streak):
    """The alert text: the plain crash for the first one, a count for a reminder."""
    detail = f"{type(error).__name__}: {scrub(error)}"
    if streak is None or streak.crashed == 0:
        return f"Collector run crashed: {detail}"
    in_a_row = streak.crashed + 1
    count = f"{PAGE}+" if in_a_row > PAGE else str(in_a_row)
    return f"Collector has now crashed {count} runs in a row. Latest: {detail}"


def recovered_message(streak):
    count = f"{PAGE}+" if streak.crashed >= PAGE else str(streak.crashed)
    return f"Collector is back. It crashed {count} run{'' if count == '1' else 's'} in a row before this one succeeded."
