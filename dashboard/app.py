import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, redirect, render_template, request, session, url_for

from db.db import get_conn, get_open_postings, mark_linkedin_seen, set_applied
from filters import is_remote_us

app = Flask(__name__)
# Falls back to a fixed dev value locally; set a real SECRET_KEY once this is
# deployed off localhost (Story 2) so session cookies can't be forged.
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-not-for-production")
app.permanent_session_lifetime = timedelta(days=90)

# Hardcoded pending auth (Sprint 1, Story 2 Stage C). Every route below should
# read the user id from here, not inline, so swapping this for a real
# session-derived current_user is a one-line change to this constant's
# definition, not a hunt through every route.
CURRENT_USER_ID = 1


def humanize_posted_at(dt):
    if dt is None:
        return ""
    seconds = (datetime.now(timezone.utc) - dt).total_seconds()
    if seconds < 3600:
        minutes = max(int(seconds // 60), 1)
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    if seconds < 7 * 86400:
        days = int(seconds // 86400)
        return f"{days} day{'s' if days != 1 else ''} ago"
    return dt.strftime("%b %-d, %Y")


app.jinja_env.filters["humanize"] = humanize_posted_at


@app.route("/")
def index():
    conn = get_conn()
    try:
        postings = get_open_postings(conn, CURRENT_USER_ID)
    finally:
        conn.close()

    remote_us_only = session.get("remote_us_only", False)
    if remote_us_only:
        postings = [p for p in postings if is_remote_us(p["location"], p["remote_type"])]

    return render_template("index.html", postings=postings, remote_us_only=remote_us_only)


@app.route("/toggle_remote_us", methods=["POST"])
def toggle_remote_us():
    session.permanent = True
    session["remote_us_only"] = request.form.get("remote_us_only") == "true"
    return redirect(url_for("index"))


@app.route("/mark_seen/<int:posting_id>", methods=["POST"])
def mark_seen(posting_id):
    conn = get_conn()
    try:
        mark_linkedin_seen(conn, posting_id, CURRENT_USER_ID)
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("index"))


@app.route("/set_applied/<int:posting_id>", methods=["POST"])
def set_applied_route(posting_id):
    applied = request.form.get("applied") == "true"
    conn = get_conn()
    try:
        set_applied(conn, posting_id, applied, CURRENT_USER_ID)
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, port=5050)
