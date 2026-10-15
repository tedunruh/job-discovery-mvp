# Field Guide

Scout's design system. Tokens and components live here, in the same repo as the
product, so a token change ships in the same commit as the screens it affects.

Design intent lives in Figma (file `w7hKbZWJIgb74guqh5uGUF`):
- **Foundations** (node `127:22`): what each color and typeface is for
- **Color + Text** (node `126:11`): exact values and a sample discovery card

## Layout

```
field-guide/
├── tokens/            SOURCE OF TRUTH. W3C design-token JSON ($value, $type, {refs})
│   ├── color.json     palette + semantic roles
│   ├── typography.json
│   ├── space.json
│   └── radius.json
├── build.py           tokens/*.json -> dist/tokens.css (plain Python, no Node)
├── dist/tokens.css    GENERATED. Committed. Never edit by hand.
├── css/fonts.css      @font-face for the Roboto fallback (one weight lighter)
├── fonts/             self-hosted Roboto woff2 + license
├── js/filter.js       closes .fg-filter menus on outside click / Escape
└── css/components.css hand-written components (.fg-*), semantic tokens only
```

**Gallery:** `/field-guide` (signed in) renders every token and component, in every
state, with a "use this when" note. Swatches and the type scale are read from
`tokens/*.json`; components use the real CSS and the `_fg.html` macros, so the
page can't drift from what ships. `quality_check.py` fails if any `.fg-` class in
`components.css` has no example there.

**Macros:** components with more than one-class markup live in
`dashboard/templates/_fg.html` (`fg.toggle`, `fg.filter`, `fg.chevron`). Import with
`{% import "_fg.html" as fg %}`; screens and the gallery share them.

Flask serves `dist/` and `css/` at `/field-guide/...`. Every template includes
`dashboard/templates/_field_guide_head.html` for fonts, tokens and components.

## Figma mirror (code → Figma)

Code is the source of truth; the Field Guide Figma file (`w7hKbZWJIgb74guqh5uGUF`)
mirrors it so designs can be made by hand with the same tokens.

- **Variables:** `_Primitives` (hidden; palette, the alpha steps semantic tokens use,
  raw dimensions) and `Semantic` (every semantic token, aliased to a primitive,
  scoped to where it may be used, WEB code syntax = its `var(--fg-…)` name).
  Goldenrod/Clay/Sage signal tokens are scoped to fills and strokes only - never
  text fill - so Figma enforces the same rule the code does.
- **Effect style:** `Elevation/Raised` = `--fg-shadow-raised`.
- **Not mirrored:** line height and letter spacing (unitless / em values have no
  clean Figma variable type).

To resync after a token change: run `python field-guide/figma/export_variables.py`
and execute `field-guide/figma/sync_variables.js` with `const PLAN = <that JSON>;`
prepended, via the Figma MCP `use_figma` tool (ask Claude: "resync Field Guide
tokens to Figma"). It updates variables in place by name and reports, rather
than deletes, anything in Figma that no longer exists in code.

## Changing a token

1. Edit the JSON in `tokens/`.
2. `python field-guide/build.py`
3. Commit both the JSON and `dist/tokens.css`. CI runs `build.py --check` and
   fails if they disagree.

## Two layers

| Layer | Example | Who uses it |
|---|---|---|
| Palette | `--fg-color-palette-forest` | Only the semantic layer |
| Semantic | `--fg-color-action-primary` | Components and templates |

Pages and components reference semantic tokens. If a role you need doesn't
exist, add a semantic token rather than reaching for a palette value.

## Color roles

| Color | Hex | Role | Use for |
|---|---|---|---|
| Parchment | `#f6f2e9` | Canvas | Page backgrounds, large surfaces, empty states. Dominant everywhere. |
| Ink | `#292824` | Voice | Text, icons, high emphasis. Secondary at 70%, tertiary at 45%. |
| Forest | `#355447` | Direction | Primary actions, links, active and selected states, progress |
| Goldenrod | `#d6a84f` | Discovery | New matches, recommendations, "found for you" |
| Clay | `#c9785b` | Attention | Warnings, time-sensitive info, secondary destructive actions. Sparingly. |
| Sage | `#a8b39c` | Support | Tags, secondary statuses, subtle backgrounds |

Contrast on Parchment: Ink 13.2:1, Ink 70% 5.2:1, Forest 7.5:1,
Clay 3.0:1, Goldenrod 2.0:1, Sage 2.0:1, Ink 45% 2.6:1 (placeholders only).
Ink on Goldenrod 6.7:1, on Sage 6.7:1, on Clay 4.4:1 (large or bold text only).

## Typography

- **Fraunces** (`--fg-font-family-display`, class `.fg-display`): personality.
  Wordmark, editorial headlines, introductions, discovery moments.
  Always with `font-variation-settings: "SOFT" 0, "WONK" 1`.
- **Avenir Next** (`--fg-font-family-ui`): clarity. Everything else.
  Avenir Next ships with Apple devices only (no web license), so everywhere else
  falls back to **Roboto**, self-hosted in `fonts/` as the family "Field Guide Roboto"
  and mapped one weight lighter (400→300, 500→400, 600→500) so it matches Avenir's
  color on the page. See `css/fonts.css`.
- No other typefaces.

Scale: 12 / 14 / 15 (body) / 16 / 20 / 24 / 28 / 36, plus 96 for the sign-in wordmark.

## Components (Sprint 1)

| Class | Notes |
|---|---|
| `.fg-page` | Body base: Parchment, Ink, Avenir Next |
| `.fg-display` | Fraunces moment |
| `.fg-button` + `--primary` / `--secondary` / `--block` | Primary is Forest |
| `.fg-icon-button` | 44px hit area, takes `currentColor` |
| `.fg-field` / `.fg-label` / `.fg-input` | Input text is 16px so iOS doesn't zoom |
| `.fg-check` / `.fg-checkbox` | Checked = Forest |
| `.fg-message` + `--success` / `--attention` / `--discovery` | Signal color in fill and left rule, text stays Ink |
| `.fg-link` | Forest |
| `.fg-button--ghost` | Forest text, no outline (header "Role preferences") |
| `.fg-radio` | Checked = Forest check-disc |
| `.fg-filter` / `.fg-filter-pill` / `.fg-menu` / `.fg-menu-item` | Dropdown filter on `<details>` via `fg.filter`; include `/field-guide/js/filter.js` for outside-click and Escape |
| `.fg-toggle` | One item's on/off status ("Applied") via `fg.toggle`; disc carries the state, label stays fixed |

## Principles that are build rules

1. **Color supports meaning; it never carries it alone.** Every Goldenrod, Clay
   or Sage element also has text or an icon.
2. **Hierarchy from contrast and opacity, not more colors.** No new grays.
3. **Most of Scout is calm.** Semantic colors are for moments, not decoration.
4. **Free text is always left-aligned.** A container can be centered on the page, but
   wrapping text inside it (messages, descriptions, body copy) aligns left. Only short
   single-line headings or wordmarks may center.
5. Ask: does this feel calm, human, purposeful, like someone looking out for me?
