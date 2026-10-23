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


def ensure_conn(conn):
    """Return `conn` if it still works, else a fresh connection (and close the dead one). A
    connection that sat idle for minutes while the scanner waited on the network can be dropped
    by the server ("SSL connection has been closed unexpectedly") - callers commit before they
    wait so nothing is lost, then call this before touching the database again."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return conn
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        return get_conn()


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


def content_hash(posting):
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
    Unchanged postings are skipped entirely (see content_hash), so last_seen_at
    only moves when something about the posting changed or it reopened.

    Storage: only design roles keep their full raw payload - for everything else
    the HTML body etc. is dropped ({}), which is most of the bytes.

    backfill=True is the company's first scan: first_seen_at is set from the ATS's own
    posted date (capped at now) instead of now - and treated as ~6 months old when the ATS
    gives no date - so the "discovered within" dashboard filter doesn't present hundreds of
    long-open roles as discovered today.
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
            content_hash(p), bool(p.get("is_design")), backfill, p.get("ats_posted_at"),
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
                CASE WHEN %s THEN LEAST(COALESCE(%s::timestamptz, now() - interval '180 days'), now()) ELSE now() END,
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
    """Open postings from every company in the directory, with this user's own
    applied relevance joined in from user_postings - a posting no other user has
    touched simply has no row there, hence the LEFT JOIN + COALESCE. (Every user
    sees every company; what narrows the list is their role profile, not a
    per-user company list - see user_companies, now unused.)

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
            LEFT JOIN user_postings up ON up.posting_id = p.id AND up.user_id = %s
            WHERE p.status = 'open'
              AND (p.is_design OR p.title ~* ANY(%s::text[]))
              AND (%s::timestamptz IS NULL OR p.first_seen_at >= %s)
            ORDER BY p.first_seen_at DESC
            """,
            (user_id, keyword_patterns, since, since),
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


PROFILE_COLUMNS = "user_id, level, level_note, one_level_up, country, city, alerts_started_at, alerts_paused_at, version"


def _attach_items(profiles, items):
    for p in profiles.values():
        p["titles"], p["skills"] = [], []
    for user_id, kind, value in items:
        if user_id in profiles:
            profiles[user_id]["titles" if kind == "title" else "skills"].append(value)
    return profiles


def get_all_user_profiles(conn):
    """{user_id: profile} for everyone who has a profile row (SC-46) - loaded once per scheduler
    run. A profile is a dict: level, one_level_up, country, city, alerts_started_at,
    alerts_paused_at, version, titles, skills. People without one still use their old role
    categories and keywords."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"SELECT {PROFILE_COLUMNS} FROM user_profiles")
        profiles = {row["user_id"]: dict(row) for row in cur.fetchall()}
    if not profiles:
        return {}
    with conn.cursor() as cur:
        cur.execute("SELECT user_id, kind, value FROM user_profile_items ORDER BY value")
        return _attach_items(profiles, cur.fetchall())


def get_user_profile(conn, user_id):
    """This person's profile (see get_all_user_profiles), or None if they don't have one yet."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"SELECT {PROFILE_COLUMNS} FROM user_profiles WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
    if row is None:
        return None
    profiles = {user_id: dict(row)}
    with conn.cursor() as cur:
        cur.execute("SELECT user_id, kind, value FROM user_profile_items WHERE user_id = %s ORDER BY value", (user_id,))
        return _attach_items(profiles, cur.fetchall())[user_id]


def create_profile_from_legacy(conn, user_id, titles, alerts_started_at):
    """Create a profile for someone who has none, from titles made out of their old role
    categories and keywords (role_categories.profile_from_legacy). Idempotent: returns False and
    changes nothing if they already have one. The old tables are left exactly as they were."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO user_profiles (user_id, alerts_started_at) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
            (user_id, alerts_started_at),
        )
        if cur.rowcount == 0:
            return False
        if titles:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO user_profile_items (user_id, kind, value, source) VALUES %s ON CONFLICT DO NOTHING",
                [(user_id, "title", t, "converted") for t in titles],
            )
        return True


def legacy_alerts_start(conn, user_id):
    """When alerts count as started for someone getting a profile for the first time: when they
    onboarded (they have been getting alerts since), or now if they never finished onboarding."""
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(onboarded_at, now()) FROM users WHERE id = %s", (user_id,))
        return cur.fetchone()[0]


def save_profile(conn, user_id, *, titles, skills, level, one_level_up, city, work_types, level_note=None):
    """Save the whole profile from the profile screen (the form posts everything, not a diff) and
    mark the person onboarded. Creates the profile if they have none; alerts start now for someone
    who never onboarded, and are left as they were for everyone else. Values that were already
    there keep their source (resume, converted); new ones are 'typed'. work_types None = never chose
    (the default, Remote, applies). Caller commits."""
    started = legacy_alerts_start(conn, user_id)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_profiles (user_id, level, level_note, one_level_up, city, alerts_started_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                level = EXCLUDED.level, level_note = EXCLUDED.level_note,
                one_level_up = EXCLUDED.one_level_up, city = EXCLUDED.city,
                alerts_started_at = COALESCE(user_profiles.alerts_started_at, EXCLUDED.alerts_started_at),
                version = user_profiles.version + 1, updated_at = now()
            """,
            (user_id, level, level_note if level else None, one_level_up, city, started),
        )
        wanted = {("title", t) for t in titles} | {("skill", s) for s in skills}
        cur.execute("SELECT kind, value FROM user_profile_items WHERE user_id = %s", (user_id,))
        have = set(cur.fetchall())
        for kind, value in have - wanted:
            cur.execute("DELETE FROM user_profile_items WHERE user_id = %s AND kind = %s AND value = %s",
                        (user_id, kind, value))
        if wanted - have:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO user_profile_items (user_id, kind, value, source) VALUES %s ON CONFLICT DO NOTHING",
                [(user_id, kind, value, "typed") for kind, value in sorted(wanted - have)],
            )
        cur.execute("UPDATE users SET work_types = %s, onboarded_at = COALESCE(onboarded_at, now()) WHERE id = %s",
                    (list(work_types) if work_types is not None else None, user_id))


def set_alerts_paused(conn, user_id, paused):
    """Pause or resume this person's alerts. They need a profile row for it to live on; callers
    create one first (ensure_profile in dashboard/profile.py). Caller commits."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE user_profiles SET alerts_paused_at = CASE WHEN %s THEN COALESCE(alerts_paused_at, now()) ELSE NULL END "
            "WHERE user_id = %s",
            (bool(paused), user_id),
        )


def get_week_stats(conn, user_id):
    """The last 7 days for one person: roles that reached them, that they opened, that they applied to."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM user_postings WHERE user_id = %s AND notified_at > now() - interval '7 days'",
            (user_id,),
        )
        reached = cur.fetchone()[0]
        cur.execute(
            """
            SELECT count(DISTINCT posting_id) FILTER (WHERE event_type = 'opened'),
                   count(DISTINCT posting_id) FILTER (WHERE event_type = 'applied')
            FROM user_events WHERE user_id = %s AND created_at > now() - interval '7 days'
            """,
            (user_id,),
        )
        opened, applied = cur.fetchone()
    return {"reached": reached, "opened": opened, "applied": applied}


def save_resume(conn, user_id, filename, content):
    """Store this person's resume PDF, replacing any earlier one. Caller commits."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_resumes (user_id, filename, content, size_bytes)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                filename = EXCLUDED.filename, content = EXCLUDED.content,
                size_bytes = EXCLUDED.size_bytes, uploaded_at = now()
            """,
            (user_id, filename, psycopg2.Binary(content), len(content)),
        )


def get_resume(conn, user_id, with_content=False):
    """This person's resume as a dict (filename, size_bytes, uploaded_at, and content if asked), or None."""
    cols = "filename, size_bytes, uploaded_at" + (", content" if with_content else "")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"SELECT {cols} FROM user_resumes WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
    if row is None:
        return None
    row = dict(row)
    if with_content:
        row["content"] = bytes(row["content"])
    return row


def delete_resume(conn, user_id):
    """Remove the resume file for good, and the fields read from it that haven't been saved (they
    are in resume_parses.result). The cost rows stay, without any content. Returns whether a file
    was removed. Caller commits."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM user_resumes WHERE user_id = %s", (user_id,))
        removed = cur.rowcount > 0
        cur.execute("UPDATE resume_parses SET result = NULL WHERE user_id = %s AND result IS NOT NULL", (user_id,))
    return removed


def log_resume_parse(conn, user_id, *, model, input_tokens, output_tokens, cost_usd, price_version,
                     status, error_kind=None, request_id=None, result=None):
    """Record one call to the resume-reading model (every call, failed ones too). Returns its id.
    Caller commits."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO resume_parses (user_id, model, input_tokens, output_tokens, cost_usd, price_version,
                                       status, error_kind, request_id, result)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (user_id, model, input_tokens, output_tokens, cost_usd, price_version, status, error_kind,
             request_id, psycopg2.extras.Json(result) if result is not None else None),
        )
        return cur.fetchone()[0]


def get_resume_parse_result(conn, user_id, parse_id, max_age_hours=24):
    """The fields a recent successful read produced for this person, or None (not theirs, too old,
    failed, or wiped by deleting the resume)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT result FROM resume_parses
            WHERE id = %s AND user_id = %s AND status = 'ok' AND result IS NOT NULL
              AND created_at > now() - make_interval(hours => %s)
            """,
            (parse_id, user_id, max_age_hours),
        )
        row = cur.fetchone()
    return row[0] if row else None


def resume_usage(conn, user_id):
    """(this person's successful reads this month, everyone's spend this month in dollars), in UTC months."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FILTER (WHERE user_id = %s AND status = 'ok'), COALESCE(sum(cost_usd), 0)
            FROM resume_parses WHERE created_at >= date_trunc('month', now())
            """,
            (user_id,),
        )
        mine, spend = cur.fetchone()
    return mine, float(spend)


def set_posting_description(conn, posting_id, text):
    """Store a role's description (plain text) and when it was fetched. Caller commits."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE postings SET description = %s, description_fetched_at = now() WHERE id = %s",
            (text, posting_id),
        )


def clear_closed_descriptions(conn, grace_days=14):
    """Drop the stored description of roles that closed more than `grace_days` ago, so storage doesn't
    grow with every role Scout has ever seen. Returns how many were cleared. Caller commits."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE postings SET description = NULL
            WHERE status = 'closed' AND description IS NOT NULL
              AND last_seen_at < now() - make_interval(days => %s)
            """,
            (grace_days,),
        )
        return cur.rowcount


def get_postings_missing_description(conn, company_id, ats_posting_ids):
    """{ats_posting_id: postings.id} for these open roles of one company that have no description yet
    (NULL; an empty string means "fetched, the platform had none" and is not asked for again)."""
    if not ats_posting_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ats_posting_id, id FROM postings
            WHERE company_id = %s AND status = 'open' AND description IS NULL AND ats_posting_id = ANY(%s)
            """,
            (company_id, list(ats_posting_ids)),
        )
        return {ats_id: pid for ats_id, pid in cur.fetchall()}


def get_open_described_ids(conn, company_ids):
    """{company_id: {ats_posting_id, ...}} of open roles that already have a description (or a note that
    there is none), in one query: lets the discovered-board scan skip boards with nothing to do."""
    result = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT company_id, ats_posting_id FROM postings "
            "WHERE status = 'open' AND description IS NOT NULL AND company_id = ANY(%s)",
            (list(company_ids),),
        )
        for cid, ats_id in cur.fetchall():
            result.setdefault(cid, set()).add(ats_id)
    return result


def mark_full_ingest_done(conn, company_id):
    with conn.cursor() as cur:
        cur.execute("UPDATE companies SET full_ingest_at = now() WHERE id = %s", (company_id,))


def get_companies_to_scan(conn):
    """Every company that isn't switched off (tier 'off'). `tier` says how to scan it - see
    the tier note in schema.sql. Coverage is Scout's job, not a per-user list."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT c.id, c.name, c.ats_type, c.ats_identifier, c.full_ingest_at, c.tier, c.scan_marker,
                   sh.last_checked_at
            FROM companies c LEFT JOIN source_health sh ON sh.company_id = c.id
            WHERE c.tier <> 'off' ORDER BY c.name
            """
        )
        return cur.fetchall()


def get_all_users(conn):
    """Everyone who gets alerts. Each is matched against their own role profile
    (role_categories.matches_profile) and Work type before being notified.
    work_types: None = never chose (default applies), [] = Any."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, ntfy_topic, work_types FROM users ORDER BY id")
        return cur.fetchall()


def get_user_work_types(conn, user_id):
    with conn.cursor() as cur:
        cur.execute("SELECT work_types FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        return row[0] if row else None


def set_user_work_types(conn, user_id, work_types):
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET work_types = %s WHERE id = %s", (list(work_types), user_id))


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


# --- company directory (SC-45b) ---------------------------------------------------

def add_company_if_absent(conn, name, ats_type, ats_identifier):
    """Find or create a directory entry. Returns (company_id, created).

    Matches case-insensitively on the identifier (Ashby slugs differ only by
    case across sources - 'workhuman' vs 'Workhuman' are the same board), and
    never renames an existing company, unlike upsert_company.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM companies WHERE ats_type = %s AND lower(ats_identifier) = lower(%s)",
            (ats_type, ats_identifier),
        )
        row = cur.fetchone()
        if row:
            return row[0], False
        cur.execute(
            """
            INSERT INTO companies (name, ats_type, ats_identifier) VALUES (%s, %s, %s)
            ON CONFLICT (ats_type, ats_identifier) DO NOTHING RETURNING id
            """,
            (name, ats_type, ats_identifier),
        )
        row = cur.fetchone()
        if row:
            return row[0], True
        cur.execute("SELECT id FROM companies WHERE ats_type = %s AND ats_identifier = %s", (ats_type, ats_identifier))
        return cur.fetchone()[0], False


# --- discovered boards: lean, batched bookkeeping (SC-49) ---------------------------



def record_tail_scan_results(conn, ok_ids, failed_ids, first_scan_ids, empty_ids=()):
    """One round of batched health bookkeeping for a run's worth of discovered boards, instead
    of ~6 queries per board. ok = fetched fine; failed = request error (counted, never alerted
    per board - a few thousand boards means some are always down). first_scan_ids are marked
    scanned so later new design roles can alert."""
    with conn.cursor() as cur:
        touched = list(set(ok_ids) | set(failed_ids) | set(empty_ids))
        if touched:
            cur.execute(
                "INSERT INTO source_health (company_id) SELECT unnest(%s::int[]) ON CONFLICT (company_id) DO NOTHING",
                (touched,),
            )
        if ok_ids:
            cur.execute(
                """
                UPDATE source_health
                SET last_success_at = now(), last_checked_at = now(), consecutive_failures = 0,
                    consecutive_zero_results = 0, status = 'healthy'
                WHERE company_id = ANY(%s)
                """,
                (list(ok_ids),),
            )
        if empty_ids:
            # Returned nothing: leave success/failure state alone, but note it was checked so it
            # doesn't sit at the front of the queue forever.
            cur.execute("UPDATE source_health SET last_checked_at = now() WHERE company_id = ANY(%s)", (list(empty_ids),))
        if failed_ids:
            cur.execute(
                """
                UPDATE source_health
                SET last_checked_at = now(), consecutive_failures = consecutive_failures + 1,
                    status = CASE WHEN consecutive_failures + 1 >= %s THEN 'degraded' ELSE status END
                WHERE company_id = ANY(%s)
                """,
                (FAILURE_THRESHOLD * 8, list(failed_ids)),
            )
        if first_scan_ids:
            cur.execute("UPDATE companies SET full_ingest_at = now() WHERE id = ANY(%s)", (list(first_scan_ids),))


def get_wide_scanned_ids(conn, company_ids):
    """Which of these boards have had their first wide scan (SC-51 slice 2b), as a set of company ids.
    Also reads wide_requested_at, so the query fails (and the feature switches itself off) unless the
    whole slice-3 schema is there."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, wide_requested_at FROM companies WHERE wide_scan_at IS NOT NULL AND id = ANY(%s)",
            (list(company_ids),),
        )
        return {r[0] for r in cur.fetchall()}


def get_db_now(conn):
    """The database's clock: one clock for "when did the run load everyone's settings" and "when was a
    catch-up requested", whichever machine each ran on. clock_timestamp(), not now(): now() is the start of
    the transaction, so two things in one transaction would tie."""
    with conn.cursor() as cur:
        cur.execute("SELECT clock_timestamp()")
        return cur.fetchone()[0]


def mark_wide_scanned(conn, company_ids, settings_loaded_at=None):
    """Record that these boards have had their first wide scan: from now on a new wanted role alerts.
    Skipped for a board asked for a catch-up after `settings_loaded_at`: this run's picture of what people
    want was out of date for it, so it stays due and the next run does the quiet catch-up."""
    if company_ids:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE companies SET wide_scan_at = now()
                WHERE id = ANY(%s) AND (%s::timestamptz IS NULL OR wide_requested_at IS NULL OR wide_requested_at <= %s)
                """,
                (list(company_ids), settings_loaded_at, settings_loaded_at),
            )


def request_wide_catch_up(conn):
    """Ask every discovered board (Workday excepted: it does not store these roles yet) for another quiet
    first-style scan, because someone's settings now let in roles that were not stored. Returns how many
    boards were asked. Caller commits."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE companies SET wide_scan_at = NULL, wide_requested_at = clock_timestamp()
            WHERE tier IN ('hot', 'cold') AND ats_type <> 'workday'
            """
        )
        return cur.rowcount


def promote_hot_boards(conn):
    """A cold board that now has a design role becomes hot (scanned every run). Returns how many."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE companies c SET tier = 'hot'
            WHERE c.tier = 'cold' AND EXISTS (SELECT 1 FROM postings p WHERE p.company_id = c.id AND p.is_design)
            """
        )
        return cur.rowcount


def get_open_posting_hashes(conn, company_ids):
    """{company_id: {ats_posting_id: content_hash}} for these companies' open postings, in one
    query - lets the scanner skip every database call for a board whose roles haven't changed."""
    result = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT company_id, ats_posting_id, content_hash FROM postings WHERE status = 'open' AND company_id = ANY(%s)",
            (list(company_ids),),
        )
        for cid, ats_id, h in cur.fetchall():
            result.setdefault(cid, {})[ats_id] = h
    return result


def set_scan_markers(conn, markers):
    """Store each board's latest change marker ({company_id: marker}; None clears it)."""
    if not markers:
        return
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            "UPDATE companies c SET scan_marker = v.marker FROM (VALUES %s) AS v(id, marker) WHERE c.id = v.id",
            [(cid, m) for cid, m in markers.items()],
            template="(%s::int, %s::text)",
        )
