from collectors.http import request_with_retries

BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def fetch(ats_identifier: str, light: bool = False) -> list[dict]:
    """light=True skips the full job descriptions (content=true): ~22x smaller (e.g. 148 KB vs
    3.3 MB for a 210-job board) while still returning title, location, URL and posted dates.
    Used for the many discovered boards that are only scanned for design roles."""
    url = BOARD_URL.format(token=ats_identifier)
    resp = request_with_retries("GET", url, params=None if light else {"content": "true"}, timeout=30)
    jobs = resp.json().get("jobs", [])
    return [_normalize(job) for job in jobs]


def _normalize(job: dict) -> dict:
    return {
        "ats_posting_id": str(job["id"]),
        "title": job.get("title", ""),
        "location": (job.get("location") or {}).get("name"),
        "remote_type": None,
        "url": job.get("absolute_url"),
        # first_published is the true original post date; updated_at bumps on any
        # edit/republish and can make a months-old posting look freshly posted.
        "ats_posted_at": job.get("first_published") or job.get("updated_at"),
        "raw_json": job,
    }
