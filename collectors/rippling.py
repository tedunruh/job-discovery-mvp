from collectors.http import request_with_retries

BOARD_URL = "https://ats.rippling.com/api/v2/board/{slug}/jobs"
PAGE_SIZE = 100
MAX_PAGES = 30  # 3,000 jobs: more than any single board has


def fetch(ats_identifier: str) -> list[dict]:
    """All open jobs on one company's Rippling board (ats_identifier = the board slug in
    ats.rippling.com/<slug>/jobs). Pages through the public v2 API; unlike the other platforms
    its locations carry a real workplace type (REMOTE / HYBRID / ON_SITE) and a country."""
    jobs, page = [], 0
    while page < MAX_PAGES:
        resp = request_with_retries(
            "GET", BOARD_URL.format(slug=ats_identifier),
            params={"page": page, "pageSize": PAGE_SIZE}, timeout=30,
        )
        data = resp.json()
        items = data.get("items") or []
        jobs += items
        page += 1
        if not items or page >= (data.get("totalPages") or 1):
            break
    return [_normalize(j) for j in jobs]


def _normalize(job: dict) -> dict:
    locations = job.get("locations") or []
    return {
        "ats_posting_id": job.get("id"),
        "title": (job.get("name") or "").strip(),
        "location": _location_text(locations),
        "remote_type": _remote_type(locations),
        "url": job.get("url"),
        # The API gives no posting date. A board's first scan treats these as old; roles that
        # appear after that are stamped with the time we first saw them.
        "ats_posted_at": None,
        "raw_json": job,
    }


def _location_text(locations):
    """"San Francisco, CA, United States"-style text, one entry per location, so the dashboard's
    US check (state abbreviation / 'United States') works the same as for other platforms."""
    parts = []
    for loc in locations:
        name = (loc.get("name") or "").strip()
        country = (loc.get("country") or "").strip()
        if name and country and country.lower() not in name.lower() and (loc.get("countryCode") or "").upper() not in name.upper().split(", "):
            name = f"{name}, {country}"
        if name:
            parts.append(name)
    return "; ".join(parts) or None


def _remote_type(locations):
    """remote if any location is remote, else hybrid if any is hybrid, else None (on-site)."""
    kinds = {(loc.get("workplaceType") or "").upper() for loc in locations}
    if "REMOTE" in kinds:
        return "remote"
    if "HYBRID" in kinds:
        return "hybrid"
    return None
