import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors import ashby, greenhouse, lever, workable, workday
from collectors.filters import is_design_role
from dashboard.filters import work_type
from db.db import (
    content_hash,
    finish_scheduler_run,
    get_all_user_keywords,
    get_all_user_role_categories,
    get_all_users,
    get_companies_to_scan,
    get_conn,
    get_open_posting_hashes,
    get_source_health,
    mark_full_ingest_done,
    mark_notified,
    mark_stale_postings_closed,
    promote_hot_boards,
    record_fetch_failure,
    record_fetch_success,
    record_tail_scan_results,
    record_zero_result,
    start_scheduler_run,
    touch_source_health,
    upsert_postings,
)
from role_categories import matches_profile, matches_work_type
from scheduler.heartbeat import ping_failure, ping_success
from scheduler.notify import notify_health_alerts, notify_new_postings

# The schedule is every ~15 min (external cron); a gap past this means runs
# were missed - cron-job.org down, GitHub dispatch delayed, or the job failing early.
MAX_RUN_GAP_MINUTES = 90

# The core companies (tier 'core') are fetched concurrently, in full: network-bound,
# different hosts mostly; all DB work stays sequential on one connection.
FETCH_WORKERS = 8

# Discovered boards (tiers 'hot' / 'cold', SC-49) are scanned for design roles only.
# Hot boards (have had a design role) every run; cold boards in a rotating slice so each
# is checked about every COLD_SLICES x 15 min (~4h) - a new design role there is found
# within hours, and the board turns hot.
RUN_INTERVAL_SECONDS = 15 * 60
COLD_SLICES = 16
TAIL_WORKERS = 12
TAIL_CHUNK = 240  # boards fetched per batch, so the time budget can stop the run between batches
# Per-ATS cap on simultaneous requests, so a few thousand boards don't hammer one host.
TAIL_PER_ATS = {"greenhouse": 8, "ashby": 5, "lever": 4, "workable": 4, "workday": 2}
TAIL_COMMIT_EVERY = 100
# Past this, don't start more tail work this run (the next run picks it up); keeps a run
# well inside the 15 min cadence even if an ATS is slow.
TAIL_TIME_BUDGET_SECONDS = 600
# Alert the operator when this share of scanned discovered boards failed (individual boards
# fail all the time at this scale; a spike means something systemic).
TAIL_FAILURE_ALERT_RATIO = 0.10
TAIL_FAILURE_ALERT_MIN = 30

COLLECTORS = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
    "workday": workday.fetch,
    "workable": workable.fetch,
}
# Same, but cheaper where the ATS allows it (Greenhouse without full job descriptions).
TAIL_COLLECTORS = dict(COLLECTORS, greenhouse=lambda ident: greenhouse.fetch(ident, light=True))
_ats_slots = {ats: threading.Semaphore(n) for ats, n in TAIL_PER_ATS.items()}


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


def _fetch_tail(company):
    """Worker-thread fetch for a discovered board. Returns (result, None) or (None, exception)
    where result = {"design": [storable design postings], "alive": any real posting came
    back}. Only design postings are kept; everything else is dropped immediately."""
    ats = company["ats_type"]
    with _ats_slots.get(ats, threading.Semaphore(2)):
        try:
            postings = TAIL_COLLECTORS[ats](company["ats_identifier"])
        except Exception as e:
            return None, e
    storable = [p for p in postings if _is_storable(p)]
    design = []
    for p in storable:
        if is_design_role(p["title"]):
            p["is_design"] = True
            design.append(p)
    return {"design": design, "alive": bool(storable)}, None


def _is_storable(p):
    return bool(p.get("ats_posting_id")) and bool((p.get("title") or "").strip()) and bool(p.get("url"))


def _tail_slot(now=None):
    return int((now if now is not None else time.time()) // RUN_INTERVAL_SECONDS) % COLD_SLICES


def select_tail(companies, now=None):
    """Which discovered boards this run scans: every hot one, plus the cold ones whose id falls
    in this run's slice (a stateless rotation: each cold board comes up once per COLD_SLICES runs)."""
    slot = _tail_slot(now)
    return [
        c for c in companies
        if c["tier"] == "hot" or (c["tier"] == "cold" and c["id"] % COLD_SLICES == slot)
    ]


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
        all_users = get_all_users(conn)
        everything = get_companies_to_scan(conn)
        if not everything:
            print("No companies in the directory — nothing to do.")
            finish_scheduler_run(conn, run_id)
            conn.commit()
            return
        core = [c for c in everything if c["tier"] == "core"]
        tail = select_tail([c for c in everything if c["tier"] != "core"])

        def fan_out(company, posting, posting_id):
            """Alert every user whose profile AND Work type fit this new role."""
            nonlocal new_postings_count
            new_postings_count += 1
            wtype = work_type(posting.get("location"), posting.get("remote_type"))
            for user in all_users:
                if not matches_profile(
                    posting["title"],
                    posting["is_design"],
                    all_user_categories.get(user["id"], set()),
                    all_user_keywords.get(user["id"], []),
                ):
                    continue
                if not matches_work_type(wtype, user["work_types"]):
                    continue
                bucket = new_postings_by_user.setdefault(
                    user["id"], {"ntfy_topic": user["ntfy_topic"], "postings": []}
                )
                bucket["postings"].append(
                    {"company": company["name"], "title": posting["title"], "url": posting["url"]}
                )
                mark_notified(conn, user["id"], posting_id)

        # ---- core companies: every role stored, full fetch -------------------------------
        t_core = time.time()
        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
            fetched = list(pool.map(_fetch_company, core))

        for company, (postings, error) in zip(core, fetched):
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

                for posting in storable:
                    posting_id = new_ids.get(str(posting["ats_posting_id"]))
                    if posting_id is None:
                        continue
                    if backfill:
                        # First scan of this company: filling the table, not news. Without
                        # this, a newly added company would alert you to every role it
                        # already had open.
                        continue
                    fan_out(company, posting, posting_id)
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
        print(f"core: {len(core)} companies in {time.time() - t_core:.0f}s")

        # ---- discovered boards: design roles only, lean DB path ---------------------------
        if tail:
            _scan_tail(conn, tail, health_alerts, fan_out)

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


def _scan_tail(conn, tail, health_alerts, fan_out):
    """Scan this run's discovered boards. Only design roles are stored. The common case - a
    board with no design roles that never had any - costs no database work at all; health
    bookkeeping is batched. A zero-result board is simply skipped (never isolated or mass-closed):
    there are thousands of these and nobody could clear isolations by hand."""
    t0 = time.time()
    stored = get_open_posting_hashes(conn, [c["id"] for c in tail])
    ok_ids, failed_ids, first_scan_ids = [], [], []
    stats = {"scanned": 0, "design_boards": 0, "writes": 0, "unchanged": 0, "empty": 0, "skipped_budget": 0}

    with ThreadPoolExecutor(max_workers=TAIL_WORKERS) as pool:
        for start in range(0, len(tail), TAIL_CHUNK):
            if time.time() - t0 > TAIL_TIME_BUDGET_SECONDS:
                stats["skipped_budget"] = len(tail) - start  # next run picks these up
                break
            chunk = tail[start:start + TAIL_CHUNK]
            for company, (result, error) in zip(chunk, pool.map(_fetch_tail, chunk)):
                cid = company["id"]
                stats["scanned"] += 1
                if error is not None:
                    failed_ids.append(cid)
                    continue
                if not result["alive"]:
                    stats["empty"] += 1  # nothing came back: change nothing, close nothing
                    continue
                ok_ids.append(cid)
                backfill = company["full_ingest_at"] is None
                design = result["design"]
                stored_for = stored.get(cid, {})
                if design or stored_for:
                    stats["design_boards"] += 1 if design else 0
                    # Same roles, same content as last time: no database work at all.
                    if not backfill and {str(p["ats_posting_id"]): content_hash(p) for p in design} == stored_for:
                        stats["unchanged"] += 1
                        continue
                    new_ids = upsert_postings(conn, cid, design, backfill=backfill)
                    if not backfill:
                        for posting in design:
                            posting_id = new_ids.get(str(posting["ats_posting_id"]))
                            if posting_id is not None:
                                fan_out(company, posting, posting_id)
                    mark_stale_postings_closed(conn, cid, [str(p["ats_posting_id"]) for p in design])
                    stats["writes"] += 1
                if backfill:
                    first_scan_ids.append(cid)
                if stats["scanned"] % TAIL_COMMIT_EVERY == 0:
                    conn.commit()
    record_tail_scan_results(conn, ok_ids, failed_ids, first_scan_ids)
    promoted = promote_hot_boards(conn)
    conn.commit()
    print(
        f"discovered boards: scanned {stats['scanned']} in {time.time() - t0:.0f}s | "
        f"{len(ok_ids)} ok, {len(failed_ids)} failed, {stats['empty']} empty | "
        f"{stats['design_boards']} with design roles, {stats['unchanged']} unchanged (no DB work), {stats['writes']} wrote | "
        f"{len(first_scan_ids)} first scans | {promoted} promoted to hot"
        + (f" | {stats['skipped_budget']} skipped (time budget)" if stats["skipped_budget"] else "")
    )
    if len(failed_ids) >= TAIL_FAILURE_ALERT_MIN and len(failed_ids) > TAIL_FAILURE_ALERT_RATIO * max(stats["scanned"], 1):
        health_alerts.append(
            f"{len(failed_ids)} of {stats['scanned']} discovered job boards failed this run "
            f"(more than {TAIL_FAILURE_ALERT_RATIO:.0%}) - an ATS may be down or blocking us."
        )


if __name__ == "__main__":
    run()
