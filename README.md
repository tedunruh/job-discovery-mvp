# Job Discovery MVP

Pulls fresh UX/Product Design job postings directly from ATS APIs (Greenhouse,
Lever, Ashby) and tracks how many days ahead of LinkedIn they showed up.

## Setup

1. **Create a Postgres database.** Free options: [Supabase](https://supabase.com)
   or [Neon](https://neon.tech). Copy the connection string.

2. **Configure the DB connection.**
   ```bash
   cp .env.example .env
   # edit .env and set DATABASE_URL
   ```

3. **Install dependencies.**
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

4. **Create the schema.**
   ```bash
   psql "$DATABASE_URL" -f db/schema.sql
   ```

5. **Add companies to track.** Companies live in the `companies` table (the
   shared directory) plus a `user_companies` row per user who tracks them —
   the scraper only fetches companies with at least one `user_companies` row
   (`db.get_tracked_companies`). Verify a candidate's `ats_type` and
   `ats_identifier` against the live API before adding it (see the format
   notes that used to live in `collectors/company_list.py`, now folded into
   `db/schema.sql`'s comments), then insert both rows directly, e.g.:
   ```sql
   INSERT INTO companies (name, ats_type, ats_identifier) VALUES ('Figma', 'greenhouse', 'figma') RETURNING id;
   INSERT INTO user_companies (user_id, company_id) VALUES (1, <id from above>);
   ```

6. **Run the collectors once, manually.**
   ```bash
   set -a; source .env; set +a
   python scheduler/run_collectors.py
   ```

7. **Run the dashboard.**
   ```bash
   set -a; source .env; set +a
   python dashboard/app.py
   # open http://localhost:5000
   ```

## Automating collection

`.github/workflows/collect.yml` runs the collectors every 4 hours via GitHub
Actions. Add `DATABASE_URL` as a repo secret (Settings → Secrets and
variables → Actions) and push this repo to GitHub — the workflow needs no
other setup. GitHub Actions cron timing is best-effort and can slip during
high load; that's fine for this use case.

## Tracking "days ahead of LinkedIn"

This is the actual hypothesis under test and it's still manual: periodically
search LinkedIn for roles that showed up in the dashboard, and click "Mark
seen on LinkedIn" on the matching row. `days_ahead_of_linkedin` is computed
automatically from `first_seen_at` once that's set.

## Notes

- Postings are deduped on the ATS's own stable posting ID
  (`company_id` + `ats_posting_id`), not on title/URL, since those can drift
  between fetches for the same posting.
- A posting flips to `status = 'closed'` once a collector run no longer sees
  it in the ATS's feed, so the dashboard only shows currently-open roles.
- Title filtering (`collectors/filters.py`) is a simple keyword match. Expect
  to tune the keyword list — it will both miss some relevant titles and
  include some noise.
- Ashby's public job-board API isn't enabled for every org; a failed Ashby
  fetch for one company won't stop the rest of the run.
