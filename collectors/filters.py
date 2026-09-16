import re

KEYWORDS = [
    "designer",
    "design",
    "ux",
    "ui",
    "user experience",
    "user research",
]

# "ui"/"ux" as plain substrings false-positive on words like "recruiter" and
# "acquisition" (both contain "ui"). Word-boundary matching avoids that.
_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in KEYWORDS) + r")\b", re.IGNORECASE
)


def is_design_role(title: str) -> bool:
    return bool(_PATTERN.search(title))
