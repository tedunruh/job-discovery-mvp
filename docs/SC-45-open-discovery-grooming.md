# SC-45 — Open discovery to all roles and all companies: grooming

Groomed 2026-10-05. Numbers are measured from the live pipeline today (all 59 companies fetched
read-only), not estimated. Plan/pricing limits are from memory and flagged **[check]**.

## Headline

This is **two projects with different risk**, and the card should be split:

- **A. All roles at the 59 companies we already track.** Cheap to turn on, because the collectors
  *already download every posting and throw away the non-design ones.* The hard part is not
  fetching: it's storage, the first-ingest notification flood, and the dashboard.
- **B. More companies.** This is the real growth lever and the slow, unglamorous one: no ATS offers
  an all-jobs feed, so every company has to be found and verified one at a time.

Doing A first is useful on its own and de-risks B.

## Measured today

| Measure | Value |
|---|---|
| Postings available across the 59 companies, all roles | **8,074** (Greenhouse 6,333 · Ashby 1,120 · Lever 373 · Workday 147* · Workable 101) |
| Postings we store today (design only) | 308 (255 open) — **~26× growth** from turning the filter off |
| Median postings per company | 82; largest: Databricks 887, Stripe 718, Anthropic 645 |
| Fetch time, all 59 companies, sequential | **35 s** (~0.6 s/company) |
| Scheduler run duration in production | 0.7–1.3 min |
| Raw payload for 8,074 postings as fetched | **~94 MB** (~11.6 KB each; Greenhouse `content=true` pulls full HTML descriptions) |
| Whole database today | 13 MB (postings 4 MB) |

\*Workday is under-counted: its collector pre-filters by a design keyword search, so all-roles Workday
needs separate pagination work. Only 3 of 59 companies use it.

## The four questions on the card

**1. What does "all boards" mean?** Both, but separable. *All roles* = remove the design filter on
companies we have (data is already in memory each cycle). *More companies* = more rows in the shared
directory. It doesn't mean more ATS platforms: Greenhouse, Lever, Ashby, Workable, Workday already
cover the common cases, and each new platform is a new collector for diminishing coverage. Add a
platform only when a specific high-value company needs it.

**2. Store everything, filter per user?** Yes — with three hazards below that need real design:

- **First-ingest flood (highest risk).** The collector treats any never-seen posting as new and
  fans it out to notifications. Turning on all roles would make ~7,800 postings "new" in one cycle.
  Needs a **silent backfill**: the first time a company (or a role class) is ingested, mark postings
  seen/notified without sending anything.
- **"Empty preferences = show everything."** `matches_categories` treats a user with no picks as
  wanting everything. That's harmless while only design roles are stored; with all roles it means a
  user with no picks gets 8,000 alerts. Flip the default: no profile → no non-design alerts. The
  design categories stay as today's default profile.
- **There is no taxonomy for non-design roles.** `ROLE_CATEGORIES` has five design buckets. All
  roles needs either a broader taxonomy or per-user title keywords. This is the same problem SC-46
  (resume → tailored search) solves, so **design them together**, not twice.

**3. Volume and cost.**

- **Storage.** 8k postings × ~11.6 KB raw is ~94 MB — fits today. At ~300 companies it's ~350 MB and
  at ~1,000 companies ~1.2 GB, past a typical free-tier cap **[check Neon plan limits]**. Cheap fix,
  do it in A: stop storing the full raw payload (and stop requesting `content=true`) for roles
  nobody has a profile for; store title, location, URL, posted-at, and a content hash.
- **Write load.** Every cycle upserts every posting one round trip at a time. 8k single upserts on
  Neon is minutes, not seconds. Needs **batched upserts** and change detection (hash), so unchanged
  postings don't rewrite.
- **Dashboard.** `get_open_postings` returns every open posting and filters in Python, and the page
  renders them all. At 8k that needs server-side filtering + pagination.
- **Collector time.** 35 s for 59 companies is ~1,000 companies in ~10 min sequentially, ~1–2 min
  with a small thread pool. Fine — but see the next point.
- **GitHub Actions minutes.** Runs are ~1–2 min billed (including checkout/pip) at ~48/day ≈
  2,000–2,900 min/month. If the repo is **private**, the free tier is ~2,000 min **[check repo
  visibility and plan]** — we may already be near it before adding anything. Longer runs make it
  worse; this is the unknown most likely to bite first.
- **Source health.** The "baseline" should track *total* postings per company, not design-role
  count, so "Databricks dropped from 887 to 0" is detectable. Simplifies the SC-29 logic.

**4. Fix the 9 broken sources first (SC-29).** Done: all 59 healthy.

## Recommendation: split the card

**SC-45a — All roles at the existing 59 companies** (~6–9 days)
- Ingest: remove design-only gate; slim storage; batched upsert + hash change detection; silent
  first-ingest backfill; health baseline on total count — **3–4 d**
- Per-user filtering: broader role taxonomy or keyword profile; notifications default-off for
  non-design; coordinate with SC-46 — **2–3 d**
- Dashboard: server-side filter + pagination — **1–2 d**

**SC-45b — Grow the company directory** (~6–8 days)
- ATS detector: given a company name/domain, probe board slugs on Greenhouse/Lever/Ashby/Workable
  and verify they return real postings — **2 d**
- Add-company flow (this *is* SC-30, "self-serve company tracking"): shared directory + existing
  `user_companies` model, mostly UI — **2–3 d**
- Concurrent collector + Actions-minutes headroom — **1 d**
- Seed batch: run the detector over a list of target companies — **1–2 d**

## Highest-risk unknowns

1. **Actions minutes / repo visibility** — cheap to check, expensive if wrong.
2. **Does anyone want non-design roles?** The alpha testers are design-focused. All-roles is a
   product bet for the public launch (SC-37). A is justified mainly as groundwork; confirm with
   the alpha testers before investing in B.
3. **Taxonomy/matching quality** for roles we have no classifier for.
4. **ToS and rate limits at scale** — thousands of companies is more load on public ATS APIs
   (SC-40 covers the written answer).
5. **Neon plan limits** — verify before B.

## Housekeeping

SC-28 ("Expand source coverage beyond 59 companies") is absorbed by SC-45b and can be removed.
