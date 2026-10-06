"""Find which ATS a company hires on, and its board identifier (SC-45b).

    detect("Figma")                                  -> probe likely slugs on each ATS
    detect("https://jobs.lever.co/palantir/abc-123") -> read the identifier straight off the URL

Returns candidates ranked best-first. Each is a dict:
    ats_type, ats_identifier, name (board's own name when the ATS gives one),
    job_count, count_is_lower_bound, samples (a few job titles),
    confidence ("high" | "medium" | "low"), reason.

Safety: this runs inside a web request on user input, so it only ever contacts a
fixed set of ATS hosts, and user text only ever becomes a sanitized path segment
([a-z0-9-]) on those hosts. It never fetches a user-supplied hostname, so it
can't be pointed at internal addresses.

Identity matters because a slug can belong to a different company than the one
meant ("linear", "scale"). Greenhouse and Workable return the board's name, so
that's compared. Lever and Ashby don't, so for those we look for the company's
name in the posting text. "high" means one of those checks passed (or the
identifier came off a URL the user pasted); everything else needs a human look.
"""
import re
from concurrent.futures import ThreadPoolExecutor

import requests

# Short on purpose: this runs inside a web request on a single gunicorn worker, and a
# probe that hasn't answered in 8s is a non-match anyway. Worst case is
# ceil(probes / pool) * TIMEOUT, kept under gunicorn's 30s request limit.
TIMEOUT = 8
MAX_SLUGS = 6
SAMPLE_TITLES = 3
# Workable accounts that are agencies/marketplaces list many employers' jobs - not a
# single company (see db/schema.sql). Flag very large boards.
AGENCY_JOB_THRESHOLD = 1000

GREENHOUSE_BOARD = "https://boards-api.greenhouse.io/v1/boards/{slug}"
GREENHOUSE_JOBS = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LEVER_POSTINGS = "https://api.lever.co/v0/postings/{slug}"
ASHBY_BOARD = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
WORKABLE_WIDGET = "https://apply.workable.com/api/v1/widget/accounts/{slug}"
WORKDAY_CXS = "https://{tenant}.{instance}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
IDENTIFIER_RES = {
    "greenhouse": SLUG_RE,
    "lever": SLUG_RE,
    "ashby": re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$"),  # Ashby slugs can be mixed case
    "workable": SLUG_RE,
    "workday": re.compile(r"^[a-z0-9-]+\.wd\d+/[A-Za-z0-9_-]+$"),
}

# Hosts a user may paste a link from -> (ats_type, regex capturing the identifier)
URL_PATTERNS = [
    ("greenhouse", re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9._%-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)", re.I)),
]
WORKDAY_URL = re.compile(
    r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Za-z]{2}/)?([A-Za-z0-9_-]+)", re.I
)

_COMPANY_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company",
    "labs", "lab", "technologies", "technology", "tech", "systems", "software", "group", "holdings",
}
_CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}


# --- input handling ------------------------------------------------------------


def normalize(text):
    """Lowercase alphanumerics only - the form used to compare names."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _core_words(name):
    words = [w for w in re.split(r"[^a-z0-9]+", name.lower()) if w]
    kept = [w for w in words if w not in _COMPANY_SUFFIXES]
    return kept or words


def slug_candidates(name):
    """Plausible board slugs for a company name, most likely first."""
    words = _core_words(name)
    if not words:
        return []
    joined, hyphen = "".join(words), "-".join(words)
    all_words = [w for w in re.split(r"[^a-z0-9]+", name.lower()) if w]
    out = [joined, hyphen, "".join(all_words), "-".join(all_words), words[0], joined + "hq", joined + "inc"]
    seen, result = set(), []
    for slug in out:
        if slug and slug not in seen and SLUG_RE.match(slug):
            seen.add(slug)
            result.append(slug)
    return result[:MAX_SLUGS]


def parse_ats_url(text):
    """If text is (or contains) a link to a known ATS board, return
    (ats_type, identifier); else None. Pure string parsing - nothing is fetched."""
    m = WORKDAY_URL.search(text)
    if m:
        tenant, instance, site = m.group(1).lower(), m.group(2).lower(), m.group(3)
        return "workday", f"{tenant}.{instance}/{site}"
    for ats_type, pattern in URL_PATTERNS:
        m = pattern.search(text)
        if m:
            ident = m.group(1)
            if ats_type != "ashby":
                ident = ident.lower()
            return ats_type, ident
    return None


def valid_identifier(ats_type, identifier):
    pattern = IDENTIFIER_RES.get(ats_type)
    return bool(pattern and identifier and pattern.match(identifier))


# --- probing -------------------------------------------------------------------


def _get(url, **kwargs):
    """GET one URL, no retries. Returns the response only on 200; None for any
    miss or network error (a probe that fails is just a non-match)."""
    try:
        resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "Scout/1.0"}, **kwargs)
    except requests.RequestException:
        return None
    return resp if resp.status_code == 200 else None


def _json(resp):
    try:
        return resp.json()
    except ValueError:
        return None


def _candidate(ats_type, identifier, name, titles, count, lower_bound=False, text=""):
    return {
        "ats_type": ats_type,
        "ats_identifier": identifier,
        "name": name,
        "job_count": count,
        "count_is_lower_bound": lower_bound,
        "samples": [t for t in titles if t][:SAMPLE_TITLES],
        "text": text,  # scratch: posting text used for the identity check; removed before returning
        "confidence": "low",
        "reason": "",
    }


def probe(ats_type, slug):
    """Check one (ATS, slug). Returns a candidate dict if the board exists and has
    postings, else None."""
    if ats_type == "greenhouse":
        board = _get(GREENHOUSE_BOARD.format(slug=slug))
        if not board:
            return None
        name = (_json(board) or {}).get("name")
        jobs_resp = _get(GREENHOUSE_JOBS.format(slug=slug))
        jobs = (_json(jobs_resp) or {}).get("jobs", []) if jobs_resp else []
        if not jobs:
            return None
        return _candidate("greenhouse", slug, name, [j.get("title") for j in jobs], len(jobs))

    if ats_type == "lever":
        resp = _get(LEVER_POSTINGS.format(slug=slug), params={"mode": "json", "limit": 25})
        jobs = _json(resp) if resp else None
        if not isinstance(jobs, list) or not jobs:
            return None
        text = " ".join((j.get("text") or "") + " " + (j.get("descriptionPlain") or "")[:3000] for j in jobs)
        # limit=25 keeps the probe small; the true count is at least what came back
        return _candidate("lever", slug, None, [j.get("text") for j in jobs], len(jobs), len(jobs) >= 25, text)

    if ats_type == "ashby":
        resp = _get(ASHBY_BOARD.format(slug=slug))
        jobs = (_json(resp) or {}).get("jobs", []) if resp else []
        if not jobs:
            return None
        text = " ".join((j.get("title") or "") + " " + (j.get("descriptionPlain") or "")[:3000] for j in jobs[:60])
        return _candidate("ashby", slug, None, [j.get("title") for j in jobs], len(jobs), text=text)

    if ats_type == "workable":
        resp = _get(WORKABLE_WIDGET.format(slug=slug))
        data = _json(resp) if resp else None
        jobs = (data or {}).get("jobs", [])
        if not jobs:
            return None
        return _candidate("workable", slug, (data or {}).get("name"), [j.get("title") for j in jobs], len(jobs))

    return None


def probe_workday(identifier):
    """Verify a Workday tenant/site by asking its search endpoint for one result.
    (Workday needs a non-empty search term; the collector pulls design roles only.)"""
    host, site = identifier.split("/", 1)
    tenant, instance = host.split(".", 1)
    try:
        resp = requests.post(
            WORKDAY_CXS.format(tenant=tenant, instance=instance, site=site),
            json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": "design"},
            headers={"Content-Type": "application/json", "User-Agent": "Scout/1.0"},
            timeout=TIMEOUT,
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    data = _json(resp) or {}
    titles = [j.get("title") for j in data.get("jobPostings", [])]
    return _candidate("workday", identifier, None, titles, data.get("total"), lower_bound=False)


def verify(ats_type, identifier):
    """Re-check a client-supplied (ats_type, identifier) before trusting it.
    Returns the candidate dict, or None if invalid or no such board."""
    if not valid_identifier(ats_type, identifier):
        return None
    cand = probe_workday(identifier) if ats_type == "workday" else probe(ats_type, identifier)
    if cand:
        cand.pop("text", None)
    return cand


# --- ranking -------------------------------------------------------------------


def _name_matches(board_name, wanted):
    """Does a board's own name plausibly refer to the company we were asked about?"""
    a, b = normalize(board_name), normalize(" ".join(_core_words(wanted)))
    if not a or not b:
        return False
    return a == b or a.startswith(b) or b.startswith(a)


def _score(cand, wanted, slug_rank):
    """Set confidence + reason from the strongest identity evidence available."""
    if cand["name"] and _name_matches(cand["name"], wanted):
        cand["confidence"], cand["reason"] = "high", f"board is named \"{cand['name']}\""
    elif cand["text"] and normalize(wanted) and len(normalize(wanted)) >= 3 and (
        " ".join(_core_words(wanted)) in cand["text"].lower()
    ):
        cand["confidence"], cand["reason"] = "high", "company name appears in its job postings"
    elif cand["name"]:
        cand["confidence"], cand["reason"] = "low", f"board is named \"{cand['name']}\" - may be a different company"
    elif slug_rank == 0:
        cand["confidence"], cand["reason"] = "medium", "matching name, but this ATS doesn't confirm who owns the board"
    else:
        cand["confidence"], cand["reason"] = "low", "slug is only a guess at the name"
    if cand["ats_type"] == "workable" and (cand["job_count"] or 0) >= AGENCY_JOB_THRESHOLD:
        cand["confidence"], cand["reason"] = "low", "very large board - may be a recruiting agency, not one company"
    return cand


def _from_url(ats_type, identifier):
    cand = verify(ats_type, identifier)
    if cand is None:
        return []
    cand["confidence"], cand["reason"] = "high", "taken from the link you pasted"
    return [cand]


def detect(query, pool_size=8):
    """Find candidate boards for a company name, domain, or pasted ATS link."""
    query = (query or "").strip()[:200]
    if not query:
        return []

    parsed = parse_ats_url(query)
    if parsed:
        return _from_url(*parsed)

    # A bare domain ("figma.com") -> use its main label as the name
    name = query
    if " " not in query and "." in query:
        labels = re.sub(r"^https?://", "", query.lower()).split("/")[0].split(".")
        labels = [l for l in labels if l not in ("www", "careers", "jobs")]
        name = labels[-2] if len(labels) >= 2 else labels[0]

    slugs = slug_candidates(name)
    jobs = [(ats, slug, rank) for rank, slug in enumerate(slugs) for ats in ("greenhouse", "lever", "ashby", "workable")]
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        found = list(pool.map(lambda j: (probe(j[0], j[1]), j[2]), jobs))

    results = []
    for cand, rank in found:
        if cand:
            results.append(_score(cand, name, rank))
    for cand in results:
        cand.pop("text", None)
    results.sort(key=lambda c: (_CONFIDENCE_RANK[c["confidence"]], -(c["job_count"] or 0)))
    return results
