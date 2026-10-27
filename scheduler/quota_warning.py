"""A warning before the database plan runs out (SC-65).

The Neon plan includes a fixed amount of compute and network transfer a month. Going over it made every run crash,
and nothing warned first. Each run asks Neon's usage-history API how much of the month's allowance is used (one
small request, logged so the numbers can be checked), and once a day, if either is at WARN_AT or more, says so on the
health channel. If the figures cannot be read, the same daily message says that instead of staying quiet, so a
changed API cannot switch the warning off unnoticed. It needs NEON_API_KEY (a GitHub secret); without it nothing
happens. Nothing here may stop a run.

Storage is not watched: it is far from its limit (0.28 of 5 GB when this was written) and Neon's usage history
reports it in byte-hours, not a size.
"""
import os
from datetime import datetime, timedelta, timezone

import requests

API = "https://console.neon.tech/api/v2"
WARN_AT = 0.8
ALERT_HOUR_UTC = 14  # the daily warning goes out in the run that starts in the first quarter hour of this hour
# What the $10 plan includes each month (Neon's published figures; update here if the plan changes).
ALLOWANCE = {
    "compute": ("compute", 250.0, "hours"),
    "transfer": ("network transfer", 25.0, "GB"),
}
METRICS = {"compute_unit_seconds": "compute", "public_network_transfer_bytes": "transfer"}
GB = 1024 ** 3


def usage(response, project_id):
    """{'compute': CU-hours, 'transfer': GB} for the current billing period from a usage-history answer, or None
    when the answer has no entry for the project (so we never claim 0% when we simply cannot see)."""
    for entry in (response or {}).get("projects", []):
        if entry.get("project_id") != project_id:
            continue
        periods = entry.get("periods") or []
        if not periods:
            return None
        totals = {"compute": 0.0, "transfer": 0.0}  # the last period is the current one (oldest first)
        for day in periods[-1].get("consumption", []):
            for metric in day.get("metrics", []):
                key = METRICS.get(metric.get("metric_name"))
                if key:
                    totals[key] += metric.get("value") or 0
        return {"compute": totals["compute"] / 3600, "transfer": totals["transfer"] / GB}
    return None


def warnings(used):
    """The warning lines for a usage dict: one per allowance at WARN_AT or more, or a single line saying usage
    cannot be seen (used is None). Empty when all is well."""
    if used is None:
        return ["Neon usage warning is blind: could not read this month's usage. Check the Neon console."]
    lines = []
    for key, (label, allowed, unit) in ALLOWANCE.items():
        share = used[key] / allowed
        if share >= WARN_AT:
            lines.append(f"Neon {label} is at {share:.0%} of this month's allowance ({used[key]:.1f} of {allowed:g} {unit}). "
                         f"Going over stops the collector and the dashboard.")
    return lines


def _headers():
    return {"Authorization": f"Bearer {os.environ['NEON_API_KEY']}", "Accept": "application/json"}


def _project():
    wanted = os.environ.get("NEON_PROJECT_ID")
    if wanted:
        response = requests.get(f"{API}/projects/{wanted}", headers=_headers(), timeout=10)
        response.raise_for_status()
        return response.json()["project"]
    response = requests.get(f"{API}/projects", headers=_headers(), params={"limit": 10}, timeout=10)
    response.raise_for_status()
    projects = response.json().get("projects", [])
    if len(projects) != 1:
        raise RuntimeError(f"{len(projects)} projects visible to the key; set NEON_PROJECT_ID to pick one")
    return projects[0]


def fetch_usage(now):
    """Ask Neon for this month's usage. Raises on any problem; check() decides what that means."""
    project = _project()
    org = project.get("org_id") or os.environ.get("NEON_ORG_ID")
    if not org:
        raise RuntimeError("the project record names no organization; set NEON_ORG_ID")
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    params = {
        "org_id": org, "project_ids": project["id"], "granularity": "daily",
        "from": (midnight - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": (midnight + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metrics": ",".join(METRICS),
    }
    response = requests.get(f"{API}/consumption_history/v2/projects", headers=_headers(), params=params, timeout=15)
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code} {response.text[:200]}")
    return usage(response.json(), project["id"])


def check(now=None):
    """Alert lines for this run (usually none). Logs the numbers every run; only returns warnings in the daily slot."""
    now = now or datetime.now(timezone.utc)
    if not os.environ.get("NEON_API_KEY"):
        return []
    try:
        used = fetch_usage(now)
    except Exception as e:  # noqa: BLE001 - a courtesy check: nothing here may stop a run
        print(f"Neon usage check failed (non-fatal): {e}")
        used = None
    else:
        print("Neon usage this month: " + ("not reported" if used is None else
              ", ".join(f"{ALLOWANCE[k][0]} {v:.2f} {ALLOWANCE[k][2]}" for k, v in used.items())))
    if now.hour != ALERT_HOUR_UTC or now.minute >= 15:
        return []
    return warnings(used)
