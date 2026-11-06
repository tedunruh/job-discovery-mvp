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

## Sprint 3 (done on the engineering side; chat "Scout — 2026 Q4 Sprint 3")
**Goal:** turn "a list of design jobs" into a tool that knows who you are and where each job stands: a profile, a
resume, descriptions to match against, Applications, and a score for how well a job fits.

Shipped (details and root causes in `QUALITY_LOG.md`):
- **Profiles and resumes (SC-46):** a profile per person (target titles, level, skills), title and level matching
  for the dashboard and alerts, and a resume reader (Claude Haiku 5.5, Scout pays, per-person and monthly caps,
  every call logged in `resume_parses`). Settings, a single page header, an Alerts switch.
- **Job text (SC-51):** descriptions are stored for roles that pass someone's gate, discovered boards and Workday
  keep non-design roles that pass, a settings change that widens what is wanted triggers a quiet catch-up, and a
  skills minimum on the profile uses the stored text.
- **Open roles and Applications (SC-58, SC-63, SC-59, SC-69):** two tabs; "Did you apply?" when you come back
  from a job; Yes moves it to Applications with a toast and Undo; Hide with an optional reason; day groups (Today,
  Yesterday, Earlier this week, Last week), company monograms, Scout's line, and a warmer empty state. Added at
  the close: every opened job keeps asking "Did you apply?" until answered (it used to expire after 24 hours and
  only the latest asked), and a "Viewed jobs" list of what you opened, last opened first, ignoring the filters.
- **Pay and fit (SC-71, SC-70):** pay ranges collected from the boards (Ashby, Greenhouse, Lever as data) and read
  from description text elsewhere, with guards for equity, bonus and OTE; a 0 to 10 fit score shown as five pips,
  from the posting's top requirements (read once per role, only for roles on a page someone is shown, capped by
  `POSTING_MONTHLY_CAP_USD`), the person's resume text and skills, and title and level. About $0.0004 a role.
- **Reliability and ops:** a crash that keeps happening sends one alert, not one per run, and a warning before the
  Neon plan runs out (SC-65); an ATS that starts refusing requests is left alone for the rest of the run (SC-61);
  taps on push alerts are counted (SC-55); the public repo's history no longer names testers (SC-54).
- **Design system:** Field Guide gained Tabs, Toast, Switch, Progress, Button working, the Job card (desktop and
  phone), Monogram, Fit and Day heading, a Suggested button and one subtle outline for every outlined action,
  all mirrored in Figma; Albert Sans replaced Avenir Next, Parchment became
  linen, and Fraunces renders at weight 400 (SC-68).
- **Designed, not built (SC-64):** how Scout follows a job after you apply: a push on day 5 ("Did you hear back?"),
  a week later again, "Should I close it?" on day 21, never more than one push a day, no weekly digest. Mock on the
  Sprint 3 design canvas; decisions on the SC-64 board card.

Numbers at close: 575 offline tests, 375 browser checks (`scripts/quality_check.py`), about 29k open roles stored, of
which about 2k have text and the fit score reads more as pages are seen. Collector runs stayed at 8-12 minutes.

Incidents: Workable refused ~425 requests in one run (SC-61, fixed above); a fit-score check passed or failed with the
data until it was made independent of wrapped titles and places (and its fit-line twin, which went stale the day real fit
lines appeared: both are fixed in `quality_check.py`); a ranking nobody asked for (SC-53) was built and removed.

Not done / yours: SC-20 (pricing question to users), SC-27 (alpha check-ins).

## Sprint 4 (in progress; chat "Scout — 2026 Q4 Sprint 4")
**SC-72, Apply with Scout, built in three PRs.** Decisions (Ted, 11/6): three PRs with the panel first; tailored files are PDF and Word
generated fresh in a Scout style (pure-Python libraries, so the pip-only Render build keeps working); Sonnet 5.5 writes the tailored resume
and letter (reading stays on Haiku 5.5); 10 tailorings per person per month as the starting number.
1. **Panel and Prep (built, in review):** the panel beside the list, Prep from stored data with no model call, the scroll fold, the focus
   rules, the phone sheet, the edge states. Branch `sc-72-panel-prep`.
2. **Tailor and Review (next):** a structured resume read, the tailor call with a validator that rejects anything not in the resume
   (numbers, names, skills), per-person and Scout-wide caps, every call logged, Review with Undo and Redo and "Not added".
3. **Letter, Ready and export:** the optional cover letter (placeholders for unknowns, can be written later without using up the limit),
   PDF and Word, saved with the job, the Tailored marker, and the files saved with the application when you answer Yes.

## Open items for the next sprint
- **Build SC-64** (check-in pushes and the Did you hear back / Should I close it rows). It needs a card of its own and
  the "What did they say?" menu and stages from the pipeline mock (Interview, Offer, Not moving forward) for the Yes
  path, plus a place to store "closed by you". It also stops new-job alerts for jobs already applied to or hidden.
- **Fit score follow-ups:** sort or filter by fit, a "what matched" detail, use fit to decide alerts. The skills part
  counts hits over all of a person's skills, so people with many skills rarely reach five pips: tune if testers say
  scores run low. A "Pay listed" filter, and Pay as a hide reason, wait on testers.
- **Viewed jobs / questions:** the question has no cap, so someone who opens many jobs sees many (the heaviest two had
  about 25 waiting on deploy; Ted: that is how people work, leave it). Watch whether anyone asks for "answer all".
- Coverage: Lever (thin), ~800 Workable names never validated, the starter-list companies
  that need a manual platform lookup. Workable, Rippling and Workday pay is only read for roles whose text is stored.
- Alert volume vs. precision: Remote-US Product Design is ~2-4 roles/day; widening Work type
  gives ~20/day. Tune with real tester feedback.
- Optional: DMARC record for the sending domain; refresh `README.md`.
