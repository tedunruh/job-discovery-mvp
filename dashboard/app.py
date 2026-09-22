import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, redirect, render_template, request, url_for

from db.db import get_conn, get_open_postings, mark_linkedin_seen, set_applied

app = Flask(__name__)


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
        postings = get_open_postings(conn)
    finally:
        conn.close()
    return render_template("index.html", postings=postings)


@app.route("/mark_seen/<int:posting_id>", methods=["POST"])
def mark_seen(posting_id):
    conn = get_conn()
    try:
        mark_linkedin_seen(conn, posting_id)
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("index"))


@app.route("/set_applied/<int:posting_id>", methods=["POST"])
def set_applied_route(posting_id):
    applied = request.form.get("applied") == "true"
    conn = get_conn()
    try:
        set_applied(conn, posting_id, applied)
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, port=5050)
