# Scout sprint log

A new chat per sprint, opened in this repo's folder, starts by reading `CLAUDE.md` and this file.
Chats are named `Scout — <year> Q<n> Sprint <n>` (e.g. `Scout — 2026 Q4 Sprints 1–2`) and filed in the
sidebar's **Scout** group. Update this log at the end of each sprint.

## Sprint 1 (done)
Remote-US filter, multi-tenancy (per-person data, magic-link sign-in), dashboard polish, design
tokens (Field Guide).

## Sprint 2 (done on the engineering side)
**Goal:** learn how alpha testers actually use it, and let that decide what's built next.

Shipped:
- **Reliability:** source-health detector fix (a quiet board is not a broken one), scheduler-gap /
  notification-failure / crash alerts to the health channel, healthchecks.io outage watchdog,
  rate-limited sign-in emails, 30-day server-side sessions, engagement logging
  (`scripts/engagement.py`), automated UI checks (`scripts/quality_check.py`).
- **Coverage:** from 59 companies to ~8.7k boards. All roles stored for the core companies;
  discovered boards scanned for design roles only; platforms: Greenhouse, Ashby, Lever, Workable,
  Workday (fixed a paging bug that truncated results), Rippling. Discovery tooling
  (`scripts/discover_boards.py`) finds boards from Common Crawl and verifies each.
- **Product:** "posted within" and Work type filters (default: past week + Remote), onboarding
  pre-checks Product Design, optional keyword profile for non-design roles, and **alerts follow
  each person's saved Work type**.
- **Ops:** repo made public (free Actions minutes), keep-awake for the free Render plan.

Numbers at close: ~8.7k boards, ~10.5k open design-gate roles, collector runs every 15 min
(6-10 min each), all scanned sources healthy.

Incidents (see `QUALITY_LOG.md`): a long dry-run transaction blocked one real run; the first
all-at-once Workday import ran 17 min and crashed on a dropped idle connection (fixed by
batching, reconnecting, and delivering core alerts before the discovered-board scan).

Not done / yours: SC-20 (pricing question to users), SC-27 (alpha check-ins).

## Open items for the next sprint
- **SC-46** resume -> tailored search (the real "open" version of search; replaces the keyword
  stopgap and the design-only default). Needs job descriptions for matching (not stored for
  non-design roles today) and a decision on who pays for AI parsing (see `docs/SC-42-*`).
- Coverage: Lever (thin), ~800 Workable names never validated, the starter-list companies
  that need a manual platform lookup.
- Alert volume vs. precision: Remote-US Product Design is ~2-4 roles/day; widening Work type
  gives ~20/day. Tune with real tester feedback.
- Optional: DMARC record for the sending domain; refresh `README.md`.
