import os
import sys
from datetime import datetime, timedelta, timezone
from functools import wraps

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, g, redirect, render_template, request, send_from_directory, session, url_for

from dashboard.auth import (
    create_magic_link,
    get_user_by_email,
    send_magic_link_email,
    too_many_recent_links,
    verify_magic_link,
)
from dashboard.filters import is_remote_us
from db.db import (
    SESSION_TTL_DAYS,
    create_session,
    delete_session,
    get_conn,
    get_open_postings,
    get_posting_url,
    get_user_keywords,
    get_session_user,
    get_user_role_categories,
    is_onboarded,
    log_event,
    log_visit,
    set_applied,
    set_user_keywords,
    set_user_role_categories,
)
from role_categories import ROLE_CATEGORIES, MAX_KEYWORDS, clean_keywords, matches_profile

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

# "Discovered within" filter on the dashboard: key -> (label, window). Keyed on
# first_seen_at (when Scout found it - what each row shows as "Discovered").
# 24h is the default so the list opens on what's new.
RANGES = {
    "24h": ("Past 24 hours", timedelta(hours=24)),
    "week": ("Past week", timedelta(days=7)),
    "month": ("Past month", timedelta(days=30)),
    "all": ("All time", None),
}
DEFAULT_RANGE = "24h"

# The dashboard renders this many roles, then offers "show more" - the table holds
# every role at every tracked company, so an unbounded list is thousands of rows.
PAGE_SIZE = 50
MAX_SHOWN = 1000

FIELD_GUIDE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "field-guide")


@app.route("/field-guide/<any(dist, css):folder>/<path:filename>")
def field_guide_static(folder, filename):
    """Serve the Field Guide design system (tokens + component CSS) to templates."""
    return send_from_directory(os.path.join(FIELD_GUIDE_DIR, folder), filename)


@app.errorhandler(405)
def method_not_allowed(_error):
    """A POST-only URL requested as a plain page load. The usual cause is Render's
    wake-up page replaying an interrupted form submit as a GET. Send people to
    the dashboard (or sign-in) instead of a bare error page."""
    return redirect(url_for("index"))


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
        if user and too_many_recent_links(conn, user["id"]):
            # Rate-limited: send nothing, but respond identically so the
            # limit doesn't reveal that the address is registered. Links
            # already sent stay valid for their 15 minutes.
            print(f"[rate-limit] sign-in emails for user {user['id']} capped")
        elif user:
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
        log_event(conn, user_id, "sign_in")
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

    # The filter controls submit as plain GETs (/?range=week, /?remote_us_only=true).
    # A GET can be safely replayed - e.g. by Render's wake-up page, which reloads the
    # URL once a sleeping free-tier service is up. A POST-only filter lands on a 405.
    if "range" in request.args or "remote_us_only" in request.args:
        session.permanent = True
        if request.args.get("range") in RANGES:
            session["posted_range"] = request.args["range"]
        if "remote_us_only" in request.args:
            session["remote_us_only"] = request.args["remote_us_only"] == "true"
        return redirect(url_for("index"))

    posted_range = session.get("posted_range", DEFAULT_RANGE)
    if posted_range not in RANGES:
        posted_range = DEFAULT_RANGE
    window = RANGES[posted_range][1]
    cutoff = datetime.now(timezone.utc) - window if window is not None else None

    conn = get_conn()
    try:
        if not is_onboarded(conn, user_id):
            return redirect(url_for("onboarding"))
        log_visit(conn, user_id)
        conn.commit()
        role_categories = get_user_role_categories(conn, user_id)
        keywords = get_user_keywords(conn, user_id)
        postings = get_open_postings(conn, user_id, since=cutoff, keywords=keywords)
    finally:
        conn.close()

    remote_us_only = session.get("remote_us_only", False)
    if remote_us_only:
        postings = [p for p in postings if is_remote_us(p["location"], p["remote_type"])]
    postings = [
        p for p in postings
        if matches_profile(p["title"], p["is_design"], role_categories, keywords)
    ]

    total = len(postings)
    limit = min(max(request.args.get("n", PAGE_SIZE, type=int), PAGE_SIZE), MAX_SHOWN)
    shown = postings[:limit]

    return render_template(
        "index.html",
        postings=shown,
        total=total,
        next_n=limit + PAGE_SIZE if total > limit and limit < MAX_SHOWN else None,
        page_size=PAGE_SIZE,
        remote_us_only=remote_us_only,
        ranges=RANGES,
        posted_range=posted_range,
    )


@app.route("/onboarding", methods=["GET", "POST"])
@require_login
def onboarding():
    user_id = g.user_id
    conn = get_conn()
    try:
        if request.method == "POST":
            selected = [c for c in request.form.getlist("categories") if c in ROLE_CATEGORIES]
            set_user_role_categories(conn, user_id, selected)
            set_user_keywords(conn, user_id, clean_keywords(request.form.get("keywords", "")))
            conn.commit()
            return redirect(url_for("index"))
        selected = get_user_role_categories(conn, user_id)
        keywords = get_user_keywords(conn, user_id)
    finally:
        conn.close()
    return render_template(
        "onboarding.html",
        categories=ROLE_CATEGORIES,
        selected=selected,
        keywords=", ".join(keywords),
        max_keywords=MAX_KEYWORDS,
    )


@app.route("/toggle_remote_us", methods=["POST"])
@require_login
def toggle_remote_us():
    session.permanent = True
    session["remote_us_only"] = request.form.get("remote_us_only") == "true"
    return redirect(url_for("index"))


@app.route("/set_range", methods=["POST"])
@require_login
def set_range():
    choice = request.form.get("range", DEFAULT_RANGE)
    session.permanent = True
    session["posted_range"] = choice if choice in RANGES else DEFAULT_RANGE
    return redirect(url_for("index"))


@app.route("/set_applied/<int:posting_id>", methods=["POST"])
@require_login
def set_applied_route(posting_id):
    applied = request.form.get("applied") == "true"
    conn = get_conn()
    try:
        set_applied(conn, posting_id, applied, g.user_id)
        if applied:
            log_event(conn, g.user_id, "applied", posting_id)
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("index"))


@app.route("/open/<int:posting_id>")
@require_login
def open_posting(posting_id):
    """Log that this user opened a posting, then send them on to the ATS page.
    The dashboard's job links go through here so opens can be counted."""
    conn = get_conn()
    try:
        url = get_posting_url(conn, posting_id)
        if url:
            log_event(conn, g.user_id, "opened", posting_id)
            conn.commit()
    finally:
        conn.close()
    return redirect(url) if url else redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, port=5050)
