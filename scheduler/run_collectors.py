import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors import ashby, greenhouse, lever, rippling, workable, workday
from collectors.filters import is_design_role
from dashboard.filters import work_type
from db.db import (
    content_hash,
    ensure_conn,
    finish_scheduler_run,
    get_all_user_keywords,
    get_all_user_profiles,
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
    set_scan_markers,
    start_scheduler_run,
    touch_source_health,
    upsert_postings,
)
from role_categories import alerts_active, matches_city, matches_profile, matches_profile_titles, matches_work_type
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
WORKDAY_EVERY = 2  # hot Workday tenants are probed every Nth run
# Huge enterprise tenants (their "design" search matches hundreds+ of postings, mostly noise)
# churn nearly every run, so each probe turns into a 25-page scan. Check those hourly.
WORKDAY_BIG_TOTAL = 400
WORKDAY_BIG_EVERY = 4
TAIL_CHUNK = 48  # boards fetched per batch: the time budget is only checked between batches, and a batch
# of Workday first scans (several seconds each) must not be able to overrun it by minutes
# Per-ATS cap on simultaneous requests, so a few thousand boards don't hammer one host.
TAIL_PER_ATS = {"greenhouse": 8, "ashby": 5, "lever": 4, "workable": 4, "rippling": 4, "workday": 6}
TAIL_COMMIT_EVERY = 100
# An ATS that answers this many requests in one run with 429/403 is rate-limiting or blocking
# us. Stop asking it for the rest of the run (the boards stay first in line for the next
# run) instead of failing hundreds of boards and prolonging the block - Workable once held
# one for ~23 hours.
RATE_LIMIT_TRIP = 5
WORKABLE_EVERY = 2  # hot Workable boards are checked every Nth run (they rarely post)
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
    "rippling": rippling.fetch,
}
# Same, but cheaper where the ATS allows it (Greenhouse without full job descriptions).
TAIL_COLLECTORS = dict(COLLECTORS, greenhouse=lambda ident: greenhouse.fetch(ident, light=True))
_ats_slots = {ats: threading.Semaphore(n) for ats, n in TAIL_PER_ATS.items()}
_rate_limited = {}  # ats -> rate-limit answers so far this run
_rate_lock = threading.Lock()


class AtsBlocked(Exception):
    """This ATS is rate-limiting us this run: the board was not requested."""


def _note_rate_limit(ats, error):
    status = getattr(getattr(error, "response", None), "status_code", None)
    if status in (429, 403):
        with _rate_lock:
            _rate_limited[ats] = _rate_limited.get(ats, 0) + 1


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
    marker = None
    with _ats_slots.get(ats, threading.Semaphore(2)):
        if _rate_limited.get(ats, 0) >= RATE_LIMIT_TRIP:
            return None, AtsBlocked(ats)
        try:
            if ats == "workday":
                # Workday's search is ordered by relevance, not date, so there's no "newest
                # first" to read. One cheap request returns a fingerprint; only page through
                # everything when it changed - or on this tenant's turn in the rotation, in
                # case one role replaced another and the count didn't move.
                marker = workday.probe_marker(company["ats_identifier"])
                forced = company["id"] % COLD_SLICES == _tail_slot()
                if marker == company.get("scan_marker") and company["full_ingest_at"] is not None and not forced:
                    return {"unchanged": True, "alive": True, "design": [], "marker": marker}, None
                postings = workday.fetch_light(company["ats_identifier"])
            else:
                postings = TAIL_COLLECTORS[ats](company["ats_identifier"])
        except Exception as e:
            _note_rate_limit(ats, e)
            return None, e
    storable = [p for p in postings if _is_storable(p)]
    design = []
    for p in storable:
        if is_design_role(p["title"]):
            p["is_design"] = True
            design.append(p)
    return {"design": design, "alive": bool(storable), "marker": marker}, None


def _is_storable(p):
    return bool(p.get("ats_posting_id")) and bool((p.get("title") or "").strip()) and bool(p.get("url"))


def _tail_slot(now=None):
    return int((now if now is not None else time.time()) // RUN_INTERVAL_SECONDS) % COLD_SLICES


def _marker_total(company):
    """The match count in a Workday tenant's stored change marker ("design:451" -> 451), or 0."""
    m = re.fullmatch(r"design:(\d+)", company.get("scan_marker") or "")
    return int(m.group(1)) if m else 0


def select_tail(companies, now=None):
    """Which discovered boards this run scans: every hot one, plus the cold ones whose id falls
    in this run's slice (a stateless rotation: each cold board comes up once per COLD_SLICES runs)."""
    slot = _tail_slot(now)
    picked = []
    for c in companies:
        if c["tier"] == "hot":
            # Workday tenants cost a request each even when nothing changed, and there are
            # hundreds: check each every WORKDAY_EVERY runs (30 min) instead of every run.
            if c["ats_type"] == "workday":
                every = WORKDAY_BIG_EVERY if _marker_total(c) >= WORKDAY_BIG_TOTAL else WORKDAY_EVERY
                if c["id"] % every != slot % every:
                    continue
            elif c["ats_type"] == "workable" and c["id"] % WORKABLE_EVERY != slot % WORKABLE_EVERY:
                continue
            picked.append(c)
        elif c["tier"] == "cold" and c["id"] % COLD_SLICES == slot:
            picked.append(c)
    return picked


def _deliver(new_postings_by_user, health_alerts):
    """Send the alerts collected so far, then clear them. Called after the core companies and
    again after the discovered boards: alerts are marked as sent in the database as they are
    created, so they must go out before anything slow or fallible runs - otherwise a crash in
    between would lose them for good."""
    if not new_postings_by_user:
        return
    n_users = len(new_postings_by_user)
    print(f"{sum(len(b['postings']) for b in new_postings_by_user.values())} alert(s) for {n_users} user(s) — sending notifications")
    failures = 0
    for bucket in list(new_postings_by_user.values()):
        if not notify_new_postings(bucket["postings"], bucket["ntfy_topic"]):
            failures += 1
    new_postings_by_user.clear()
    if failures:
        health_alerts.append(
            f"Push notification failed for {failures} of {n_users} user(s) this run — they won't be "
            f"told about their new posting(s) (still visible on the dashboard)."
        )


def order_for_fairness(boards):
    """Least-recently-checked first (never-checked boards lead). When a backlog is bigger than one
    run's time budget, the budget cuts the list off; ordering by name would let the same early
    boards win every run and starve the rest. This way the cut-off boards go first next time."""
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(boards, key=lambda c: c.get("last_checked_at") or oldest)


def _wants_posting(user_id, posting, profiles, categories, keywords):
    """Does this person's profile want this role (before their Work type is checked)? People with
    a profile (SC-46) are matched on target titles and level, and only while their alerts are on;
    everyone else on their old role categories and keywords, exactly as before."""
    profile = profiles.get(user_id)
    if profile is None:
        return matches_profile(posting["title"], posting["is_design"],
                               categories.get(user_id, set()), keywords.get(user_id, []))
    return alerts_active(profile) and matches_profile_titles(
        posting["title"], profile["titles"], profile["level"], profile["one_level_up"])


def _run(health_alerts):
    conn = get_conn()
    new_postings_by_user = {}  # user_id -> {"ntfy_topic": str, "postings": [...]}
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
        all_user_profiles = get_all_user_profiles(conn)
        all_users = get_all_users(conn)
        everything = get_companies_to_scan(conn)
        if not everything:
            print("No companies in the directory — nothing to do.")
            finish_scheduler_run(conn, run_id)
            conn.commit()
            return
        core = [c for c in everything if c["tier"] == "core"]
        tail = order_for_fairness(select_tail([c for c in everything if c["tier"] != "core"]))

        def fan_out(conn, company, posting, posting_id):
            """Alert every user whose profile AND Work type fit this new role."""
            wtype = work_type(posting.get("location"), posting.get("remote_type"))
            for user in all_users:
                if not _wants_posting(user["id"], posting, all_user_profiles, all_user_categories, all_user_keywords):
                    continue
                if not matches_work_type(wtype, user["work_types"]):
                    continue
                profile = all_user_profiles.get(user["id"])
                if profile is not None and not matches_city(wtype, posting.get("location"), profile["city"]):
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
                # scan_marker == 'rescan' asks for a silent re-scan: used when a collector fix
                # makes a board return more of its (already open) roles than before.
                backfill = company["full_ingest_at"] is None or company.get("scan_marker") == "rescan"
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
                    fan_out(conn, company, posting, posting_id)
                mark_stale_postings_closed(conn, company_id, [str(p["ats_posting_id"]) for p in storable])
                if backfill:
                    mark_full_ingest_done(conn, company_id)
                if company.get("scan_marker") == "rescan":
                    set_scan_markers(conn, {company_id: None})
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
        conn.commit()
        _deliver(new_postings_by_user, health_alerts)  # before the slow part, not after it

        # ---- discovered boards: design roles only, lean DB path ---------------------------
        if tail:
            try:
                conn = _scan_tail(conn, tail, health_alerts, fan_out)
            except Exception as e:  # noqa: BLE001
                # Never let the discovered-board scan take down the run: the core results are
                # already saved and delivered. Report it and carry on with a usable connection.
                print(f"discovered-board scan failed: {type(e).__name__}: {e}")
                health_alerts.append(f"Discovered-board scan failed this run: {type(e).__name__}: {e}")
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                conn = ensure_conn(conn)

        finish_scheduler_run(conn, run_id)
        conn.commit()
    finally:
        conn.close()

    _deliver(new_postings_by_user, health_alerts)  # alerts found by the discovered-board scan


def _scan_tail(conn, tail, health_alerts, fan_out):
    """Scan this run's discovered boards. Only design roles are stored. The common case - a
    board with no design roles that never had any - costs no database work at all; health
    bookkeeping is batched. A zero-result board is simply skipped (never isolated or mass-closed):
    there are thousands of these and nobody could clear isolations by hand.

    Works in small batches: fetch a batch (the database connection sits idle meanwhile), reconnect
    if the server dropped it, write, then save progress - so a long run never holds a long idle
    connection or loses what it already did. Returns the connection to use afterwards."""
    t0 = time.time()
    _rate_limited.clear()
    blocked = {}  # ats -> boards left unrequested because it was rate-limiting us
    stored = get_open_posting_hashes(conn, [c["id"] for c in tail])
    stats = {"scanned": 0, "design_boards": 0, "writes": 0, "unchanged": 0, "empty": 0, "skipped_budget": 0,
             "ok": 0, "failed": 0, "first_scans": 0}
    acc = {"ok": [], "failed": [], "first": [], "empty": [], "markers": {}}

    def flush(conn):
        record_tail_scan_results(conn, acc["ok"], acc["failed"], acc["first"], acc["empty"])
        set_scan_markers(conn, acc["markers"])
        conn.commit()
        stats["ok"] += len(acc["ok"]); stats["failed"] += len(acc["failed"]); stats["first_scans"] += len(acc["first"])
        for k in ("ok", "failed", "first", "empty"):
            acc[k] = []
        acc["markers"] = {}

    with ThreadPoolExecutor(max_workers=TAIL_WORKERS) as pool:
        for start in range(0, len(tail), TAIL_CHUNK):
            if time.time() - t0 > TAIL_TIME_BUDGET_SECONDS:
                stats["skipped_budget"] = len(tail) - start  # next run picks these up
                break
            chunk = tail[start:start + TAIL_CHUNK]
            conn.commit()  # nothing pending while we wait on the network
            results = list(pool.map(_fetch_tail, chunk))
            conn = ensure_conn(conn)
            for company, (result, error) in zip(chunk, results):
                cid = company["id"]
                if isinstance(error, AtsBlocked):
                    # Not requested, so neither a success nor a failure: leave its health alone
                    # and its place at the front of the queue.
                    blocked[company["ats_type"]] = blocked.get(company["ats_type"], 0) + 1
                    continue
                stats["scanned"] += 1
                if error is not None:
                    acc["failed"].append(cid)
                    continue
                if not result["alive"]:
                    stats["empty"] += 1  # nothing came back: change nothing, close nothing
                    acc["empty"].append(cid)
                    continue
                acc["ok"].append(cid)
                if result.get("unchanged"):
                    stats["unchanged"] += 1  # the change marker didn't move: no scan, no DB work
                    continue
                if result.get("marker") is not None:
                    acc["markers"][cid] = result["marker"]
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
                                fan_out(conn, company, posting, posting_id)
                    mark_stale_postings_closed(conn, cid, [str(p["ats_posting_id"]) for p in design])
                    stats["writes"] += 1
                if backfill:
                    acc["first"].append(cid)
            flush(conn)  # progress is saved after every batch
    promoted = promote_hot_boards(conn)
    conn.commit()
    print(
        f"discovered boards: scanned {stats['scanned']} in {time.time() - t0:.0f}s | "
        f"{stats['ok']} ok, {stats['failed']} failed, {stats['empty']} empty | "
        f"{stats['design_boards']} with design roles, {stats['unchanged']} unchanged (no DB work), {stats['writes']} wrote | "
        f"{stats['first_scans']} first scans | {promoted} promoted to hot"
        + (f" | {stats['skipped_budget']} skipped (time budget)" if stats["skipped_budget"] else "")
    )
    for ats, n in blocked.items():
        print(f"{ats} is rate-limiting us: {n} board(s) not requested this run")
        if _tail_slot() % 4 == 0:  # remind hourly, not every run, while a block lasts
            health_alerts.append(f"{ats.capitalize()} is rate-limiting us - {n} board(s) skipped this run. "
                                 f"Retrying every run; no action needed unless this lasts a day.")
    if stats["failed"] >= TAIL_FAILURE_ALERT_MIN and stats["failed"] > TAIL_FAILURE_ALERT_RATIO * max(stats["scanned"], 1):
        health_alerts.append(
            f"{stats['failed']} of {stats['scanned']} discovered job boards failed this run "
            f"(more than {TAIL_FAILURE_ALERT_RATIO:.0%}) - an ATS may be down or blocking us."
        )
    return conn


if __name__ == "__main__":
    run()
