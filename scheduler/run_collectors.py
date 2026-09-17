import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors import ashby, greenhouse, lever, workday
from collectors.company_list import COMPANIES
from collectors.filters import is_design_role
from db.db import get_conn, mark_stale_postings_closed, upsert_company, upsert_posting
from scheduler.notify import notify_new_postings

COLLECTORS = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
    "workday": workday.fetch,
}


def run():
    conn = get_conn()
    new_postings = []
    try:
        for company in COMPANIES:
            fetch = COLLECTORS[company["ats_type"]]
            print(f"Fetching {company['name']} ({company['ats_type']})...")
            try:
                postings = fetch(company["ats_identifier"])
            except Exception as e:
                print(f"  FAILED: {e}")
                continue

            design_postings = [p for p in postings if is_design_role(p["title"])]
            print(f"  {len(postings)} total, {len(design_postings)} design roles")

            company_id = upsert_company(
                conn, company["name"], company["ats_type"], company["ats_identifier"]
            )
            for posting in design_postings:
                is_new = upsert_posting(conn, company_id, posting)
                if is_new:
                    new_postings.append(
                        {
                            "company": company["name"],
                            "title": posting["title"],
                            "url": posting["url"],
                        }
                    )

            mark_stale_postings_closed(
                conn, company_id, [p["ats_posting_id"] for p in design_postings]
            )
            conn.commit()
    finally:
        conn.close()

    if new_postings:
        print(f"{len(new_postings)} new posting(s) — sending notification")
        notify_new_postings(new_postings)


if __name__ == "__main__":
    if not COMPANIES:
        print("collectors/company_list.py is empty — add companies before running.")
        sys.exit(1)
    run()
