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
