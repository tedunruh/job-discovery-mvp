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


# --- SC-53: the strict reading of "a design role" -------------------------------------------------------
# The gate above is deliberately loose: it decides what is *stored*, and "design" appears in plenty of work
# that is not design. Measured on 11,989 stored roles, about six in ten were engineering or hardware ("Analog IC
# Design Engineer", "Civil Design Engineer"), plus interior, apparel and instructional design. This reads a title
# the way a product designer would: it names a design function, and is not a hardware, engineering, interior or
# similar role. It decides what a person sees when they have not chosen what they want themselves (an old
# profile with no role categories); once they have, their own titles decide. On the stored roles it keeps about
# four in ten of what the loose gate does (4,555 of 11,989).

_FUNCTION = re.compile(
    r"""\b(
      product\s+design(er)?s? | ux | ui | user\s+experience | user\s+research(er)? | researcher
      | interaction\s+design(er)? | visual\s+design(er)? | brand\s+design(er)? | graphics?\s+design(er)?
      | communications?\s+design(er)? | content\s+design(er)? | service\s+design(er)? | experience\s+design(er)?
      | motion\s+design(er)? | web\s+design(er)? | digital\s+design(er)? | creative\s+design(er)?
      | email\s+design(er)? | marketing\s+design(er)?
      | design\s+systems? | design\s+(manager|director|lead|head|strategist|ops|technologist)
      | head\s+of\s+design | vp,?\s+(of\s+)?design | creative\s+director | art\s+director
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)
# "Senior Designer, Brand" and "Founding Designer": the title is seniority words, then designer.
_BARE_DESIGNER = re.compile(
    r"^\W*((senior|sr|staff|lead|principal|associate|junior|jr|mid|group|chief|head|founding|i|ii|iii|iv)\W+)*designer\b",
    re.IGNORECASE,
)
_NOT_DESIGN_WORK = re.compile(
    r"\b(mechanical|electrical|electronics?|civil|structural|hardware|asic|fpga|analog|verification|silicon|rtl|"
    r"propulsion|automotive|interior|landscape|apparel|fashion|knitwear|footwear|textile|garment|jewelry|instructional|"
    r"learning|training|process|physical|package|packaging|embedded|firmware|manufacturing|tooling|technician|"
    r"construction|building|energy|power|film|window|cafe|district|architectural|bakery|pcb|circuit|bridge|"
    r"low\s+voltage|water|piping|cad|telecom|thermal|photonic|women'?s|men'?s)\b",
    re.IGNORECASE,
)
_ENGINEER_ROLE = re.compile(r"\b(engineer(s|ing)?|developer|architect|programmer|software|solutions?)\b", re.IGNORECASE)
# "Lead Product Designer | Platform Engineering": a team name is not the job. A person who is a designer of a
# kind a product designer would recognise is kept even when an engineering word appears elsewhere.
_DESIGNER_OF_A_KIND = re.compile(
    r"\b((ux|ui)(\s*/\s*(ux|ui))?|product|visual|brand|graphic|graphics|interaction|content|service|experience|"
    r"communications?|motion|web|digital|creative|email|marketing)\s+designers?\b",
    re.IGNORECASE,
)
_UX_ENGINEER = re.compile(r"\b(ux|user\s+experience)\s+engineer\b|\bdesign\s+technologist\b", re.IGNORECASE)


def is_strict_design_role(title: str) -> bool:
    title = title or ""
    if not (_FUNCTION.search(title) or _BARE_DESIGNER.search(title)):
        return False
    if _NOT_DESIGN_WORK.search(title):
        return False
    if _ENGINEER_ROLE.search(title) and not (_DESIGNER_OF_A_KIND.search(title) or _UX_ENGINEER.search(title)):
        return False
    return True
