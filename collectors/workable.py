from collectors.http import request_with_retries

WIDGET_URL = "https://apply.workable.com/api/v1/widget/accounts/{account}"


def fetch(ats_identifier: str) -> list[dict]:
    url = WIDGET_URL.format(account=ats_identifier)
    resp = request_with_retries("GET", url, timeout=30)
    jobs = resp.json().get("jobs", [])

    # Multi-location postings appear once per location with the same shortcode;
    # keep only the first occurrence (parity with how the other collectors handle
    # a single location string).
    postings_by_id = {}
    for job in jobs:
        posting = _normalize(job)
        postings_by_id.setdefault(posting["ats_posting_id"], posting)
    return list(postings_by_id.values())


def _normalize(job: dict) -> dict:
    city = job.get("city")
    country = job.get("country")
    location = ", ".join(part for part in (city, country) if part)
    return {
        "ats_posting_id": job.get("shortcode"),
        "title": job.get("title", ""),
        "location": location or None,
        "remote_type": "remote" if job.get("telecommuting") else None,
        "url": job.get("url"),
        # published_on is when it went public (day precision, no time - still
        # more reliable than Workday's relative-text-only dates since it's a
        # real date from the source, not something we have to parse/approximate).
        "ats_posted_at": job.get("published_on"),
        "raw_json": job,
    }
