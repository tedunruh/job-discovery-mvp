"""The panel for one application (SC-73): Where it stands (changeable), a short history and what was sent.

It is drawn on the server like the prep panel (dashboard/prep.py) and opened the same way: the page asks for
/application/<posting id> when a card in the Applications tab is clicked. Changing the stage answers with the panel's
stage area again (the select and the history), plus what the toast should say and which change an Undo would take back;
the page then asks /applications/list for the grouped list so the row moves to its new group."""
from flask import g, render_template, request

from dashboard.elevate import STAGE_OPTIONS, application_history, monogram, short_date, stage_toast, tint
from db.db import (
    CHECKIN_ANSWERS, applied_tailoring, get_applications, get_conn, get_stage_history, record_checkin, set_stage, undo_stage,
)


def build(conn, user_id, posting_id, toast="", undo_log=None):
    """The panel's content for one application, or None when this person has not applied to that job."""
    rows = get_applications(conn, user_id, posting_id)
    if not rows:
        return None
    a = rows[0]
    files = applied_tailoring(conn, user_id, posting_id)
    letter = (files or {}).get("letter") or ""
    return {
        "id": posting_id, "company": a["company_name"], "title": a["title"], "mono": monogram(a["company_name"]),
        "tint": tint(a["company_name"]), "stage": a["stage"], "options": STAGE_OPTIONS,
        "employer_closed": a["posting_status"] == "closed",
        "history": application_history(get_stage_history(conn, user_id, posting_id), a["applied_at"], bool(files), bool(letter)),
        "files": files, "letter": letter, "letter_words": len(letter.split()),
        "saved": short_date(files["saved_at"]) if files and files["saved_at"] else "",
        "toast": toast, "undo_log": undo_log,
    }


def register(app, require_login):
    def answer(conn, posting_id, toast="", undo_log=None):
        body = build(conn, g.user_id, posting_id, toast, undo_log)
        conn.commit()
        if body is None:
            return "", 404
        response = app.make_response(render_template("_application_panel.html", a=body))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/application/<int:posting_id>")
    @require_login
    def application_panel(posting_id):
        conn = get_conn()
        try:
            return answer(conn, posting_id)
        finally:
            conn.close()

    @app.route("/application/<int:posting_id>/stage", methods=["POST"])
    @require_login
    def application_stage(posting_id):
        """The person picked where this application stands. Answers with the panel; the toast words and the Undo's
        change ride on it (empty when nothing changed, for instance the same stage picked again)."""
        stage = request.form.get("stage", "")
        conn = get_conn()
        try:
            log_id = set_stage(conn, g.user_id, posting_id, stage, request.form.get("source", "panel"))
            return answer(conn, posting_id, stage_toast(stage) if log_id else "", log_id)
        finally:
            conn.close()

    @app.route("/application/<int:posting_id>/checkin", methods=["POST"])
    @require_login
    def application_checkin(posting_id):
        """An answer on the row's check-in question (SC-73): heard_yes, heard_no or close_no. Yes, close it and What did they
        say? are stage changes and go through /stage with source=checkin. Nothing comes back (204): the row's script
        already shows what it needs."""
        answer = request.form.get("answer", "")
        if answer not in CHECKIN_ANSWERS:
            return "", 400
        conn = get_conn()
        try:
            record_checkin(conn, g.user_id, posting_id, answer)
            conn.commit()
        finally:
            conn.close()
        return "", 204

    @app.route("/application/<int:posting_id>/stage/undo", methods=["POST"])
    @require_login
    def application_stage_undo(posting_id):
        """Undo on the toast: only the change it was made for, and only while it is still the latest."""
        try:
            log_id = int(request.form.get("log", ""))
        except ValueError:
            log_id = 0
        conn = get_conn()
        try:
            undo_stage(conn, g.user_id, posting_id, log_id)
            return answer(conn, posting_id)
        finally:
            conn.close()
