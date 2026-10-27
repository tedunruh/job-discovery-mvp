# Build quality log

Started 2026-10-01, after a retro on how to measure whether build-process changes
are actually helping: not "are we using the tool," but whether work ships correct
the first time, with less rework, measured against real incidents from this
project - not hypothetical QA scenarios.

Every entry below is a real piece of shipped work. **Rework count** is how many
follow-up correction messages it took after the work was first called "done"
before it was actually correct. Each rework gets a **root cause category** - a
named, specific failure mode, not "mistake was made" - and where the category is
preventable by a mechanical check, that check gets added to the standing list
below so it's never relearned the hard way twice.

The goal is the average rework count trending down over time, and fewer *novel*
root-cause categories appearing - i.e. we're actually learning, not just logging.

## Standing checks

Run these before calling any UI change done. Each exists because it caught a real
bug that already shipped once - see the dated log entry it links to.

> **Automated:** `python scripts/quality_check.py` runs the viewport, bounds,
> overflow and toolbar-alignment checks below (plus computed-style drift) across
> six viewport sizes. Verified to fail on each regression class it targets. The
> rest still need a human eye.

- [ ] **Viewport correctness.** `<meta name="viewport" content="width=device-width, initial-scale=1">`
      is present on every page - check the actual shared head partial, don't assume
      it's inherited. Verify with `window.innerWidth` at an emulated mobile size and
      confirm it reports the true width (a bare ~980 means the tag is missing or not
      taking effect, regardless of how correct the CSS looks). *(2026-10-01 #1)*
- [ ] **Numeric alignment, not eyeballed.** Any row of elements meant to align
      (toolbar items, label/value pairs, icon+text groups) gets checked via
      `getBoundingClientRect()` - compare `centerY` (or `top`, whichever is the
      actual alignment intent) across every element, not a screenshot glance. A
      few px of drift is invisible at a glance and still wrong. *(2026-10-01 #2)*
- [ ] **Selector-scope audit.** After editing a CSS rule that targets multiple
      selectors at once (e.g. unifying font-size across siblings), diff
      `getComputedStyle()` for every property that rule touches - not just the one
      you meant to fix - on every element the selector matches, before vs. after.
      A fix for one property can silently override another (color swept up while
      fixing font-size). *(2026-10-01 #3)*
- [ ] **Fixed-size decorative assets scale, never clip.** Any Figma artwork with a
      native pixel canvas (illustrations, decorative backgrounds) that's centered
      over a responsive page must scale down via `transform: scale(min(1, 100vw /
      <native-width>px, 100vh / <native-height>px))` to fit the viewport - never
      `overflow:hidden` clipping. Clipping doesn't just crop edges; elements near
      the canvas edge (often the most important ones - start/end markers, a CTA)
      disappear below ordinary viewport widths, and an arbitrary crop into the
      middle of the artwork can make unrelated elements look misaligned or
      overlapping even though their real relative positions are untouched. Verify
      with `getBoundingClientRect()` that every element in the canvas is within
      `[0, innerWidth] x [0, innerHeight]` at a width well below the native
      canvas size, not just a screenshot at native size. *(2026-10-01 #4)*

## Log

### 2026-10-01 — Dashboard/login/onboarding mobile pass
**Shipped:** viewport meta tag, toolbar side-padding (48px → 16px), toolbar
vertical alignment, link color on "Edit roles."
**Rework count:** 3
**Root causes:**
1. *Verification method didn't match the real failure mode.* The whole
   mobile-responsive redesign was "verified" via emulated viewport dimensions set
   directly by tooling - which bypasses the exact bug a real phone hits without a
   viewport meta tag. The check didn't exercise the actual failure path.
2. *Eyeballed instead of measured.* Toolbar alignment was confirmed by screenshot
   only; a 2.5-3px offset across two nested causes (font-size mismatch, then a
   `display:contents` form quirk) wasn't visible until numerically measured.
3. *Selector-scope bleed.* The fix for #2 unified `font-size`/`line-height` across
   `.fg-check`, `.fg-link`, and `.role-count` in one rule that also carried
   `color` - overriding `.fg-link`'s distinct link color as a side effect of a fix
   aimed at something else entirely.
**Standing checks added:** all three above.

### 2026-10-01 — Sign-in page route animation (Figma motion, nodes 121:2052 / 140:137)
**Shipped:** a dashed-trail reveal animation plus destination-pin bounce, implemented
from Figma's motion data across a desktop (1440x900) and mobile (390x844) canvas.
**Rework count:** 1
**Root cause:** *Fixed-size decorative canvas clipped instead of scaled.* The two
canvases were centered over the page and clipped by `overflow:hidden` whenever the
viewport was narrower than their native size - which is most real browser windows,
not an edge case. This single flaw produced four distinct-looking symptoms reported
together: the start/end markers (positioned near the canvas edges by design)
disappearing first as the window shrank, and an arbitrary crop into the middle of
the curve making unrelated dashes appear to overlap or misalign, even though their
true relative positions (re-verified against a fresh Figma fetch) were untouched.
Worth noting for next time: the user's report read as four separate bugs; it was
one. When several visual complaints arrive together about the same component,
check whether a single structural cause (here: clip vs. scale) explains all of them
before investigating each symptom individually.
**Standing checks added:** the fourth one above.

### 2026-10-01 — Route animation: destination pin too far from wordmark
**Shipped:** moved the pin closer to the wordmark; changed the animation from an
infinite 4.5s loop to running once per page load (replaying only on reload).
**Rework count:** 1
**Root cause:** *Assumed a fixed ratio between two independently-positioned
systems.* First attempt multiplied a canvas-space pixel offset by the same
`--route-scale` factor used to shrink the canvas, assuming the pin-to-wordmark gap
would then stay proportional at any viewport size. It doesn't: the wordmark is
positioned by `body`'s flexbox centering (a function of viewport height and the
card's own content height), while the canvas is positioned by a CSS transform (a
function of `--route-scale`) - two unrelated layout systems with no fixed ratio
between them. Verified correct at the viewport it was tuned on (1440x900, exactly
on target) and silently wrong elsewhere - a smaller test viewport (800x600) showed
the pin actually overlapping the wordmark (gap of -29px). Fixed by measuring the
real gap with `getBoundingClientRect()` after layout and setting the offset
precisely, recalculated on resize, instead of deriving it from a formula.
**Standing checks added:**
- [ ] **No fixed-ratio assumptions across independent layout systems.** If two
      elements are positioned by different mechanisms (flexbox/grid centering vs. a
      transform, a sibling's layout vs. an absolutely-positioned overlay, etc.),
      don't assume their relationship scales by a shared factor just because one
      side of it does. Verify a derived spacing/alignment value at more than one
      viewport size - tuning against a single size and assuming the formula
      generalizes is how this shipped wrong the first time.

### 2026-10-01 — Route animation: fixing one end pushed the other off-screen
**Shipped:** mobile route canvas now solves for scale AND offset together so both
the pin (top) and the start dot (bottom) land at independent target gaps.
**Rework count:** 1 (but caused entirely by the previous entry's own fix, not
independently reported broken until the user hit it on a real device)
**Root cause:** *A rigid transform has one vertical degree of freedom; two
constraints need two.* The previous fix pulled the canvas down (via
`--route-offset-y`) to bring the pin closer to the wordmark - correct for the top,
but `translateY` shifts the *entire* canvas as one rigid unit, so the same move
pushed the start dot, already near the bottom edge by design, further down too.
On mobile this pushed it 38px past the viewport bottom entirely (and well past
real mobile Safari/Chrome's bottom toolbar before that). Not caught at the time
because verification checked the pin-to-wordmark gap (the thing being fixed) but
not the start dot's position (a different element, unrelated to that specific
change) - the regression was real but invisible to a check scoped only to what
was being changed. Fixed by recognizing the real constraint: the on-screen
distance between the pin and the start dot is fixed by *scale* alone (translate
can't change it, since it moves both ends equally), so hitting two independent
gap targets needs solving for scale first - from the required pin-to-start
distance - then offset, rather than offset alone.
**Standing checks added:**
- [ ] **A fix verified only at the element(s) being changed can still break a
      sibling.** When a fix moves, resizes, or restyles a shared container (here:
      translating the whole canvas to fix the pin), re-check every other element
      inside that container too, not just the one the fix targeted - they share
      the same transform/layout context and a move that's correct for one can be
      wrong for another. One rigid transform has exactly as many free parameters
      as it has (translate = 1 per axis, scale = 1 more) - if a problem has more
      independent positional constraints than that, translate/offset alone cannot
      solve it and the extra parameter (usually scale) has to be solved for too.

### 2026-10-05 — SC-29: the "9 broken data sources"
**Shipped:** health detector no longer treats "no design roles right now" as an
anomaly; Amplitude and Workhuman re-mapped to Ashby; stuck sources reset.
**Rework count:** 0 (diagnosed before any fix was shipped)
**Root cause:** *A health signal that conflated two different states.* The
detector counted "fetched fine, zero design roles" as a possible schema break,
the same as "fetched nothing / titles blank." For a source with a design-role
baseline, three quiet cycles flipped it to `isolated`, and isolation is sticky
(writes are skipped until a human resets it) - so a healthy company that simply
had no design opening for a few days then silently stayed hidden when it did post
one. Clerk's Brand Designer was being missed this way. Of the 9 "broken" sources,
7 were false positives of this kind; 2 (Amplitude, Workhuman) had genuinely moved
ATS. Found by fetching each endpoint directly and checking whether it returned
real, titled postings, rather than trusting the status column. Fix separates
"endpoint alive, nothing relevant" (`record_no_design_roles`, healthy) from
"empty/blank result" (the existing anomaly path).
**Standing checks added:**
- [ ] **A sticky alarm state needs a precision check.** Any detector that latches
      (isolated, blocked, quarantined) and suppresses data until a human clears it
      must be audited against ground truth - hit the real source and compare - not
      just reviewed for whether its trigger *can* fire. A false-positive latch
      loses data silently, which is worse than a noisy alert.
- [ ] **Dry-run pipeline changes against live data inside a rolled-back
      transaction** with notifications stubbed, and read the resulting rows before
      shipping.

### 2026-10-06 — SC-45a: all roles at the existing 59 companies
**Shipped:** the collector stores every role (not just design), batched upserts
with change detection, concurrent fetching, a silent first-ingest backfill,
per-user keyword profiles, and a paginated dashboard.
**Rework count:** 0 (all defects below were caught by a rolled-back dry run
against live data before anything shipped)
**Root cause:** *Change detection hashed a volatile field.* The "skip unchanged
postings" hash included `ats_posted_at`, but Workday derives that timestamp from
relative text ("posted 3 days ago"), so its seconds differ on every fetch and all
147 Workday rows looked changed every cycle - the "do nothing" run still wrote
147 rows. Found only because the dry run's second pass was checked for write
count, not just for correctness of output. Fixed by hashing date precision only.
**Also designed up front, from the grooming:** turning the filter off would have
made ~7,800 existing postings "new" and fanned them out to notifications, and
"empty preferences = show everything" would have sent a no-profile user every
role. Both were closed before building (silent backfill; non-design roles need an
explicit keyword match).
**Standing checks added:**
- [ ] **Idempotency: a run with no upstream changes must write ~0 rows.** For any
      sync/ingest job, run it twice back-to-back in a rolled-back transaction and
      assert the second pass's insert/update counts
      (`pg_stat_xact_user_tables`). Nonzero means a volatile field is leaking into
      change detection, and the job is doing needless writes forever.
- [ ] **Before widening an ingest filter, trace what "new" triggers downstream.**
      Anything keyed off "first time seen" (alerts, counters, emails) fires for the
      whole backlog the moment the filter opens. Dry-run it and read the
      notifications it would send.

### 2026-10-06 — Dashboard refresh from Figma (node 117:2047)
**Shipped:** Work type / Posted dropdown filters (new Field Guide `.fg-filter`,
`.fg-menu`, `.fg-radio`, ghost button), whole-row job links with hover tint,
labeled Applied toggle, header "Role preferences". Quality check updated for the
new toolbar markup, plus a new menu-in-viewport and Escape-closes check.
**Rework count:** 1
3. *Mobile layout copied the desktop grouping instead of re-deciding it.* On
   phones the row kept the desktop "Second" group (Discovered + Applied) as one
   unit, so Applied lined up with the Discovered timestamp instead of the title.
   Fixed by dissolving the group (`display: contents`) and placing Applied
   beside the title, centered on its first line. New automated check:
   `Applied toggle centered on title line` at phone widths, mutation-tested.
**Caught before shipping:**
1. *Inherited rule collapsed a new component.* An old `.filters form { display:
   contents }` from the previous toolbar removed the box of the new menu (also a
   `<form>`), so menus rendered in-flow instead of floating. Caught by screenshot.
2. *Check measured against a width the bug itself changes.* The first menu-bounds
   check compared against `innerWidth`; on a mobile page an overflowing menu widens
   the layout viewport, so `innerWidth` grew to fit it and the check always passed.
   Found by mutation-testing the check (shifting the menu off-screen on purpose).
   Fixed by comparing against the requested viewport width.
**Standing checks added:**
- [ ] **Mutation-test every new check.** Break the thing a new check guards
      (deliberately, then revert) and confirm the check fails before trusting it.
      A check that can't fail is worse than none.
- [ ] **Bounds checks use the true viewport width**, never `innerWidth` or
      `scrollWidth` read after the element under test has rendered.

### 2026-10-06 — UI fallback font for non-Apple devices
**Shipped:** a self-hosted fallback face for devices without Avenir Next, each weight
mapped one step lighter so it keeps Avenir's color. Fonts installed locally so the
quality check and screenshots render real type rather than the container's default.
**Rework count:** 1 (first pass read too heavy at matching weights; Applied gap too wide)
**Root cause:** *Font substitution judged by family, not by rendered weight.*
**Standing checks added:**
- [x] Automated: `UI fallback font loads` - every UI weight of the self-hosted
      face actually loads (a 404 otherwise drops silently to generic sans-serif).
      Mutation-tested by removing one woff2 file.

### 2026-10-06 — HOTFIX: Work type filter 500'd in production
**Shipped:** Work type pill label computed in Python (`work_type_label`) instead of
a Jinja `map("extract", …)` filter, which doesn't exist in Jinja (it's Ansible's).
**Rework count:** 1 (reported from production: choosing Remote gave an Internal
Server Error)
**Root cause:** *Verification only exercised the default state.* Every check and
screenshot loaded the dashboard with Work type "Any", which short-circuits before
the broken filter call; any real selection crashed. Made worse because the choice
is saved in the session, so the user stayed locked out until it was reset.
**Standing checks added:**
- [x] Automated: `dashboard renders with Work type …` / `… Posted …` - loads the
      dashboard under every filter combination and requires HTTP 200.
      Mutation-tested against the broken code (failed on all 6 partial selections).
- [ ] **Exercise every state a control can put the page in**, not just the one it
      loads in - especially state that persists (session, cookies, DB).

### 2026-10-13 — SC-50: Rippling + scaling Workday
**Shipped:** Rippling collector (public v2 API, real remote/hybrid/on-site flag), a Workday paging
fix, a cheap per-tenant change marker, and fair (least-recently-checked-first) scan ordering.
**Rework count:** 0 user-visible; one self-inflicted production delay (below).
**Root causes found before shipping:**
1. *A "total" read from the wrong page.* Workday reports the match count only on the first results
   page; later pages say 0. The collector read it per page, so every search stopped after two pages
   (40 results). The four Workday companies already being watched were returning a fraction of
   their open roles (Wolters Kluwer: 68 of 451). Found by comparing a stored marker to the paged
   result count while building the change check - not by any alert or error.
2. *Alphabetical order starves the tail under a backlog.* With a time budget shorter than the
   work, processing by name meant the same early boards won every run. Fixed by
   least-recently-checked-first.
**Self-inflicted incident:** a rolled-back dry run held one transaction (with row locks on live
`companies`/`source_health` rows) open for ~50 minutes against the production database. The real
22:00 collector run blocked on those locks and took 23 minutes instead of ~3 until the connection
dropped. No data lost, one delayed run.
**Standing checks added:**
- [ ] **Never hold a long transaction on the live database for a dry run.** Run multi-run or
      long dry runs on a Neon branch (a copy), not in a rolled-back transaction on production:
      uncommitted writes still take row locks that block the real scheduler.
- [ ] **When a loop's stop condition comes from a response field, check it on every page, not
      only the first.** Page through a case you know is large (hundreds of results) and compare
      against the reported total.

### 2026-10-15 — Field Guide component gallery (/field-guide)
**Shipped:** a signed-in gallery page rendering every token (from tokens/*.json)
and every component in every state, each with a "use this when" note. The Applied
toggle moved out of index.html into Field Guide (`.fg-toggle` + `fg.toggle`
macro), the filter pill markup into `fg.filter`; the dashboard now uses both
macros. Disabled styles added for checkbox, radio, input and toggle.
**Rework count:** 0 so far
**Why:** there was no single place showing the approved set of controls, so each
new screen was reconstructed from memory - the root of visual drift.
**Standing checks added:**
- [x] Automated: `gallery shows every Field Guide class` - fails when a `.fg-`
      class exists in components.css with no example on /field-guide.
      Mutation-tested (added a stray `.fg-badge`, check failed naming it).
- [x] /field-guide joins the viewport, overflow and mobile-width checks at all six sizes.

### 2026-10-15 — Field Guide mirrored into Figma (variables, components, gallery)
**Shipped:** `_Primitives` + `Semantic` variable collections (95 variables, scoped,
aliased, WEB code syntax), `Elevation/Raised` effect style, a Components page of
variable-bound component sets, and a Field Guide gallery page of instances.
**Rework count:** 0 reported; 4 caught before handoff
**Caught before handoff:**
1. *Vector resized to a box distorts its path.* Check marks drawn as a path then
   `resize()`d to 18×18 stretched into slashes. Draw paths at native size and
   position them instead.
2. *`resize()` after hug sizing pins the frame (twice).* The gallery frame and 32
   swatches were created as hug-content auto-layout, then resized for width -
   which silently resets both axes to fixed, collapsing them. Set width, then
   set the primary axis back to AUTO.
3. *Default frame clipping cuts effects.* Menu shadows were clipped by their
   parent frames; gallery layout frames now have clipsContent off.
4. *Wrong component-property key.* Looked up a text property by its default
   value ("Option") instead of its name ("Label").
**Standing checks added:**
- [ ] **Figma builds: after any `resize()`, re-set `primaryAxisSizingMode` /
      `counterAxisSizingMode` explicitly, and screenshot every composed frame**
      before reporting - all four issues above were invisible in the returned
      structure and obvious in the first screenshot.

### 2026-10-15 — Field Guide gallery broke the typeface rule
**Shipped:** removed monospace from the code gallery (class/token names now use
the UI font with slight tracking) and the monospace face from the Figma gallery page.
**Rework count:** 1 (reported by Ted: "you didn't even follow the font guidelines")
**Root causes:**
1. *Wrote an exception to a rule instead of following it.* To make `--fg-…` read
   as two hyphens I set token names in monospace and documented it as a
   "gallery-only exception" - the rule says never add another typeface, full stop.
2. *Assumed a font was unavailable instead of testing it.* Figma text was set in
   a substitute face because Avenir Next didn't appear in the font list; loading it was
   never attempted. (It genuinely can't load in the Figma MCP runtime - it's a
   local font on Ted's Mac - but that was found only after the report.)
**Standing checks added:**
- [x] Automated: `only Field Guide typefaces` - every rendered text element's
      first-choice family must be Fraunces, Avenir Next or its fallback face,
      on every page. Verified against the monospace version (failed, naming `code`).
- [ ] **No self-granted exceptions to CLAUDE.md rules.** If a rule blocks a
      reasonable goal, ask - don't write the exception into a comment.

### 2026-10-15 — Control type from Ted's Figma component edits
**Shipped:** Ted edited the Figma Components (Avenir Next); his choices pushed to
code: buttons, links, checkbox/radio/toggle labels and menu rows 14→16px
demibold; input values and messages 16px medium. Onboarding's local `.fg-check`
override removed (the component now owns it). Rule recorded in the README.
**Rework count:** 0 (design change, not a defect)
**Process note:** Figma → code changes were read from the components themselves
(fonts, sizes, fills, spacing per variant) and diffed against the original
build, rather than eyeballed from a screenshot.

### 2026-10-15 — UI minimum weight is Medium
**Shipped:** `.fg-page` now sets Medium (500), so body copy, hints and notes no
longer fall back to the browser's 400. Regular is reserved for Fraunces.
**Rework count:** 0 (design rule from Ted)
**Root cause of the gap:** the base style never set a weight, so any text without
an explicit weight silently rendered Regular - including onboarding subtext/hints.
**Standing checks added:**
- [x] Automated: the typeface check now also fails any UI text whose
      computed weight is below 500. Verified against the old base style (failed on
      onboarding subtext and hints).

### 2026-10-15 — Placeholder text passes AA
**Shipped:** `--fg-color-text-tertiary` (input placeholders) 45% → 70% Ink, 2.6:1 → 5.2:1.
Secondary text stays at 70% (Ted). Typed values stay distinct at 100% Ink. Figma
`Semantic/color/text/tertiary` now aliases `ink/70`; the unused `ink/45` primitive is gone.
**Rework count:** 1 (Ted flagged secondary-text contrast; measuring showed secondary
already passed at 5.2:1 and only placeholders failed)
**Root cause:** the tertiary step was picked by look, never measured; README even
labeled it "placeholders only" as if placeholders were exempt (they aren't, WCAG 1.4.3).
**Standing checks:**
- [ ] Manual: any new or changed text-color token gets its ratio on Parchment measured
      and written in the README contrast line; text tokens must be >= 4.5:1.

### 2026-10-15 — Dashboard to Figma 226:849
**Shipped:** Sage band (support-subtle, 277px) with the topographic contours from Figma
behind the header; list moved into a raised Parchment card (radius xl, `--fg-shadow-raised`);
rows separated by an 8px gap and 16px padding instead of rules, 4px radius for the hover tile;
page inset 24/16, header-to-card 36px. Contours are `_topography.svg`, exported from the
Figma path (0.1px rounding, verified by checksum against Figma), stroked with `currentColor`
= `--fg-color-signal-attention-subtle`. Phone (no Figma frame): card padding 12px, rows 12px.
**Rework count:** 0
**Not changed (Figma disagrees with itself):** the frame's "Applied" label is loose text at
14px Medium; the Toggle component is 16px Demi Bold, which code follows. Asked Ted.
**Tooling:** `QC_SHOTS=<dir> python scripts/quality_check.py` saves a screenshot per page and
width for side-by-side review against Figma.

### 2026-10-15 — Toggle label Medium; sentence case written down
**Shipped:** `.fg-toggle` label ("Applied") 16px Demi Bold → 16px **Medium** (Ted: demibold
read too strong for a status). Control-type rule updated: status labels join values and
messages at medium. Sentence case is now a written rule (CLAUDE.md, README principle 5);
templates audited, no title-case labels.
**Rework count:** 0
**Figma not updated:** Toggle component (206:57) and dashboard frame Applied labels still
need Avenir Next Medium 16 by hand - the MCP runtime can't load Avenir Next.
**Standing checks:**
- [ ] Manual: new UI copy is sentence case (proper names excepted).

### 2026-10-15 — Fallback font no longer named in docs, gallery or Figma
**Shipped:** every mention of the fallback typeface removed from CLAUDE.md, the README,
the gallery, token descriptions, code comments, check names and this log. Its family is
now "Field Guide UI" and its files `fonts/ui-*.woff2`; the font's license stays as
`fonts/LICENSE.txt` (the open font license requires shipping it with the files).
**Rework count:** 1 (Ted: the fallback was my implementation choice, not a design rule,
so it shouldn't be documented as part of the system)
**Root cause:** *Documented an implementation detail as if it were design intent.*
The design rule is "Fraunces and Avenir Next only"; how non-Apple devices cope is plumbing.
**Standing checks:**
- [ ] Manual: docs and the gallery describe Ted's design decisions, not workarounds I chose.

### 2026-10-18 — SC-46 profile components into Field Guide
**Shipped:** 20 components for the resume and profile screens (chips with deal-breaker
and "new" variants, select, checkbox group, segmented radio group, add row, card with
eyebrow/emphasis/discovery variants, group label, page header + intro, split with sticky
side panel, status strip, action bar, file row, dropzone, list rows with placeholder and
empty states, stat, before-and-after, suggestion), each with a macro and a gallery spec row.
All controls 44px (Ted). Disabled buttons readable (secondary on secondary fill, 5.0:1)
instead of 45% fade. New token `surface.card` = palette `parchment-light` #fbf7ee (Ted
preferred the canvas's flat, lighter card with a hairline border over the raised card);
mirrored to Figma variables. Canvas "Scout Profile Review" re-skinned from the real macros
and the real tokens.css/components.css (uploaded to the canvas), so no hex or px remains.
Also carries the unmerged fallback-font cleanup (3422376) that missed PR #5.
**Rework count:** 2. (1) Floated fieldset legend squeezed the segmented options to 0px
and overflowed phones; caught by the overflow check. (2) Gallery-only `.gallery h2` style
leaked into card titles inside specimens (28px, 64px margin); scoped to `.gallery > h2`.
**Root causes:** *Fieldset/legend layout quirks* (legends aren't ordinary blocks; floats
beside a flex row collapse it). *Page-level element selectors in a gallery restyle the
components it is meant to show faithfully.*
**Standing checks added:**
- [x] Automated: `every control is at least 44px tall` on the gallery. Mutation-tested
      (buttons back to 36px → fails, naming each short button).
- [ ] Manual: gallery page styles target gallery classes or direct children only,
      never bare elements a component also uses (h2, p, ul).

### 2026-10-18 — Albert Sans replaces Avenir Next; SC-46 components in Figma
**Shipped:** Albert Sans (Ted) is the UI typeface: self-hosted 500/600, the old fallback face
removed, token/rules/checks updated. Figma: every Avenir/Roboto layer on Components (36
ranges) and the gallery page (180) moved to Albert Sans (Demi Bold→SemiBold, else Medium);
`font/family/ui` = Albert Sans. Existing Button and Text field reconciled with code (44px,
readable disabled), Checkbox/Radio rows 44px. 21 new Figma components for SC-46, bound to
Semantic variables. Foundations page left alone (Ted's exploration page; mixed fonts).
**Rework count:** 1. `resize()` after setting auto-layout sizing reset heights to FIXED, so
the Card set and fixed-width components collapsed (48px card set). Fixed by re-applying hug.
**Root cause:** the standing check from 2026-10-15 ("after any resize(), re-set sizing")
existed but the build script set sizing modes *before* resize. Order matters: resize first.
**Standing checks:**
- [ ] Figma builds: call `resize()` first, then set `primaryAxisSizingMode` /
      `counterAxisSizingMode`; check returned heights are not the resize placeholder (10px).

### 2026-10-19 — SC-46 step 1: profiles, title and level matching, dual-read
**Shipped:** `user_profiles` and `user_profile_items` tables (additive); target-title and level matching
in `role_categories.py`; alerts (`_wants_posting` in `scheduler/run_collectors.py`) and the dashboard read a
person's profile when they have one and fall back to their role categories and keywords when they don't.
`scripts/convert_profiles.py` (dry run by default, idempotent) and `scripts/compare_matchers.py` (read-only).
Nothing converts anyone yet: profiles are created when the new screens ship, because the old onboarding form
writes the old tables. Rehearsed on a Neon branch of production: old and new matchers identical for all 5
people across 29,086 open postings; dashboard shows the same roles before and after conversion; alert
matching identical; conversion is idempotent; `quality_check.py` 142/142.
**Rework count:** 2. (1) The run-isolation test's fake DB didn't know the new `get_all_user_profiles`, so 3
tests errored; caught by the suite. (2) Undoing a mutation test with `git checkout <file>` also threw away my
uncommitted real edits to that file; caught when a grep for them came back empty, and re-applied.
**Root causes:** *A new DB read added to the scheduler without updating the test's fakes.* *Reverted a test
mutation with git instead of restoring a copy, which discards uncommitted work in the same file.*
**Decisions made while building (each recorded in `docs/SC-46-decision-card.md`):** a title that states no
level passes every level (hiding it would hide most roles); existing people get `alerts_started_at` = account
creation, not onboarding, so the 2 who never finished onboarding keep their alerts.
**Standing checks added:**
- [x] Automated: `tests/test_profiles.py` (22 tests). Mutation-tested: breaking a level pattern, dropping the
      keywords from conversion, and ignoring `alerts_started_at` each fail it.
- [ ] Manual: mutation-test against a copy of the file (`cp` before, `cp` back after), never `git checkout`.
- [ ] Manual: `quality_check.py` boots the app against the database in `.env.local`. A change that reads a new
      table must run it on a Neon branch with the schema applied, and the schema goes to production before the code.

### 2026-10-19 — SC-46 step 2: the profile screen (/profile)
**Shipped:** `/profile` for first-time setup and for settings (one template, two modes), a live side panel
(`/profile/preview` returns the panel as HTML), pause and resume alerts, and the city rule in alerts and the
dashboard. `/onboarding` and the "Role preferences" link now go to `/profile`. Field Guide: `fg.chip(name=)`
and `fg.add_row(list=)`, `field-guide/js/profile.js`, a `[hidden]` rule. `quality_check.py` covers `/profile`
at every width in both modes (a second throwaway user who never onboarded). Rehearsed on a Neon branch with
headless Chromium: 44 steps (setup, error path, chips, level, unsaved count, discard, save, pause, resume,
leave warning, phone width), each with a database check; `quality_check.py` 157/157; 107 offline tests.
**Rework count:** 1. A mutation test showed no test changed the work type and expected it counted; added.
**Root cause:** *A counting function tested only on the parts the first tests happened to change.*
**Decisions made while building (recorded in `docs/SC-46-decision-card.md`):** an explicit "Any level" segment,
because a radio can't be unselected; "reached you / you opened / you applied to" use the gallery wording; the
"change vs last week" figure on This week was left out (no design for it).
**Standing checks added:**
- [x] Automated: `tests/test_profile_screen.py` (31 tests). Mutation-tested six ways; each failed it after the
      work-type test was added.
- [x] Automated: `quality_check.py` runs the profile screen's controls through the 44px check, both modes.
- [ ] Manual: a field that looks like a setting must change what Scout does. City was added to the matcher in
      the same change as its field, not left for later (the deal-breakers card was cut for the same reason).
- [ ] Manual: mutation-test against copies of files, and run each mutation against the *whole* suite.

### 2026-10-19 — SC-46 step 3: resume reading
**Shipped:** `/start` (screen 1), a "Your resume" card on `/profile` (View, Replace, Delete, or an inline dropzone),
and `dashboard/resume.py`: one call per upload to Claude Haiku 5.5 on Scout's own key, returning target titles, a
level with a one-line reason, and skills. What it reads arrives as an unsaved draft on `/profile`; nothing is saved
or alerted on until the person presses save. Caps: 3 reads per person and $5 across everyone per month (UTC), both
enforced on the server before the model is called. Every call, failed ones too, is logged in `resume_parses` with
tokens, cost, price version, model and request id. The PDF (2 MB) is stored in Postgres; Delete removes the row and
wipes the stored reads, and keeps the cost rows. New tables `user_resumes`, `resume_parses`, column
`user_profiles.level_note`. `anthropic==0.125.0` pinned; `scripts/try_resume_read.py` for one real call.
Rehearsed on a Neon branch in headless Chromium, model stubbed: 50 steps (non-PDF, read, draft, View, another
person's file and read, save, the limit and a forced 4th upload, delete, model down, spend cap, oversize, an
existing person's settings, phone); `quality_check.py` 171/171; 146 offline tests.
**Not verified:** a real call to the API (no key was configured yet). Run `python scripts/try_resume_read.py`.
**Rework count:** 2. (1) The first rehearsal expected a dropzone after deleting a resume, but that person had used
all three reads, so the limit message is right; the check was wrong. (2) The upload held a database transaction open
during the model call (up to 25s); it now commits first, with a test for the order.
**Root causes:** *A test expectation written without the rule that applies to the person in that state.*
*Held a transaction across a slow external call, which this log already warns about for production dry runs.*
**Standing checks added:**
- [x] Automated: `tests/test_resume.py` (30 tests) and the resume cases in `tests/test_profile_screen.py`.
      Mutation-tested 13 ways (price, logging, storing, PDF check, level overwrite, off-by-one limit, unknown level,
      temperature, caps, stale reason, delete form, saved-state hand-off, commit-before-call); none survived.
- [x] Automated: `quality_check.py` covers `/start` and the setup view of `/profile` at every width.
- [ ] Manual: no database transaction is open across a call to an outside service.
- [ ] Manual: a rehearsal check asserts the rule for the state the person is in (a person at a limit is not a person
      with a fresh allowance).

### 2026-10-20 — Out-of-credit API error was reported as a bad file
**Shipped:** the resume reader now treats the API's "credit balance is too low" error as "unavailable" (Scout's
problem) instead of "unreadable" (the person's file). `scripts/try_resume_read.py` now prints the API's own words
when a read fails, since the app deliberately turns every failure into a short message for the person.
**Found by:** the first real call, with the Console balance still at $0.00. The API answers an empty balance with an
HTTP 400 (`invalid_request_error`), the same status as a bad PDF, and the app mapped every 400 to "I couldn't read
that file", so a good resume would have been blamed.
**Rework count:** 1 (found live before anyone used it; nothing shipped wrong to a person).
**Root cause:** *A provider error shape the SC-42 spike marked as unverified (`[verify against a live zero-credit
key]`) was assumed, not tested.* The spike said so in writing; the build didn't carry it forward as a test.
**Standing checks added:**
- [x] Automated: a test with the real out-of-credit message maps to "unavailable"; mutation-tested (mapping removed
      -> fails).
- [ ] Manual: anything a spike or plan marks `[verify]` against a live service gets a real example before it ships,
      or a named test, or is removed from the plan. Don't let it fade into an assumption.

### 2026-10-21 — Profile screen polish: no 'Leave site?' on our own actions, progress while reading, tidier layout
**Shipped (Ted's review of the live screen):**
- Replace resume, Delete and Pause/Resume alerts no longer raise the browser's "Leave site?" dialog when you have an
  unsaved edit. Walking away from unsaved edits still asks first.
- While a resume is read: plain text that changes ("Opening your resume…" to "Almost there…") over a progress bar that
  eases toward full and never claims to finish early. The boxed "Reading your resume" message is gone. New Field Guide
  component `fg.progress`.
- The add-a-title and add-a-skill fields now sit above their chips.
- The "I read your resume and filled in what I could" banner is removed.
- The "I'm watching" strip is one compact line to the right of the page title (`fg.status(compact=True)`,
  `fg.intro` takes an aside), with a quiet Pause button.
**Found by:** the live screen, in review. The dialog reproduced exactly (edit, then upload, or edit, then Pause) on a
Neon copy before fixing.
**Rework count:** 1. At 360px the compact status beside the title made the page 3px too wide (`flex: none` on a block
that needed to wrap); the existing "mobile page reports true width" check caught it.
**Root causes:** *An unsaved-changes guard that didn't distinguish the page's own navigations from walking away.*
*A flex child that cannot shrink, placed beside a headline.*
**Standing checks added:**
- [x] Automated: `quality_check.py` now fails if an unsaved edit plus pausing alerts raises a "Leave site?" dialog,
      or if walking away from an unsaved edit stops asking. Mutation-tested (own-navigation exemption removed ->
      both checks fail).
- [x] Automated: tests that the add fields precede their chips, the banner is gone, the status is inside the title
      row, and progress exists in place of a banner (`tests/test_profile_screen.py`, `tests/test_resume.py`).
- [ ] Manual: any "unsaved changes" guard is tried both ways in a real browser: its own actions don't trip it,
      and leaving does.

### 2026-10-21 — Settings had no obvious way back to the dashboard
**Shipped:** the "Settings" label at the top right of the profile screen is now a "Back to dashboard" link
(`fg.header(back=...)`). The setup view keeps its "Getting started" label, since a new person can't use the
dashboard until they have started. The wordmark still links home.
**Found by:** Ted: "how do I go to the dashboard from this page?"
**Rework count:** 0.
**Root cause:** *The only way out was the wordmark, which doesn't read as a link.* A page a person can leave needs
a visible way out.
**Standing checks added:**
- [x] Automated: `quality_check.py` fails if settings shows no "Back to dashboard" link or it doesn't reach the
      dashboard; a test covers settings vs. setup. Mutation-tested (link removed -> both fail).

### 2026-10-21 — Trimmed four redundant items from the profile screen
**Shipped (Ted's review):** removed the "Saved. Your alerts follow this from the next scan." banner after saving, the
"What I'll match on" and "What I'll read for in each posting" group headings with their sub-lines, and the "All
changes saved." note. The save redirect no longer adds `?saved=1`. With nothing unsaved the action bar shows no note,
only the two disabled buttons; "{n} changes not saved." still shows while editing. `fg.group_label` stays in Field
Guide as a component. Browser rehearsals on a Neon copy: 46 steps (setup, settings, pause, resume, discard, leave
warning) and 61 (resume flows); `quality_check.py` 173/173; 154 offline tests.
**Rework count:** 1 (a mutation test showed the banner test never exercised the banner, since the route stopped
passing its flag; the test now passes the flag and asserts nothing renders).
**Root cause:** *Copy added in the first build to explain what the screen did, which the layout and the buttons'
state already said.* Each of the four said something already visible: the cards say what they are, the disabled Save
says nothing is unsaved, and a refreshed page says it saved.
**Standing checks added:**
- [x] Automated: tests that none of the four strings render in setup or settings (including when handed the old flag).
      Mutation-tested (each put back -> fails).
- [ ] Manual: before adding a confirmation or a heading, ask what the screen already shows. Say it once.

### 2026-10-21 — SC-61: 430 of 3,271 discovered boards failed in one run
**What happened:** The 9:26 PM (Central) run raised the health alert "13% of discovered boards failed". The database
shows 425 of the 430 were Workable (369 hot, 56 cold), all on their first failure in a row; Greenhouse and Ashby had
about 10 between them, normal noise. Workable boards had been checked without trouble in the hours before, then
nearly every request in that run was refused. Run length (6-11 min) and database connections were fine, so this was
Workable limiting us, not our run. Core-board alerts go out before the discovered-board scan, so they were not delayed.
**Fix:** once an ATS answers 5 requests in a run with 429/403, the rest of that ATS's boards are left unrequested
for that run (not counted as failures, health and queue position untouched), and the log and an hourly health alert
name the ATS. Hot Workable boards are now checked every other run instead of every run, which halves the load
(they rarely post). The 10% alert still fires for any other kind of mass failure.
**Not changed:** the 10% threshold is right; it caught this. The failure record keeps no error class per board, so the
exact status (429 vs 403) isn't visible in the database; the first lines of the next run's log will show it.
**Rework count:** 0.
**Root cause:** *Workable rate-limits per source, our request rate to it (about 380 boards every 15 min plus
rotating cold ones) was close to its limit, and nothing stopped the collector from pushing on once it was refused.*
**Standing checks added:**
- [x] Automated: tests/test_rate_limit.py (stops after the trip count, 404s don't trip it, one ATS blocked doesn't
      stop another, blocked boards aren't recorded as failed, alert names the ATS, Workable cadence covers every board).
- [ ] Manual: when adding an ATS to the discovered-board scan, check its published or observed rate limit first and
      set TAIL_PER_ATS and the cadence to stay well under it.

### 2026-10-21 — Parchment cleaned up to linen
**Shipped:** `palette.parchment` #f6f2e9 → #faf9f7 and `palette.parchment-light` #fbf7ee → #fefdfd
(Ted: page and card read too yellow and dingy; keep the ethereal feel, cleaner and a little
brighter). Chosen from four rendered options of the settings screen, then brightened. Every
surface, tint and on-action color follows through the semantic layer. Contrast re-measured and
the README updated (Ink 14.0:1, Ink 70% 5.4:1, Forest 7.9:1, disabled button 5.1:1). Figma
`_Primitives` variables updated to match; the SC-46 canvas re-linked to the new CSS.
**Rework count:** 0
**Note:** the local quality check first crashed because the local DB lacked tables added on
`main` (`user_profiles`); applying `db/schema.sql` to the local DB fixed it.
**Standing checks:**
- [ ] Before running the quality check on a fresh `main`, apply `db/schema.sql` to the local DB.

### 2026-10-22 — Alerts: a switch instead of a status line and a Pause button
**Shipped:** New Field Guide **Switch** (`fg.switch`, `.fg-switch`, `role="switch"`): 40×24 track,
Forest when on, a fixed label and a small detail line under it. It is not the Toggle: the Toggle
marks an item (Applied), while the Switch changes a setting and takes effect at once. On settings
it replaces "I'm watching. Since Oct 1. / Pause alerts" with "Alerts" plus "On since Oct 1" or
"Paused since Oct 21" (Ted's idea). `field-guide/js/switch.js` posts it with fetch and flips it
in place, so unsaved edits on the rest of the page survive. Without JavaScript it is a plain form
button: the page reloads with the switch flipped. No notice banner either way (Ted: just the switch). Gallery spec row (on, off, disabled, no detail line),
README row and Figma component added.
**Rework count:** 2 (both caught before commit by the new checks)
**Root causes:**
1. *A form control named `action` shadows `form.action`*: `sw.form.action` returned the button,
   so the fetch went to `/[object HTMLButtonElement]`, failed, and fell back to a full reload.
   Fixed by reading `getAttribute("action")`.
2. *A function-local import was copied into one route and not the other*: the no-JS post
   crashed on `resume_mod` (a NameError). The unit tests render the template, not the route, so
   only the browser check saw it.
**Standing checks added:**
- [x] Automated: quality_check flips the switch with an unsaved edit on the page. The page must
      not reload (checked with a window marker, since the fallback redirects back to the same
      URL), the edit must survive, and the detail line must say the new state. A second,
      JavaScript-disabled context checks the form fallback flips the switch and its date line.
      Mutation-tested: each check fails on its own bug.
- [ ] Manual: in page scripts, read form URLs with `getAttribute("action")`, never `form.action`.
**Note:** "Applied toggle centered on title line" @360/@390 fails locally with "no rows", on
`main` too: the local DB has no recent postings to draw. Not a regression.

### 2026-10-22 — One page header on every page
**Shipped:** The dashboard's hand-built header (an `h1` "Scout" plus its own button row) and the
settings header (wordmark plus a text link) are now one Field Guide component: `fg.header` with
the 36px wordmark first and one or two actions at the end of the row, passed as a call block
(`.fg-header-actions`). Going somewhere is a ghost button ("Role preferences" on the dashboard,
"Back to dashboard" on settings, the same button now); a second action is secondary ("Sign out").
Onboarding keeps its "Getting started" context. The wordmark went from 28px to 36px. The
dashboard's top page padding is gone, so the wordmark sits at the same height on both pages. A
ghost button at the end of the row lines its text up with the content edge. The dashboard keeps a
visually hidden `h1` ("Open roles"). Gallery row, README row and Figma component updated.
`.fg-header-link` removed.
**Rework count:** 1. The first render left the dashboard wordmark 24px lower than on settings
(page padding plus header padding), so the logo jumped between pages.
**Root cause:** *two pages each built their own header*: fonts, button styles and spacing drifted
apart because nothing compared them.
**Standing checks added:**
- [x] Automated: on dashboard and settings at every width, the page uses `header.fg-header`,
      the wordmark is 36px, there are one or two actions and each is 44px and on screen. Across
      the two pages: the wordmark styles match, the first action is the same button (class and
      computed style), and the wordmark sits at the same height. Mutation-tested (a secondary
      nav button and the old page padding both fail at every width).

### 2026-10-22 — Page headline (h1) down to 28px
**Shipped:** `.fg-display--page` (the Fraunces h1 from `fg.intro`, on settings, onboarding and
start) went from 36px (`4xl`) to 28px (`3xl`), a step under the 36px header wordmark (Ted).
Token descriptions now say which size is which. The Figma "Page intro" headline is bound to
`font/size/3xl`.
**Rework count:** 0
**Root cause of the request:** *the wordmark grew to 36px in the page-header change, the same
size as the headline under it*, so the two competed. That change should have rechecked the type
sizes next to the header.
**Standing checks added:**
- [x] Automated: quality_check checks that the settings headline is 28px at every width.
- [ ] Manual: when a header or headline size changes, look at the two together. The header wordmark
      stays a step above the page headline.

### 2026-10-22 — SC-51 slice 1: reading job descriptions (no behavior change yet)
**Shipped:** `collectors/descriptions.py` turns a role's description into plain text: Ashby and Lever
from the list response Scout already downloads (no request), Greenhouse, Workable, Rippling and
Workday with one request per role through a `FetchBudget` (run cap; a platform that answers 429/403
five times is not asked again that run, per SC-61). `collectors/http.py` now honors `Retry-After`
(capped at 30s). `db.set_posting_description` / `clear_closed_descriptions` and the two additive
`postings` columns are in `schema.sql`. Nothing calls them from the collector yet (slice 2).
**Rework count:** 2, both caught before commit. Unit tests found HTML-to-text leaving blank lines
between bullets and a lone "- " for `<li><p>`. The real-endpoint check then found Ashby's own
`descriptionPlain` carried 3+ blank lines in a row.
**Root cause:** *the unit-test fixtures were tidy, and the plain-text fields were assumed clean
while only the HTML path was cleaned.* Real payloads were the only thing that showed it.
**Standing checks added:**
- [x] Automated: tests for both HTML shapes and for plain-text tidying, and 11 mutation checks on
      file copies (budget stop, Workday path, Retry-After cap, and more) all fail when the code is
      broken. Never `git checkout` a file to undo a mutation.
- [x] Manual, done each time a platform reader changes: fetch two real roles per platform
      read-only and check for leftover tags, entities, blank-line runs and the length cap.
- [x] Schema before code: the columns were applied twice (re-runnable) to a throwaway Neon branch
      and both functions exercised there. Production has no description columns yet; apply
      `schema.sql`'s SC-51 block to production before any code that writes them ships.

### 2026-10-22 — SC-51 slice 2: the collector fetches descriptions for wanted roles
**Shipped:** `scheduler/describe.py`. A role is *wanted* when it passes at least one person's gate
(target titles, level, work type, city; old categories and keywords for people without a profile; paused
alerts don't matter). Wanted roles that are already stored get a description: from the list payload when it
has one (Ashby, Lever, Greenhouse's full core board), otherwise one request per role under a 200-request
run budget. Core roles are described after alerts are delivered, never before. A role the platform has no
text for is stored as "" so it isn't asked again. Hourly, long-closed roles lose their text. Nothing new
is stored: roles that weren't stored before still aren't (slice 2b, with the silent catch-up).
**Rework count:** 2 before commit. (1) A rehearsal with the columns missing, i.e. code shipped before the
schema, took down the whole discovered-board scan: one "which roles already have text" query sat outside
the savepoint guard. (2) The first version fetched core descriptions before alerts went out, so a big
catch-up would have delayed alerts by minutes.
**Root cause:** *two kinds of path were only tested on the happy path: the new code's own failure mode (its
table missing) and its place in the run's ordering.* Unit tests with fakes passed both times; only running
the real path on a branch showed them.
**Standing checks added:**
- [x] Automated: gate (each of title, level, work type, city, legacy users, paused alerts), the step (budget,
      failed request, nothing-to-store, nothing pending while requests run, savepoint on a database error),
      the discovered-board scan (changed, unchanged-but-newly-wanted, unchanged-and-done, columns missing)
      and "alerts before descriptions". 16 mutation checks on a copy of the repo all fail when the code is
      broken. Two first passed only because the tests were weak (a fixture that couldn't fail, an
      `AssertionError` swallowed by the code's own `except`): after a mutation survives, fix the test.
- [x] Manual, for any change to the run's order or to a table the run reads: rehearse the real
      `_run` on a throwaway Neon branch **twice**, once *before* the schema exists (the run must survive
      and say so) and once after (storing, then idempotent on the second run). Alerts, health pushes and
      pings are stubbed. Cross-check by recomputing the gate on the stored rows: wanted == described.
- [x] A wide-open gate on the same boards (700 stored on run 1, 156 left for run 2, no failures) proves
      the budget without touching production.

### 2026-10-23 — SC-51 slice 2b: discovered boards keep non-design roles that pass someone's gate
**Shipped:** a discovered board used to keep design roles only. It now also keeps any other role that
passes at least one person's gate (title, level, work type, city; their old keywords if they have no
profile). Ashby, Lever, Greenhouse, Workable and Rippling only: Workday still searches design words
only (slice 2c). A board's **first wide scan is silent**: those roles were already open, so they are
stored dated by the ATS (not "found today") and nobody is alerted. `companies.wide_scan_at` records
that it happened; after it, a new wanted role alerts like a new design role. Setting it back to NULL
asks for another silent catch-up (slice 3's job when a profile widens what is wanted). Their
descriptions come through the slice-2 step, under the same per-run budget.
**Rework count:** 0 before commit. Written from the slice-2 lesson: the column's absence was
rehearsed first.
**Root cause it guards against:** *storing more roles looks like "new roles"*, and alerting old jobs is
the one mistake nobody forgives. Silence is decided per board by a marker, not by guessing from dates.
**Standing checks added:**
- [x] Automated: first scan silent, later scan alerts, design alerts unchanged during the first wide
      scan, a board with nothing wanted still gets marked, unchanged boards cost nothing, Workday
      excluded (worker and scan), column missing means nothing non-design is stored and **nothing is
      closed**. 12 mutation checks on a copy of the repo all fail when the code is broken.
- [x] Manual rehearsal on a throwaway Neon branch with real boards and a stand-in user who wants
      "engineer": column missing (run survives, health alert says why) → first wide scan (76 stored, 0
      alerts, none dated today, 15 boards marked, Workday untouched) → repeat (0 writes) → one stored
      role deleted to look new (1 write, 1 alert).
- [x] Schema before code: `schema.sql` adds `companies.wide_scan_at`; apply it to production before the
      code ships.

### 2026-10-23 — SC-51 slice 3: a settings change that widens what is wanted triggers a quiet catch-up
**Shipped:** discovered boards only store roles somebody's gate lets through, so when a person's
settings let in more (a new target title, a broader level or "one level up", an added Work type, a
removed or changed city), roles that are already open but were never stored have to be picked up.
`role_categories.gate_widened` / `work_types_widened` decide when; the profile save and the
dashboard's Work type filter then call `ask_for_catch_up`, which sets `wide_scan_at = NULL` on every
discovered board but Workday. Each board's next scan stores the newly wanted roles **silently**
(slice 2b's first-wide-scan rule): hot boards within a run, cold ones within about 4 hours. Narrowing,
or editing only skills, asks for nothing. The request is made after the save is committed and can never
fail the save. Core boards need nothing: every role is already stored there.
**Rework count:** 0 before commit, 1 found by rehearsal (below).
**Root cause it guards against:** *a run already in flight finishes later and marks boards "done" using
the old settings*, silently cancelling a request made while it ran. `companies.wide_requested_at` plus
the database-clock time each run read everyone's settings (`DescriptionStep.settings_loaded_at`) lets a
run mark a board only if no request came after it read.
**Standing checks added:**
- [x] Automated: the widening rules (titles, levels, Work types incl. default and Any, city), both save
      routes (asks / does not ask), a failed request never raising into the save, the run handing its
      settings time to the step, and marking against that time. 14 mutation checks on a copy of the repo
      all fail when the code is broken (one first survived, "run passes no settings time", until a test
      was added for it).
- [x] Manual, on a throwaway Neon branch: column missing (run survives, feature off, health alert);
      request made through the real function (8,242 boards asked; core and Workday untouched); an in-flight
      run's marking skipped (0 of 5) and a later run's allowed (5 of 5); then two collector runs: 76
      roles stored silently, none dated "found now", 0 alerts, 15 scanned boards re-marked, the rest
      still waiting.
- [x] Found by that rehearsal: the guard compared `now()` values, which are the *transaction's start*, so
      two things in one transaction tied and the guard let the in-flight run through. Both sides now use
      `clock_timestamp()`. Lesson: compare real clock readings, never `now()`, when ordering two events.
- [x] Schema before code: `companies.wide_requested_at` (additive, in `schema.sql`). Apply it to
      production before this ships; if it isn't there the collector survives with the feature off, but
      the Work type filter and profile save would log a failed request and nothing would catch up.

### 2026-10-23 — SC-51 slice 2c: Workday boards keep non-design roles that pass someone's gate
**Shipped:** Workday only searched design words, so non-design roles never came back from it. It now
also searches each target-title phrase someone wants that the design words would not return
(`describe.extra_workday_queries`: non-design titles, or old keywords for people without a profile,
sorted, at most 6). The change marker carries each phrase's count (`design:451|product manager:184`),
so a new phrase or a moved count triggers a scan, and a quiet catch-up that is owed forces one even when
no count moved. Wanted roles are stored under the same silent first wide scan as other platforms;
Workday is no longer excluded from the catch-up request. With nobody wanting a non-design title there
are no extra phrases and Workday behaves exactly as before: no new requests.
**Paging rule (measured, not assumed):** Workday's search also matches words inside descriptions, so a
phrase's result count is mostly noise (a tenant reported 1,714 results for "product manager" and had 8
such titles). On tenants with 800 to 1,100 results, every title match was in the first five pages. An
extra search therefore stops after two pages in a row with no title containing all the phrase's words,
and never goes past 10 pages. Broad phrases ("engineer") can still outrun that: best effort, by design.
**Rework count:** 0 before commit; two weak tests found by mutation (below).
**Root cause it guards against:** *assuming a search engine's total means what it says.* The first probe
showed "design: 125 results, 0 titles containing design".
**Standing checks added:**
- [x] Automated: paging stops after two quiet pages / at the cap / at the total; titles sharing only some
      of the words are not matches; no extra phrases means exactly the old requests; markers carry each
      count; an owed catch-up forces a scan only when there are extra phrases; Workday gets the silent
      first wide scan and later alerts; the run hands the phrases to the step. 19 mutation checks on a copy
      of the repo all fail when the code is broken. Two first survived (a decoy title sharing one word;
      the run not passing the phrases), so tests were added.
- [x] Manual, throwaway Neon branch, three real tenants with product-manager titles and a stand-in user:
      stored 8, 13 and 2 real PM titles with text, none dated "found now", markers carry the phrase, repeat
      run 0 writes, a removed role with a changed count alerts once, and a catch-up request with unchanged
      markers forces the scan (9 s -> 59 s) and re-marks all three, 0 writes, 0 alerts.
- [x] Found by the rehearsal, not a defect: changing someone's keywords directly in SQL (skipping the
      app's catch-up request) made 5 old non-design roles alert. Gate changes must go through the app so
      the request is made; any script that edits what someone wants must call `request_wide_catch_up`.
- [x] No schema change in this slice.

### 2026-10-23 — SC-51 slice 4a, piece 1: the skill matching rule (no UI yet)
**Shipped:** the rule that scores a role against a person's skills, and nothing that uses it yet.
`role_categories.skill_phrases / skill_regex / skills_matched / effective_cutoff / skill_status`: a skill
matches as a whole word or phrase, any capitalisation, after bracketed text is removed from it; repeats
count once; a one-letter "skill" is not counted; the person's cutoff can never exceed the number of skills
they have; a role with no stored text is "unscored" and never hidden. `db.get_skill_matches` runs the same
rule inside Postgres, returning only a few booleans per role, not the text. No schema change, no behaviour
change for anyone.
**Rework count:** 0. The matching rule was measured on real descriptions before it was written (the card's
cutoff table), which is what changed the first design from a percent to a count.
**Root cause it guards against:** *one rule written twice drifts.* The dashboard will match in Postgres and
the preview and tests in Python, so both take the same `skill_regex` text.
**Standing checks added:**
- [x] Automated: normalising, whole-word, symbols (c++, node.js), no synonyms, repeats, no text, the cutoff
      cap, the three statuses, and the SQL's shape (one boolean per skill, text-only roles, same regex). 15
      mutation checks on a copy of the repo all fail when the code is broken. One "survivor" was a mutation
      that replaced a line with itself: check that a mutation changes something before trusting "missed".
- [x] Manual, read-only on production: Python and Postgres compared on the 735 stored descriptions, with the
      13 real skills and with awkward ones (symbols, brackets, repeats): 0 differences over 1,910 and 697
      matches. Postgres took 250 to 400 ms for 735 roles; fine now, to watch once the dashboard runs it on
      every page load.
- [x] Any change to `skill_regex` must re-run that comparison: Python's and Postgres's regex dialects are
      close but not identical.

### 2026-10-23 — SC-51 slice 4a: skills minimum on the profile and the dashboard
**Shipped:** a person can set a minimum number of their skills a role's stored text must mention
(Skills card: "Show roles that match at least [N of my skills]", 1 to 10, default no minimum). With one set,
the dashboard hides roles that mention fewer, says so ("182 roles are hidden by your skills cutoff. Show
them"), and names what each role mentions ("Mentions Figma, CSS"). A role with no stored text is never
hidden ("Not scored for your skills"). The minimum is capped at the number of skills the person has; no
skills or no minimum changes nothing and costs no lookup. The profile's live preview counts follow the
minimum. Alerts are unchanged. Nobody has a minimum yet, so nothing changes for anyone until they set one.
Reuses `fg.select` (no new Field Guide component). One additive column, `user_profiles.skill_cutoff`,
read and written only through savepoint-guarded helpers.
**Rework count:** 1. My first idea for the line on the row showed "Accessibility (WCAG 2.2)" although only
"accessibility" is matched; shown without the bracket now.
**Root cause it guards against:** *a filter that hides roles looks like a broken dashboard unless it says
what it did and what it could not judge.* So: the hidden count with a way back, the matched skills by name, and
unread roles shown, not hidden.
**Standing checks added:**
- [x] Automated: the filter (drops under the minimum and counts them, never hides unread roles, names skills as
      written, show-them keeps them with an honest line, cap at number of skills, no lookup without a minimum),
      the dashboard page, the "show more" link keeping show-them, the profile preview, the save order and the
      guarded column helpers. 20 mutation checks on a copy of the repo all fail when the code is broken (three
      first survived: the paging link, reading the saved value for the form, and the select's selected value;
      tests were added for each).
- [x] Automated, in the browser (`scripts/quality_check.py`, now 259 checks): changing the minimum counts as an
      unsaved change and putting it back clears the count. The Python tests cannot run `profile.js`, which was
      where this field could silently have been left out of the unsaved-change snapshot. Proven to fail when
      the field is dropped from the script.
- [x] Manual, throwaway Neon branch, real dashboard and real data, no mocks: with a minimum of 2 the
      dashboard goes 254 to 194 roles (60 hidden), with 4 it goes to 72 (182 hidden); every shown role has
      stored text with at least that many matches, or none (shown by rule); "show them" restores all 254. The
      minimum adds about 120 ms to a load that takes 1.8 s on that path (1810 ms to 1927 ms).
- [x] A page change that adds a column must read it through a guarded helper if the page or the collector
      would otherwise break without it (`db._optional`); the column was also applied to production first.

### 2026-10-24 — SC-53: best matches first, and a strict reading of "a design role"
**Shipped:** (1) **Ranking.** The roles a person's titles match are listed closest-first: a title that names a
seniority they wrote ("Principal Product Designer") first, then exact titles, then longer phrases over shorter,
then generic matches ("product designer" anywhere in a title); newest first among equals. Same roles, same
count, only the order: nothing is hidden by it. One saved profile exists (687 matching roles): 21 exact
seniority titles come first, 11 more that contain a seniority title, 86 exact generic ones, then 569 generic.
(2) **Strict design check** (`collectors/filters.is_strict_design_role`) for anyone who picked no role
categories and has no profile: of 11,989 stored roles that pass the loose gate only 4,555 are real design
roles; the rest were engineering and hardware with "design" in the title ("Analog IC Design Engineer"), plus
interior, apparel and instructional design. The pre-filled Role preferences for such a person are now 16 real
design titles instead of six loose words, so what they see in Role preferences is what they are matched on.
**Reality check:** all four people without a profile had already picked role categories, so (2) changes
nothing for anyone today; it protects the next person who picks nothing. (1) is what changes what the one
profile user sees. The dashboard orders the list, alerts are unaffected.
**Rework count:** 3 on the classifier before it was right: a deny-list alone kept noise ("Cafe Assistant
Manager (Design District)"), an allow-list alone lost real roles, and a team name ("Lead Product Designer |
Platform Engineering") dropped a real designer. Each was found by reading random kept and dropped titles from
the real data, not by tests.
**Root cause:** *a one-line request ("show the best matches") has several readings.* The first reading (the
design gate board note) turned out to affect nobody; asking what "best" meant, with their own numbers on the
table, found the real one. Measure who a change touches before building it.
**Standing checks added:**
- [x] Automated: 40 real design titles that must pass and 40 non-design titles that must not (engineering,
      hardware, interior, apparel, instructional, "ux" in other work); the ranking order, ties, whole-phrase
      matching, the dashboard order, and that a person without a profile keeps newest first. 18 mutation
      checks on a copy of the repo all fail when the code is broken.
- [x] Any change to the classifier or the default titles: re-read 45 random kept and 40 random dropped titles
      from `postings` (read-only) before trusting it; and rerun the before-and-after count per user.
- [x] The old-vs-new conversion test now names the real design roles the 16 default titles do not cover
      (VP of Design, UX Engineer, Design Technologist, Experience Designer) so the loss is a decision.
- [ ] `scripts/quality_check.py` was not re-run: no template, CSS or script changed, only the order of rows.

### 2026-10-24 — SC-53: the ranking was taken out again
**What happened:** the dashboard briefly listed matches "closest first". Ted had not asked for ranking: the request
was "what is set in Role preferences should show the best job matches", and the options I offered were my
guesses at it; the one he clicked was the nearest, not what he wanted. He wanted the plain rule: a job that matches
what he set is shown when it is posted, newest first as before. Removed the same day; the strict design check
(for people who picked no role categories) stays.
**Rework count:** 1 (the whole ranking feature).
**Root cause:** *I offered a menu of my own ideas instead of repeating the request back in plain words.* The
answer I should have asked for first was "show me how it works today, and what would you change?".
**Standing checks added:**
- [x] When a request is one line and could mean several things, say back what happens today in two sentences
      and ask what is wrong with it, before offering options. Options I invented are not the user's answer.
- [x] Do not add ordering, scoring or ranking to a list that is already "newest first" unless it was asked for
      by name.

### 2026-10-25 — SC-55: taps on push notifications are counted
**Shipped:** a push alert for a single new job now links to Scout (`/a/<private token>`), which logs an
`opened_alert` event and forwards to the job's own page, with no sign-in needed (a phone often has no session).
`scripts/engagement.py` has a new `from_alert` column. A link preview that only asks for headers (HEAD) is
forwarded without counting; an unknown link goes to the dashboard and reveals nothing. The token is random, one
per person and job, in one new column (`user_postings.alert_token`), read and written through guarded helpers, so
if the column is missing alerts go out with the job's own link exactly as before.
**Deliberate trade-off:** Scout's site sleeps overnight on the free plan (kept awake 6 AM to midnight Central), and
a sleeping site would make the first tap wait about a minute. So between midnight and 6 AM Central alerts keep the
direct link and those opens are NOT counted. Grouped alerts (several jobs in one push) have no single link, so they
are unchanged and not counted.
**Rework count:** 1, in my own tests: a placeholder test that asserted nothing was caught on review and replaced with
a real check of when the token helper is called.
**Root cause it guards against:** *routing a tap through a service that can be asleep or down turns a tracking
feature into an outage of the alert itself.* Hence the quiet hours, the guarded column and the fallback to the
direct link.
**Standing checks added:**
- [x] Automated: the link in the alert, the quiet-hours boundaries (5:59 off, 6:00 on, 23:59 on), the route (valid
      link forwards and counts once with no sign-in; preview not counted; unknown or address-less link goes to the
      dashboard), the token helpers (kept once made, random, missing column gives none), the whole collector run
      (link only while awake and only when a token exists; none even requested overnight) and the summary's column
      order. 18 mutation checks on a copy of the repo all fail when the code is broken.
- [x] Manual, throwaway Neon branch, real alerts: with the column missing the alert is still sent with the direct
      link; with it, the alert carries Scout's link; a tap with no sign-in forwards and counts 1, a second tap 2,
      a HEAD preview 0, an unknown link leaks nothing.
- [x] A change that puts Scout between a person and an outside page must say what happens when Scout is asleep.

### 2026-10-25 — SC-58: applied jobs leave the main list
**Shipped:** the dashboard has a **Status** filter beside Work type and Posted: *Not applied yet* (the default),
*Applied* and *All*, remembered for the session. Marking a job Applied keeps its row where it is for one more page
load, with the toggle checked and "Applied just now"; unchecking it is the undo. On the next refresh or filter
change the row leaves, and the open-roles count already excludes it. Marking returns to the same row (an anchor)
and keeps "Show more" and "Show them" as they were, so the page no longer jumps to the top and forgets where you
were. The applied state was already saved per person; nothing about it changed. No schema change, no new Field Guide
component (the filter, toggle and radio are the existing ones; the kept row's styling is page-local like the other
row classes).
**Was today's behaviour a bug?** (the card's first criterion) No: the state saved and showed the same way every time,
the row simply never left the list. The one real rough edge was the reload to the top and losing "Show more", now fixed.
**Rework count:** 1 in my own work: the kept row counted as one of the 50 shown, so "Show 50 more" miscounted by one
(found by a test I wrote for it, fixed before commit).
**Root cause it guards against:** *a confirmation row that is also a list row gets counted as both.* The kept row is
excluded from the count, from "Showing X of Y" and from the page size.
**Standing checks added:**
- [x] Automated: the three Status views and their empty state, the filter's pill and options, the choice remembered
      and an unknown value ignored (and not wiping the chosen view), the kept row (in place, checked, saying so, not a
      link, out of the count), gone on refresh, settled by a filter change, a new answer or un-marking another job,
      undo returning the job to its place, the anchor and page size on the redirect (capped), and the counts. 21
      mutation checks on a copy of the repo all fail when the code is broken (three first survived; two real gaps
      got tests, one was a cosmetic mutation).
- [x] Browser (`scripts/quality_check.py`, 277 of 277) and by hand on a throwaway Neon branch with real clicks:
      687 open roles, click Applied, 686 with "Applied just now" and the URL on the row, refresh drops it (checked by
      the job's own id, not its title: another company can have the same title), the Applied view shows it, undoing
      from there and going back gives 687, phone layout fine.
- [x] Check a row by its id, never its title, when a list can hold the same title twice.

### 2026-10-25 — SC-62: the sign-in screen shows that the email is on its way
**Shipped:** the sign-in page now says what is happening, following the design mock. The button reads "Email me a
sign-in link". Pressing it shows a working button (spinner, "Sending…", same colors and width, not disabled); the
page does not move. Then "Link sent" with a check mark, and below the button a confirmation, "Check your email" with
the address named ("If you have access, a sign-in link is on its way to <address>. It works once and expires in 15
minutes."). After 30 seconds the button becomes a secondary "Send another link". An incomplete address, too many tries
and a failed request each say so, in the same place. The form still works as a plain post with no script. New Field
Guide piece: the button's working state (`aria-busy` plus `.fg-spinner`), with a gallery section, README row and CSS.
No schema change.
**Copy I wrote that the mock did not have** (check with the designer): the failed-request message ("That didn't go
through" / "Check your connection and try again.") and a title for the bad-sign-in-link message ("That link didn't
work"). The label is now "Email" (sentence case), up from the old lower-case "email".
**SC-33 rule kept:** nothing on the page says whether an address is registered. "Too many tries" is a limit on this
device (5 links per address in 15 minutes, remembered in the browser), not the server's per-address cap, which still
answers every address identically; a test proves a registered, an unknown and a rate-limited address get the same page.
**Rework count:** 2 in my own work, both found by looking at a picture, not by a check: (1) the "Link sent" check mark
never appeared, because an SVG has no `hidden` property and my script assigned it; my browser check only read the
button's label. (2) The route's dashed trail showed through the translucent done button.
**Root cause:** *a check that reads text cannot see a missing icon or a see-through button.* Both are now checked
(the mark's size, the spinner's size and animation) and the page is photographed at desktop and phone width.
**Standing checks added:**
- [x] Automated, server: copy, wording and limits on the form, every message present and hidden, status area after the
      button as a live region, the plain-post page naming the address with "Send another link", identical answers for
      registered / unknown / capped addresses, a hostile address escaped, a bad link's reason shown. 16 mutation checks.
- [x] Automated, in the browser (`scripts/quality_check.py`, 295 checks): at rest; an incomplete address sends nothing;
      sending is busy with a drawn, turning spinner, the same width, one request, a second press ignored; sent shows
      the mark and names the address; nothing above the message moves and it sits below the button; "Send another
      link" unlocks; typing starts again; too many tries and a failed request say so; a phone has no sideways scroll;
      with no script the plain form works. 12 deliberate breakages of the page each fail a check. The request is held
      and answered by the check itself, so no email is ever sent.
- [x] Never trust `element.hidden = false` on an SVG; set the attribute.
- [ ] Not done: the Figma mirror of the Button's working state (a rebuild request for the designer; code is the source
      of truth).

### 2026-10-25 — SC-54: tester names removed from the public repo's history
**Shipped:** the repo has been public since the free Actions minutes, and two sprint write-ups
(docs/SC-42-byo-ai-key-spike.md and docs/SC-45-open-discovery-grooming.md) named testers by first name in
three lines. The current files were already clean; the history was not. History was rewritten so those lines
read "a tester": 147 commits, 18 branches, every branch tip byte-for-byte the same contents as before, only
the commit IDs changed. A full backup of the old history was made first (`before-scrub.bundle`, in the session's
scratch folder; verified complete). A fresh clone of GitHub afterwards has 0 lines mentioning a tester. The site
redeployed from the new `main` (`bf314d7`) with no change in behaviour. No accounts, database rows or sign-ins
were involved: accounts live in Neon, never in git.
**Before searching:** no real email addresses, no API keys or tokens (five provider patterns), and one
connection-string-shaped line that is a placeholder in `.env.example`.
**Rework count:** 2 in the tooling, none in the result: the first rewrite attempt failed because the shell's
plain `python3` is a stub that fails inside git's filter (use the project's own Python), and my first
verification script read the wrong column of git's output and reported "0" for the wrong reason.
**Root cause:** *names were written into documents because the people were the source of the facts.* The
documents were about testers' feedback and decisions, in a repo that was private when they were written.
**Standing checks added:**
- [x] Automated: `tests/test_no_tester_names.py` reads the tester names from `private/` at run time (so no name
      is ever written in the repo) and fails if any appears in a tracked file; it names the file, never the
      person. Skipped on machines without `private/`, so it protects the place the documents are written.
      Proven to fail when a name is added to a copy.
- [x] A verification that can't fail proves nothing: both the rewrite check and the name check were shown to
      report the "before" state (3 lines) before they were trusted to report "after" (0).
- [ ] Remaining, GitHub's side: the 20 merged pull requests keep their old commits reachable through hidden
      links (refs/pull/*) until GitHub support removes them. Not done; the names are no longer in any branch.
- [x] A local branch `backup/pre-scrub` (an older snapshot, 80 files behind `main`) still holds the old history
      on this machine only. It is not on GitHub. Do not push it.
- [x] After a history rewrite, delete stale local branches before using GitHub Desktop's Push or Sync: any of
      them would put the old commits back.

### 2026-10-25 — SC-63: "Did you apply for this one?" when someone comes back from a job
**Shipped:** a job the person has opened says **Viewed** with when (icon plus words). The job they opened *last* asks,
on its own row (a Discovery outline and a question under its details): "Welcome back. Did you apply for this one?",
"If you did, mark it Applied. You opened it 12 minutes ago.", and a **Not yet** button. Yes is the row's existing Applied
toggle (which then follows SC-58: "Applied just now"). If several jobs were opened only the latest asks and the help line
says how many others are marked Viewed. "Not yet" closes it for good until that job is opened again. It appears the moment
the tab comes back into view (a small script asks the server what is true now), and also on any reload. It never takes
focus and never blocks anything; a hidden live region announces it to screen readers. A push-alert tap counts as an open
(SC-55). No schema change: opens were already logged, and "Not yet" is a new kind of event in the same table.
**Rules chosen (check with Ted):** asked about only if opened in the last 24 hours (older opens are just Viewed); only the
single latest-opened job is ever asked about, so after "Not yet" the next-older job does not step in; a job that is not on
the current page (filtered out, or past "Show more") cannot ask, and nothing is asked about an applied job.
**Rework count:** 2, both in my own checks, none in the page: the first sign of a failure was a test that matched the hidden
`<template>` copies as well as the page; and my browser check blamed the page for a focus change that was my own click.
**Root cause:** *a check that cannot tell the thing it checks from its own scaffolding.* Tests now read the page without
its templates; the focus check is taken after the click and before the return.
**Standing checks added:**
- [x] Automated, the rule (`dashboard/return_ask.py`): nothing opened, the latest only, the 24-hour edge, older opens not counted,
      applied, "Not yet" then opened again, the next-older job not stepping in, and the help line singular and plural.
- [x] Automated, server: the question and Viewed lines on the right rows with the right help, several opens, no focus
      attributes, a live region, a job off the page, the templates the script copies, the state endpoint (JSON, never
      cached, applied jobs left out, sign-in needed) and the "Not yet" route (quiet answer for the script, back to the
      same row without it, an unknown job records nothing). 22 mutation checks on a copy of the repo.
- [x] Automated, in a real browser (`scripts/quality_check.py`, 310 checks): open a job in another tab, return, and the row asks
      with no reload and no change of focus; a second job moves the question and the first says Viewed; a reload still shows
      it; "Not yet" closes it, stays closed after a reload and asks again once the job is opened again; Applied answers it; a
      phone fits it. The job's outside page is stubbed, so nothing leaves the machine. 6 deliberate breakages of the script
      each fail a check.
- [ ] Not done: the Figma mirror of the Viewed line and the asking row (code is the source of truth).

### 2026-10-27 — SC-65: a crash that keeps happening sends one alert, not one per run
**Why:** when the database went over its quota, every run crashed and every crash pushed its own alert, a new one every
15 minutes for a day, each with the database's host name in the text. **Shipped:** when a run crashes it asks GitHub
whether the runs just before it crashed too. The first crash alerts at once; while it continues the next alert comes
only when a new two-hour stretch of the clock begins (about one reminder per two hours, "Collector has now crashed 9
runs in a row"); the first run that succeeds after crashes says once, "Collector is back". If GitHub cannot be asked
the alert is sent anyway (a duplicate beats silence). Alert text no longer carries the database address, IP numbers or
connection URL. The external watchdog (healthchecks.io) is untouched and still hears every failure. The workflow now
declares `permissions: contents: read, actions: read` and passes its own `GITHUB_TOKEN` to the collector: no new secret.
The same step also lowered the database to a fixed 0.25 compute unit (SC-65 item 2), logged on the board card.
**Rework count:** 1, caught by my own test rewrite before it left my machine. **Root cause:** *a counter used as the
timer.* The first version reminded every 8th crashed run, but GitHub returns a bounded list of runs, so in a day-long
outage the count would stop growing and the reminders would stop with it. Reminders now follow the clock; the count is
only used in the wording ("100+" when it hits the limit), and a test runs 30 hours of runs and counts 15 reminders.
**Standing checks added:**
- [x] Automated (`tests/test_crash_alerts.py`, 30 tests): the streak read (current run left out, cancelled runs ignored,
      timed-out counts as a crash, GitHub down / no token / odd answer means "unknown" and never raises), when to
      alert, the wording, nothing database-shaped in any alert, what `run()` really sends in each case (first crash,
      repeat, unknown, recovery, ordinary success; other health alerts ride with the crash; the failure ping still goes
      out when the alert is quiet), and the workflow text (read-only permission, token passed to the collect step).
      30 mutation checks on a copy of the repo, all caught (4 were first missed and led to the wiring and workflow tests).
- [ ] Not done: a real crash streak on GitHub has not been seen yet, so the first live check is the next time a run
      fails. The streak read is checked against GitHub's real answer shape in the tests, not against a live run.

### 2026-10-27 — SC-65: a warning before the Neon plan runs out
**Shipped:** each run reads the project's usage so far this billing period from Neon (with the `NEON_API_KEY` GitHub
secret): compute hours and network transfer, written to the run log. Once a day (the run that starts in the first quarter
hour of 14:00 UTC, 9 AM Central) it adds a health alert if either is at 80% or more of the $10 plan ("Neon compute is at
82% of this month's allowance (205.0 of 250 hours)"). If the figures cannot be read (Neon refuses, is down, or the record
lacks them) that daily message says "Neon usage warning is blind" instead of staying quiet, so a changed API cannot turn
the warning off unnoticed. No key: nothing happens. A failure never stops a run. Storage is not watched (0.28 of 5 GB, and
the record reports it in byte-hours).
**Rework count:** 2, both found by live runs. **Root cause:** *built on a data source nobody had read the docs for.* Version
one read usage off the project record when the console tool showed it as 0 (it had only just been refreshed); the first
real run logged "not reported". Version two used Neon's usage-history API from its docs, but that endpoint is "included
with Launch plans and above" and this project's plan answers HTTP 403. Neon's docs for plans without it point at the
project record, which by then carried real numbers (2,527 compute seconds, 59 MB transferred about six hours into the
period). Lesson: check that an API is offered on *this* plan, with a real call, before building on it; and a zero seen
early in a period is not the same as a field that is never filled.
**Not yet verified:** a warning actually firing (nothing is near 80%), and how far the record's figures lag behind real use
(Neon calls them approximate; the active-time figure was about two hours behind the clock).
**Standing checks added:**
- [x] Automated (`tests/test_quota_warning.py`, 22 tests): the conversions (seconds to hours, bytes to decimal GB), zero
      is a real answer but missing or non-numeric figures are "unknown", the 80% line for each allowance and the edges,
      the one daily run, "blind" when Neon refuses / is down / lacks the figures / several projects, the request itself
      (key, the right project's record, timeouts), the numbers and failure reason logged, and the workflow passing the
      key. 27 mutation checks on a copy of the repo, all caught.
- [ ] Not done: a real warning at 80% (see above).

### 2026-10-27 — SC-63: Open roles and Applications tabs, with "Did you apply?" and a toast
**Shipped:** the dashboard has two tabs, **Open roles** (default) and **Applications**, each with a count. An open role's row
shows one state line ("Discovered 45 minutes ago", or "Viewed 12 minutes ago" once opened) and one action spot: **Apply**
(opens the posting), which becomes **"Did you apply?" with Yes and Not yet** when you come back from that job. Not yet puts
Apply back. Yes sends the row out of Open roles at once, moves both counts, and shows a toast ("You've applied to this
role. You can find it in Applications.") with **View** (the Applications tab) and **Undo** (puts the row back). The toast
never takes focus, is announced politely, stays eight seconds and longer while pointed at or focused. Applications lists
every job applied to, newest first, as "Applied 3 days ago", with nothing to click but the job; empty, it says "No
applications yet." Removed: the Status filter, the Applied toggle on rows, the "Welcome back" sentence, help line and
outline, and the Viewed icon. New in Field Guide with gallery rows: **Tabs** and **Toast** (plus its live region).
**No schema change.** "When" for Applications is the time of the job's latest `applied` event; a job marked applied before
events were kept says just "Applied". Applications includes jobs that have since closed (you still applied to them).
**Rules kept from SC-63's first build (check with Ted):** asked about only if opened in the last 24 hours; only the latest-
opened job ever asks (after Not yet the next-older one does not step in); a job not on the current page cannot ask.
**Rework count:** 3 in my own checks, none reached a person. **Root causes:**
- *a page rule that outranks a component's own position.* The toast was drawn at the bottom of a 9,000-pixel page, not at
  the bottom of the screen, because the dashboard's `body > :not(.band) { position: relative }` beat the toast region's
  `position: fixed`. The phone check ("the toast fits the screen") caught it; the desktop screenshot hid it.
- *a fixed wait standing in for an answer.* An Undo check waited 600 ms for a database round trip; it now waits for the
  toast to go.
- *checks that cannot fail in the way that matters.* Two deliberate breakages slipped through at first ("Yes hides the row
  even if the server failed", "the toast never goes"), because no check made the server fail and the hover check never
  let the toast time out on its own. Both now have a check, and both breakages are caught.
**Standing checks added:**
- [x] Automated, the rule (`dashboard/return_ask.py`): nothing opened, the latest only, the 24-hour edge, an old latest does not
      let an older one step in, applied, "Not yet" then opened again, and the next-older job not stepping in.
- [x] Automated, server (`tests/test_applications_tab.py`, `tests/test_return_ask.py`): the tabs and counts, the default and an
      unknown tab, applied jobs out of Open roles, the Status filter and toggle gone, filters on Open roles only,
      Applications' order, wording, empty state and no controls in its rows, marking and undoing (quiet answer for the
      script, redirects without it, no new event on undo), the toast and its live region, the Apply and question markup,
      `/return.json`, and the Applications query (applied flag, newest event first, closed jobs kept). 29 mutation checks
      on a copy of the repo (one equivalent: the page cannot tell whether "applied" is read before or after filtering).
- [x] Automated, in a real browser (`scripts/quality_check.py`, 314 checks): open a job in another tab, return, and Apply
      becomes the question with no reload and no change of focus; only the latest asks; reload keeps it; Not yet restores
      Apply and stays closed after a reload; opening again asks again; a failing server makes Yes claim nothing; Yes moves
      the row, the counts and shows the toast; the toast does not take focus, is polite, left-aligned, goes by itself near
      eight seconds, and waits while pointed at; Undo restores row, counts and toast; View lands on Applications with the
      job first; Apply stays inside its card at every width and sits under the state line on a phone; the question and the
      toast fit a phone. The job's outside page is stubbed. 22 deliberate breakages of the scripts and template, all caught
      on a throwaway database branch (deleted afterwards).
- [ ] Not done: Figma mirrors of Tabs, Toast and the new row (code is the source of truth); SC-64 (alerts) and the
      Applications pipeline are not part of this.

### 2026-10-27 — Field Guide mirrored into Figma: Tabs, Toast, Switch, Progress, Button working
**Shipped (Figma file `w7hKbZWJIgb74guqh5uGUF`, Components page):** `Tab` (State=Default/Selected) and `Tabs` (two tabs on a
hairline), `Toast` (Actions=Two/One), `Switch` (On x Disabled, state in words under the label), `Progress` (Fill=0/50/100%),
and `Working` added to Button's three styles (spinner plus busy label). Fills, strokes, spacing, radii and minimum heights
are bound to the Semantic variables (the Toast's Ink fill to the `color/ink` primitive, as in the CSS); text is Albert
Sans; each has a description naming its code class or macro. The gallery page has a new "Navigation & feedback" section
with a spec row for each, built from instances. README updated. Nothing in the code changed.
**Rework count:** 0 reached Ted; 3 in my own build: cloned variants land on the page, not in the set (moved in by hand); the
first spinner was a pie wedge, not an open arc; instances cloned from the Toggle row came out squashed until the fixed-width
ones (Toast, Progress, Tabs) were sized to their components. **Root cause:** *assuming an API call put a node where the
editor would.* Each was caught by a screenshot of the result, not by the script's own return value.
**Not done (follow-ups):** Variant order inside the Button set is not grouped by style (the editor ignored the x positions
for the new variants). The job card (Open roles and Applications rows) is page-local CSS in `index.html`, not a Field Guide
component, so it is not mirrored. The Figma gallery still stops after Messages (Chips, Layout, Content not mirrored).
