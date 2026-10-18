# SC-51 — How does matching get job descriptions?

Decided 2026-10-18. Numbers are measured against the production database (read-only counts and sizes)
and a read-only probe of public ATS endpoints on 12–30 randomly sampled discovered boards per
platform. Where a figure is an extrapolation from that sample, it says so. Plan and rate-limit claims
that were not tested are flagged **[verify]**. No board names or tester data appear here: the repo is public.

## Decision

SC-46 matches a resume to jobs on title *and* description. SC-45a stopped storing descriptions for
non-design roles, so we need a way back. Chosen: **store only gate-passers.**

1. A job's description is fetched once, and stored, when the job passes a **cheap gate** for at least one
   user: title, seniority, location and work type, all computable from the list response. "Matches"
   means passes the gate. It does not mean "scores well on the description": that would be circular,
   because scoring needs the description.
2. The gate is the **union of all users' gates**. Roles that pass no one's gate are not stored (discovered
   boards) or are stored title-only (core boards, as today).
3. When the union widens (a new sign-up, a profile edit), **rescan to catch up**. Core boards rematch
   from stored titles. Discovered boards re-run a silent scan (reset `companies.full_ingest_at`, the
   existing silent-first-scan path); hot boards come back next run, cold ones within about 4 hours.
   The catch-up must not alert: seed `user_postings` as seen for the person whose gate widened.
4. The scheduler's description scoring is deterministic (skills and keyword overlap). LLM scoring stays
   on demand with the user's own key (SC-42), which is why the description has to be on hand when a user
   opens a posting.

Rejected: **fetch on demand and keep nothing.** It makes every profile edit, rescore and posting view
refetch from the ATS, and makes the dashboard depend on the ATS being up.

## What is stored today

| Where | Stored | Descriptions |
|---|---|---|
| Core boards (~140) | every role: ~17k open non-design rows (title-only, about 0.3 KB each plus index) and ~500 open design rows | design only (full payload, 11.6 KB as text, 5.8 KB compressed) |
| Discovered boards (hot + cold, ~8.7k) | design roles only; everything else is dropped in memory in `_fetch_tail` | Greenhouse is fetched `light=True`, so none. Ashby and Lever design rows keep the list payload, which happens to include the description. Workable, Rippling and Workday list payloads carry none. |

Database today: 66 MB total, 53 MB `postings`, 33k rows. 5 users, 3 with keywords.

So **open search over discovered boards needs an ingest change regardless of this decision**: their
non-design roles are not stored at all, not even as titles.

## Measured: where each platform exposes the description

| Platform | In the list response? | Per-job endpoint | Measured size (characters) |
|---|---|---|---|
| Ashby | **Yes** (`descriptionPlain`, `descriptionHtml`) | not needed | plain 4.9k, HTML 6.7k (mean of 205 jobs) |
| Lever | **Yes** (`descriptionPlain`, `lists`, `additionalPlain`) | not needed | about 1.5k + 2.9k + 1.3k |
| Greenhouse | Only with `content=true`: payload **11–25×** larger (e.g. 21 KB light vs 454 KB; 111 KB vs 1.6 MB) | `GET /v1/boards/{token}/jobs/{id}` works, 0.1–0.2 s | 4.2k–15.3k HTML, mean 9.8k (8 jobs) |
| Workable | Only with `?details=true`: 0.6–1.8 MB per board (112–426 jobs) | `GET /api/v2/accounts/{account}/jobs/{shortcode}` works, 0.1–0.2 s, 2–8 KB | 0.4k–7.2k |
| Rippling | No (list items carry id, name, url, locations, department, language) | `GET /api/v2/board/{slug}/jobs/{id}` works, 0.2–0.4 s, 16–22 KB | 8.7k–17.7k |
| Workday | No | `GET .../wday/cxs/{tenant}/{site}{externalPath}` works, 0.2–0.5 s, 4–17 KB | 2.8k–15.0k |

Take: Ashby and Lever cost nothing extra. Greenhouse, Workable, Rippling and Workday need one request
per gate-passer, so that is where throttling matters. Use the per-job endpoint for Workable, not
`details=true`.

## Measured: how many roles are out there

Open roles per sampled discovered board (hot + cold):

| Platform | Boards sampled | Mean | Median | p90 | Max | Design share of roles |
|---|---|---|---|---|---|---|
| Greenhouse | 30 | 22.7 | 10 | 54 | 102 | 1.8% |
| Ashby | 30 | 14.3 | 8 | 35 | 64 | 4.0% |
| Lever | 25 | 35.0 | 20 | 77 | 98 | 8.0% |
| Workable | 30 | 43.0 | 10.5 | 112 | 426 | 3.3% |
| Rippling | 25 | 21.0 | 15 | 25 | 102 | 11.6% |
| Workday (hot) | 12 | 789 | n/a | n/a | 2,000 (capped) | not measured |

Scaled by board counts, that is roughly **190k open roles** across Greenhouse, Ashby, Lever, Workable
and Rippling, and **on the order of 200k more at Workday** (12-board sample, heavily skewed, capped
results: treat as low confidence). So about **400k open roles** if we kept everything:
- Titles only, at about 0.5 KB per row with its index: about **200 MB**.
- With descriptions at 4–6 KB of text each: well over **1 GB**.
That is why discovered boards store gate-passers only.

Inflow: 2,988 new design postings in the last 7 days (about 430 a day). At a 5.3% design share across
the sample, all-roles inflow is about **8k new roles a day**. A gate that passes 5–15% for the union of
five users means a few hundred to about 1,200 description fetches a day, roughly one a minute across
96 runs. Steady state is not the risk; bursts are (first scan of a new board, a catch-up rescan).

## Measured: rate limits

25 back-to-back per-job requests against one board each, sequential, no pause:

| Endpoint | Result | Rate |
|---|---|---|
| Workable v2 job | 25 × 200 | about 7.5 req/s |
| Rippling job | 25 × 200 | about 4.6 req/s |
| Greenhouse job | 25 × 200 | about 10 req/s |

No 429, no `Retry-After`, no rate-limit headers. **This does not establish a limit**, and Workday was
deliberately not burst-tested against real employers. We also have no written record of the earlier
Workable rate-limit incident in this repo (not in `QUALITY_LOG.md`, `SPRINTS.md` or commit messages), so
the cause is unexplained. Build the fetcher defensively regardless:
- Honor `Retry-After`. `request_with_retries` retries 429 and 403 on a fixed 1/2/4 s backoff and ignores it.
- Keep the per-ATS concurrency caps (`TAIL_PER_ATS`).
- Add a per-run fetch budget, and spread the first fetch of a new board across runs.

## A correction to the Workday assumption

`collectors/workday.py` says an empty `searchText` is rejected with a 400, and the discovered-board scan
only runs design-keyword queries because of that. The probe got **200 with the real total** from all 12
tenants for both `""` and an omitted `searchText` (e.g. 521, 1,545, 48). So all-role enumeration looks
possible. Two tenants returned exactly 2,000, which looks like a result cap, so large tenants likely need
facets or several queries to enumerate fully **[verify]**. The code comment is stale or tenant-dependent
and should be re-checked before the Workday work starts.

## Design

- **Gate** (cheap, list-response only): target titles (today's `user_role_keywords`), seniority band,
  work type, country. Evaluated in `_fetch_tail` and the core path instead of `is_design_role` alone, with
  the design gate kept as the default profile.
- **Fetch** for gate-passers that are new or changed (content hash, as now). Per-platform fetchers per
  the table above.
- **Store** plain text (HTML stripped, capped around 20 KB), not the raw payload. Delete the text when the
  role closes, with a grace period. Keep title-only rows for core boards.
- **Score** deterministically in the scheduler at ingest. Alerts fire after the description score, so
  alert latency grows by the fetch time, and core alerts still go out before the discovered-board scan.
- **Profile** (stored parsed, not as a resume), in two parts, with a version so an edit triggers a rematch:
  - gate fields: target titles, seniority, work type, country;
  - description fields: skills (must-have, nice-to-have), years of experience, domains, deal-breakers.

## Effort (estimates, days)

| Work | Days |
|---|---|
| Per-platform description fetchers (known endpoints) | 2 |
| Throttling: `Retry-After`, per-run budget, staggering new boards | 1 |
| Union gate in the core and discovered-board paths | 1 |
| Description column, rematch on profile change, retention | 2–3 |
| Discovered boards: keep gate-passing non-design roles, including Workday full enumeration | 2 |
| **Total** | **8–9** |

## Risks and open items

- **The union gate grows with users.** At 5 users it is small. At hundreds it approaches "store
  everything", so the retention rule and the per-role size cap are not optional.
- Gate pass rate is **assumed** (5–15%), not measured. Measure it with real profiles before sizing storage.
- The sample is 12–30 boards per platform, with means well above medians: the 190k and 400k figures are
  order-of-magnitude.
- Unexplained earlier Workable incident; burst tests are small (25 requests).
- Workday: result cap behaviour and per-tenant request rate are untested.
- Neon plan storage cap **[check]**. The database is 66 MB today.
