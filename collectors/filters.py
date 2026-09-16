KEYWORDS = [
    "designer",
    "design",
    "ux",
    "ui",
    "user experience",
    "user research",
]


def is_design_role(title: str) -> bool:
    t = title.lower()
    return any(k in t for k in KEYWORDS)
