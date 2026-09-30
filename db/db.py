import os

import psycopg2
import psycopg2.extras

# Health-tracking thresholds (see source_health table).
FAILURE_THRESHOLD = 3  # consecutive transient failures before marking 'degraded'
ZERO_RESULT_THRESHOLD = 3  # consecutive anomalous zero-result cycles before 'isolated'
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


def upsert_posting(conn, company_id, posting):
    """Insert or update a posting. Returns (posting_id, is_new) where is_new is
    True if this was a brand-new posting (never seen before), False if it
    already existed and was just refreshed.

    Uses the `xmax = 0` trick: xmax is unset (0) on a freshly inserted row
    version and gets set by the UPDATE path of ON CONFLICT DO UPDATE, so it
    reliably distinguishes insert from update within the same statement.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO postings (
                company_id, ats_posting_id, title, location, remote_type,
                url, ats_posted_at, raw_json, last_seen_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (company_id, ats_posting_id) DO UPDATE SET
                title = EXCLUDED.title,
                location = EXCLUDED.location,
                remote_type = EXCLUDED.remote_type,
                url = EXCLUDED.url,
                ats_posted_at = EXCLUDED.ats_posted_at,
                raw_json = EXCLUDED.raw_json,
                last_seen_at = now(),
                status = 'open'
            RETURNING id, (xmax = 0) AS is_new
            """,
            (
                company_id,
                posting["ats_posting_id"],
                posting["title"],
                posting.get("location"),
                posting.get("remote_type"),
                posting["url"],
                posting.get("ats_posted_at"),
                psycopg2.extras.Json(posting.get("raw_json") or {}),
            ),
        )
        return cur.fetchone()


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


def get_open_postings(conn, user_id):
    """Open postings from companies this user tracks (user_companies), with
    this user's own applied/linkedin_seen_at relevance joined in from
    user_postings - a posting no other user has touched simply has no row
    there, hence the LEFT JOIN + COALESCE."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT p.id, c.name AS company_name, p.title, p.location, p.remote_type,
                   p.url, p.ats_posted_at, p.first_seen_at,
                   up.linkedin_seen_at,
                   COALESCE(up.applied, false) AS applied,
                   CASE WHEN up.linkedin_seen_at IS NOT NULL
                        THEN (up.linkedin_seen_at::date - p.first_seen_at::date)
                   END AS days_ahead_of_linkedin
            FROM postings p
            JOIN companies c ON c.id = p.company_id
            JOIN user_companies uc ON uc.company_id = c.id AND uc.user_id = %s
            LEFT JOIN user_postings up ON up.posting_id = p.id AND up.user_id = %s
            WHERE p.status = 'open'
            ORDER BY p.ats_posted_at DESC NULLS LAST
            """,
            (user_id, user_id),
        )
        return cur.fetchall()


def mark_linkedin_seen(conn, posting_id, user_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_postings (user_id, posting_id, linkedin_seen_at)
            VALUES (%s, %s, now())
            ON CONFLICT (user_id, posting_id) DO UPDATE SET
                linkedin_seen_at = COALESCE(user_postings.linkedin_seen_at, EXCLUDED.linkedin_seen_at)
            """,
            (user_id, posting_id),
        )


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


def get_tracked_companies(conn):
    """Companies at least one user actually tracks - the scraper only fetches
    these, not every row ever added to the shared companies directory."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT DISTINCT c.id, c.name, c.ats_type, c.ats_identifier
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
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE source_health
                SET last_success_at = now(), last_checked_at = now(),
                    consecutive_failures = 0, consecutive_zero_results = 0
                WHERE company_id = %s
                """,
                (company_id,),
            )
        return False, 0

    new_streak = health["consecutive_zero_results"] + 1
    is_new_isolation = new_streak >= ZERO_RESULT_THRESHOLD
    new_status = "isolated" if is_new_isolation else health["status"]
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
