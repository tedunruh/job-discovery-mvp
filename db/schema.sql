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
