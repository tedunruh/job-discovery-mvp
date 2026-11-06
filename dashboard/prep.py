"""Prep (SC-72): what the panel beside the list says about one job before the person applies.

It is made only from what is already stored: the role's requirements (read once for the fit score, SC-70), the person's
resume text, skills and titles. Nothing here calls a model, so opening a panel costs nothing. The one exception is a role
whose requirements have not been read yet: opening it queues the same background read the page would have queued.

The page asks for this when a card is opened (/prep/<id>) and gets the panel's markup, drawn here by Jinja, which
escapes everything: requirement labels come from a model that read an untrusted posting."""
from flask import g, render_template

from dashboard.elevate import monogram, tint
from dashboard.fit import apply_fit, covered_flags, evidence, resume_text_for
from dashboard.requirements import queue as queue_reads
from db.db import get_conn, get_posting_prep, get_postings_by_ids, get_resume_text, get_user_profile

FIT_LABELS = ["", "Light fit", "Okay fit", "Good fit", "Great fit", "Strong fit"]
MAX_LEAD = 3

# `say` is the panel's first line; `message` (no resume only) sits above the buttons.
MESSAGES = {
    "no_text": "I couldn’t read this posting, so I can’t check your fit or tailor for it.",
    "reading": "I’m still reading this posting. Open it again in a minute and I’ll show how you line up.",
    "no_resume": "I need your resume to check your fit and tailor it. It takes about a minute.",
}


def say(flags):
    """Scout's line for a job whose requirements were checked against a resume."""
    got, total = sum(flags), len(flags)
    if got == total:
        return "You cover everything they list."
    line = f"You cover {got} of the {total} things they list."
    return line if got >= total - 1 else line + " Worth a look before you apply."


def build(conn, user_id, posting_id):
    """The panel's content for one job, or None when there is no such open job. `state` is one of ok, no_resume,
    no_text (nothing to read) or reading (requirements not read yet); every state keeps Apply available."""
    postings = get_postings_by_ids(conn, user_id, [posting_id])
    prep = get_posting_prep(conn, posting_id)
    if not postings or prep is None:
        return None
    posting, (has_text, requirements) = postings[0], prep
    company = posting["company_name"]
    out = {"id": posting_id, "company": company, "title": posting["title"], "mono": monogram(company),
           "tint": tint(company), "source": (posting.get("ats_type") or "").capitalize(),
           "state": "ok", "say": "", "message": "", "fit": None, "requirements": [], "lead": []}

    if not has_text or requirements == []:
        out.update(state="no_text", say=MESSAGES["no_text"])
        return out
    if requirements is None:
        queue_reads([posting_id])   # the same background read a page load would have queued; capped like the others
        out.update(state="reading", say=MESSAGES["reading"])
        return out

    has_resume, _ = get_resume_text(conn, user_id)
    if not has_resume:
        out.update(state="no_resume", say="Here’s what they’re asking for.", message=MESSAGES["no_resume"],
                   requirements=[{"text": r["label"], "mark": "unknown", "state": ""} for r in requirements])
        return out

    profile = get_user_profile(conn, user_id)
    skills, titles = (profile or {}).get("skills") or [], (profile or {}).get("titles") or []
    flags = covered_flags(requirements, evidence(resume_text_for(conn, user_id), skills, titles))
    out["say"] = say(flags)
    out["requirements"] = [
        {"text": r["label"], "mark": "yes" if hit else "no", "state": "In your resume" if hit else "Not in your resume"}
        for r, hit in zip(requirements, flags)
    ]
    # What to lead with: the things they ask for that your resume names, most important first. Specific lines about
    # your own work come with the structured resume read (the tailoring step); this stays plain matching.
    out["lead"] = [f"{r['label']}. They list it, and your resume mentions it."
                   for r, hit in zip(requirements, flags) if hit][:MAX_LEAD]
    if profile is not None:
        posting = dict(posting)
        apply_fit(conn, user_id, profile, [posting], [])
        pips = posting.get("fit") or 0
        if pips:
            out["fit"] = {"pips": pips, "label": FIT_LABELS[pips]}
    return out


def register(app, require_login):
    @app.route("/prep/<int:posting_id>")
    @require_login
    def prep_panel(posting_id):
        conn = get_conn()
        try:
            body = build(conn, g.user_id, posting_id)
            conn.commit()
        finally:
            conn.close()
        if body is None:
            return "", 404
        response = app.make_response(render_template("_prep_panel.html", prep=body))
        response.headers["Cache-Control"] = "no-store"
        return response
