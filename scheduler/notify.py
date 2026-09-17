import os

import requests

NTFY_URL = "https://ntfy.sh"


def notify_new_postings(new_postings):
    """Push a phone notification via ntfy.sh for genuinely new postings.

    new_postings: list of {"company": str, "title": str, "url": str}
    No-ops silently if NTFY_TOPIC isn't set, so this stays optional.
    """
    topic = os.environ.get("NTFY_TOPIC")
    if not topic or not new_postings:
        return

    if len(new_postings) == 1:
        p = new_postings[0]
        title = f"New: {p['company']}"
        message = p["title"]
        click = p["url"]
    else:
        title = f"{len(new_postings)} new design roles"
        message = "\n".join(f"{p['company']}: {p['title']}" for p in new_postings[:10])
        click = None

    payload = {"topic": topic, "title": title, "message": message, "priority": 4}
    if click:
        payload["click"] = click

    try:
        requests.post(NTFY_URL, json=payload, timeout=10)
    except requests.RequestException as e:
        print(f"Notification failed (non-fatal): {e}")
