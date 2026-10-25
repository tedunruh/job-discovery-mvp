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

    - A design role shows per their categories. With no categories picked, a design role
      shows only if it is a real one (collectors.filters.is_strict_design_role, SC-53): the loose
      gate that decides what is stored let through engineering and hardware roles with "design"
      in the title.
    - Any role shows if its title matches one of their keywords, which is how a
      user opts into non-design roles.
    - A non-design role that matches no keyword never shows."""
    if is_design:
        from collectors.filters import is_strict_design_role

        if categories and matches_categories(title, categories):
            return True
        if not categories and is_strict_design_role(title):
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


# What "every design role" stands for as target titles, for someone who never picked a role category (SC-53).
# They are real roles a designer would want, so the pre-filled Role preferences say what the person is matched on.
DEFAULT_DESIGN_TITLES = [
    "product designer", "product design", "ux designer", "ui designer", "ux/ui designer", "ui/ux designer",
    "visual designer", "brand designer", "graphic designer", "interaction designer", "design lead",
    "design manager", "design director", "head of design", "ux researcher", "user research",
]


def profile_from_legacy(categories, keywords):
    """Target titles equivalent to a person's role categories and keywords, for converting them
    to a profile. Each category stands for its own keywords. No categories used to mean every
    design role, so DEFAULT_DESIGN_TITLES stand in (the old loose design words matched engineering
    and hardware roles too)."""
    DESIGN_WORDS = DEFAULT_DESIGN_TITLES

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


# --- SC-51 slice 3: did a save let in roles the old settings kept out? ----------------------------
# Discovered boards only store the roles somebody's gate lets through (see scheduler/describe.py), so when a
# gate widens, roles that are already open but were never stored have to be picked up, quietly. These say
# when. Narrowing, or changing only skills, needs nothing: every role such a person wants is already stored.

def _allowed_levels(level, one_level_up):
    """The levels a title may state and still pass, or None for every level."""
    if not level:
        return None
    allowed = {level}
    if one_level_up and level in LEVELS and LEVELS.index(level) + 1 < len(LEVELS):
        allowed.add(LEVELS[LEVELS.index(level) + 1])
    return allowed


def level_widened(old_level, old_up, new_level, new_up) -> bool:
    old, new = _allowed_levels(old_level, old_up), _allowed_levels(new_level, new_up)
    if old is None:
        return False
    return new is None or not new <= old


def _work_set(prefs):
    if prefs is None:
        prefs = DEFAULT_WORK_TYPES
    if not prefs or len(set(prefs)) >= len(WORK_TYPE_KEYS):
        return set(WORK_TYPE_KEYS)
    return set(prefs)


def work_types_widened(old, new) -> bool:
    """Does the new Work type preference admit a work type the old one did not? (None = never chose.)"""
    return not _work_set(new) <= _work_set(old)


def _norm(text):
    return " ".join((text or "").lower().split())


def gate_widened(old, new) -> bool:
    """Does saving profile `new` over `old` let in roles that `old` kept out? Both are dicts with titles,
    level, one_level_up, city and work_types. Conservative: when unsure it says yes, which only costs a
    quiet re-scan."""
    if {_norm(t) for t in new["titles"]} - {_norm(t) for t in old["titles"]}:
        return True
    if level_widened(old["level"], old["one_level_up"], new["level"], new["one_level_up"]):
        return True
    if work_types_widened(old["work_types"], new["work_types"]):
        return True
    old_city, new_city = _norm(old["city"]), _norm(new["city"])
    return bool(old_city) and new_city != old_city  # a city only narrows hybrid and on-site roles


# --- SC-51 slice 4: a person's skills against a role's stored description ----------------------------
# A role's score is how many of the person's skills its stored text mentions. The matching rule is plain on
# purpose (decided on docs/SC-51-slice-4-decision-card.md): a skill matches as a whole word or phrase, any
# capitalisation, after bracketed text is removed from the skill ("Accessibility (WCAG 2.2)" looks for
# "accessibility"). No synonyms. `skill_regex` is the one rule: Python uses it here, and db.get_skill_matches
# hands the same text to Postgres, so the dashboard and the tests cannot drift apart.

MIN_SKILL_LENGTH = 2  # a one-letter "skill" would match stray letters, so it is not counted


def skill_phrase(skill) -> str:
    """What to look for in a description for one saved skill: bracketed text removed, whitespace collapsed,
    lower case. Empty when nothing usable is left."""
    text = " ".join(re.sub(r"\([^)]*\)", " ", skill or "").lower().split())
    return text if len(text) >= MIN_SKILL_LENGTH else ""


def skill_phrases(skills):
    """The usable phrases for these skills: in order, without repeats (ignoring case and brackets)."""
    seen, out = set(), []
    for skill in skills or ():
        phrase = skill_phrase(skill)
        if phrase and phrase not in seen:
            seen.add(phrase)
            out.append(phrase)
    return out


def skill_regex(phrase: str) -> str:
    """Whole word or phrase: not inside a longer run of letters or digits. Match it case-insensitively."""
    return r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])"


def skills_matched(text, skills):
    """Which of the person's skills (as phrases) this text mentions."""
    if not text:
        return []
    return [phrase for phrase in skill_phrases(skills) if re.search(skill_regex(phrase), text, re.IGNORECASE)]


def effective_cutoff(cutoff, skills):
    """The number of matches a role needs, or None for no cutoff. The person's number can never be higher than
    the number of skills they have (or nothing could ever pass), and nobody with no usable skills has a cutoff."""
    usable = len(skill_phrases(skills))
    if not cutoff or cutoff < 1 or usable == 0:
        return None
    return min(int(cutoff), usable)


def skill_status(matched_count, cutoff, has_text) -> str:
    """"unscored" (no stored text: never hidden, nothing to say about it), "hidden" (under the cutoff) or "shown"."""
    if not has_text:
        return "unscored"
    if cutoff is not None and matched_count < cutoff:
        return "hidden"
    return "shown"
