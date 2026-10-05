"""Dead-man's-switch heartbeat (SC-31 follow-up).

The collector can alert on its own gaps/crashes (run_collectors.py), but if the
scheduler stops entirely nothing runs to notice. An external monitor
(healthchecks.io or compatible) closes that gap: we ping it after every
successful run, and *it* alerts when the pings stop. No-op unless
HEALTHCHECK_URL is set. A failed ping never affects the run.
"""
import os

import requests


def _ping(suffix=""):
    url = os.environ.get("HEALTHCHECK_URL", "").rstrip("/")
    if not url:
        return False
    try:
        requests.get(url + suffix, timeout=10).raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"Heartbeat ping failed (non-fatal): {e}")
        return False


def ping_success():
    return _ping()


def ping_failure():
    """Tells the monitor the run failed right now, instead of waiting out its grace period."""
    return _ping("/fail")
