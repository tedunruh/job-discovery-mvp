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
