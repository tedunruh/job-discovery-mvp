import os
import secrets
from datetime import datetime, timedelta, timezone

import psycopg2.extras
import requests

MAGIC_LINK_TTL_MINUTES = 15
RESEND_URL = "https://api.resend.com/emails"


def get_user_by_email(conn, email):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        return cur.fetchone()


def create_magic_link(conn, user_id, ttl_minutes=MAGIC_LINK_TTL_MINUTES):
    """Insert a fresh single-use token for this user. Returns the token."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO magic_links (token, user_id, expires_at) VALUES (%s, %s, %s)",
            (token, user_id, expires_at),
        )
    return token


def verify_magic_link(conn, token):
    """Validate and consume a token in one step. Returns the user_id on
    success, or None if the token doesn't exist, already expired, or was
    already used - callers can't distinguish which, by design (same as not
    revealing whether an email is registered)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            UPDATE magic_links
            SET used_at = now()
            WHERE token = %s AND used_at IS NULL AND expires_at > now()
            RETURNING user_id
            """,
            (token,),
        )
        row = cur.fetchone()
        return row["user_id"] if row else None


def send_magic_link_email(email, link_url):
    """Send the login link via Resend. If RESEND_API_KEY isn't set, prints
    the link instead - lets the whole flow be tested locally (and on a fresh
    Render/Railway deploy before secrets are configured) without a live
    email account."""
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        print(f"[dev] RESEND_API_KEY not set — magic link for {email}: {link_url}")
        return

    resp = requests.post(
        RESEND_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "from": os.environ.get("RESEND_FROM", "Scout <onboarding@resend.dev>"),
            "to": [email],
            "subject": "Your Scout sign-in link",
            "text": f"Sign in: {link_url}\n\nThis link expires in {MAGIC_LINK_TTL_MINUTES} minutes and works once.",
        },
        timeout=10,
    )
    resp.raise_for_status()
