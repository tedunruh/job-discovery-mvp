import hashlib
import json
import os
import re
import secrets

import psycopg2
import psycopg2.extras

# Health-tracking thresholds (see source_health table).
FAILURE_THRESHOLD = 3  # consecutive transient failures before marking 'degraded'
ZERO_RESULT_THRESHOLD = 3  # consecutive anomalous zero-result cycles before 'isolated'
SESSION_TTL_DAYS = 30  # sliding: renewed on use
SESSION_RENEW_AFTER_SECONDS = 3600  # don't write on every request
EMA_ALPHA = 0.3  # weight given to the newest observation in the rolling baseline


def get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def upsert_company(conn, name, ats_type, ats_identifier):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO companies (name, ats_type, ats_identifier)
            VALUES (%s, %s, %s)
            ON CONFLICT (ats_type, ats_identifier) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            (name, ats_type, ats_identifier),
        )
        return cur.fetchone()[0]


def _content_hash(posting):
    """Fingerprint of what we store. Unchanged postings (same hash) aren't
    rewritten, so a cycle that finds nothing new does almost no writes."""
    core = [
        posting["title"], posting.get("location"), posting.get("remote_type"),
        # Date only: Workday derives its posted time from relative text ("3 days ago"),
        # so the seconds differ on every fetch and would make every row look changed.
        posting["url"], str(posting.get("ats_posted_at") or "")[:10],
    ]
    if posting.get("is_design"):
        core.append(posting.get("raw_json"))  # full payload is kept (and so tracked) for design roles only
    return hashlib.sha256(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()


def upsert_postings(conn, company_id, postings, backfill=False):
    """Insert/refresh every posting from one company's fetch in a single batched
    statement. Each posting dict carries is_design (set by the collector).

    Returns {ats_posting_id: postings.id} for the postings that were brand new
    (never seen before).
    Unchanged postings are skipped entirely (see _content_hash), so last_seen_at
    only moves when something about the posting changed or it reopened.

    Storage: only design roles keep their full raw payload - for everything else
    the HTML body etc. is dropped ({}), which is most of the bytes.

    backfill=True is the company's first all-roles ingest: first_seen_at is set
    from the ATS's own posted date (capped at now) instead of now, so the
    "discovered within" dashboard filter doesn't treat hundreds of long-open
    roles as discovered today.
    """
    by_id = {str(p["ats_posting_id"]): p for p in postings if p.get("ats_posting_id") is not None}
    if not by_id:
        return {}
    rows = []
    for ats_id, p in by_id.items():
        rows.append((
            company_id, ats_id, p["title"], p.get("location"), p.get("remote_type"),
            p["url"], p.get("ats_posted_at"),
            psycopg2.extras.Json((p.get("raw_json") or {}) if p.get("is_design") else {}),
            _content_hash(p), bool(p.get("is_design")), backfill, p.get("ats_posted_at"),
        ))
    new_ids = {}
    with conn.cursor() as cur:
        returned = psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO postings (
                company_id, ats_posting_id, title, location, remote_type, url,
                ats_posted_at, raw_json, content_hash, is_design, first_seen_at, last_seen_at
            )
            VALUES %s
            ON CONFLICT (company_id, ats_posting_id) DO UPDATE SET
                title = EXCLUDED.title,
                location = EXCLUDED.location,
                remote_type = EXCLUDED.remote_type,
                url = EXCLUDED.url,
                ats_posted_at = EXCLUDED.ats_posted_at,
                raw_json = EXCLUDED.raw_json,
                content_hash = EXCLUDED.content_hash,
                is_design = EXCLUDED.is_design,
                last_seen_at = now(),
                status = 'open'
            WHERE postings.content_hash IS DISTINCT FROM EXCLUDED.content_hash
               OR postings.status <> 'open'
            RETURNING id, ats_posting_id, (xmax = 0) AS is_new
            """,
            rows,
            template="""(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                CASE WHEN %s THEN LEAST(COALESCE(%s::timestamptz, now()), now()) ELSE now() END,
                now())""",
            page_size=500,
            fetch=True,
        )
        for posting_id, ats_id, is_new in returned:
            if is_new:
                new_ids[ats_id] = posting_id
    return new_ids


def mark_stale_postings_closed(conn, company_id, seen_ats_posting_ids):
    """Flip postings to 'closed' if this run's fetch no longer includes them."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE postings
            SET status = 'closed'
            WHERE company_id = %s AND status = 'open' AND ats_posting_id != ALL(%s)
            """,
            (company_id, seen_ats_posting_ids or ["__none__"]),
        )


def get_open_postings(conn, user_id, since=None, keywords=()):
    """Open postings from companies this user tracks (user_companies), with
    this user's own applied relevance joined in from user_postings - a
    posting no other user has touched simply has no row there, hence the
    LEFT JOIN + COALESCE.

    The table now holds every role, so rows are narrowed in SQL before they
    reach Python: design roles always, other roles only if the title matches one
    of the user's keywords (a coarse pre-filter; role_categories.matches_profile
    makes the exact call), and optionally only those first seen since `since`.
    """
    keyword_patterns = [r"\y" + re.escape(kw) + r"\y" for kw in keywords]
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT p.id, c.name AS company_name, c.ats_type, p.title, p.location, p.remote_type,
                   p.url, p.first_seen_at, p.is_design,
                   COALESCE(up.applied, false) AS applied
            FROM postings p
            JOIN companies c ON c.id = p.company_id
            JOIN user_companies uc ON uc.company_id = c.id AND uc.user_id = %s
            LEFT JOIN user_postings up ON up.posting_id = p.id AND up.user_id = %s
            WHERE p.status = 'open'
              AND (p.is_design OR p.title ~* ANY(%s::text[]))
              AND (%s::timestamptz IS NULL OR p.first_seen_at >= %s)
            ORDER BY p.first_seen_at DESC
            """,
            (user_id, user_id, keyword_patterns, since, since),
        )
        return cur.fetchall()


def set_applied(conn, posting_id, applied, user_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_postings (user_id, posting_id, applied)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, posting_id) DO UPDATE SET applied = EXCLUDED.applied
            """,
            (user_id, posting_id, applied),
        )


def is_onboarded(conn, user_id):
    with conn.cursor() as cur:
        cur.execute("SELECT onboarded_at IS NOT NULL FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        return bool(row and row[0])


def get_user_role_categories(conn, user_id):
    """Set of role-category keys (role_categories.ROLE_CATEGORIES) this user
    picked during onboarding. Empty set means no preference - callers treat
    that as "show everything" (see role_categories.matches_categories)."""
    with conn.cursor() as cur:
        cur.execute("SELECT category FROM user_role_interests WHERE user_id = %s", (user_id,))
        return {row[0] for row in cur.fetchall()}


def set_user_role_categories(conn, user_id, categories):
    """Replace this user's role-category picks wholesale (the onboarding
    form posts the full current selection each time, not a diff) and mark
    them onboarded in the same transaction."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM user_role_interests WHERE user_id = %s", (user_id,))
        if categories:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO user_role_interests (user_id, category) VALUES %s",
                [(user_id, category) for category in categories],
            )
        cur.execute(
            "UPDATE users SET onboarded_at = COALESCE(onboarded_at, now()) WHERE id = %s",
            (user_id,),
        )


def get_all_user_role_categories(conn):
    """role categories for every user with at least one pick, as {user_id:
    set(category)} - loaded once per scheduler run rather than per-user
    per-company, since the whole table is tiny at this scale."""
    with conn.cursor() as cur:
        cur.execute("SELECT user_id, category FROM user_role_interests")
        result = {}
        for user_id, category in cur.fetchall():
            result.setdefault(user_id, set()).add(category)
        return result


def get_user_keywords(conn, user_id):
    """This user's non-design keyword profile, in a stable order."""
    with conn.cursor() as cur:
        cur.execute("SELECT keyword FROM user_role_keywords WHERE user_id = %s ORDER BY keyword", (user_id,))
        return [row[0] for row in cur.fetchall()]


def set_user_keywords(conn, user_id, keywords):
    """Replace this user's keyword profile wholesale (callers pass the cleaned list)."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM user_role_keywords WHERE user_id = %s", (user_id,))
        if keywords:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO user_role_keywords (user_id, keyword) VALUES %s",
                [(user_id, kw) for kw in keywords],
            )


def get_all_user_keywords(conn):
    """{user_id: [keyword, ...]} for every user with a profile - loaded once per
    scheduler run, like get_all_user_role_categories."""
    with conn.cursor() as cur:
        cur.execute("SELECT user_id, keyword FROM user_role_keywords")
        result = {}
        for user_id, keyword in cur.fetchall():
            result.setdefault(user_id, []).append(keyword)
        return result


def mark_full_ingest_done(conn, company_id):
    with conn.cursor() as cur:
        cur.execute("UPDATE companies SET full_ingest_at = now() WHERE id = %s", (company_id,))


def get_tracked_companies(conn):
    """Companies at least one user actually tracks - the scraper only fetches
    these, not every row ever added to the shared companies directory."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT DISTINCT c.id, c.name, c.ats_type, c.ats_identifier, c.full_ingest_at
            FROM companies c
            JOIN user_companies uc ON uc.company_id = c.id
            ORDER BY c.name
            """
        )
        return cur.fetchall()


def get_users_tracking_company(conn, company_id):
    """Users who track this company (user_companies), for per-user notification
    fan-out - each gets notified only about companies they actually follow."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT u.id, u.ntfy_topic
            FROM users u
            JOIN user_companies uc ON uc.user_id = u.id
            WHERE uc.company_id = %s
            """,
            (company_id,),
        )
        return cur.fetchall()


def mark_notified(conn, user_id, posting_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_postings (user_id, posting_id, notified_at)
            VALUES (%s, %s, now())
            ON CONFLICT (user_id, posting_id) DO UPDATE SET
                notified_at = COALESCE(user_postings.notified_at, EXCLUDED.notified_at)
            """,
            (user_id, posting_id),
        )


def get_source_health(conn, company_id):
    """Fetch (creating if needed) this company's health row."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "INSERT INTO source_health (company_id) VALUES (%s) ON CONFLICT (company_id) DO NOTHING",
            (company_id,),
        )
        cur.execute("SELECT * FROM source_health WHERE company_id = %s", (company_id,))
        return cur.fetchone()


def record_fetch_success(conn, company_id, design_role_count):
    """Call when a fetch succeeds with a non-anomalous (non-empty) result.
    Updates the rolling baseline and clears failure/zero-result streaks.
    Caller must not call this while the source is 'isolated' - see run_collectors.py.
    """
    health = get_source_health(conn, company_id)
    old_avg = health["typical_posting_count"]
    new_avg = (
        design_role_count
        if old_avg is None
        else (old_avg * (1 - EMA_ALPHA) + design_role_count * EMA_ALPHA)
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE source_health
            SET last_success_at = now(), last_checked_at = now(),
                consecutive_failures = 0, consecutive_zero_results = 0,
                status = 'healthy', typical_posting_count = %s
            WHERE company_id = %s
            """,
            (new_avg, company_id),
        )


def record_zero_result(conn, company_id):
    """Call when a fetch succeeds but returns 0 design-role postings.

    If this source has no established baseline (or has always been zero), this
    is treated as a normal healthy check-in, not an anomaly. If it normally
    returns postings, consecutive zero-result cycles accumulate and cross
    ZERO_RESULT_THRESHOLD flips status to 'isolated'.

    Returns (is_new_isolation, consecutive_zero_results).
    """
    health = get_source_health(conn, company_id)
    baseline = health["typical_posting_count"]
    if baseline is None or baseline <= 0:
        # The fetch itself succeeded, so a failure-driven 'degraded' is resolved.
        # (Callers never reach here for 'isolated' - run_collectors skips those.)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE source_health
                SET last_success_at = now(), last_checked_at = now(),
                    consecutive_failures = 0, consecutive_zero_results = 0,
                    status = 'healthy'
                WHERE company_id = %s
                """,
                (company_id,),
            )
        return False, 0

    new_streak = health["consecutive_zero_results"] + 1
    is_new_isolation = new_streak >= ZERO_RESULT_THRESHOLD
    new_status = "isolated" if is_new_isolation else ("healthy" if health["status"] == "degraded" else health["status"])
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE source_health
            SET last_checked_at = now(), consecutive_failures = 0,
                consecutive_zero_results = %s, status = %s
            WHERE company_id = %s
            """,
            (new_streak, new_status, company_id),
        )
    return is_new_isolation, new_streak


def record_fetch_failure(conn, company_id):
    """Call when a fetch raises after retries are exhausted (transient failure).
    Returns (is_new_degradation, consecutive_failures)."""
    health = get_source_health(conn, company_id)
    new_streak = health["consecutive_failures"] + 1
    is_new_degradation = new_streak >= FAILURE_THRESHOLD and health["status"] != "degraded"
    new_status = "degraded" if new_streak >= FAILURE_THRESHOLD else health["status"]
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE source_health
            SET last_checked_at = now(), consecutive_failures = %s, status = %s
            WHERE company_id = %s
            """,
            (new_streak, new_status, company_id),
        )
    return is_new_degradation, new_streak


def touch_source_health(conn, company_id):
    """Record that an isolated source was checked this cycle, without letting
    the result silently clear isolation - that needs a manual reset."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE source_health SET last_checked_at = now() WHERE company_id = %s",
            (company_id,),
        )


def reset_source_health(conn, company_id):
    """Manually clear a source back to healthy after reviewing an isolation."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE source_health
            SET status = 'healthy', consecutive_failures = 0, consecutive_zero_results = 0
            WHERE company_id = %s
            """,
            (company_id,),
        )


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn, user_id):
    """New login session for this user. Returns the raw token for the cookie;
    only its hash is stored."""
    token = secrets.token_urlsafe(32)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_sessions (token_hash, user_id, expires_at)
            VALUES (%s, %s, now() + make_interval(days => %s))
            """,
            (_hash_token(token), user_id, SESSION_TTL_DAYS),
        )
    return token


def get_session_user(conn, token):
    """Validate a session token. Returns (user_id, renewed): user_id is None if
    the token is unknown or expired. renewed is True when this call slid the
    expiry forward (the caller then re-sends the cookie so its max-age slides
    too). Renewal is throttled to once per SESSION_RENEW_AFTER_SECONDS."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE user_sessions
            SET last_seen_at = now(), expires_at = now() + make_interval(days => %s)
            WHERE token_hash = %s AND expires_at > now()
              AND last_seen_at < now() - make_interval(secs => %s)
            RETURNING user_id
            """,
            (SESSION_TTL_DAYS, _hash_token(token), SESSION_RENEW_AFTER_SECONDS),
        )
        row = cur.fetchone()
        if row:
            return row[0], True
        cur.execute(
            "SELECT user_id FROM user_sessions WHERE token_hash = %s AND expires_at > now()",
            (_hash_token(token),),
        )
        row = cur.fetchone()
        return (row[0] if row else None), False


def delete_session(conn, token):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM user_sessions WHERE token_hash = %s", (_hash_token(token),))


def revoke_user_sessions(conn, user_id):
    """Log a user out everywhere (e.g. removing an alpha tester). Returns the
    number of sessions ended."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM user_sessions WHERE user_id = %s", (user_id,))
        return cur.rowcount


def log_event(conn, user_id, event_type, posting_id=None):
    """Append an engagement event (see user_events in schema.sql)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO user_events (user_id, event_type, posting_id) VALUES (%s, %s, %s)",
            (user_id, event_type, posting_id),
        )


def get_posting_url(conn, posting_id):
    with conn.cursor() as cur:
        cur.execute("SELECT url FROM postings WHERE id = %s", (posting_id,))
        row = cur.fetchone()
        return row[0] if row else None


def log_visit(conn, user_id, gap_minutes=30):
    """Log a dashboard visit unless this user already has one in the last
    gap_minutes - the dashboard reloads after every filter toggle or applied
    click, which shouldn't each count as a visit."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_events (user_id, event_type)
            SELECT %s, 'visit'
            WHERE NOT EXISTS (
                SELECT 1 FROM user_events
                WHERE user_id = %s AND event_type = 'visit'
                  AND created_at > now() - make_interval(mins => %s)
            )
            """,
            (user_id, user_id, gap_minutes),
        )


def start_scheduler_run(conn):
    """Record that a collector run started. Returns (run_id, minutes since the
    previous run started, or None if there was none)."""
    with conn.cursor() as cur:
        cur.execute("SELECT EXTRACT(EPOCH FROM now() - max(started_at)) / 60 FROM scheduler_runs")
        gap = cur.fetchone()[0]
        cur.execute("INSERT INTO scheduler_runs DEFAULT VALUES RETURNING id")
        run_id = cur.fetchone()[0]
        cur.execute("DELETE FROM scheduler_runs WHERE started_at < now() - interval '30 days'")
    return run_id, (float(gap) if gap is not None else None)


def finish_scheduler_run(conn, run_id):
    with conn.cursor() as cur:
        cur.execute("UPDATE scheduler_runs SET finished_at = now() WHERE id = %s", (run_id,))
