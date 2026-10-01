import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors import ashby, greenhouse, lever, workable, workday
from collectors.filters import is_design_role
from db.db import (
    get_all_user_role_categories,
    get_conn,
    get_source_health,
    get_tracked_companies,
    get_users_tracking_company,
    mark_notified,
    mark_stale_postings_closed,
    record_fetch_failure,
    record_fetch_success,
    record_zero_result,
    touch_source_health,
    upsert_posting,
)
from role_categories import matches_categories
from scheduler.notify import notify_health_alerts, notify_new_postings

COLLECTORS = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
    "workday": workday.fetch,
    "workable": workable.fetch,
}


def run():
    conn = get_conn()
    new_postings_count = 0
    new_postings_by_user = {}  # user_id -> {"ntfy_topic": str, "postings": [...]}
    health_alerts = []
    try:
        all_user_categories = get_all_user_role_categories(conn)
        companies = get_tracked_companies(conn)
        if not companies:
            print("No companies tracked by any user (user_companies is empty) — nothing to do.")
            return
        for company in companies:
            fetch = COLLECTORS[company["ats_type"]]
            company_id = company["id"]
            label = f"{company['name']} ({company['ats_type']})"
            print(f"Fetching {label}...")

            try:
                postings = fetch(company["ats_identifier"])
            except Exception as e:
                print(f"  FAILED: {e}")
                is_new_degradation, streak = record_fetch_failure(conn, company_id)
                conn.commit()
                if is_new_degradation:
                    health_alerts.append(f"{label} has failed {streak} consecutive cycles: {e}")
                continue

            design_postings = [p for p in postings if is_design_role(p["title"])]
            print(f"  {len(postings)} total, {len(design_postings)} design roles")

            health = get_source_health(conn, company_id)
            if health["status"] == "isolated":
                # Isolation is sticky - a source coming back non-zero on its own
                # doesn't prove a schema issue is actually fixed. Skip writing
                # until a human clears it (db.reset_source_health).
                print("  ISOLATED — skipping write, needs manual review to clear")
                touch_source_health(conn, company_id)
                conn.commit()
                continue

            if design_postings:
                record_fetch_success(conn, company_id, len(design_postings))
                users_tracking = None
                for posting in design_postings:
                    posting_id, is_new = upsert_posting(conn, company_id, posting)
                    if is_new:
                        new_postings_count += 1
                        if users_tracking is None:
                            # Looked up once per company (not per posting) -
                            # every posting from this company fans out to the
                            # same set of users.
                            users_tracking = get_users_tracking_company(conn, company_id)
                        for user in users_tracking:
                            user_categories = all_user_categories.get(user["id"], set())
                            if not matches_categories(posting["title"], user_categories):
                                continue
                            bucket = new_postings_by_user.setdefault(
                                user["id"], {"ntfy_topic": user["ntfy_topic"], "postings": []}
                            )
                            bucket["postings"].append(
                                {
                                    "company": company["name"],
                                    "title": posting["title"],
                                    "url": posting["url"],
                                }
                            )
                            mark_notified(conn, user["id"], posting_id)
                mark_stale_postings_closed(
                    conn, company_id, [p["ats_posting_id"] for p in design_postings]
                )
            else:
                # An anomalous empty result shouldn't mass-close this company's
                # real open postings, so mark_stale_postings_closed is
                # deliberately skipped here - only called in the branch above.
                is_new_isolation, streak = record_zero_result(conn, company_id)
                if is_new_isolation:
                    print(f"  possible schema issue: 0 design roles for {streak} consecutive cycles")
                    health_alerts.append(
                        f"{label} returning 0 results for {streak}+ cycles — "
                        f"may indicate a schema change, needs manual review"
                    )

            conn.commit()
    finally:
        conn.close()

    if new_postings_count:
        print(f"{new_postings_count} new posting(s) across {len(new_postings_by_user)} user(s) — sending notifications")
        for bucket in new_postings_by_user.values():
            notify_new_postings(bucket["postings"], bucket["ntfy_topic"])

    if health_alerts:
        print(f"{len(health_alerts)} health alert(s) — sending")
        notify_health_alerts(health_alerts)


if __name__ == "__main__":
    run()
