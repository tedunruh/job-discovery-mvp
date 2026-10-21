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
