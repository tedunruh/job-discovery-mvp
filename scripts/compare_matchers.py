"""Old matcher vs new matcher on stored postings (SC-46). Read-only.

    python scripts/compare_matchers.py

For each person who has no profile yet, converts their role categories and keywords to titles and
compares what the old rule (role_categories.matches_profile) and the new one
(matches_profile_titles) would show across every stored open posting. Any difference is listed,
with the posting title, so it can be explained before profiles are converted for real.
Point DATABASE_URL at a Neon branch, not production. Prints user ids and public job titles only.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(".env.local")

from db.db import get_all_user_keywords, get_all_user_profiles, get_all_user_role_categories, get_conn  # noqa: E402
from role_categories import matches_profile, matches_profile_titles, profile_from_legacy  # noqa: E402

SHOW = 15


def main():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users ORDER BY id")
            user_ids = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT title, is_design FROM postings WHERE status = 'open'")
            postings = cur.fetchall()
        categories = get_all_user_role_categories(conn)
        keywords = get_all_user_keywords(conn)
        existing = get_all_user_profiles(conn)
    finally:
        conn.close()

    print(f"{len(postings)} open postings")
    differences = 0
    for user_id in user_ids:
        if user_id in existing:
            print(f"user {user_id}: already has a profile, skipped")
            continue
        cats, kws = categories.get(user_id, set()), keywords.get(user_id, [])
        titles = profile_from_legacy(cats, kws)
        old_only, new_only, both = [], [], 0
        for title, is_design in postings:
            old = matches_profile(title, is_design, cats, kws)
            new = matches_profile_titles(title, titles)
            if old and new:
                both += 1
            elif old:
                old_only.append(title)
            elif new:
                new_only.append(title)
        differences += len(old_only) + len(new_only)
        print(f"user {user_id}: both {both}, only old {len(old_only)}, only new {len(new_only)}")
        for label, rows in (("only old", old_only), ("only new", new_only)):
            for t in sorted(set(rows))[:SHOW]:
                print(f"    {label}: {t}")
    print("Identical for everyone." if differences == 0 else f"{differences} difference(s) to explain.")


if __name__ == "__main__":
    main()
