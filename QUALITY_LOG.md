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
