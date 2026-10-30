"""The fit score (SC-70): how well a role fits a person, 0 to 10, shown as one to five pips.

It is Scout's estimate from the posting and the person's resume and profile, not an employer's screen (never call it
an ATS score). Three parts, each plain matching with no model call at the time of the request:
  - Requirements covered (6 points): the role's top requirements, read once per role (dashboard/requirements.py),
    and how many of them the person's resume, skills or titles name.
  - Their skills mentioned (2 points): how many of the profile's skills the role's stored text mentions (SC-51).
  - Title and level (2 points): the level the title states against the person's level; exact is 2, one step off 1.
A part that can't be judged (no resume or skills to check against, a title that states no level, a person with no
level or no skills) is left out and the rest are scaled to 10, so missing input never counts as a miss. A role with no
stored text, or whose requirements haven't been read yet, gets no score and shows no pips."""
import io
import logging
import math
import re

from db.db import (
    get_requirements,
    get_resume_content,
    get_resume_text,
    get_skill_matches,
    get_unread_postings,
    set_resume_text,
)
from role_categories import LEVELS, skill_phrase, skill_phrases, title_level

log = logging.getLogger(__name__)

REQUIREMENTS_POINTS, SKILLS_POINTS, LEVEL_POINTS = 6, 2, 2
CLOSE_PIPS = 4              # 4 and 5 pips count as "close to what you're after" in Scout's line
MAX_RESUME_TEXT = 20000     # characters kept from a resume
READ_PER_VISIT = 100        # at most this many unread roles are queued per page load

WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")


def pdf_text(content):
    """The text of a PDF, or "" when it can't be taken (a scan, an odd file). Never raises."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        parts, size = [], 0
        for page in reader.pages[:10]:
            part = page.extract_text() or ""
            parts.append(part)
            size += len(part)
            if size >= MAX_RESUME_TEXT:
                break
        return " ".join(" ".join(parts).split())[:MAX_RESUME_TEXT]
    except Exception:  # noqa: BLE001 - a resume nobody can read just scores on skills and titles
        return ""


def evidence(resume_text, skills, titles):
    """Everything a requirement is checked against, lower case: the resume text plus saved skills and titles."""
    return " \n ".join([(resume_text or "").lower(), *[s.lower() for s in skills or ()], *[t.lower() for t in titles or ()]])


def term_regex(phrase):
    """A requirement's term as a whole word or phrase, like a skill, but a plural counts too ("design system" finds
    "design systems"): the model writes a term once and a resume may use either form."""
    return r"(?<![a-z0-9])" + re.escape(phrase) + r"(?:e?s)?(?![a-z0-9])"


def covered(requirements, text):
    """How many of these requirements appear in the text: a requirement counts when any of its terms does."""
    hit = 0
    for req in requirements:
        for term in req.get("terms") or ():
            phrase = skill_phrase(term)
            if phrase and re.search(term_regex(phrase), text, re.IGNORECASE):
                hit += 1
                break
    return hit


def level_points(posting_title, person_level):
    """2 for the same level, 1 for one step off, 0 further; None when either side states none. A title like "Junior"
    sits below every level a person can pick, so it is further than one step."""
    stated = title_level(posting_title)
    if stated is None or person_level not in LEVELS:
        return None
    if stated not in LEVELS:
        return 0
    gap = abs(LEVELS.index(stated) - LEVELS.index(person_level))
    return max(0, LEVEL_POINTS - gap)


def score(requirements, text, skills_total, skills_hit, posting_title, person_level):
    """0 to 10, or None when there is nothing to judge. `requirements` None = not read yet (no score); a role only
    has requirements once it has stored text, so a role without text never scores."""
    if requirements is None:
        return None
    earned = possible = 0.0
    if requirements and text.strip():
        earned += REQUIREMENTS_POINTS * covered(requirements, text) / len(requirements)
        possible += REQUIREMENTS_POINTS
    if skills_total:
        earned += SKILLS_POINTS * min(skills_hit, skills_total) / skills_total
        possible += SKILLS_POINTS
    pts = level_points(posting_title, person_level)
    if pts is not None:
        earned += pts
        possible += LEVEL_POINTS
    if not possible:
        return None
    return round(10 * earned / possible)


def pips(points):
    """1 to 5 pips for a 0 to 10 score (0 to 2 -> 1, 3 to 4 -> 2, 5 to 6 -> 3, 7 to 8 -> 4, 9 to 10 -> 5); 0 = no score."""
    if points is None:
        return 0
    return max(1, math.ceil(points / 2))


def resume_text_for(conn, user_id):
    """The person's resume text, taken from the PDF (and kept) the first time it is needed. "" when there is none."""
    has_resume, text = get_resume_text(conn, user_id)
    if not has_resume:
        return ""
    if text is None:
        content = get_resume_content(conn, user_id)
        text = pdf_text(content) if content else ""
        set_resume_text(conn, user_id, text)
        conn.commit()
    return text


def apply_fit(conn, user_id, profile, postings, shown):
    """Give each posting its `fit` (0 = no score, else 1 to 5 pips) where its requirements have been read. Returns
    (how many look close, the ids among `shown` still waiting to be read, as given). Only roles on the page being shown
    are ever offered for reading, so a role nobody looks at is never scored or paid for. Reads only; the caller queues them."""
    ids = [p["id"] for p in postings]
    reqs = get_requirements(conn, ids)
    skills = profile.get("skills") or []
    text = evidence(resume_text_for(conn, user_id), skills, profile.get("titles"))
    matches = get_skill_matches(conn, list(reqs), skills) if reqs else {}
    total_skills = len(skill_phrases(skills))
    close = 0
    for p in postings:
        points = score(reqs.get(p["id"]), text, total_skills, len(matches.get(p["id"], ())),
                       p["title"], profile.get("level"))
        p["fit"] = pips(points)
        close += p["fit"] >= CLOSE_PIPS
    waiting = [p["id"] for p in shown if p["id"] not in reqs]
    return close, [pid for pid, _ in get_unread_postings(conn, waiting, READ_PER_VISIT)]


def close_phrase(count):
    """"Two look close to what you're after." / "One looks close ...", or "" for none."""
    if count <= 0:
        return ""
    n = WORDS[count].capitalize() if count < len(WORDS) else str(count)
    return f"{n} {'looks' if count == 1 else 'look'} close to what you’re after."
