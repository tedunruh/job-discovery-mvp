import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

NTFY_URL = "https://ntfy.sh"

# For the "Discovered" label in notifications only - purely display, doesn't
# affect anything stored (all timestamps in the DB stay UTC). cron-job.org's
# schedule is configured in this zone, so it's a reasonable stand-in for
# "the user's local time" for a single-user personal tool.
LOCAL_TZ = ZoneInfo("America/Chicago")


def _format_discovered_at(dt):
    return dt.astimezone(LOCAL_TZ).strftime("%b %-d, %-I:%M %p %Z")


def notify_new_postings(new_postings, discovered_at=None):
    """Push a phone notification via ntfy.sh for genuinely new postings.

    new_postings: list of {"company": str, "title": str, "url": str}
    No-ops silently if NTFY_TOPIC isn't set, so this stays optional.

    Includes an explicit "Discovered <time>" line in the message itself -
    this is when *we* found it, which can differ by hours from the ATS's own
    claimed posting date the dashboard shows in its "Posted" column. Labeling
    it directly in the notification avoids the two being confused later.
    """
    topic = os.environ.get("NTFY_TOPIC")
    if not topic or not new_postings:
        return

    discovered_label = _format_discovered_at(discovered_at or datetime.now(timezone.utc))

    if len(new_postings) == 1:
        p = new_postings[0]
        title = f"New: {p['company']}"
        message = f"{p['title']}\nDiscovered {discovered_label}"
        click = p["url"]
    else:
        title = f"{len(new_postings)} new design roles"
        lines = [f"{p['company']}: {p['title']}" for p in new_postings[:10]]
        message = "\n".join(lines) + f"\n\nDiscovered {discovered_label}"
        click = None

    payload = {"topic": topic, "title": title, "message": message, "priority": 4}
    if click:
        payload["click"] = click

    try:
        requests.post(NTFY_URL, json=payload, timeout=10)
    except requests.RequestException as e:
        print(f"Notification failed (non-fatal): {e}")


def notify_health_alerts(alerts):
    """Push pipeline health/ops alerts via a separate ntfy topic from job
    matches - mixing them in would undermine "notification = new job" as a
    mental model. No-ops silently if NTFY_HEALTH_TOPIC isn't set.

    alerts: list of plain-text alert strings.
    """
    topic = os.environ.get("NTFY_HEALTH_TOPIC")
    if not topic or not alerts:
        return

    title = f"{len(alerts)} pipeline health alerts" if len(alerts) > 1 else "Pipeline health alert"
    message = "\n\n".join(alerts[:10])
    payload = {"topic": topic, "title": title, "message": message, "priority": 4, "tags": ["warning"]}

    try:
        requests.post(NTFY_URL, json=payload, timeout=10)
    except requests.RequestException as e:
        print(f"Health alert notification failed (non-fatal): {e}")
