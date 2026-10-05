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


def _post(payload, label):
    """POST to ntfy. Returns True on success; False (and prints) on any network
    error or non-2xx response - callers use this to raise a health alert rather
    than failing silently."""
    try:
        resp = requests.post(NTFY_URL, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"{label} send failed: {e}")
        return False


def notify_new_postings(new_postings, ntfy_topic, discovered_at=None):
    """Returns True if sent (or nothing to send), False if the send failed.

    Push a phone notification via ntfy.sh for genuinely new postings, to
    one user's own topic - each user has their own ntfy_topic (users table),
    so this is called once per user with their matching subset of postings.

    new_postings: list of {"company": str, "title": str, "url": str}

    Includes an explicit "Discovered <time>" line in the message itself -
    this is when *we* found it, which can differ by hours from the ATS's own
    claimed posting date the dashboard shows in its "Posted" column. Labeling
    it directly in the notification avoids the two being confused later.
    """
    topic = ntfy_topic
    if not topic or not new_postings:
        return True

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

    return _post(payload, "Notification")


def notify_health_alerts(alerts):
    """Push pipeline health/ops alerts via a separate ntfy topic from job
    matches - mixing them in would undermine "notification = new job" as a
    mental model. No-ops silently if NTFY_HEALTH_TOPIC isn't set.

    alerts: list of plain-text alert strings.
    """
    topic = os.environ.get("NTFY_HEALTH_TOPIC")
    if not topic or not alerts:
        return True

    title = f"{len(alerts)} pipeline health alerts" if len(alerts) > 1 else "Pipeline health alert"
    message = "\n\n".join(alerts[:10])
    payload = {"topic": topic, "title": title, "message": message, "priority": 4, "tags": ["warning"]}

    return _post(payload, "Health alert")
