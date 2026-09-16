import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors import ashby, greenhouse, lever
from collectors.company_list import COMPANIES
from collectors.filters import is_design_role
from db.db import get_conn, mark_stale_postings_closed, upsert_company, upsert_posting

COLLECTORS = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
}


def run():
    conn = get_conn()
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
                upsert_posting(conn, company_id, posting)

            mark_stale_postings_closed(
                conn, company_id, [p["ats_posting_id"] for p in design_postings]
            )
            conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    if not COMPANIES:
        print("collectors/company_list.py is empty — add companies before running.")
        sys.exit(1)
    run()
