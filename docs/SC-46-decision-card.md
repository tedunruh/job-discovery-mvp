# SC-46 — Decision card: resume and profile screens

Drafted 2026-10-19 from the "SC-46 engineering handoff" doc (screens 1, 2a, 2b, 3), checked against
`main` at `4350594`, `docs/SC-51-matching-descriptions.md` and `docs/SC-42-byo-ai-key-spike.md`.
Each decision has a **recommended answer** for you to accept or change. Nothing here is built.

**Updated 2026-10-19 with product answers:** decision 1 changed (Scout pays for resume reading in
alpha; the resume path ships this sprint and does not wait for SC-52), City radius and non-US country
are hidden in v1, and the suggestion card and the deal-breakers card are cut. Decision 8 changed: no
dashboard banner, and the incomplete-profile dot and note are not in this sprint. Decisions 2, 4, 5 and 6
stand as drafted.

Current state, measured: 5 users, 3 onboarded. Profiles today are role categories
(`user_role_interests`), keywords (`user_role_keywords`) and `users.work_types`, set at `/onboarding`.
Alerts fan out per new posting in `scheduler/run_collectors.py` (`fan_out`: `matches_profile`, then
`matches_work_type`).

## At a glance

| # | Decision | Recommended answer |
|---|---|---|
| 1 | How a resume is read | **Decided:** Scout's own key, fast model, one call per upload, hard monthly spend limit, tokens and cost logged per user on every parse. Keep the PDF (2 MB cap); Settings delete must really remove it. Ships this sprint with 2b and 3. SC-52 moves out. |
| 2 | Profile data model | New `user_profiles` plus item and work-history tables. Titles replace categories and keywords. Existing profiles convert on first visit. |
| 3 | What matching uses | Gate fields (titles, level, where) filter now. Skills are saved and used once SC-51 ships. **Deal-breakers are removed from v1 (decided).** **City radius and non-US country are hidden in v1 (decided).** |
| 4 | Routes and flow | `/start` (1), `/profile` (2a, 2b and 3, one template). Everyone already onboarded lands on 3. |
| 5 | "If you'd started last week" | Count on a short debounce, not per keystroke, using the same matcher. Say "so far" when the profile has non-design titles. |
| 6 | Pause alerts | Stops ntfy pushes only. The dashboard keeps listing. No catch-up flood on resume. |
| 7 | Suggestions | Hide the card in v1. |
| 8 | Save rules | **Decided: no dashboard banner.** People must meet the required minimum before they can move on (**a job title only; empty level = any level, empty work type = Remote**; "Save and finish later" is removed), so there is no half-finished state to flag. A dot on "Role preferences" plus a one-line "complete your profile" note on the settings screen is wanted later, **not this sprint** (it blocks nothing). |

## Two findings the handoff doesn't mention

1. **City radius and non-US country have no engine behind them.** Nothing in the repo geocodes
   locations. `work_type()` and `is_us_location()` in `dashboard/filters.py` match US words and state
   abbreviations in the location text, and a remote role outside the US lands in no work-type bucket.
   The canvas's "Within 10, 25, 50 or 100 miles" would need a city-to-coordinates table, and a
   country other than the United States would need `work_type()` generalised. **Decided:** v1 matches on
   the country and on the city name appearing in the posting's location; the radius control and non-US
   countries are hidden. Geocoding is its own follow-up.
2. **Screen 1 has no state for "resume reading unavailable".** Scout pays for parsing under a hard monthly
   limit, so the dropzone needs a state for when the limit is reached or the API is down, pointing to
   "Tell me about yourself instead". A per-person upload cap is also needed, because Replace runs another
   parse. This is a design addition, not a rebuild.

## 1. How a resume is read

**Decided:** during alpha Scout pays. One call per upload, on the fast model, using Scout's own key,
extracting titles, level, skills and work history. The resume path ships this sprint with 2b and 3.
SC-52 (bring your own key, spiked in SC-42) moves out until coach Q&A or on-demand scoring need it.

- **Key:** a Render environment variable, read only by the dashboard process. Never in the scheduler or
  in GitHub Actions secrets.
- **Spend limit:** a hard monthly limit set at the provider, plus an app-side check against the logged
  cost so the dropzone can say "unavailable" before a call is refused. Confirm the provider offers a hard
  cap before relying on it **[verify]**.
- **Cost log from the first call:** a `resume_parses` row per call, including failed ones: user, model,
  input and output tokens, status, time. Store tokens, and compute cost from a dated price table so a
  price change doesn't rewrite history.
- **Per-person cap:** Replace runs another parse, so cap parses per person per month (proposal: 3).
- **File:** keep the PDF in a `user_resumes` row (bytea, 2 MB cap). The screen promises the resume is
  private and can be deleted any time from Settings, so **delete must remove the file**: a hard
  `DELETE` of the row, also run when a user is removed, with a test that View returns not-found
  afterwards. Store no copy of the extracted text beyond the profile fields, and keep resume contents out
  of logs and error reports.
- **Backups:** Neon's history can retain deleted rows for its restore window **[check the plan's window]**.
  Word the delete promise so it stays true ("removed from Scout") or shorten the window.
- **Privacy note on screen 1:** it should say the resume is sent to Anthropic to be read. Check
  Anthropic's current API data-retention terms before writing the final copy **[verify]**.
- **Untrusted output:** the extracted fields pre-fill 2a for the person to check. Nothing acts on them
  until "Looks right, start watching".
- **Replace** re-runs extraction and offers to update fields. Fields the person edited stay unless they say so.
- **Cost of the build:** well under SC-42's 7–9 days, because there is no per-user encryption, settings
  page or retry queue. Not sized here.

## 2. Profile data model

**Recommend:** additive tables, applied to Neon before the code ships.

- `user_profiles(user_id PK, level, one_level_up, country, city, resume_id, alerts_started_at,
  alerts_paused_at, version, updated_at)`. `version` increments on save and drives the SC-51 rematch.
- `user_profile_items(user_id, kind, value, source)` with kind `title` or `skill`, and source
  `resume`, `typed` or `suggested`.
- `user_work_history(user_id, title, company, start_year, end_year)`.
- `users.work_types` stays as it is.

**Migration for the existing profiles:** each saved category becomes the title keywords it already
stands for (from `ROLE_CATEGORIES`), and each keyword becomes a title. The old tables stay and are
read-only until the cutover, so nothing a person sees changes until they save.

**Level:** classify a posting's level from title words (Senior, Staff, Principal, Lead, Manager; no
marker means Mid) in `role_categories.py`. The canvas order is Mid, Senior, Staff, Principal, Lead or
manager. "One level up" from Lead or manager is undefined; recommend the checkbox is simply not offered
for the top level. An empty level matches any level (decision 8).

## 3. What matching uses

**Recommend:** adopt the SC-51 split exactly.

- **Gate** ("What I'll match on"): target titles, level, work type, country and city. These filter the
  dashboard and the alerts, and they are the union gate that decides what discovered boards store.
- **Read for** ("What I'll read for"): skills only. Saved from day one, used once SC-51's description
  storage exists (estimated 8–9 days). Keep the field; helper copy "Saved. I'll start using this soon."
  (product's proposal). It makes no promise about filtering, so it stays true.
- **Deal-breakers: removed from v1 (decided).** The "Anything you'd rather not hear about?" card is
  dropped from 2a, 2b and 3, and `deal_breaker` is not a profile item kind. Deal-breakers mostly live in
  the description ("on-call"), which Scout can't read until SC-51, so a v1 card could not keep its
  promise. Revisit when SC-51 ships. The "What I'll read for" group then holds one card.

## 4. Routes and flow

**Recommend:** the handoff's proposal, with one template.

- `/start` is screen 1, for people with no `onboarded_at`.
- `/profile` is 2a, 2b and 3: the same cards, with setup or settings mode and an empty or filled profile.
- `/onboarding` redirects to `/start` for new people and to `/profile` for everyone else.
- People already onboarded go straight to 3 with their converted values. They are not asked to start over.

## 5. "If you'd started last week"

**Recommend:** a server-side count of postings from the last 7 days that match the draft profile, using
the same matcher as alerts and the same posted-date basis as the dashboard's "posted within" filter.
Recompute after a short pause in editing, not per keystroke. With title-only setup (decision 8), 2b's
side panel switches from the placeholder to the real preview as soon as a title exists, not a title and a level.

**Conflict with SC-51, accepted:** discovered-board roles that pass no one's gate are not stored, so a
brand-new profile's count undercounts them until the catch-up rescan runs. It is accurate for design
titles (always stored) and for any role at the core boards. When the profile has a non-design title,
show "N so far" and a help line that the count grows as Scout scans more boards. The draft profile's gate
joins the union only on save.

## 6. Pause alerts

**Recommend:** pause stops ntfy pushes only. The dashboard is a pull surface, so it keeps listing roles.

- State is `alerts_paused_at` (null means on). "Alerts have been on since [date]" is `alerts_started_at`,
  set when the person presses "start watching" and reset on resume.
- In `fan_out`, skip paused users before building their bucket. Roles posted while paused are not alerted
  on resume, so there is no flood.
- Existing users get `alerts_started_at = onboarded_at`.

## 7. Suggestions

**Recommend:** hide the "Something I noticed" card until its signals are defined. With 5 users there is
too little open and apply data to suggest anything meaningful, and `fg.suggestion` stays in the gallery.

## 8. Save rules

**Decided: no "Finish your profile" banner on the dashboard.** Setup can't be left half-done, so a banner
would flag a state that doesn't exist. Instead:
- **Setup:** the person can't move past 2a or 2b until the required minimum is filled. Everything else
  can stay empty, and they can still act on the dashboard and edit later via "Role preferences".
- **Incomplete profile, later, not this sprint:** a dot on the "Role preferences" button and a one-line
  note at the top of the settings screen (along the lines of "Complete your profile"), both gone once
  the profile is complete. It blocks nothing, so it is out of SC-46's build. When it is picked up: the dot
  is a new Field Guide component (CSS, macro, gallery row) that needs a text equivalent for screen
  readers and must not be the only signal (the note covers that), and "incomplete" needs a definition
  of which optional fields count.
- **Required minimum, decided: a job title only,** the same on 2a and 2b. The handoff said a title, a
  level and a work type; that is relaxed.
  - An empty level means **any level**. "Also show me roles one level up" is only offered once a level is set.
  - An empty work type falls back to the existing default (**Remote**, `DEFAULT_WORK_TYPES`).
  - This widens what a new person is alerted about (roles at every level), and it changes 2b's copy:
    the action bar note "Add a title, a level and a work type to continue." and the disabled-until-three
    rule on the primary button become title-only. Designer to update the copy.
- **"Save and finish later": removed (decided).** With the minimum enforced there is no draft state for
  it to save. The action bar on 2a and 2b has the primary button only.
- **"Looks right, start watching":** saves, sets `alerts_started_at` and `onboarded_at`, and lands on the
  dashboard.
- **Screen 3, nothing unsaved:** note "All changes saved." with the primary button disabled. Warn before
  leaving with unsaved changes.

## Rollout and testing

**Existing profiles: import, then ask for a review.** The 3 onboarded users are converted automatically so
their alerts never stop. On their next sign-in they see the new profile screen pre-filled with the converted
values, framed as "review your profile", which gets first-use feedback without an outage. The 2 users who never
onboarded go through the new flow. Forcing onboarding without converting would mean either stopping their
alerts until they finish or falling back to the old tables anyway.

**Why testing is still needed.** The risk is not what testers see: it is the schema change, the new matcher in
`fan_out` (it decides who is alerted, for everyone, every 15 minutes) and the deploy. A push to `main`
auto-deploys the dashboard, and the collector runs whatever `main` is at its next dispatch, so the two can be on
different versions for a while.

**No separate test stack: a Neon branch of production is the stack.**
1. Rehearse the schema and the conversion on a Neon branch, then run the same script on production.
2. Run the old and the new matcher side by side on the last 7 days of stored postings for each converted user.
   Results must match, or every difference must be explained. This is the main check: converting categories to
   titles is lossy (a "Product design" chip would not word-match a posting titled "Product Designer").
3. Run the dashboard locally against the branch, with the offline unit tests and `scripts/quality_check.py`.
4. Check the resume path on the branch: the cost log rows are written, the per-person cap holds, and Settings
   delete removes the file (View then returns not-found).
5. Never dry-run the collector against production; use the branch.

**Order that stays safe.**
1. Apply the additive tables to Neon first (old code ignores them).
2. Ship code that reads the new profile when one exists and falls back to the old tables, so a Render and
   collector version mismatch can't break alerts.
3. Leave the old tables read-only for a sprint, so rolling back is a revert.
4. Drop the old tables in a later sprint.

## Suggested build order

1. Schema, conversion of existing profiles, and the title and level gate in `role_categories.py` and `fan_out`.
2. `/profile` for 2b and 3 (no AI), plus pause alerts and the preview.
3. Resume path (screen 1, 2a) on Scout's key, this sprint: parse call, cost log, per-person cap, stored
   PDF with a real delete, and the unavailable state.
4. Skills applied to descriptions, and deal-breakers reconsidered, after SC-51.

## Open or unverified

- Whether the provider offers a hard monthly spend cap, and Anthropic's current API retention terms
  (both feed the screen 1 privacy copy).
- Neon's restore window for deleted rows (the delete promise).
- The level classifier and the preview count have not been run against real titles. Measure the
  classifier's agreement on a sample of stored titles before relying on it.
- Effort figures for steps 1, 2 and the extraction step are not estimated here; size them once you have
  accepted or changed the answers above.
