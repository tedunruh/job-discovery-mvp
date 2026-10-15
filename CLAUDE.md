# Scout (job-discovery-mvp)

Python app: ATS collectors, Neon Postgres, Flask dashboard (`dashboard/`), deployed on Render
with a pip-only build.

## UI work: use Field Guide

All UI is styled through **Field Guide**, the design system in `field-guide/`. Read
`field-guide/README.md` before touching any template or CSS.

Rules:
- Never hard-code a color, font, font size, spacing or radius value in a template or component.
  Use a `var(--fg-…)` semantic token. Layout widths and breakpoints are fine as raw px.
- **The gallery at `/field-guide` is the catalog of approved controls.** Before building a
  screen, pick from it. Reuse `.fg-*` classes from `field-guide/css/components.css` and the
  macros in `dashboard/templates/_fg.html` (`fg.toggle`, `fg.filter`); never copy their markup.
- A new component goes into Field Guide first: CSS in `components.css`, a macro in `_fg.html`
  if its markup is more than one class on a native element, and a spec row with every state
  in `dashboard/templates/field_guide.html`. `quality_check.py` fails if a `.fg-` class is
  missing from the gallery.
- Every template includes `{% include "_field_guide_head.html" %}` and uses `<body class="fg-page">`.
- To change a token: edit `field-guide/tokens/*.json`, run `python field-guide/build.py`,
  commit the JSON and `field-guide/dist/tokens.css` together. Never edit `dist/` by hand.
- Fonts are Fraunces (display) and Avenir Next (UI) only, with Roboto as the UI fallback on
  non-Apple devices (Avenir Next isn't web-licensed), self-hosted one weight lighter. Never add another typeface.
  Avenir Next (UI) text is never lighter than Medium (500); Regular is for Fraunces only.
- Goldenrod, Clay and Sage are never text colors on Parchment, and never the only signal of
  meaning. Pair them with Ink text or an icon.
- Free text (messages, descriptions, body copy) is always left-aligned, even inside a centered
  container. Only short headings and the wordmark may center.
- UI text is sentence case: buttons, links, labels, headings ("Role preferences", not
  "Role Preferences"). Only proper names (Scout, Field Guide, LinkedIn) are capitalized.
- Primary actions and links are Forest. Secondary hierarchy comes from Ink opacity, not new grays.

Design intent lives in Figma file `w7hKbZWJIgb74guqh5uGUF` (Foundations `127:22`,
Color + Text `126:11`). If code and Figma disagree, ask before changing either.

## Before calling UI work done

First run `python scripts/quality_check.py` - it automates the mechanical
standing checks (viewport meta, no overflow, login artwork in bounds, toolbar
alignment, computed-style drift vs. `scripts/quality_baseline.json`) and must
pass. If it flags style drift you intended, accept it with `--update-baseline`.
Add a check there when a new mechanically-checkable cause shows up. Dev setup:
`pip install -r requirements-dev.txt`.

Then read `QUALITY_LOG.md` and run its **Standing checks** - each exists because it
caught a real bug that already shipped once; don't relearn the lesson the hard
way a second time. After finishing a change, log it there: what shipped, how
many corrections it took to get right, and why (a named root cause, not "bug
fixed"). If a rework cause is new and mechanically preventable, add a check for
it so it catches the next one automatically instead of relying on memory.
