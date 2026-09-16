import requests

BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def fetch(ats_identifier: str) -> list[dict]:
    url = BOARD_URL.format(token=ats_identifier)
    resp = requests.get(url, params={"content": "true"}, timeout=30)
    resp.raise_for_status()
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
