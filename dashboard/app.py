import os
import sys
from datetime import datetime, timedelta, timezone
from functools import wraps

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, g, redirect, render_template, request, send_from_directory, session, url_for

from dashboard.auth import create_magic_link, get_user_by_email, send_magic_link_email, verify_magic_link
from dashboard.filters import is_remote_us
from db.db import (
    SESSION_TTL_DAYS,
    create_session,
    delete_session,
    get_conn,
    get_open_postings,
    get_session_user,
    get_user_role_categories,
    is_onboarded,
    set_applied,
    set_user_role_categories,
)
from role_categories import ROLE_CATEGORIES, matches_categories

app = Flask(__name__)
# Falls back to a fixed dev value locally; set a real SECRET_KEY once this is
# deployed off localhost (Story 2) so session cookies can't be forged.
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-not-for-production")
app.permanent_session_lifetime = timedelta(days=90)  # only carries the remote-US filter now

# Login lives in a server-side session (user_sessions), not the Flask cookie.
# Secure only on Render: plain http://localhost would drop a Secure cookie.
IS_PROD = bool(os.environ.get("RENDER"))
SESSION_COOKIE = "scout_session"
LAST_EMAIL_COOKIE = "scout_last_email"
COOKIE_OPTS = {"httponly": True, "secure": IS_PROD, "samesite": "Lax"}
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SECURE=IS_PROD, SESSION_COOKIE_SAMESITE="Lax")


def set_session_cookie(response, token):
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL_DAYS * 86400, **COOKIE_OPTS)
    return response

FIELD_GUIDE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "field-guide")


@app.route("/field-guide/<any(dist, css):folder>/<path:filename>")
def field_guide_static(folder, filename):
    """Serve the Field Guide design system (tokens + component CSS) to templates."""
    return send_from_directory(os.path.join(FIELD_GUIDE_DIR, folder), filename)


def require_login(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        token = request.cookies.get(SESSION_COOKIE)
        user_id = None
        g.renewed_session = False
        if token:
            conn = get_conn()
            try:
                user_id, g.renewed_session = get_session_user(conn, token)
                conn.commit()
            finally:
                conn.close()
        if not user_id:
            return redirect(url_for("login"))
        g.user_id = user_id
        return view(*args, **kwargs)

    return wrapped


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


@app.after_request
def slide_session_cookie(response):
    # Re-send the cookie whenever the server-side expiry slid, so the
    # browser's max-age slides with it.
    if g.get("renewed_session"):
        set_session_cookie(response, request.cookies[SESSION_COOKIE])
    return response


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html", sent=False, error=None, last_email=request.cookies.get(LAST_EMAIL_COOKIE, ""))

    email = request.form.get("email", "").strip().lower()
    conn = get_conn()
    try:
        user = get_user_by_email(conn, email)
        if user:
            token = create_magic_link(conn, user["id"])
            conn.commit()
            link_url = url_for("verify", token=token, _external=True)
            send_magic_link_email(email, link_url)
        # Same response whether or not the email is registered - don't leak
        # which emails exist. This is a hand-invited beta list, not
        # self-serve signup: an unrecognized email just doesn't get a link.
    finally:
        conn.close()
    response = app.make_response(render_template("login.html", sent=True, error=None, last_email=email))
    if email:
        # Remember what they typed on this device so next time it's pre-filled.
        response.set_cookie(LAST_EMAIL_COOKIE, email, max_age=365 * 86400, **COOKIE_OPTS)
    return response


@app.route("/verify")
def verify():
    token = request.args.get("token", "")
    conn = get_conn()
    try:
        user_id = verify_magic_link(conn, token)
        conn.commit()
    finally:
        conn.close()

    if not user_id:
        return render_template("login.html", sent=False, error="That link is invalid, expired, or already used.", last_email=request.cookies.get(LAST_EMAIL_COOKIE, ""))

    conn = get_conn()
    try:
        token = create_session(conn, user_id)
        conn.commit()
    finally:
        conn.close()
    session.clear()
    return set_session_cookie(app.make_response(redirect(url_for("index"))), token)


@app.route("/logout", methods=["POST"])
def logout():
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        conn = get_conn()
        try:
            delete_session(conn, token)
            conn.commit()
        finally:
            conn.close()
    session.clear()
    response = redirect(url_for("login"))
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.route("/")
@require_login
def index():
    user_id = g.user_id
    conn = get_conn()
    try:
        if not is_onboarded(conn, user_id):
            return redirect(url_for("onboarding"))
        postings = get_open_postings(conn, user_id)
        role_categories = get_user_role_categories(conn, user_id)
    finally:
        conn.close()

    remote_us_only = session.get("remote_us_only", False)
    if remote_us_only:
        postings = [p for p in postings if is_remote_us(p["location"], p["remote_type"])]
    postings = [p for p in postings if matches_categories(p["title"], role_categories)]

    return render_template("index.html", postings=postings, remote_us_only=remote_us_only)


@app.route("/onboarding", methods=["GET", "POST"])
@require_login
def onboarding():
    user_id = g.user_id
    conn = get_conn()
    try:
        if request.method == "POST":
            selected = [c for c in request.form.getlist("categories") if c in ROLE_CATEGORIES]
            set_user_role_categories(conn, user_id, selected)
            conn.commit()
            return redirect(url_for("index"))
        selected = get_user_role_categories(conn, user_id)
    finally:
        conn.close()
    return render_template("onboarding.html", categories=ROLE_CATEGORIES, selected=selected)


@app.route("/toggle_remote_us", methods=["POST"])
@require_login
def toggle_remote_us():
    session.permanent = True
    session["remote_us_only"] = request.form.get("remote_us_only") == "true"
    return redirect(url_for("index"))


@app.route("/set_applied/<int:posting_id>", methods=["POST"])
@require_login
def set_applied_route(posting_id):
    applied = request.form.get("applied") == "true"
    conn = get_conn()
    try:
        set_applied(conn, posting_id, applied, g.user_id)
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, port=5050)
