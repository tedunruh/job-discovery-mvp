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
