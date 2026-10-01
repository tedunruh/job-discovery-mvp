import re

# Coarse, keyword-based role categories a user picks from during onboarding.
# Matched against posting titles - same word-boundary approach as
# collectors/filters.py's is_design_role, which is the broader net these
# categories sit inside (every posting in the DB already passed that filter,
# so this is purely about which subset of design-adjacent roles a given user
# wants to see/be notified about, not a new ingestion-time filter).
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
