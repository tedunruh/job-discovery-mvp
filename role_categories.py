import re

# Coarse, keyword-based role categories a user picks from during onboarding.
# Matched against posting titles - same word-boundary approach as
# collectors/filters.py's is_design_role, which is the broader net these
# categories sit inside: the DB holds every role a company lists, and
# postings.is_design marks the ones that passed that filter. Categories pick
# which *design* roles a user sees; keywords (below) opt a user into
# non-design roles.
ROLE_CATEGORIES = {
    "product_design": {
        "label": "Product Design",
        "keywords": ["product designer", "product design"],
    },
    "ux_research": {
        "label": "UX Research",
        "keywords": ["ux research", "user research", "researcher"],
    },
    "design_management": {
        "label": "Design Management",
        "keywords": [
            "design manager",
            "design director",
            "head of design",
            "vp of design",
            "design lead",
        ],
    },
    "ux_engineering": {
        "label": "UX Engineering",
        "keywords": ["ux engineer", "design engineer", "design technologist"],
    },
    "brand_visual": {
        "label": "Brand & Visual Design",
        "keywords": ["brand designer", "visual designer", "graphic designer"],
    },
}

_COMPILED = {
    key: re.compile(
        r"\b(" + "|".join(re.escape(kw) for kw in cat["keywords"]) + r")\b",
        re.IGNORECASE,
    )
    for key, cat in ROLE_CATEGORIES.items()
}


def matches_categories(title: str, categories) -> bool:
    """True if title matches any of the given category keys. An empty/None
    categories collection means "no preference set" - treated as no filter
    (show everything) rather than matching nothing, so a user who hasn't
    picked anything isn't left staring at an empty dashboard."""
    if not categories:
        return True
    return any(_COMPILED[key].search(title) for key in categories if key in _COMPILED)


MAX_KEYWORDS = 20
MAX_KEYWORD_LENGTH = 40


def clean_keywords(raw):
    """Parse user-typed keywords (comma- or newline-separated) into a clean list:
    lowercased, de-duplicated in order, 2-40 chars each, at most 20."""
    seen, out = set(), []
    for part in re.split(r"[,\n]", raw or ""):
        kw = " ".join(part.lower().split())
        if len(kw) < 2 or len(kw) > MAX_KEYWORD_LENGTH or kw in seen:
            continue
        seen.add(kw)
        out.append(kw)
    return out[:MAX_KEYWORDS]


def matches_keywords(title: str, keywords) -> bool:
    """True if the title contains any keyword as a whole word/phrase. No
    keywords matches nothing - unlike categories, "no preference" does not mean
    "everything" here, or a user with no profile would be shown every role."""
    if not keywords:
        return False
    pattern = r"\b(" + "|".join(re.escape(kw) for kw in keywords) + r")\b"
    return bool(re.search(pattern, title, re.IGNORECASE))


def matches_profile(title: str, is_design: bool, categories, keywords) -> bool:
    """Whether a posting belongs in this user's dashboard and alerts.

    - A design role shows per their categories (no categories = all design roles,
      today's behavior).
    - Any role shows if its title matches one of their keywords, which is how a
      user opts into non-design roles.
    - A non-design role that matches no keyword never shows."""
    if is_design and matches_categories(title, categories):
        return True
    return matches_keywords(title, keywords)


# --- SC-46: profile-based matching ---------------------------------------------------------
# A profile is target titles + an optional level. It replaces role categories and keywords
# (both kept for people who have no profile yet; see matches_any_profile). Titles are matched
# like keywords: whole words/phrases anywhere in the posting title, design role or not.

LEVELS = ("mid", "senior", "staff", "principal", "lead_manager")
LEVEL_LABELS = {"mid": "Mid", "senior": "Senior", "staff": "Staff", "principal": "Principal",
                "lead_manager": "Lead or manager"}

# First match wins, so "Senior Design Manager" is a manager role and "Staff Design Lead" a lead.
_LEVEL_PATTERNS = [
    ("lead_manager", re.compile(r"\b(lead|manager|managing|director|head of|vp|vice president|chief)\b", re.I)),
    ("principal", re.compile(r"\bprincipal\b", re.I)),
    ("staff", re.compile(r"\bstaff\b", re.I)),
    ("senior", re.compile(r"\b(senior|sr)\b", re.I)),
    ("mid", re.compile(r"\bmid[- ]?level\b", re.I)),
    # Below every level a person can pick, so it never matches one.
    ("junior", re.compile(r"\b(junior|jr|intern|interns|associate|apprentice|entry[- ]level)\b", re.I)),
]


def title_level(title: str):
    """The level a posting title states ("Senior Product Designer" -> senior), or None when it
    states none. Most titles ("Product Designer") don't say, and the real level is in the posting."""
    for level, pattern in _LEVEL_PATTERNS:
        if pattern.search(title or ""):
            return level
    return None


def matches_level(title: str, level, one_level_up: bool = False) -> bool:
    """Does the title fit the person's level? No level chosen means every level. A title that
    states no level always passes: Scout can't tell, and hiding it would hide most roles."""
    if not level:
        return True
    stated = title_level(title)
    if stated is None:
        return True
    allowed = {level}
    if one_level_up and level in LEVELS:
        i = LEVELS.index(level)
        if i + 1 < len(LEVELS):
            allowed.add(LEVELS[i + 1])
    return stated in allowed


def matches_profile_titles(title: str, titles, level=None, one_level_up: bool = False) -> bool:
    """Whether a posting belongs in this person's dashboard and alerts under a profile:
    the title matches one of their target titles, and its stated level (if any) fits."""
    return matches_keywords(title, titles) and matches_level(title, level, one_level_up)


def matches_city(posting_work_type, location, city) -> bool:
    """A city only narrows hybrid and on-site roles: the posting's location has to name it. Remote
    roles pass, and so does everything when no city is set."""
    if not city:
        return True
    if posting_work_type not in ("hybrid", "onsite"):
        return True
    return bool(location) and re.search(r"\b" + re.escape(city.strip()) + r"\b", location, re.IGNORECASE) is not None


def alerts_active(profile) -> bool:
    """Alerts go out only after the person has started watching, and not while paused."""
    return bool(profile.get("alerts_started_at")) and not profile.get("alerts_paused_at")


def profile_from_legacy(categories, keywords):
    """Target titles equivalent to a person's role categories and keywords, for converting them
    to a profile. Each category stands for its own keywords. No categories used to mean every
    design role, which is exactly what the design title gate matches, so those words stand in."""
    from collectors.filters import KEYWORDS as DESIGN_WORDS

    titles = []
    chosen = [key for key in ROLE_CATEGORIES if key in (categories or ())]
    for key in chosen:
        titles.extend(ROLE_CATEGORIES[key]["keywords"])
    if not chosen:
        titles.extend(DESIGN_WORDS)
    titles.extend(keywords or [])
    seen, out = set(), []
    for t in titles:
        t = " ".join(t.lower().split())
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


# Work type (see dashboard.filters.work_type): remote = remote AND in the US, hybrid, onsite.
# None means a remote role outside the US, which only shows when the filter is "Any".
WORK_TYPE_KEYS = ("remote", "hybrid", "onsite")
DEFAULT_WORK_TYPES = ["remote"]  # what someone who never chose gets, on the dashboard and in alerts


def matches_work_type(posting_work_type, prefs) -> bool:
    """Does a posting's work type fit this person's preference? prefs=None means they
    never chose (the default applies); an empty list, or all three, means Any."""
    if prefs is None:
        prefs = DEFAULT_WORK_TYPES
    if not prefs or len(set(prefs)) >= len(WORK_TYPE_KEYS):
        return True
    return posting_work_type in prefs
