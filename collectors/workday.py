import re
from datetime import datetime, timedelta, timezone

from collectors.filters import KEYWORDS
from collectors.http import request_with_retries

CXS_URL = "https://{tenant}.{instance}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
BASE_URL = "https://{tenant}.{instance}.myworkdayjobs.com/en-US/{site}"

PAGE_SIZE = 20  # Workday's API rejects limit > 20 with a 400
MAX_PAGES = 10  # safety cap per keyword search (200 postings) - plenty for a keyword query

_RELATIVE_DAYS_RE = re.compile(r"posted\s+(\d+)\+?\s+days?\s+ago", re.IGNORECASE)


def fetch(ats_identifier: str) -> list[dict]:
    # ats_identifier format: "{tenant}.{instance}/{site}", e.g. "workhuman.wd1/WorkhumanCareers"
    # Workday has no simple, guessable URL pattern like Greenhouse/Lever/Ashby - tenant,
    # instance (wd1/wd3/wd5/...), and site all vary per company and even change over time
    # (tenants get migrated between instances). Find these by opening the company's Workday
    # careers page, opening browser devtools' Network tab, and reading them off a request to
    # .../wday/cxs/<tenant>/<site>/jobs.
    host_prefix, site = ats_identifier.split("/", 1)
    tenant, instance = host_prefix.split(".", 1)

    cxs_url = CXS_URL.format(tenant=tenant, instance=instance, site=site)
    base_url = BASE_URL.format(tenant=tenant, instance=instance, site=site)

    # Workday's search API rejects an empty searchText (400), so there's no single
    # "fetch everything" call like the other collectors. Instead, search per design
    # keyword and merge/dedupe - this also avoids pulling a large company's entire
    # job catalog just to find the handful of design roles in it.
    postings_by_id = {}
    for keyword in KEYWORDS:
        offset = 0
        total = 0
        for _ in range(MAX_PAGES):
            resp = request_with_retries(
                "POST",
                cxs_url,
                json={
                    "appliedFacets": {},
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "searchText": keyword,
                },
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            data = resp.json()
            jobs = data.get("jobPostings", [])
            if not jobs:
                break
            if offset == 0:
                # Workday reports "total" on the FIRST page only; later pages say 0. Reading it
                # per page ended every search after two pages (40 results) whatever the real count.
                total = data.get("total", 0)
            for job in jobs:
                posting = _normalize(job, base_url)
                postings_by_id[posting["ats_posting_id"]] = posting
            offset += PAGE_SIZE
            if offset >= total:
                break
    return list(postings_by_id.values())


def _normalize(job: dict, base_url: str) -> dict:
    external_path = job.get("externalPath", "")
    return {
        "ats_posting_id": external_path,
        "title": job.get("title", ""),
        "location": job.get("locationsText"),
        "remote_type": None,
        "url": base_url + external_path,
        "ats_posted_at": _parse_posted_on(job.get("postedOn")),
        "raw_json": job,
    }


def _parse_posted_on(posted_on):
    """Workday's public API only exposes a relative string ("Posted 3 Days
    Ago"), never an exact timestamp - so ats_posted_at for Workday postings
    is inherently day-precision at best, unlike the other three collectors.
    Returns an approximate UTC datetime, or None if unparseable.
    """
    if not posted_on:
        return None
    text = posted_on.strip().lower()
    now = datetime.now(timezone.utc)
    if text in ("posted today", "today"):
        return now
    if "yesterday" in text:
        return now - timedelta(days=1)
    match = _RELATIVE_DAYS_RE.search(posted_on)
    if match:
        return now - timedelta(days=int(match.group(1)))
    return None


# --- scanning thousands of tenants (SC-50) -------------------------------------------------
# fetch() above runs six keyword searches of up to ten pages each - fine for a handful of
# companies, far too heavy for thousands. Workday's search is ordered by relevance, not date, so
# "look at the first page" would miss new roles at big employers. Instead: ask one cheap
# question per tenant per run (how many results match "design"?) and only page through the whole
# result set when that number changed (or periodically, in case one role replaced another).

LIGHT_QUERIES = ["design", "ux", "user research"]  # "designer" is the same search as "design"; the other two are cheap and catch UX/research titles
LIGHT_MAX_PAGES = 25  # 500 results per query: more than any one tenant has


def _post(cxs_url, search_text, offset, limit=PAGE_SIZE):
    return request_with_retries(
        "POST", cxs_url,
        json={"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": search_text},
        headers={"Content-Type": "application/json"}, timeout=30,
    ).json()


def _urls(ats_identifier):
    host_prefix, site = ats_identifier.split("/", 1)
    tenant, instance = host_prefix.split(".", 1)
    return (CXS_URL.format(tenant=tenant, instance=instance, site=site),
            BASE_URL.format(tenant=tenant, instance=instance, site=site))


def probe_marker(ats_identifier: str, extra_queries=()) -> str:
    """One tiny request per search: the number of results matching "design", then one for each extra
    phrase (SC-51 slice 2c). Workday needs a non-empty search term, and limit=1 keeps the response tiny.
    Changes whenever a matching role appears or goes, or when the set of extra phrases changes:
    "design:451" or "design:451|product manager:184"."""
    cxs_url, _ = _urls(ats_identifier)
    marker = f"design:{_post(cxs_url, LIGHT_QUERIES[0], 0, limit=1).get('total', 0)}"
    for query in extra_queries:
        marker += f"|{query}:{_post(cxs_url, query, 0, limit=1).get('total', 0)}"
    return marker


# SC-51 slice 2c: roles a person wants whose titles the design searches would not return ("product manager").
# Workday's search also matches words inside descriptions, so a phrase's result count is mostly noise, but
# the roles with the phrase in their title come first (measured: on tenants with 800 to 1,100 results the
# title matches were all in the first five pages, then none). So page until two pages in a row have no result
# whose title holds every word of the phrase, and never past EXTRA_MAX_PAGES.
EXTRA_MAX_PAGES = 10
EXTRA_QUIET_PAGES = 2


def _fetch_extra(cxs_url, base_url, query, postings):
    words = query.lower().split()
    offset = total = quiet = 0
    for _ in range(EXTRA_MAX_PAGES):
        data = _post(cxs_url, query, offset)
        jobs = data.get("jobPostings", [])
        if not jobs:
            break
        if offset == 0:
            total = data.get("total", 0)
        hit = False
        for job in jobs:
            p = _normalize(job, base_url)
            postings[p["ats_posting_id"]] = p
            hit = hit or all(w in p["title"].lower() for w in words)
        quiet = 0 if hit else quiet + 1
        offset += PAGE_SIZE
        if offset >= total or quiet >= EXTRA_QUIET_PAGES:
            break


def fetch_light(ats_identifier: str, extra_queries=()) -> list[dict]:
    """Every result of the light queries, de-duplicated - a full paged scan of one tenant - plus the
    leading results of each extra phrase."""
    cxs_url, base_url = _urls(ats_identifier)
    postings = {}
    for query in LIGHT_QUERIES:
        offset = 0
        total = 0
        for _ in range(LIGHT_MAX_PAGES):
            data = _post(cxs_url, query, offset)
            jobs = data.get("jobPostings", [])
            if not jobs:
                break
            if offset == 0:
                total = data.get("total", 0)  # first page only - see fetch()
            for job in jobs:
                p = _normalize(job, base_url)
                postings[p["ats_posting_id"]] = p
            offset += PAGE_SIZE
            if offset >= total:
                break
    for query in extra_queries:
        _fetch_extra(cxs_url, base_url, query, postings)
    return list(postings.values())
