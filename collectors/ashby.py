from collectors.http import request_with_retries

BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{org}"


def fetch(ats_identifier: str) -> list[dict]:
    # Note: not every org exposes this public board endpoint — some disable it.
    url = BOARD_URL.format(org=ats_identifier)
    resp = request_with_retries("GET", url, timeout=30)
    jobs = resp.json().get("jobs", [])
    return [_normalize(job) for job in jobs]


def _normalize(job: dict) -> dict:
    return {
        "ats_posting_id": job.get("id"),
        "title": job.get("title", ""),
        "location": job.get("location"),
        "remote_type": "remote" if job.get("isRemote") else None,
        "url": job.get("jobUrl") or job.get("applyUrl"),
        "ats_posted_at": job.get("publishedAt"),
        "raw_json": job,
    }
