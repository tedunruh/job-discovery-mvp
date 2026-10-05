"""Per-user engagement summary from user_events (SC-19).

    python scripts/engagement.py [days]     # default: last 14 days
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(".env.local")

from db.db import get_conn  # noqa: E402

EVENTS = ["sign_in", "visit", "opened", "applied"]


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.email,
                   count(*) FILTER (WHERE e.event_type = 'sign_in'),
                   count(*) FILTER (WHERE e.event_type = 'visit'),
                   count(*) FILTER (WHERE e.event_type = 'opened'),
                   count(*) FILTER (WHERE e.event_type = 'applied'),
                   max(e.created_at)
            FROM users u
            LEFT JOIN user_events e
                   ON e.user_id = u.id AND e.created_at > now() - make_interval(days => %s)
            GROUP BY u.id, u.email
            ORDER BY u.id
            """,
            (days,),
        )
        rows = cur.fetchall()
    conn.close()

    print(f"Engagement, last {days} days\n")
    print(f"{'user':<32}" + "".join(f"{e:>9}" for e in EVENTS) + "   last active")
    for email, *counts, last in rows:
        last_s = last.strftime("%Y-%m-%d %H:%M") if last else "-"
        print(f"{email:<32}" + "".join(f"{c:>9}" for c in counts) + f"   {last_s}")


if __name__ == "__main__":
    main()
