"""The skills cutoff (SC-51 slice 4): a role is shown when its stored text mentions at least as many of the
person's skills as their minimum. Used by the dashboard list and by the profile's live preview, so the two
can never disagree about which roles a person would see.

    kept, hidden = apply_skill_cutoff(conn, postings, skills, cutoff)

Rules (docs/SC-51-slice-4-decision-card.md): a role with no stored text is never hidden (it is "not scored");
the minimum is never higher than the number of skills; no skills, or no minimum, means nothing is hidden and
nothing is looked up."""
import re

from db.db import get_skill_matches
from role_categories import effective_cutoff, skill_phrase, skill_status

NOT_SCORED = "Not scored for your skills"
NONE_MENTIONED = "Mentions none of your skills"


def skill_labels(skills):
    """{phrase: the skill in the person's own spelling, without the bracketed part (first spelling wins)}, for
    showing matched skills by name. The bracket is not part of what was matched, so it is not shown."""
    labels = {}
    for skill in skills or ():
        phrase = skill_phrase(skill)
        if phrase and phrase not in labels:
            labels[phrase] = " ".join(re.sub(r"\([^)]*\)", " ", skill).split())
    return labels


def apply_skill_cutoff(conn, postings, skills, cutoff, show_all=False):
    """Returns (postings to show, how many fall under the minimum). With a minimum in force each posting also gets
    `skill_line`: what it mentions, or why nothing is said. `show_all` keeps the roles under the minimum in the
    list (with their line) instead of dropping them; the count is the same either way."""
    needed = effective_cutoff(cutoff, skills)
    if needed is None or not postings:
        return postings, 0
    matches = get_skill_matches(conn, [p["id"] for p in postings], skills)
    labels = skill_labels(skills)
    kept, under = [], 0
    for p in postings:
        found = matches.get(p["id"])
        status = skill_status(len(found) if found is not None else 0, needed, has_text=found is not None)
        if status == "hidden":
            under += 1
            if not show_all:
                continue
        if status == "unscored":
            p["skill_line"] = NOT_SCORED
        elif found:
            p["skill_line"] = "Mentions " + ", ".join(labels.get(phrase, phrase) for phrase in found)
        else:
            p["skill_line"] = NONE_MENTIONED
        kept.append(p)
    return kept, under
