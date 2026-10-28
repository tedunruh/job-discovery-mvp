import re

# A posting is "US" if its location text contains an explicit US signal: a
# state abbreviation after a comma, a full state name, "United States"/"USA"/
# "US"/"U.S."/"D.C.". Bare city names with no state/country qualifier ("Austin",
# "Dublin", "Warsaw") and ambiguous region names ("Americas", "Europe") are
# deliberately left unmatched - tested against all 120 distinct location
# strings actually in the database before wiring this in, rather than guessing.
US_STATE_ABBR = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
    "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
    "VA", "WA", "WV", "WI", "WY",
}
US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
    "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire",
    "new jersey", "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington", "west virginia",
    "wisconsin", "wyoming",
}

_ABBR_RE = re.compile(r",\s*(" + "|".join(sorted(US_STATE_ABBR)) + r")\b")
_STATE_NAME_RE = re.compile(
    r"\b(" + "|".join(sorted(US_STATE_NAMES, key=len, reverse=True)) + r")\b", re.IGNORECASE
)
_REMOTE_RE = re.compile(r"\bremote\b", re.IGNORECASE)


def is_us_location(location):
    if not location:
        return False
    if re.search(r"\bunited states\b", location, re.IGNORECASE):
        return True
    if re.search(r"\bUSA\b", location):
        return True
    if re.search(r"\bU\.S\.?\b", location):
        return True
    if re.search(r"\bUS\b", location):
        return True
    if re.search(r"\bD\.?C\.?\b", location):
        return True
    if _ABBR_RE.search(location):
        return True
    if _STATE_NAME_RE.search(location):
        return True
    return False


def is_remote(location, remote_type):
    if remote_type and remote_type.lower() == "remote":
        return True
    if location and _REMOTE_RE.search(location):
        return True
    return False


def is_remote_us(location, remote_type):
    return is_remote(location, remote_type) and is_us_location(location)


_HYBRID_RE = re.compile(r"\bhybrid\b", re.IGNORECASE)

# Work-type buckets for the dashboard's "Work type" filter. Only Lever reports a
# workplace type ("remote" / "hybrid" / "onsite"); Ashby and Workable only flag
# remote; Greenhouse and Workday report nothing. So the bucket is inferred:
#   remote  - remote AND in the US (same rule as the old "Remote US only" toggle)
#   hybrid  - Lever says hybrid, or the location text says "hybrid"
#   onsite  - everything else that isn't remote
# Remote roles outside the US fall in no bucket: they show only when the filter
# is left at "Any".
WORK_TYPES = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}


def work_type(location, remote_type):
    kind = (remote_type or "").lower()
    if kind == "hybrid":
        return "hybrid"
    if is_remote(location, remote_type):
        return "remote" if is_us_location(location) else None
    if location and _HYBRID_RE.search(location):
        return "hybrid"
    return "onsite"


_WORK_LABELS = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "Onsite"}
_WORK_WORD_RE = re.compile(r"\b(remote|hybrid)\b[\s,\-\u2013\u2014:()/]*", re.IGNORECASE)
_US_NAME_RE = re.compile(r"\bUnited States(?: of America)?\b|\bU\.S\.?(?=\W|$)|\bUSA\b", re.IGNORECASE)


def location_line(location, remote_type):
    """The job row's work type and place in one short line: "Remote, US", "Hybrid, Denver, CO", "Onsite, Austin, TX".
    The work type comes from work_type() (non-US remote is still "Remote"); the place is the location text with the
    work-type words and "United States" tidied. Nothing is made up: no location and no remote or hybrid signal gives
    an empty line."""
    kind = work_type(location, remote_type)
    if kind is None and is_remote(location, remote_type):
        kind = "remote"
    place = _WORK_WORD_RE.sub("", location or "")
    place = _US_NAME_RE.sub("US", place)
    place = re.sub(r"\s+", " ", place).strip(" ,-\u2013\u2014:()/")
    if re.fullmatch(r"\d+ locations?", place, re.IGNORECASE):  # a company's own "2 Locations": say so, not a work type
        place = place.lower()
        return ", ".join(p for p in (_WORK_LABELS.get(kind) if kind in ("remote", "hybrid") else "", place) if p)
    if kind == "onsite" and not place:
        return ""
    label = _WORK_LABELS.get(kind, "")
    return ", ".join(part for part in (label, place) if part)
