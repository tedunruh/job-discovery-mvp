from datetime import datetime, timezone

from collectors.http import request_with_retries

POSTINGS_URL = "https://api.lever.co/v0/postings/{company}"


def fetch(ats_identifier: str) -> list[dict]:
    url = POSTINGS_URL.format(company=ats_identifier)
    resp = request_with_retries("GET", url, params={"mode": "json"}, timeout=30)
    postings = resp.json()
    return [_normalize(p) for p in postings]


def _normalize(p: dict) -> dict:
    categories = p.get("categories", {})
    created_at_ms = p.get("createdAt")
    posted_at = None
    if created_at_ms:
        posted_at = datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc)

    return {
        "ats_posting_id": p.get("id"),
        "title": p.get("text", ""),
        "location": categories.get("location"),
        "remote_type": categories.get("commitment"),
        "url": p.get("hostedUrl"),
        "ats_posted_at": posted_at,
        "raw_json": p,
    }
