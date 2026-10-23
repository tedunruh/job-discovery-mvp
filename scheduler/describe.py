"""Descriptions for the roles people would see (SC-51, slice 2).

A role is "wanted" when it passes at least one person's gate: target titles, level, work type and city
(or, for someone without a profile yet, their old categories and keywords). Alerts being paused doesn't
matter: the person still sees the role on the dashboard. Only wanted roles get a description fetched
and stored; nothing else is stored that wasn't before.

The step is told about each fetched role twice:
    prepare(company, posting)       in the fetch worker, while the full payload is still there
    store(conn, company, postings)  on the main thread, after the roles are in the database

Descriptions never hold up ingestion: a database error here is rolled back to a savepoint and switches
the step off for the rest of the run; a platform that is refusing us just leaves those roles for next run.
"""
from contextlib import contextmanager

import psycopg2

from collectors import descriptions
from dashboard.filters import work_type
from db import db as _db
from role_categories import matches_city, matches_profile, matches_profile_titles, matches_work_type

REQUESTS_PER_RUN = 200  # description requests one run may make (the rest wait for the next run)


@contextmanager
def _savepoint(conn):
    """Writes made inside are undone on a database error without touching what the caller already wrote
    in the same transaction."""
    with conn.cursor() as cur:
        cur.execute("SAVEPOINT describe")
    try:
        yield
    except psycopg2.Error:
        try:
            with conn.cursor() as cur:
                cur.execute("ROLLBACK TO SAVEPOINT describe")
        except psycopg2.Error:
            pass  # the connection itself is gone; the caller reconnects
        raise
    with conn.cursor() as cur:
        cur.execute("RELEASE SAVEPOINT describe")


def build_gate(users, profiles, categories, keywords):
    """A function posting -> bool: does any person's gate let this role through?"""

    def wanted(posting):
        title = posting.get("title") or ""
        wtype = work_type(posting.get("location"), posting.get("remote_type"))
        for user in users:
            uid = user["id"]
            profile = profiles.get(uid)
            if profile is None:
                if not matches_profile(title, bool(posting.get("is_design")),
                                       categories.get(uid, set()), keywords.get(uid, [])):
                    continue
            elif not matches_profile_titles(title, profile["titles"], profile["level"], profile["one_level_up"]):
                continue
            if not matches_work_type(wtype, user["work_types"]):
                continue
            if profile is not None and not matches_city(wtype, posting.get("location"), profile["city"]):
                continue
            return True
        return False

    return wanted


class DescriptionStep:
    def __init__(self, wanted, budget=None):
        self.wanted = wanted
        self.budget = budget or descriptions.FetchBudget(max_requests=REQUESTS_PER_RUN)
        self.stored = 0     # roles that got a description (or a note that there is none)
        self.waiting = 0    # wanted roles left for the next run: budget spent or platform refusing
        self.failed = 0     # requests that failed outright
        self.broken = None  # the database error that switched the step off, if any
        self.wide_broken = None  # the database error that switched off storing non-design roles, if any

    # ---- in the fetch worker ------------------------------------------------------------------

    def prepare(self, company, posting):
        """Mark the role wanted or not and, when the list response already carries its description
        (Ashby, Lever, Greenhouse's full board), keep the text before the payload is dropped."""
        posting["wanted"] = bool(self.wanted(posting))
        if posting["wanted"]:
            posting["description"] = descriptions.from_list_payload(company["ats_type"], posting.get("raw_json"))

    # ---- on the main thread -------------------------------------------------------------------

    def described_ids(self, conn, company_ids):
        """{company_id: ats ids that already have a description}, or {} if the database can't say (the
        step switches itself off instead of failing the scan)."""
        if self.broken:
            return {}
        try:
            with _savepoint(conn):
                return _db.get_open_described_ids(conn, company_ids)
        except psycopg2.Error as e:
            self._break(e, conn)
            return {}

    def wide_scanned_ids(self, conn, company_ids):
        """Boards that have had their first wide scan, or None when the database can't say (slice 2b is
        then off for the run: wanted non-design roles are not stored, and nothing is closed)."""
        if self.wide_broken:
            return None
        try:
            with _savepoint(conn):
                return _db.get_wide_scanned_ids(conn, company_ids)
        except psycopg2.Error as e:
            self.wide_broken = f"{type(e).__name__}: {e}"
            return None

    def lacking(self, postings, described):
        """Wanted roles with no description yet, given the ats ids that already have one."""
        return [p for p in postings if p.get("wanted") and str(p["ats_posting_id"]) not in described]

    def store(self, conn, company, postings):
        """Fetch (where the text isn't already in hand) and store descriptions for this company's wanted
        roles that lack one. Returns the connection to keep using. Requests happen with no transaction
        open: whatever was written so far is committed first."""
        wanted = [p for p in postings if p.get("wanted")]
        if self.broken or not wanted:
            return conn
        cid, ats, ident = company["id"], company["ats_type"], company["ats_identifier"]
        try:
            with _savepoint(conn):
                missing = _db.get_postings_missing_description(conn, cid, [str(p["ats_posting_id"]) for p in wanted])
        except psycopg2.Error as e:
            return self._break(e, conn)
        todo = [(p, missing[str(p["ats_posting_id"])]) for p in wanted if str(p["ats_posting_id"]) in missing]
        if not todo:
            return conn
        conn.commit()  # nothing pending while we wait on the network

        results = []  # (posting id, text)
        for p, pid in todo:
            text = p.get("description")
            if text is None and ats in descriptions.PER_JOB_PLATFORMS:
                try:
                    text = descriptions.fetch_description(ats, ident, p, self.budget)
                except descriptions.Skipped:
                    self.waiting += 1
                    continue
                except Exception:  # noqa: BLE001 - the request failed; the role is retried next run
                    self.failed += 1
                    continue
            results.append((pid, text or ""))  # "" = looked, there is none: don't ask again every run
        if not results:
            return conn

        conn = _db.ensure_conn(conn)
        try:
            with _savepoint(conn):
                for pid, text in results:
                    _db.set_posting_description(conn, pid, text)
        except psycopg2.Error as e:
            return self._break(e, conn)
        self.stored += len(results)
        return conn

    def _break(self, error, conn):
        self.broken = f"{type(error).__name__}: {error}"
        return conn

    def summary(self):
        return f"descriptions: {self.stored} stored, {self.waiting} waiting for the next run, {self.failed} failed"
