"""Create a profile (SC-46) for everyone who doesn't have one, from their role categories and keywords.

    python scripts/convert_profiles.py            # dry run: shows what would be created
    python scripts/convert_profiles.py --apply    # writes it

Idempotent, and the old tables are never touched. Alerts keep going for everyone who gets them
today: alerts_started_at is set to when the account was created, so people who haven't finished
the old onboarding still receive them. Run only once the new profile screens are live: until then
the old onboarding form writes the old tables, which a converted profile would ignore.
Prints user ids and counts only (the repo is public; no emails or titles of people's choosing).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(".env.local")

from db.db import (  # noqa: E402
    create_profile_from_legacy,
    get_all_user_keywords,
    get_all_user_profiles,
    get_all_user_role_categories,
    get_conn,
)
from role_categories import profile_from_legacy  # noqa: E402


def main():
    apply = "--apply" in sys.argv
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, created_at FROM users ORDER BY id")
            users = cur.fetchall()
        categories = get_all_user_role_categories(conn)
        keywords = get_all_user_keywords(conn)
        existing = get_all_user_profiles(conn)
        made = 0
        for user_id, created_at in users:
            if user_id in existing:
                print(f"user {user_id}: already has a profile, skipped")
                continue
            titles = profile_from_legacy(categories.get(user_id), keywords.get(user_id))
            print(f"user {user_id}: {'creating' if apply else 'would create'} a profile with {len(titles)} titles, alerts on since account creation")
            if apply and create_profile_from_legacy(conn, user_id, titles, created_at):
                made += 1
        if apply:
            conn.commit()
            print(f"Created {made} profile(s).")
        else:
            print("Dry run: nothing written. Re-run with --apply.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
