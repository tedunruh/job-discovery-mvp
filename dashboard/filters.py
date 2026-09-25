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
