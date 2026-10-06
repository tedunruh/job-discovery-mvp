"""Estimate GitHub Actions minutes the collector uses (watch this against your plan).

    python scripts/actions_usage.py [days]     # default: last 7 days

Built from scheduler_runs, so it's an ESTIMATE of billed minutes: each run's measured
duration plus a fixed allowance for checkout/Python setup/pip (SETUP_SECONDS), rounded up
to a whole minute the way Actions bills each job. The authoritative number is on GitHub:
Settings > Billing > Actions. Compare the projection with your plan's included minutes
(2,000/mo on a private repo's free plan; unlimited if the repo is public).
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env.local"))

from db.db import get_conn  # noqa: E402

SETUP_SECONDS = 30  # checkout + setup-python (pip cache) + pip install, before the collector starts
FREE_PLAN_MINUTES = 2000


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT extract(epoch from finished_at - started_at), started_at
            FROM scheduler_runs
            WHERE finished_at IS NOT NULL AND started_at > now() - make_interval(days => %s)
            ORDER BY started_at
            """,
            (days,),
        )
        rows = cur.fetchall()
        cur.execute("SELECT count(*) FROM companies")
        companies = cur.fetchone()[0]
    conn.close()
    if len(rows) < 2:
        sys.exit("Not enough completed runs recorded yet.")

    durations = [float(r[0]) for r in rows]
    span_days = max((rows[-1][1] - rows[0][1]).total_seconds() / 86400, 1e-9)
    runs_per_day = (len(rows) - 1) / span_days
    billed = [math.ceil((d + SETUP_SECONDS) / 60) for d in durations]
    per_run = sum(billed) / len(billed)
    per_day = per_run * runs_per_day
    month = per_day * 30

    print(f"Collector usage, last {days} days  ({len(rows)} completed runs, {companies} companies in the directory)\n")
    print(f"  cadence            one run every {24 * 60 / runs_per_day:.0f} min  ({runs_per_day:.0f}/day)")
    print(f"  run duration       avg {sum(durations) / len(durations):.0f}s   max {max(durations):.0f}s")
    print(f"  billed per run     ~{per_run:.1f} min   (duration + {SETUP_SECONDS}s setup, rounded up)")
    print(f"  estimated usage    ~{per_day:,.0f} min/day  ->  ~{month:,.0f} min/month")
    print(f"  vs free plan       {month / FREE_PLAN_MINUTES:.1f}x the {FREE_PLAN_MINUTES:,} included on a private repo's free plan")
    print("\nEstimate only. Check Settings > Billing > Actions on GitHub for the real figure.")


if __name__ == "__main__":
    main()
