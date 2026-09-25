-- Job Discovery MVP schema (Postgres)

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
    linkedin_seen_at TIMESTAMPTZ,
    applied BOOLEAN NOT NULL DEFAULT false,
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

-- --- Multi-tenancy foundation (Sprint 1, Story 2, Stage A) ---
-- Schema + data migration only in this stage. The app layer still reads/writes
-- postings.applied/linkedin_seen_at and the static company_list.py directly -
-- that cutover, and auth, are later stages. This stage just gets the new
-- model in place and backfilled so nothing has to migrate twice.

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    ntfy_topic TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
