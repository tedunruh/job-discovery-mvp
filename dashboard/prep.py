"""The panel beside the list (SC-72): Prep, then Tailor and Review, for one job.

Prep is made only from what is already stored: the role's requirements (read once for the fit score, SC-70), the person's
resume text, skills and titles. Nothing in it calls a model, so opening a panel costs nothing. The one exception is a role
whose requirements have not been read yet: opening it queues the same background read the page would have queued.

Tailoring (dashboard/tailor.py) starts only when the person clicks "Tailor my resume". The panel then shows one of three
stages for the job: prep, working (the call is running; the page polls /tailor/<id>) or review (the changes, each of which
can be undone). The stage is read from the database every time, so closing the panel or opening another job loses nothing.

The page asks for the panel's markup (/prep/<id>, and the /tailor routes below, which answer with the same markup), drawn
here by Jinja, which escapes everything: requirement labels and the model's wording come from untrusted text."""
from flask import Response, g, render_template, request

from dashboard.elevate import monogram, tint
from dashboard.fit import apply_fit, covered_flags, evidence, resume_text_for
from dashboard import letter, resume_files, tailor
from dashboard.requirements import queue as queue_reads
from db.db import (
    cancel_tailoring,
    get_company_name,
    save_letter,
    set_tailoring_ready,
    expire_stale_tailorings,
    get_conn,
    get_posting_prep,
    get_postings_by_ids,
    get_resume,
    get_resume_text,
    get_tailoring,
    get_user_profile,
    latest_tailoring,
    set_change_kept,
)

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


MAX_LETTER_CHARS = letter.MAX_CHARS
WORKING_STEPS = [
    "Reading your resume…",
    "Reading the posting…",
    "Matching your experience to what they ask for…",
    "Making the changes…",
    "Almost there…",
]
MAX_NOT_ADDED = 4


def not_added_text(labels):
    """The note under the changes for the requirements the resume lacks, or None when there are none."""
    if not labels:
        return None
    shown = ", ".join(labels[:MAX_NOT_ADDED]) + (f" and {len(labels) - MAX_NOT_ADDED} more" if len(labels) > MAX_NOT_ADDED else "")
    it = "it" if len(labels) == 1 else "them"
    return (f"Not added: {shown}. The posting asks for {it} and your resume doesn’t mention {it}, so I left {it} out. "
            f"If you’ve used {it}, add {it} to your resume and tailor again.")


def review_view(tailoring):
    """What the Review stage shows for a ready tailoring: its changes (with a name for each button), the count and the
    note about what was not added."""
    changes = tailoring["changes"] or []
    kept = tailor.kept_count(changes)
    out = []
    for c in changes:
        text = c["after"] if isinstance(c["after"], str) else (c["after"][0] if c["after"] else "")
        name = c["label"] + (f", {c['where']}" if c["where"] else "") + (f": {tailor.shorten(text, 40)}" if text else "")
        out.append(dict(c, lines=isinstance(c["after"], list), name=name))
    return {"tid": tailoring["id"], "changes": out, "total": len(changes), "kept": kept,
            "line": f"{kept} of {len(changes)} changes kept", "not_added": not_added_text(tailoring["not_added"]),
            "empty": tailor.message("nothing")}


def letter_view(tailoring, can_write):
    """The Cover letter section of Review and Ready. `state` is none (no letter yet), writing, ready or error;
    `can_write` is whether the button to write one is shown (nothing written yet, or the last try failed, and cover
    letters are available); `unavailable` says so when none is written and they are not."""
    state = tailoring.get("letter_state") or "none"
    return {"tid": tailoring["id"], "state": state, "text": tailoring.get("letter") or "",
            "can_write": can_write and state in ("none", "error"),
            "unavailable": state == "none" and not can_write,
            "message": letter.message("failed") if state == "error" else letter.message("unavailable"),
            "chars": MAX_LETTER_CHARS}


def stage_for(conn, user_id, posting_id, out, notice):
    """Which stage the panel is in for this job, set on `out`: working, review or ready when the person has a tailoring
    for this job (and the same resume file), else prep with what the Tailor button should say."""
    expire_stale_tailorings(conn, user_id)
    found = latest_tailoring(conn, user_id, posting_id)
    resume = get_resume(conn, user_id)
    if found and found["status"] == "working":
        out.update(stage="working", tid=found["id"], say=f"I’m tailoring your resume for {out['company']}.",
                   steps=WORKING_STEPS)
    elif found and found["status"] == "ready" and resume and found["resume_uploaded_at"] == resume["uploaded_at"]:
        review = review_view(found)
        can_write = bool(review["total"]) and letter.available(conn, user_id)
        out.update(stage="ready" if found["ready_at"] and review["total"] else "review", tid=found["id"], review=review,
                   letter=letter_view(found, can_write) if review["total"] else None,
                   say="You’re ready. I saved these with the job." if found["ready_at"] and review["total"]
                   else "Here’s what I changed." if review["total"] else "I didn’t find anything worth changing.")
    else:
        state, used = tailor.availability(conn, user_id)
        out["tailor"] = {"state": state, "used": used, "limit": tailor.MONTHLY_TAILORINGS_PER_PERSON,
                         "message": {"limit": f"You’ve tailored {used} resumes this month, the most for now. It starts over "
                                              f"on {tailor.next_reset()}.",
                                     "unavailable": tailor.message("unavailable"), "ok": ""}[state],
                         # a letter is available when tailoring is (same key, same month's spend): no second query
                         "letter": state == "ok" and letter.is_configured()}
        out["notice"] = notice


def build(conn, user_id, posting_id, notice=None):
    """The panel's content for one job, or None when there is no such open job. `state` is one of ok, no_resume,
    no_text (nothing to read) or reading (requirements not read yet); every state keeps Apply available. Only `ok` has
    a `stage` beyond prep (see stage_for); `notice` is a message to show above the buttons (a tailoring that failed)."""
    postings = get_postings_by_ids(conn, user_id, [posting_id])
    prep = get_posting_prep(conn, posting_id)
    if not postings or prep is None:
        return None
    posting, (has_text, requirements) = postings[0], prep
    company = posting["company_name"]
    out = {"id": posting_id, "company": company, "title": posting["title"], "mono": monogram(company),
           "tint": tint(company), "source": (posting.get("ats_type") or "").capitalize(),
           "state": "ok", "say": "", "message": "", "fit": None, "requirements": [], "lead": [],
           "stage": "prep", "tid": None, "tailor": None, "review": None, "letter": None, "steps": [], "notice": None}

    if not has_text or requirements == []:
        out.update(state="no_text", say=MESSAGES["no_text"])
        return out
    if requirements is None:
        queue_reads([posting_id])   # the same background read a page load would have queued; capped like the others
        out.update(state="reading", say=MESSAGES["reading"])
        return out

    has_resume, stored_text = get_resume_text(conn, user_id)
    if not has_resume:
        out.update(state="no_resume", say="Here’s what they’re asking for.", message=MESSAGES["no_resume"],
                   requirements=[{"text": r["label"], "mark": "unknown", "state": ""} for r in requirements])
        return out

    profile = get_user_profile(conn, user_id)
    skills, titles = (profile or {}).get("skills") or [], (profile or {}).get("titles") or []
    # The resume's text is read once for the whole panel (each read is a round trip to the database).
    resume_text = stored_text if stored_text is not None else resume_text_for(conn, user_id)
    flags = covered_flags(requirements, evidence(resume_text, skills, titles))
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
        apply_fit(conn, user_id, profile, [posting], [], resume_text)
        pips = posting.get("fit") or 0
        if pips:
            out["fit"] = {"pips": pips, "label": FIT_LABELS[pips]}
    stage_for(conn, user_id, posting_id, out, notice)
    return out


def register(app, require_login):
    def panel(conn, posting_id, notice=None):
        body = build(conn, g.user_id, posting_id, notice)
        conn.commit()
        if body is None:
            return "", 404
        response = app.make_response(render_template("_prep_panel.html", prep=body))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/prep/<int:posting_id>")
    @require_login
    def prep_panel(posting_id):
        conn = get_conn()
        try:
            return panel(conn, posting_id)
        finally:
            conn.close()

    @app.route("/tailor/start/<int:posting_id>", methods=["POST"])
    @require_login
    def tailor_start(posting_id):
        """The person clicked "Tailor my resume": start it (if they can) and answer with the panel, now working."""
        conn = get_conn()
        try:
            body = build(conn, g.user_id, posting_id)
            conn.commit()
            if body is not None and body["state"] == "ok" and body["stage"] == "prep" and body["tailor"]["state"] == "ok":
                tailor.start(conn, g.user_id, posting_id, request.form.get("letter") == "1" and body["tailor"]["letter"])
            return panel(conn, posting_id)
        finally:
            conn.close()

    @app.route("/tailor/<int:tailoring_id>", methods=["GET"])
    @require_login
    def tailor_status(tailoring_id):
        """The page asks while it is working: 204 until the tailoring ends, then the panel as it now is."""
        conn = get_conn()
        try:
            expire_stale_tailorings(conn, g.user_id)
            conn.commit()
            row = get_tailoring(conn, g.user_id, tailoring_id)
            if row is None or row["posting_id"] is None:
                return "", 404
            if row["status"] == "working":
                return "", 204
            notice = tailor.message(row["error_kind"]) if row["status"] == "error" else None
            return panel(conn, row["posting_id"], notice)
        finally:
            conn.close()

    @app.route("/tailor/<int:tailoring_id>/cancel", methods=["POST"])
    @require_login
    def tailor_cancel(tailoring_id):
        conn = get_conn()
        try:
            posting_id = cancel_tailoring(conn, g.user_id, tailoring_id)
            conn.commit()
            if posting_id is None:
                return "", 404
            return panel(conn, posting_id)
        finally:
            conn.close()

    @app.route("/tailor/<int:tailoring_id>/change", methods=["POST"])
    @require_login
    def tailor_change(tailoring_id):
        """Undo or redo one change: answers with the whole panel; the page swaps in only the card that changed."""
        change_id, kept = request.form.get("change", ""), request.form.get("kept")
        conn = get_conn()
        try:
            row = get_tailoring(conn, g.user_id, tailoring_id)
            if row is None or row["posting_id"] is None or kept not in ("0", "1") or \
                    not set_change_kept(conn, g.user_id, tailoring_id, change_id, kept == "1"):
                conn.rollback()
                return "", 404
            return panel(conn, row["posting_id"])
        finally:
            conn.close()

    @app.route("/tailor/<int:tailoring_id>/ready", methods=["POST"])
    @require_login
    def tailor_ready(tailoring_id):
        """"Looks good" (ready=1) takes the person to Ready, where the files are; "Back to the changes" (ready=0) back."""
        ready = request.form.get("ready")
        conn = get_conn()
        try:
            posting_id = set_tailoring_ready(conn, g.user_id, tailoring_id, ready == "1") if ready in ("0", "1") else None
            if posting_id is None:
                conn.rollback()
                return "", 404
            return panel(conn, posting_id)
        finally:
            conn.close()

    @app.route("/tailor/<int:tailoring_id>/letter/status", methods=["GET"])
    @require_login
    def tailor_letter_status(tailoring_id):
        """The page asks while a letter is being written: 204 until it is done, then the panel as it now is."""
        conn = get_conn()
        try:
            expire_stale_tailorings(conn, g.user_id)
            conn.commit()
            row = get_tailoring(conn, g.user_id, tailoring_id)
            if row is None or row["posting_id"] is None:
                return "", 404
            if row["letter_state"] == "writing":
                return "", 204
            return panel(conn, row["posting_id"])
        finally:
            conn.close()

    @app.route("/tailor/<int:tailoring_id>/letter", methods=["POST"])
    @require_login
    def tailor_letter(tailoring_id):
        """"Write a cover letter" on Review or Ready: written in the background, once per tailoring, free of the monthly
        limit. Answers with the panel (the page swaps in only the letter's section)."""
        conn = get_conn()
        try:
            row = get_tailoring(conn, g.user_id, tailoring_id)
            if row is None or row["posting_id"] is None:
                return "", 404
            if letter.available(conn, g.user_id):
                letter.start(conn, g.user_id, tailoring_id)
            return panel(conn, row["posting_id"])
        finally:
            conn.close()

    @app.route("/tailor/<int:tailoring_id>/letter/save", methods=["POST"])
    @require_login
    def tailor_letter_save(tailoring_id):
        """The person's own edits to their letter, saved as they leave the box."""
        text = letter.clean_letter(request.form.get("letter", ""))
        conn = get_conn()
        try:
            if not text or not save_letter(conn, g.user_id, tailoring_id, text):
                conn.rollback()
                return "", 404
            conn.commit()
            return "", 204
        finally:
            conn.close()

    def download(tailoring_id, make, mimetype, extension):
        """A file of this person's ready tailoring (they said "Looks good", or it went with an application)."""
        conn = get_conn()
        try:
            row = get_tailoring(conn, g.user_id, tailoring_id)
            if row is None or row["status"] != "ready" or not (row["ready_at"] or row["applied_at"]) or row["source"] is None:
                return "", 404
            company = get_company_name(conn, row["posting_id"]) if row["posting_id"] else ""
            structure = tailor.apply_changes(row["source"], row["changes"])
            body = make(structure, row)
            response = Response(body, mimetype=mimetype)
            response.headers["Content-Disposition"] = f'attachment; filename="{resume_files.file_stem(structure, company or "")}.{extension}"'
            response.headers["Cache-Control"] = "no-store"
            return response
        finally:
            conn.close()

    @app.route("/tailor/<int:tailoring_id>/resume.pdf")
    @require_login
    def tailor_pdf(tailoring_id):
        return download(tailoring_id, lambda structure, row: resume_files.to_pdf(structure), "application/pdf", "pdf")

    @app.route("/tailor/<int:tailoring_id>/resume.docx")
    @require_login
    def tailor_docx(tailoring_id):
        return download(tailoring_id, lambda structure, row: resume_files.to_docx(structure),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx")

    @app.route("/tailor/<int:tailoring_id>/letter.txt")
    @require_login
    def tailor_letter_file(tailoring_id):
        conn = get_conn()
        try:
            row = get_tailoring(conn, g.user_id, tailoring_id)
            if row is None or row["status"] != "ready" or not (row["ready_at"] or row["applied_at"]) or not row["letter"]:
                return "", 404
            response = Response(row["letter"] + "\n", mimetype="text/plain")
            response.headers["Content-Disposition"] = 'attachment; filename="cover-letter.txt"'
            response.headers["Cache-Control"] = "no-store"
            return response
        finally:
            conn.close()
