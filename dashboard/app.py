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
from dashboard.filters import WORK_TYPES, work_type
from db.db import (
    SESSION_TTL_DAYS,
    create_session,
    delete_session,
    get_conn,
    get_open_postings,
    get_posting_url,
    get_user_keywords,
    get_user_profile,
    get_user_work_types,
    get_session_user,
    get_alert_target,
    get_user_role_categories,
    get_user_skill_cutoff,
    is_onboarded,
    log_event,
    log_visit,
    set_applied,
    set_user_work_types,
)
from dashboard.profile import ask_for_catch_up
from dashboard.skill_filter import apply_skill_cutoff
from dashboard.profile import register as register_profile_screens
from dashboard.resume import register as register_resume
from role_categories import (
    matches_city,
    matches_profile,
    matches_profile_titles,
    work_types_widened,
)

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

# "Posted" filter on the dashboard: key -> (menu label, pill value, window). Keyed
# on first_seen_at (when Scout found it - what each row shows as "Discovered").
# 24h is the default so the list opens on what's new.
RANGES = {
    "24h": ("24 hours", "within 24 hours", timedelta(hours=24)),
    "week": ("Last week", "within a week", timedelta(days=7)),
    "month": ("Last month", "within a month", timedelta(days=30)),
    "all": ("All time", "any time", None),
}
DEFAULT_RANGE = "week"
# What a signed-in session starts on until the person changes it (changes are remembered per
# session). Remote = remote AND in the US, as inferred by dashboard.filters.work_type.
DEFAULT_WORK_TYPES = ["remote"]
# Role categories pre-checked the first time someone onboards, so the screen starts with
# something selected; they can change it.
DEFAULT_ONBOARDING_CATEGORIES = {"product_design"}

# The dashboard renders this many roles, then offers "show more" - the table holds
# every role at every tracked company, so an unbounded list is thousands of rows.
PAGE_SIZE = 50
MAX_SHOWN = 1000

# SC-58: which of a person's roles the list shows. Roles they marked applied leave the main list; the Status filter
# brings them back. Remembered for the session, like the Posted filter.
STATUSES = {"open": "Not applied yet", "applied": "Applied", "all": "All"}
DEFAULT_STATUS = "open"

FIELD_GUIDE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "field-guide")


@app.route("/field-guide/<any(dist, css, js, fonts):folder>/<path:filename>")
def field_guide_static(folder, filename):
    """Serve the Field Guide design system (tokens, component CSS, fonts, small scripts) to templates."""
    return send_from_directory(os.path.join(FIELD_GUIDE_DIR, folder), filename)


def _field_guide_tokens():
    """Flattened Field Guide tokens for the gallery, read from tokens/*.json via
    field-guide/build.py so swatches and the type scale can't drift from the
    source. Returns {group: [(dotted.path, css var name, resolved value, description)]}."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("fg_build", os.path.join(FIELD_GUIDE_DIR, "build.py"))
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    tokens = build.flatten(build.load_tokens())
    raw = build.load_tokens()

    def describe(path):
        node = raw
        for part in path.split("."):
            node = node.get(part, {})
        return node.get("$description", "") if isinstance(node, dict) else ""

    groups = {}
    for path, (value, token_type, ext) in tokens.items():
        group = path.rsplit(".", 1)[0]
        css = build.to_css(build.resolve(value, tokens), token_type, ext)
        groups.setdefault(group, []).append((path, build.css_name(path), css, describe(path)))
    return groups


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


register_profile_screens(app, require_login)
register_resume(app, require_login)


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

    # The filter controls submit as plain GETs (/?range=week, /?work_set=1&work=remote).
    # A GET can be safely replayed - e.g. by Render's wake-up page, which reloads the
    # URL once a sleeping free-tier service is up. A POST-only filter lands on a 405.
    # work_set marks a Work type submit, since unchecking every box sends no "work".
    # remote_us_only is the old toggle's parameter, still honored for saved links.
    if {"range", "work_set", "remote_us_only", "status"} & request.args.keys():
        session.permanent = True
        session.pop("just_applied", None)  # changing a filter settles the row that was kept in place (SC-58)
        if request.args.get("range") in RANGES:
            session["posted_range"] = request.args["range"]
        if request.args.get("status") in STATUSES:
            session["status"] = request.args["status"]
        chosen = None
        if "work_set" in request.args:
            chosen = [w for w in WORK_TYPES if w in request.args.getlist("work")]
        elif "remote_us_only" in request.args:
            chosen = ["remote"] if request.args["remote_us_only"] == "true" else []
        if chosen is not None:
            session["work_types"] = chosen
            # Saved on the person, not just the session: alerts are decided by the collector,
            # which can't see a cookie, and should follow the same Work type as the dashboard.
            conn = get_conn()
            try:
                before = get_user_work_types(conn, user_id)
                set_user_work_types(conn, user_id, chosen)
                conn.commit()
                if work_types_widened(before, chosen):
                    ask_for_catch_up(conn)  # roles the old choice kept out are picked up quietly (SC-51)
            finally:
                conn.close()
        return redirect(url_for("index"))

    posted_range = session.get("posted_range", DEFAULT_RANGE)
    if posted_range not in RANGES:
        posted_range = DEFAULT_RANGE
    window = RANGES[posted_range][2]
    cutoff = datetime.now(timezone.utc) - window if window is not None else None

    conn = get_conn()
    try:
        if not is_onboarded(conn, user_id):
            return redirect(url_for("start"))
        log_visit(conn, user_id)
        conn.commit()
        role_categories = get_user_role_categories(conn, user_id)
        keywords = get_user_keywords(conn, user_id)
        profile = get_user_profile(conn, user_id)  # SC-46; None = still on categories and keywords
        if profile is not None:
            keywords = profile["titles"]  # the SQL pre-filter admits non-design roles by these
        saved_work_types = get_user_work_types(conn, user_id)
        if saved_work_types is None and ("work_types" in session or "remote_us_only" in session):
            # A choice made before it was stored on the person: save it, so alerts match it too.
            saved_work_types = selected_work_types(None)
            set_user_work_types(conn, user_id, saved_work_types)
            conn.commit()
        postings = get_open_postings(conn, user_id, since=cutoff, keywords=keywords)
    finally:
        conn.close()

    work_types = selected_work_types(saved_work_types)
    if work_types and len(work_types) < len(WORK_TYPES):  # none or all checked = Any
        postings = [p for p in postings if work_type(p["location"], p["remote_type"]) in work_types]
    if profile is not None:
        postings = [
            p for p in postings
            if matches_profile_titles(p["title"], profile["titles"], profile["level"], profile["one_level_up"])
            and matches_city(work_type(p["location"], p["remote_type"]), p["location"], profile["city"])
        ]
    else:
        postings = [
            p for p in postings
            if matches_profile(p["title"], p["is_design"], role_categories, keywords)
        ]

    # SC-51 slice 4: with a minimum number of skills set, roles whose text mentions fewer are not shown (roles
    # with no stored text always are). Nothing is looked up for anyone without a minimum.
    skills_all = request.args.get("skills") == "all"
    skills_under = 0
    if profile is not None and profile["skills"]:
        conn = get_conn()
        try:
            cutoff = get_user_skill_cutoff(conn, user_id)
            postings, skills_under = apply_skill_cutoff(conn, postings, profile["skills"], cutoff, show_all=skills_all)
        finally:
            conn.close()

    # SC-58: Not applied yet (the default), Applied, or All. The job the person has just marked applied stays in
    # place for one more page load, checked and saying so, so the click can be undone; it leaves on the next refresh.
    status = session.get("status", DEFAULT_STATUS)
    if status not in STATUSES:
        status = DEFAULT_STATUS
    just_applied = session.pop("just_applied", None)
    wanted = [p for p in postings if status == "all" or bool(p["applied"]) == (status == "applied")]
    wanted_ids = {p["id"] for p in wanted}
    visible = [p for p in postings if p["id"] in wanted_ids or (status == "open" and p["id"] == just_applied and p["applied"])]
    for p in visible:
        p["just_applied"] = status == "open" and p["id"] == just_applied and bool(p["applied"])
    postings = visible

    total = len(wanted)
    limit = min(max(request.args.get("n", PAGE_SIZE, type=int), PAGE_SIZE), MAX_SHOWN)
    # the kept row is a confirmation, not one of the roles: it must not use up a place in the list
    shown, real = [], 0
    for p in postings:
        if not p.get("just_applied"):
            if real == limit:
                break
            real += 1
        shown.append(p)

    return render_template(
        "index.html",
        postings=shown,
        total=total,
        next_n=limit + PAGE_SIZE if total > limit and limit < MAX_SHOWN else None,
        page_size=PAGE_SIZE,
        work_types=work_types,
        work_label=work_type_label(work_types),
        work_type_options=WORK_TYPES,
        ranges=RANGES,
        posted_range=posted_range,
        skills_under=skills_under,
        skills_all=skills_all,
        statuses=STATUSES,
        status=status,
        shown_count=sum(1 for p in shown if not p.get("just_applied")),
        keep_n=limit if limit > PAGE_SIZE else None,
    )


def selected_work_types(saved):
    """Checked Work type options, in display order. `saved` is what's stored on the person
    (None = never chosen). Sessions from before it was stored fall back to the cookie, and the
    old remote_us_only flag maps to Remote; nothing at all means the default (Remote)."""
    if saved is not None:
        return [w for w in WORK_TYPES if w in saved]
    if "work_types" in session:
        return [w for w in WORK_TYPES if w in session["work_types"]]
    if "remote_us_only" in session:  # an explicit choice made with the old toggle
        return ["remote"] if session["remote_us_only"] else []
    return list(DEFAULT_WORK_TYPES)


def work_type_label(work_types):
    """Pill text for the Work type filter: "Any" when none or all are checked,
    otherwise the checked options' labels ("Remote, Hybrid")."""
    if not work_types or len(work_types) == len(WORK_TYPES):
        return "Any"
    return ", ".join(WORK_TYPES[w] for w in work_types)


@app.route("/field-guide")
@require_login
def field_guide_gallery():
    """The component gallery: every Field Guide token and component, rendered
    live from the shared CSS and macros. The catalog of approved controls."""
    return render_template("field_guide.html", tokens=_field_guide_tokens())


@app.route("/onboarding")
@require_login
def onboarding():
    """Role picking moved to the profile screen (SC-46); old links and bookmarks still land there."""
    return redirect(url_for("profile"))


@app.route("/toggle_remote_us", methods=["POST"])
@require_login
def toggle_remote_us():
    session.permanent = True
    session["work_types"] = ["remote"] if request.form.get("remote_us_only") == "true" else []
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
    # SC-58: the row stays where it is for one more page load, checked, so the click can be undone (a new answer
    # settles the previous one). Come back to the same row, still showing as many roles as before.
    if applied:
        session["just_applied"] = posting_id
    else:
        session.pop("just_applied", None)
    params = {}
    n = request.form.get("n", type=int)
    if n and n > PAGE_SIZE:
        params["n"] = min(n, MAX_SHOWN)
    if request.form.get("skills") == "all":
        params["skills"] = "all"
    return redirect(url_for("index", _anchor=f"posting-{posting_id}", **params))


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


@app.route("/a/<token>")
def open_from_alert(token):
    """A tap on a push notification (SC-55). The link is private to one person and one job, so no sign-in is needed
    (a phone often has no session): log that they opened it, then send them on to the job's own page. A link
    preview that only asks for the headers (HEAD) is forwarded without counting. Unknown token: the dashboard."""
    conn = get_conn()
    try:
        target = get_alert_target(conn, token)
        if target and request.method != "HEAD":
            user_id, posting_id, _ = target
            log_event(conn, user_id, "opened_alert", posting_id)
            conn.commit()
    finally:
        conn.close()
    return redirect(target[2]) if target and target[2] else redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, port=5050)
