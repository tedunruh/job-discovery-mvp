import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors import ashby, greenhouse, lever, workable, workday
from collectors.filters import is_design_role
from db.db import (
    finish_scheduler_run,
    get_all_user_keywords,
    get_all_user_role_categories,
    get_conn,
    get_source_health,
    get_tracked_companies,
    get_users_tracking_company,
    mark_full_ingest_done,
    mark_notified,
    mark_stale_postings_closed,
    record_fetch_failure,
    record_fetch_success,
    record_zero_result,
    start_scheduler_run,
    touch_source_health,
    upsert_postings,
)
from role_categories import matches_profile
from scheduler.heartbeat import ping_failure, ping_success
from scheduler.notify import notify_health_alerts, notify_new_postings

# The schedule is every ~15-30 min (external cron); a gap past this means runs
# were missed - cron-job.org down, GitHub dispatch delayed, or the job failing early.
MAX_RUN_GAP_MINUTES = 90

# Companies are fetched concurrently (network-bound, different hosts mostly);
# all DB work stays sequential on one connection.
FETCH_WORKERS = 8

COLLECTORS = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
    "workday": workday.fetch,
    "workable": workable.fetch,
}


def run():
    health_alerts = []
    try:
        _run(health_alerts)
    except Exception as e:
        # Anything that escapes (DB down, a bug) used to surface only in the
        # GitHub Actions log. Push it to the health channel, then re-raise so
        # the workflow still fails visibly.
        health_alerts.append(f"Collector run crashed: {type(e).__name__}: {e}")
        notify_health_alerts(health_alerts)
        ping_failure()
        raise
    if health_alerts:
        print(f"{len(health_alerts)} health alert(s) — sending")
        notify_health_alerts(health_alerts)
    # Only a run that completed counts as "alive" for the external monitor.
    ping_success()


def _fetch_company(company):
    """Runs in a worker thread. Returns (postings, None) or (None, exception).

    Tags each posting with is_design and drops the full payload of non-design
    ones straight away - every tracked company's whole board is held in memory
    at once, and only design roles keep their raw payload (see upsert_postings).
    """
    try:
        postings = COLLECTORS[company["ats_type"]](company["ats_identifier"])
    except Exception as e:
        return None, e
    for p in postings:
        p["is_design"] = is_design_role(p.get("title") or "")
        if not p["is_design"]:
            p["raw_json"] = None
    return postings, None


def _is_storable(p):
    return bool(p.get("ats_posting_id")) and bool((p.get("title") or "").strip()) and bool(p.get("url"))


def _run(health_alerts):
    conn = get_conn()
    new_postings_count = 0
    new_postings_by_user = {}  # user_id -> {"ntfy_topic": str, "postings": [...]}
    notify_failures = 0
    try:
        run_id, gap_minutes = start_scheduler_run(conn)
        conn.commit()
        if gap_minutes is not None and gap_minutes > MAX_RUN_GAP_MINUTES:
            health_alerts.append(
                f"Scheduler gap: {gap_minutes / 60:.1f}h since the previous collector run "
                f"(expected every 15-30 min). New roles in that window were discovered late."
            )
        all_user_categories = get_all_user_role_categories(conn)
        all_user_keywords = get_all_user_keywords(conn)
        companies = get_tracked_companies(conn)
        if not companies:
            print("No companies tracked by any user (user_companies is empty) — nothing to do.")
            finish_scheduler_run(conn, run_id)
            conn.commit()
            return

        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
            fetched = list(pool.map(_fetch_company, companies))

        for company, (postings, error) in zip(companies, fetched):
            company_id = company["id"]
            label = f"{company['name']} ({company['ats_type']})"
            print(f"{label}...")

            if error is not None:
                print(f"  FAILED: {error}")
                is_new_degradation, streak = record_fetch_failure(conn, company_id)
                conn.commit()
                if is_new_degradation:
                    health_alerts.append(f"{label} has failed {streak} consecutive cycles: {error}")
                continue

            storable = [p for p in postings if _is_storable(p)]
            design_count = sum(1 for p in storable if p["is_design"])
            print(f"  {len(postings)} total, {design_count} design roles")

            health = get_source_health(conn, company_id)
            if health["status"] == "isolated":
                # Isolation is sticky - a source coming back non-zero on its own
                # doesn't prove a schema issue is actually fixed. Skip writing
                # until a human clears it (db.reset_source_health).
                print("  ISOLATED — skipping write, needs manual review to clear")
                touch_source_health(conn, company_id)
                conn.commit()
                continue

            if storable:
                # Health is judged on the whole board, not the design subset: a
                # company with 400 postings returning 0 is an anomaly, one with
                # no design roles this week is not.
                record_fetch_success(conn, company_id, len(storable))
                backfill = company["full_ingest_at"] is None
                new_ids = upsert_postings(conn, company_id, storable, backfill=backfill)

                users_tracking = None
                for posting in storable:
                    posting_id = new_ids.get(str(posting["ats_posting_id"]))
                    if posting_id is None:
                        continue
                    if backfill and not posting["is_design"]:
                        # First all-roles ingest of this company: filling the table,
                        # not news. (Design roles keep their existing behavior - those
                        # were already being ingested, so a new one really is new.)
                        continue
                    new_postings_count += 1
                    if users_tracking is None:
                        # Looked up once per company (not per posting) -
                        # every posting from this company fans out to the
                        # same set of users.
                        users_tracking = get_users_tracking_company(conn, company_id)
                    for user in users_tracking:
                        if not matches_profile(
                            posting["title"],
                            posting["is_design"],
                            all_user_categories.get(user["id"], set()),
                            all_user_keywords.get(user["id"], []),
                        ):
                            continue
                        bucket = new_postings_by_user.setdefault(
                            user["id"], {"ntfy_topic": user["ntfy_topic"], "postings": []}
                        )
                        bucket["postings"].append(
                            {"company": company["name"], "title": posting["title"], "url": posting["url"]}
                        )
                        mark_notified(conn, user["id"], posting_id)
                mark_stale_postings_closed(conn, company_id, [str(p["ats_posting_id"]) for p in storable])
                if backfill:
                    mark_full_ingest_done(conn, company_id)
            else:
                # An anomalous empty result (nothing came back, or titles are
                # blank - the signature of a schema change) shouldn't mass-close
                # this company's real open postings, so mark_stale_postings_closed
                # is deliberately skipped here.
                is_new_isolation, streak = record_zero_result(conn, company_id)
                if is_new_isolation:
                    print(f"  possible schema issue: 0 postings for {streak} consecutive cycles")
                    health_alerts.append(
                        f"{label} returning 0 results for {streak}+ cycles — "
                        f"may indicate a schema change, needs manual review"
                    )

            conn.commit()
        finish_scheduler_run(conn, run_id)
        conn.commit()
    finally:
        conn.close()

    if new_postings_by_user:
        print(f"{new_postings_count} new posting(s) across {len(new_postings_by_user)} user(s) — sending notifications")
        for bucket in new_postings_by_user.values():
            if not notify_new_postings(bucket["postings"], bucket["ntfy_topic"]):
                notify_failures += 1
        if notify_failures:
            health_alerts.append(
                f"Push notification failed for {notify_failures} of {len(new_postings_by_user)} "
                f"user(s) this run — they won't be told about their new posting(s) "
                f"(still visible on the dashboard)."
            )


if __name__ == "__main__":
    run()
