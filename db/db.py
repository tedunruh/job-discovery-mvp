import os

import psycopg2
import psycopg2.extras


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
    """Insert or update a posting. Returns True if this was a brand-new posting
    (never seen before), False if it already existed and was just refreshed.

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
            RETURNING (xmax = 0) AS is_new
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
        return cur.fetchone()[0]


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


def get_open_postings(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT p.id, c.name AS company_name, p.title, p.location, p.remote_type,
                   p.url, p.ats_posted_at, p.first_seen_at, p.linkedin_seen_at,
                   CASE WHEN p.linkedin_seen_at IS NOT NULL
                        THEN (p.linkedin_seen_at::date - p.first_seen_at::date)
                   END AS days_ahead_of_linkedin
            FROM postings p
            JOIN companies c ON c.id = p.company_id
            WHERE p.status = 'open'
            ORDER BY p.ats_posted_at DESC NULLS LAST
            """
        )
        return cur.fetchall()


def mark_linkedin_seen(conn, posting_id):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE postings SET linkedin_seen_at = now() WHERE id = %s AND linkedin_seen_at IS NULL",
            (posting_id,),
        )
