"""Fill in pay for postings already stored (SC-71): from the pay a board gave inside the stored raw payload (Lever's
salaryRange today) and from the stored description text. Postings the collector fetches from now on get theirs as
they are written; this covers the ones already there.

    python scripts/backfill_pay.py              # dry run: counts by board, nothing written
    python scripts/backfill_pay.py --sample 25  # dry run plus a hand-check sample: the range found beside its source text
    python scripts/backfill_pay.py --write      # write what was found (only where no pay is stored yet)

Short transactions only (batches of 200): it can run against production while the collector runs.
"""
import argparse
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2.extras  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.local"))

from collectors import pay  # noqa: E402
from db.db import _pay_columns, get_conn  # noqa: E402


def snippet(text, found):
    """The part of the description around the figure that was read (for the hand check)."""
    if not text:
        return ""
    figure = f"{int(found['min']):,}" if found.get("min") else ""
    at = text.find(figure) if figure else -1
    if at < 0:
        m = re.search(r"[$£€]\s?\d", text)
        at = m.start() if m else 0
    return text[max(0, at - 90):at + 130].replace("\n", " ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--sample", type=int, default=0)
    args = ap.parse_args()

    conn = get_conn()
    conn.set_session(autocommit=True)
    found_rows = []   # (id, ats, company, title, pay, text)
    scanned = 0
    last = 0
    while True:   # in pages by id, so no cursor stays open and no transaction runs long
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT p.id, co.ats_type, co.name AS company, p.title, p.raw_json, p.description
                FROM postings p JOIN companies co ON co.id = p.company_id
                WHERE p.id > %s AND p.status = 'open' AND p.pay_source IS NULL
                  AND (p.raw_json <> '{}'::jsonb OR (p.description IS NOT NULL AND p.description <> ''))
                ORDER BY p.id LIMIT 300
                """,
                (last,),
            )
            page = cur.fetchall()
        if not page:
            break
        for row in page:
            scanned += 1
            found = pay.from_board(row["ats_type"], row["raw_json"]) or pay.from_text(row["description"])
            if found:
                found_rows.append((row["id"], row["ats_type"], row["company"], row["title"], found, row["description"]))
        last = page[-1]["id"]

    by = {}
    for _, ats, _, _, found, _ in found_rows:
        key = (ats, found["source"])
        by[key] = by.get(key, 0) + 1
    print(f"scanned {scanned} open postings with no pay stored; pay found for {len(found_rows)}")
    for (ats, source), n in sorted(by.items()):
        print(f"  {ats:<11}{source:<6}{n}")

    if args.sample:
        random.seed(11)
        for _, ats, company, title, found, text in random.sample(found_rows, min(args.sample, len(found_rows))):
            line = f"{found['min']}-{found['max']} {found['currency']} per {found['period']} ({found['source']})"
            print(f"\n[{ats}] {company}: {title}\n   found: {line}")
            if found["source"] == "text":
                print(f"   text:  ...{snippet(text, found)}...")

    if args.write:
        written = 0
        with conn.cursor() as cur:
            for i in range(0, len(found_rows), 200):
                batch = found_rows[i:i + 200]
                psycopg2.extras.execute_batch(
                    cur,
                    "UPDATE postings SET pay_min = %s, pay_max = %s, pay_currency = %s, pay_period = %s, pay_source = %s "
                    "WHERE id = %s AND pay_source IS NULL",
                    [(*_pay_columns(found), pid) for pid, _, _, _, found, _ in batch],
                )
                written += len(batch)
        print(f"\nwrote pay for {written} postings")
    else:
        print("\n(dry run: nothing written; add --write)")
    conn.close()


if __name__ == "__main__":
    main()
