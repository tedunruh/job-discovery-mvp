"""Add the boards found by scripts/discover_boards.py to the company directory (SC-49).

    python scripts/import_discovered.py --tier hot  --dry-run   # preview
    python scripts/import_discovered.py --tier hot              # boards with a design-title match
    python scripts/import_discovered.py --tier cold             # valid boards with none yet
    python scripts/import_discovered.py --tier all

Imported boards are tier 'hot' (they have, or had, a design role: scanned every run) or 'cold'
(scanned in a rotating slice, ~every 4 hours). Both store design roles only. Nothing is scanned
until the collector with the tail scanner is deployed, and a company's first scan is silent.

Skips: boards already in the directory (case-insensitive), failed/empty boards, and
agency-like Workable boards (staffing agencies list other employers' jobs). To switch a
board or a whole batch off: UPDATE companies SET tier = 'off' WHERE ...
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import psycopg2.extras  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env.local"))

from db.db import get_conn  # noqa: E402

RESULTS = os.path.join(ROOT, "scripts", "_discovery", "validated.jsonl")
AGENCY_WORDS = re.compile(r"recruit|staffing|talent|search|agency|headhunt|manpower|placement|consult", re.I)


def pretty(token):
    """A readable fallback name from a board token (Ashby gives no company name)."""
    words = re.split(r"[-_.]+", token)
    return " ".join(w if w.isupper() else w.capitalize() for w in words if w)[:80] or token


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tier", choices=["hot", "cold", "all"], required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    results = {}
    for line in open(RESULTS):
        try:
            r = json.loads(line)
            results[(r["ats"], r["token"].lower())] = r
        except ValueError:
            pass

    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT ats_type, lower(ats_identifier) FROM companies")
        known = set(cur.fetchall())

    rows, skipped = [], {"already in directory": 0, "agency-like": 0, "not a working board": 0, "other tier": 0}
    for (ats, tok), r in results.items():
        if not r.get("ok"):
            skipped["not a working board"] += 1
            continue
        if (ats, tok) in known:
            skipped["already in directory"] += 1
            continue
        if ats == "workable" and (r["total"] >= 1000 or AGENCY_WORDS.search(r.get("name") or "")):
            skipped["agency-like"] += 1
            continue
        tier = "hot" if r["design"] > 0 else "cold"
        if args.tier != "all" and tier != args.tier:
            skipped["other tier"] += 1
            continue
        name = (r.get("name") or "").strip() or pretty(r["token"])
        rows.append((name[:80], ats, r["token"], tier))

    by = {}
    for _, ats, _, tier in rows:
        by[(tier, ats)] = by.get((tier, ats), 0) + 1
    print(f"{'WOULD IMPORT' if args.dry_run else 'IMPORTING'} {len(rows):,} boards:", dict(sorted(by.items())))
    print("skipped:", {k: v for k, v in skipped.items() if v})
    print("examples:", [(n, a, t, tr) for n, a, t, tr in rows[:4]])
    if args.dry_run:
        return
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO companies (name, ats_type, ats_identifier, tier) VALUES %s ON CONFLICT (ats_type, ats_identifier) DO NOTHING",
            rows,
            page_size=500,
        )
        cur.execute("SELECT tier, count(*) FROM companies GROUP BY 1 ORDER BY 1")
        print("directory by tier now:", cur.fetchall())
    conn.commit()


if __name__ == "__main__":
    main()
