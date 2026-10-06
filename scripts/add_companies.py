"""Bulk-add companies to the shared directory by running the ATS detector over a list.

    python scripts/add_companies.py scripts/target_companies.txt --dry-run   # preview
    python scripts/add_companies.py scripts/target_companies.txt             # add

One company per line: a name, or "name | careers-link-or-domain" when the name alone is
ambiguous. Blank lines and # comments are ignored. Names already in the directory are skipped.

A company is added automatically only when the detector finds EXACTLY ONE high-confidence
board (the board's own name matches, or the company's name appears in its postings).
Anything ambiguous - no board, several boards, an unconfirmable guess - is written to
scripts/add_companies_review.txt for a human instead. On the 59 companies already tracked,
this rule had no wrong picks (a company with two plausible boards, e.g. a stale Lever board
next to its live Ashby one, goes to review rather than guessing).

This only fills the DIRECTORY. Nothing is scanned until a user tracks a company
(Edit companies in the dashboard), so adding here costs nothing at runtime.
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env.local"))

from collectors import detect  # noqa: E402
from db.db import add_company_if_absent, get_conn  # noqa: E402

REVIEW_PATH = os.path.join(ROOT, "scripts", "add_companies_review.txt")


def read_targets(path):
    targets = []
    for line in open(path):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, _, hint = line.partition("|")
        targets.append((name.strip(), (hint.strip() or name.strip())))
    return targets


def decide(cands):
    """Return (accepted candidate or None, why not)."""
    high = [c for c in cands if c["confidence"] == "high"]
    if len(high) == 1:
        return high[0], ""
    if not cands:
        return None, "no job board found"
    if len(high) > 1:
        return None, "several plausible boards"
    return None, "only unconfirmed guesses"


def describe(c):
    count = f"{c['job_count']}{'+' if c['count_is_lower_bound'] else ''} roles" if c["job_count"] is not None else "?"
    return f"{c['ats_type']}:{c['ats_identifier']} ({count}, {c['confidence']}: {c['reason']})"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if not args:
        sys.exit(__doc__)
    targets = read_targets(args[0])

    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM companies")
        known = {detect.normalize(r[0]) for r in cur.fetchall()}
    todo = [(n, q) for n, q in targets if detect.normalize(n) not in known]
    print(f"{len(targets)} listed, {len(targets) - len(todo)} already in the directory, checking {len(todo)}...\n")

    # 3 names at a time x the detector's own 8 probes keeps load on the ATS hosts modest
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda t: detect.detect(t[1]), todo))

    added, review = [], []
    for (name, _), cands in zip(todo, results):
        pick, why = decide(cands)
        if pick:
            added.append((name, pick))
            if not dry:
                add_company_if_absent(conn, name, pick["ats_type"], pick["ats_identifier"])
        else:
            review.append((name, why, cands))
    if not dry:
        conn.commit()
    conn.close()

    print(f"{'WOULD ADD' if dry else 'ADDED'} {len(added)}:")
    for name, c in added:
        print(f"  {name:28} {describe(c)}")
    print(f"\nNEEDS REVIEW {len(review)} (written to {os.path.relpath(REVIEW_PATH, ROOT)}):")
    with open(REVIEW_PATH, "w") as f:
        for name, why, cands in review:
            print(f"  {name:28} {why}")
            f.write(f"{name}  --  {why}\n")
            for c in cands[:4]:
                f.write(f"    {describe(c)}\n")


if __name__ == "__main__":
    main()
