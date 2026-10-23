# SC-51 slice 4 — Decision card: skills applied to descriptions, and deal-breakers

Drafted 2026-10-23 from `docs/SC-46-decision-card.md` (skills saved but unused; deal-breakers removed from v1
"until SC-51 ships") and `docs/SC-51-matching-descriptions.md`, checked against `main` at `141e73c`.
**Updated 2026-10-23 with product answers:** skills are a score with a cutoff, and roles under it do not show.
That replaces the first draft's "point out, never filter". "3 of 10" was only an example, so the starting number
is not fixed; the measurements below are there to help set it. All the recommendations on this card are accepted.
Nothing here is built.

## Where things stand (measured today, counts only)

- **Descriptions exist now.** 735 open roles have stored text (all design roles), out of about 11,750 open design
  roles: only roles that pass someone's gate get text, by design. Average length about 5,500 characters.
- **Skills exist, almost nobody has them.** 5 users, 1 profile, holding 13 skills (all typed, none from a resume
  read). The profile screen says "Saved. I'll start using this soon." and nothing uses them.
- **Nothing shows a description.** The dashboard lists roles (50 per page, up to 1,000), each linking out to the
  company's own posting. Alerts are a title and a link.
- **No AI is involved.** Matching skills against text is plain word matching on Scout's server: no cost, no key.
  Scoring with a model stays with SC-52 (bring your own key).

## Measured: what a cutoff would do

The one real skill list (13 skills) run against the 735 roles that already fit that person's titles, level and
work type. A skill matches as a whole word or phrase, any capitalisation.

| A role is shown if it matches at least | As written | With bracketed text removed from skills |
|---|---|---|
| 2 skills | 498 (68%) | 517 (70%) |
| 3 skills | 289 (39%) | 315 (43%) |
| 4 skills | 170 (23%) | 195 (27%) |
| 5 skills | 79 (11%) | 117 (16%) |
| 7 skills (about half) | 16 (2%) | not run |

- **A percentage of the skill list is the wrong cutoff.** Half of 13 is 7, which leaves 16 roles and an almost
  empty dashboard. The best role in the whole set matches 8 of 13. A percent also punishes people who list more
  skills. A **count** does not.
- **Exact phrases undercount.** "Accessibility (WCAG 2.2)" matched no role as written, because no job text
  contains that phrase. With the bracketed part removed ("Accessibility") the 3-skill cutoff shows 43% instead
  of 39%. "JSON" also matched nothing: that is a real miss, not a bug.
- **Most roles match one to four skills.** A cutoff of 3 keeps about four in ten of the roles that already fit.

## At a glance

| # | Decision | Answer |
|---|---|---|
| 1 | What a skill does | **Decided (Ted): a score with a cutoff. Roles under the cutoff do not show.** |
| 2 | The cutoff | **Accepted: a count of matched skills, chosen by the person on their profile. Not a percent.** The starting value is **not decided** ("3 of 10" was an example): pick it from the measurements once real people have skills. |
| 3 | Roles with no stored text | **Accepted: always shown, marked as unscored.** Never hidden. |
| 4 | Where it applies | **Accepted: the dashboard.** Alerts stay as they are for now. |
| 5 | How a match is decided | **Accepted:** whole word or phrase, bracketed text removed from the skill first, no synonyms in v1. |
| 6 | Telling people what is hidden | **Accepted:** a line on the dashboard, "12 roles are hidden by your skills cutoff", with a way to show them. |
| 7 | Few skills or none | **Accepted:** the cutoff is never higher than the number of skills; no skills means no cutoff. |
| 8 | The profile preview counts | **Accepted:** they follow the cutoff, or they would promise roles the dashboard hides. |
| 9 | Deal-breakers | **Accepted: leave them out for now.** If wanted later, flag a role ("Mentions on-call"), never hide it. |
| 10 | Copy | **Accepted:** the suggested lines in section 10 (the number in them is a placeholder until decision 2 is settled). |

## 1 to 5. Decided and accepted

- **A score with a cutoff.** A role's score is how many of the person's skills its stored text mentions. Under
  the cutoff the role is not shown; at or above, it is.
- **A count, not a percent**, set by the person on the Skills card ("Show roles that match at least [N] of my
  skills"). The person who lists 5 skills and the person who lists 20 each choose what they want. **The starting
  value of N is still to be set**: "3 of 10" was an example. On the one real list, 3 keeps about four in ten of
  the roles that already fit (see the table), which is where I would start if you want a suggestion.
- **Unscored roles show.** A role has no stored text when nobody's gate passed it (for example you widened the Work
  type filter a minute ago and the quiet catch-up has not reached it) or the board has none. Hiding those would
  drop roles only because Scout has not read them yet. They carry a small "not scored" note, so the dashboard
  never implies a score that does not exist.
- **Dashboard only.** For the core companies the text is fetched after alerts are sent so alerts are never
  delayed, so a score is not available in time to filter an alert. Revisit once people ask.
- **Matching:** the stored text is lower-cased; each skill, with any bracketed part removed, must appear as a
  whole word or phrase. "UX research" will not match "user research". Matched skills are listed by name on the
  row, so a wrong match is visible rather than hidden in a number.
- **Worked out when the page is built,** for the roles on that page (about 50 rows, roughly 250 KB of text),
  and thrown away. Nothing new is stored. Changing the cutoff or the skills takes effect on the next page
  load. If this proves slow, store the score later.

## 6. Telling people what is hidden (accepted)

A filter that hides roles silently looks like a broken dashboard. **Recommend** a quiet line above the list,
"12 roles are hidden by your skills cutoff. Show them", where "Show them" turns the cutoff off for that view
only. It uses an existing Field Guide pattern if one fits, otherwise it is a new component with a gallery row
first (house rule).

## 7. Few skills, or none (accepted)

With a cutoff of 3 and only 2 skills saved, nothing could ever pass. **Recommend:** the effective cutoff is the
lower of the person's setting and the number of skills they have; no skills means no cutoff and nothing is
hidden. Most people have no skills today, so for them nothing changes.

## 8. The profile preview counts (accepted)

"If you'd started last week" and "With your unsaved change" count roles with the same matcher as the dashboard.
If they ignore the cutoff they promise roles the dashboard then hides. **Recommend:** they apply the cutoff. They
need each role's text, so the preview fetches it for the roles it counts (a week of matches, a small set).

## 9. Deal-breakers (accepted)

The SC-46 answer was "removed from v1, revisit when SC-51 ships". It has shipped, so this is the revisit.

| Option | What it does | Risk |
|---|---|---|
| **A. Leave them out (recommended for now)** | Nothing changes. | None. One person has a profile; there is no data on whether anyone wants this. |
| B. Flag | Phrases you list ("on-call", "contract") put a line on the row: "Mentions on-call". Role stays. | Low. Wrong matches are visible and harmless. Adds one list to the profile (the Field Guide "avoid" chip exists). |
| C. Hide or skip the alert | A role that mentions a phrase is hidden or not pushed. | **High.** A phrase in a long text is often in another sentence ("no on-call required"). Roles vanish silently. |

Given that skills now hide roles, **C is not recommended**: two ways to hide roles on text matching, one of them
crude, is how a dashboard starts to feel wrong. Accepted: A. If they are wanted later: B, after skills are live.

## 10. Copy (accepted)

The skills helper line must change. Accepted wording: "I'll hide roles that match fewer than [N] of these." Plus the
cutoff control's label ("Show roles that match at least [N] of my skills") and the dashboard line in decision 6.
Sentence case, plain words. [N] is the person's own number.

## Rough size

| Step | Work | Estimate |
|---|---|---|
| 4a | Matching and cutoff function with tests (including no text, few skills, brackets); the dashboard query for the page's text; the cutoff control on the Skills card (new Field Guide control, gallery row); the hidden-roles line; preview counts follow the cutoff; quality check and log | 2 to 3 days |
| 4b (only if wanted) | Deal-breakers as flags: profile list, same matcher, same line on the row | 1 day |

No schema change is expected: the cutoff is one number per person, and could live in the existing profile row
(one additive column, `skill_cutoff`, if so; applied to Neon before the code ships, as always). No AI. No new
cost. The matching rule above was measured before any screen was built.

## Still to decide

1. **The starting value of the cutoff.** Not fixed. Suggestion for when you want one: 3, from the measurement above.
   Decide it when more than one person has skills, since the table is one list of 13.

## Not decided here

- Using a model to score fit (SC-52, bring your own key).
- Synonyms or related skills ("UX research" and "user research"). Wait for how the cutoff reads on real people.
- Applying the cutoff to alerts. Needs the text before the alert goes out.
- A "complete your profile" note that asks for skills. Separate from this slice.
