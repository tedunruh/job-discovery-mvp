-- Job Discovery MVP schema (Postgres)

-- ats_identifier's format depends on ats_type. Verify each one before relying
-- on it - board tokens don't always match the company's public name:
--
--   greenhouse: board token, e.g. "figma"
--               https://boards-api.greenhouse.io/v1/boards/{ats_identifier}/jobs
--   lever:      company slug, e.g. "palantir"
--               https://api.lever.co/v0/postings/{ats_identifier}?mode=json
--   ashby:      org slug, e.g. "notion"
--               https://api.ashbyhq.com/posting-api/job-board/{ats_identifier}
--   workday:    "{tenant}.{instance}/{site}", e.g. "workhuman.wd1/WorkhumanCareers"
--               Unlike the other four, there's no guessable URL pattern - tenant,
--               instance (wd1/wd3/wd5/...), and site vary per company and can
--               change over time (tenants get migrated between instances). Find
--               them by opening the company's Workday careers page, opening
--               browser devtools' Network tab, and reading them off a request to
--               .../wday/cxs/<tenant>/<site>/jobs. Also note: Workday's public API
--               only exposes a relative posted date ("Posted 3 Days Ago"), so
--               ats_posted_at for Workday postings is day-precision at best -
--               less accurate than the other four collectors.
--   workable:   account slug, e.g. "airhelp"
--               https://apply.workable.com/api/v1/widget/accounts/{ats_identifier}
--               Gives a real published_on date (day precision, no time) - more
--               reliable than Workday's relative text, though still less precise
--               than greenhouse/lever/ashby's full timestamps. Watch for accounts
--               that are recruiting agencies/talent marketplaces rather than a
--               single company (e.g. one account with 2000+ jobs across many
--               employers) - skip those, they're not what this directory is for.
CREATE TABLE IF NOT EXISTS companies (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    ats_type TEXT NOT NULL CHECK (ats_type IN ('greenhouse', 'lever', 'ashby', 'workday', 'workable')),
    ats_identifier TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ats_type, ats_identifier)
);

CREATE TABLE IF NOT EXISTS postings (
    id SERIAL PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    ats_posting_id TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT,
    remote_type TEXT,
    url TEXT NOT NULL,
    ats_posted_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    raw_json JSONB,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- applied/linkedin_seen_at live in user_postings, not here - a posting
    -- isn't user-specific, but whether *you* applied to it is.
    -- days_ahead_of_linkedin is computed at query time (see get_open_postings) rather
    -- than stored: a generated column can't use a timezone-dependent ::date cast.
    -- dedupe on the ATS's own stable posting id, not title/url (those drift)
    UNIQUE (company_id, ats_posting_id)
);

CREATE INDEX IF NOT EXISTS idx_postings_first_seen ON postings (first_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_postings_status ON postings (status);

-- Per-company collector health, one row per company. 'isolated' is sticky and
-- only cleared by manual review (see db.reset_source_health) - a source coming
-- back non-zero on its own doesn't prove a schema issue is actually fixed.
CREATE TABLE IF NOT EXISTS source_health (
    company_id INTEGER PRIMARY KEY REFERENCES companies(id) ON DELETE CASCADE,
    last_success_at TIMESTAMPTZ,
    last_checked_at TIMESTAMPTZ,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    consecutive_zero_results INTEGER NOT NULL DEFAULT 0,
    typical_posting_count DOUBLE PRECISION,
    status TEXT NOT NULL DEFAULT 'healthy' CHECK (status IN ('healthy', 'degraded', 'isolated'))
);

-- --- Multi-tenancy foundation (Sprint 1, Story 2) ---
-- Stage A (schema + data migration) and Stage B (app layer cutover) are both
-- done: the dashboard and scraper now read/write through these tables,
-- hardcoded to one user's id pending auth (dashboard/app.py CURRENT_USER_ID).
-- Auth and hosting are still separate later stages.

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    ntfy_topic TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- NULL until the user completes the one-time role-interest wizard
    -- (dashboard/app.py redirects here on first post-login dashboard visit).
    onboarded_at TIMESTAMPTZ
);

-- Which coarse role categories (see role_categories.py) a user wants to see.
-- No rows = no preference set = show everything (same convention as an
-- empty/unset filter elsewhere in the app, e.g. Remote US only unchecked).
CREATE TABLE IF NOT EXISTS user_role_interests (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    PRIMARY KEY (user_id, category)
);

-- Single-use, expiring magic-link tokens. A row per requested link, not per
-- user - keeps history and makes "used_at IS NULL AND expires_at > now()"
-- the entire validity check.
CREATE TABLE IF NOT EXISTS magic_links (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Server-side login sessions (SC-43). The browser holds a random token; only its
-- sha256 is stored, so a DB leak can't be replayed as live sessions. Server-side
-- (not a signed cookie) so revoking a tester deletes their rows and logs them
-- out everywhere. expires_at slides forward on use (see dashboard/app.py).
CREATE TABLE IF NOT EXISTS user_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS user_sessions_user_id_idx ON user_sessions (user_id);

-- Lightweight engagement log (SC-19): append-only, one row per event. Types:
-- sign_in (magic link redeemed), visit (dashboard loaded), opened (clicked a
-- posting's link), applied (marked a posting applied). posting_id is set for
-- opened/applied. No analytics layer - scripts/engagement.py reads it directly.
CREATE TABLE IF NOT EXISTS user_events (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    posting_id INTEGER REFERENCES postings(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS user_events_user_created_idx ON user_events (user_id, created_at DESC);

-- One row per collector run (SC-31). Lets a run notice that the *previous* one was
-- long ago - the only way to detect a missed schedule from inside the job.
CREATE TABLE IF NOT EXISTS scheduler_runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

-- NO LONGER USED (SC-45b): every user now sees, and is alerted about, every company in
-- the directory; their role profile is what narrows the list. Kept so old data isn't
-- destroyed; safe to drop later.
-- Which companies a user tracks. companies stays the shared directory -
-- verified ATS identifiers benefit every user, not duplicated per user.
CREATE TABLE IF NOT EXISTS user_companies (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, company_id)
);

-- Per-user relevance on a shared posting: applied/linkedin_seen_at move here
-- from postings (that table stays global - a posting isn't user-specific).
-- notified_at is new: not used yet, reserved for the per-user notification
-- fan-out stage.
CREATE TABLE IF NOT EXISTS user_postings (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    posting_id INTEGER NOT NULL REFERENCES postings(id) ON DELETE CASCADE,
    applied BOOLEAN NOT NULL DEFAULT false,
    linkedin_seen_at TIMESTAMPTZ,
    notified_at TIMESTAMPTZ,
    PRIMARY KEY (user_id, posting_id)
);

-- ---------------------------------------------------------------------------
-- SC-45a: all roles. Additive migrations (safe to re-run).
-- ---------------------------------------------------------------------------

-- postings now holds every role a tracked company lists, not just design ones.
-- is_design = passed collectors.filters.is_design_role at ingest. Rows that
-- predate this column were all design roles, so they're backfilled true.
-- content_hash lets the collector skip rewriting postings that haven't changed.
ALTER TABLE postings ADD COLUMN IF NOT EXISTS is_design BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE postings ADD COLUMN IF NOT EXISTS content_hash TEXT;
UPDATE postings SET is_design = true WHERE content_hash IS NULL AND is_design = false;

-- NULL until the first full (all-roles) ingest of this company has run. That
-- run is "silent" for non-design roles: it fills the table without notifying
-- anyone, otherwise ~every existing posting would look new.
ALTER TABLE companies ADD COLUMN IF NOT EXISTS full_ingest_at TIMESTAMPTZ;

-- Per-user keyword profile for non-design roles: a posting outside design shows
-- up (and alerts) only if its title contains one of these words/phrases.
-- No keywords = design roles only, exactly as before. SC-46 will fill this from
-- a resume; until then users type them in.
CREATE TABLE IF NOT EXISTS user_role_keywords (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    keyword TEXT NOT NULL,
    PRIMARY KEY (user_id, keyword)
);

-- ---------------------------------------------------------------------------
-- SC-49: scale scanning to discovered boards. Additive migrations (safe to re-run).
-- ---------------------------------------------------------------------------

-- How a company is scanned:
--   core - the original watched companies: every role stored, scanned every run
--   hot  - discovered boards that have (had) a design role: design roles only, every run
--   cold - discovered boards with none yet: design roles only, in a rotating slice
--          (each scanned about every 4 hours) - a board that posts one becomes hot
--   off  - kill switch: never scanned
ALTER TABLE companies ADD COLUMN IF NOT EXISTS tier TEXT NOT NULL DEFAULT 'core';
DO $$ BEGIN
    ALTER TABLE companies ADD CONSTRAINT companies_tier_check CHECK (tier IN ('core', 'hot', 'cold', 'off'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE INDEX IF NOT EXISTS idx_companies_tier ON companies (tier);

-- The person's Work type preference, so alerts follow the same parameters as the dashboard.
-- NULL = never chosen (the app default applies: Remote); empty = Any.
ALTER TABLE users ADD COLUMN IF NOT EXISTS work_types TEXT[];
