# SC-51 slice 4 — Decision card: skills applied to descriptions, and deal-breakers

Drafted 2026-10-23 from `docs/SC-46-decision-card.md` (skills saved but unused; deal-breakers removed from v1
"until SC-51 ships") and `docs/SC-51-matching-descriptions.md`, checked against `main` at `141e73c`.
Each decision has a **recommended answer** for you to accept or change. Nothing here is built.

## Where things stand (measured today, counts only)

- **Descriptions exist now.** 734 open roles have stored text (all of them design roles), out of 11,747 open
  design roles: only the roles that pass someone's gate get text, by design. Average length is about 5,500
  characters. The collector adds them every run, and a change to what someone wants triggers a quiet catch-up.
- **Skills exist, almost nobody has them yet.** 5 users, 1 profile, holding 13 skills (all typed, none from a
  resume read). The profile screen says "Saved. I'll start using this soon." and nothing uses them.
- **Nothing shows a description.** The dashboard lists roles (50 per page, up to 1,000) and each row links out to
  the company's own posting. Alerts are a title and a link.
- **Alerts:** 141 in the last 7 days across everyone.
- **No AI is involved in this slice.** Matching skills against text is plain word matching on Scout's server.
  No cost, no key. Scoring with a model stays with SC-52 (bring your own key).

## At a glance

| # | Decision | Recommended answer |
|---|---|---|
| 1 | What a skill does | **Point out, never filter.** A role that mentions your skills gets a line saying which. No role is hidden or reordered because of skills. |
| 2 | How a match is decided | Whole word or phrase, any capitalisation, against the stored text. No synonyms in v1. Measure on real descriptions first. |
| 3 | Where it shows | The dashboard row only. Alerts stay as they are. |
| 4 | When it is worked out | When the dashboard page is built, for the roles on that page. Nothing new is stored. |
| 5 | Roles with no stored text | Show nothing. Never "0 skills matched", which would read as a bad fit. |
| 6 | Deal-breakers | Don't bring them back yet. If you want them: phrases that **flag** a role ("Mentions on-call") but never hide it, as a second step. |
| 7 | Skill list shape | One list, as today. No must-have vs nice-to-have, no years of experience, no domains. |
| 8 | Copy | The skills helper line changes from "Saved. I'll start using this soon." to something true. |

## 1. What a skill does

**Recommend: point out, never filter.** Skills help you decide which roles to look at first. They do not
decide what you are told about.

- Filtering on skills would change who is alerted (the thing everyone trusts most) on a rule nobody has seen
  work. A role that mentions none of your skills may still be right (people list what they have used, not
  everything they could do).
- Hiding on a miss is also unsafe while only some roles have text: a role without stored text would look like
  a miss.
- If it turns out to be useful, ranking or filtering can come later from the same label, with real numbers.

## 2. How a match is decided

**Recommend:** a skill matches when it appears in the text as a whole word or phrase, ignoring capitalisation
("Figma" matches "figma", not "Figmatic"; "user research" matches that exact phrase). No synonyms or
related terms in v1 ("UX research" will not match "user research"). Matched skills are listed by name, so a
wrong match is visible rather than hidden in a score.

**Before any screen is built, measure it.** A read-only script runs the one real skill list against the 734
stored descriptions and reports: how many roles match at least one skill, how many match three or more, and the
skills that never match. If most roles match nothing, the rule is too strict and we change it first. This is a
few hours, not a slice.

## 3. Where it shows

**Recommend: the dashboard row only.** For example "Mentions Figma, user research" under the title, in Ink
text with an icon, never colour alone (Field Guide rule). It needs a spec row in the gallery before use.

Not in alerts: for the core companies the text is fetched after alerts are sent so alerts are never delayed,
and a push notification has room for a title and a link. Putting skills in it would mean fetching text before
alerting.

## 4. When it is worked out

**Recommend: when the dashboard page is built, for the roles shown on that page, with nothing stored.** The
page asks for the stored text of only those roles (about 50 rows, roughly 250 KB), matches them in Python, and
throws the text away. Changing your skills takes effect on the next page load, with no rematch job and no new
table. If this proves slow or the dashboard gets heavy, store the result later.

## 5. Roles with no stored text

**Recommend: show no line at all.** A role has no text when nobody's gate passed it (for example you widened
the Work type filter a minute ago, and the catch-up has not reached it), or when the board has none.
"No skills matched" would be false. Silence is accurate.

## 6. Deal-breakers

The SC-46 answer was "removed from v1, revisit when SC-51 ships". It has shipped, so this is the revisit.

| Option | What it does | Risk |
|---|---|---|
| **A. Leave them out (recommended for now)** | Nothing changes. | None. One person has a profile; there is no data on whether anyone wants this. |
| B. Flag | Phrases you list ("on-call", "contract") put a line on the row: "Mentions on-call". Role stays. | Low. Wrong matches are visible and harmless. Adds one list to the profile (the Field Guide "avoid" chip already exists). |
| C. Hide or skip the alert | A role that mentions a phrase is hidden or not pushed. | **High.** A phrase in a long text is often in a different sentence ("no on-call required"). Roles vanish silently. Not recommended. |

If you want deal-breakers: **B, as a second step after skills are out and you have seen how well skill
matching reads.** It reuses the same matching and the same line on the row, so it is cheap once skills exist.

## 7. Skill list shape

**Recommend: keep one list.** The SC-51 write-up floated must-have vs nice-to-have, years of experience and
domains. None is collected today, and each is a new field to ask people for. Add them only if the first
version shows a clear need.

## 8. Copy

The skills card's helper line has to change when this ships, because "I'll start using this soon" stops being
true. Suggested: "I'll point out roles that mention these." (needs your approval; sentence case, no promise
about filtering).

## Rough size

| Step | Work | Estimate |
|---|---|---|
| 0 | Measurement script on real descriptions (read-only) | a few hours |
| 4a | Matching function with tests, the dashboard query for the page's text, the line on the row, a Field Guide spec row, quality check, helper-line copy | 1 to 2 days |
| 4b (only if wanted) | Deal-breakers as flags: profile list, same matcher, same line | 1 day |

No schema change in 4a or 4b. No AI. No new cost.

## Questions for you

1. **Point out, never filter.** Is "never filter or reorder on skills" right for the first version?
   *Recommended: yes.*
2. **Run the measurement first (step 0)?** It tells us whether plain word matching is good enough before we
   build a line on the row. *Recommended: yes, and I will report the numbers before building.*
3. **Dashboard row only, alerts unchanged?** *Recommended: yes.*
4. **Deal-breakers: A (leave out), B (flag), or C (hide)?** *Recommended: A for now; B later if you want
   them. Not C.*
5. **Helper-line copy:** is "I'll point out roles that mention these." acceptable, or do you want to write it?
6. **Anything you want skills to do that this card doesn't cover?** (For example a "good fit" badge, or sorting
   by fit. Those need a definition of "fit" first, and are not part of this recommendation.)

## Not decided here

- Using a model to score fit (SC-52, bring your own key).
- Synonyms or related skills. Wait for the measurement.
- A "complete your profile" note that asks for skills. Separate from this slice.
