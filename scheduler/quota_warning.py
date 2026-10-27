"""A warning before the database plan runs out (SC-65).

The Neon plan includes a fixed amount of compute, storage and network transfer a month. Going over it made every run
crash, and nothing warned first. Each run asks Neon how much of the month's allowance is used (one small request,
logged so the numbers can be checked), and once a day, if any of the three is at WARN_AT or more, says so on the
health channel. It needs NEON_API_KEY (a GitHub secret); without it, or if Neon cannot be reached, it does nothing:
this is a courtesy and must never stop a run.
"""
import os
from datetime import datetime, timezone

import requests

API = "https://console.neon.tech/api/v2"
WARN_AT = 0.8
ALERT_HOUR_UTC = 14  # the one hour of the day in which the daily warning may go out (9 AM Central)
# What the $10 plan includes each month (Neon's published figures; update here if the plan changes).
ALLOWANCE = {
    "compute": ("compute", 250.0, "hours"),
    "storage": ("storage", 5.0, "GB"),
    "transfer": ("network transfer", 25.0, "GB"),
}
GB = 1024 ** 3


def usage(project):
    """{'compute': hours, 'storage': GB, 'transfer': GB} from Neon's project record, or None when the record
    carries no usage at all (so we do not claim 0% when we simply cannot see)."""
    seconds = project.get("compute_time_seconds")
    storage = project.get("synthetic_storage_size")
    transfer = project.get("data_transfer_bytes")
    if not any(isinstance(v, (int, float)) and v > 0 for v in (seconds, storage, transfer)):
        return None
    return {
        "compute": (seconds or 0) / 3600,
        "storage": (storage or 0) / GB,
        "transfer": (transfer or 0) / GB,
    }


def _parse(stamp):
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def warnings(project, now=None):
    """The warning lines for a project record: one per allowance at WARN_AT or more, or a single line saying usage
    cannot be seen. Empty when all is well."""
    used = usage(project)
    now = now or datetime.now(timezone.utc)
    if used is None:
        # A month that has only just begun legitimately shows nothing yet; a month well under way should not.
        try:
            started = _parse(project["consumption_period_start"])
        except (KeyError, ValueError, TypeError, AttributeError):
            started = now
        if (now - started).days >= 2:
            return ["Neon usage warning is blind: Neon reports no usage for this month. Check the Neon console."]
        return []
    lines = []
    for key, (label, allowed, unit) in ALLOWANCE.items():
        share = used[key] / allowed
        if share >= WARN_AT:
            lines.append(f"Neon {label} is at {share:.0%} of this month's allowance ({used[key]:.1f} of {allowed:g} {unit}). "
                         f"Going over stops the collector and the dashboard.")
    return lines


def _project():
    key = os.environ.get("NEON_API_KEY")
    if not key:
        return None
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    wanted = os.environ.get("NEON_PROJECT_ID")
    if wanted:
        response = requests.get(f"{API}/projects/{wanted}", headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()["project"]
    response = requests.get(f"{API}/projects", headers=headers, params={"limit": 10}, timeout=10)
    response.raise_for_status()
    projects = response.json().get("projects", [])
    if len(projects) != 1:
        print(f"Neon usage: {len(projects)} projects visible to the key; set NEON_PROJECT_ID to pick one.")
        return None
    return projects[0]


def check(now=None):
    """Alert lines for this run (usually none). Logs the numbers every run; only returns warnings in the daily hour."""
    now = now or datetime.now(timezone.utc)
    try:
        project = _project()
        if project is None:
            return []
        used = usage(project)
        print("Neon usage this month: " + ("not reported" if used is None else
              ", ".join(f"{ALLOWANCE[k][0]} {v:.2f} {ALLOWANCE[k][2]}" for k, v in used.items())))
        lines = warnings(project, now)
    except Exception as e:  # noqa: BLE001 - a courtesy check: nothing here may stop a run
        print(f"Neon usage check failed (non-fatal): {e}")
        return []
    return lines if now.hour == ALERT_HOUR_UTC else []
