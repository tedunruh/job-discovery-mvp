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
├── css/fonts.css      @font-face for Albert Sans (UI), weights 500 and 600
├── fonts/             self-hosted Albert Sans woff2 + license
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
- **Components page:** real Figma components with variants for every state,
  fills/strokes/padding/radius bound to the Semantic variables: Button
  (Style × State), Link, Icon button, Checkbox, Radio, Toggle, Text field,
  Filter pill, Filter menu (built from Checkbox/Radio instances), Message; and from
  SC-46: Chip, Add chip, Select, Segment, Segmented control, Checkbox group, Add row,
  Group label, Page header, Page intro, Card (Style × Header, Content is an instance
  swap of "Card content"), Suggestion, Status strip, Action bar, File row, Dropzone,
  List row, List empty, Stat, Before and after. Controls have a 44px minimum height.
  Each description names its code class or macro. Text is Albert Sans.
- **Field Guide gallery page:** the `/field-guide` page laid out in Figma from
  instances of those components, same sections and "use this when" notes.
  A visual mirror, not wired: there's no Code Connect link yet, so a change in
  code needs a resync (variables) or a rebuild request (components).
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
| Parchment | `#faf9f7` | Canvas | Page backgrounds, large surfaces, empty states. Dominant everywhere. A clean, nearly neutral linen (Ted, 2026-10-21; was `#f6f2e9`). |
| Parchment light | `#fefdfd` | Card | Card fill on settings-style pages, a step lighter than the page. Text on it passes AA. |
| Ink | `#292824` | Voice | Text, icons, high emphasis. Secondary and placeholder (tertiary) at 70%. |
| Forest | `#355447` | Direction | Primary actions, links, active and selected states, progress |
| Goldenrod | `#d6a84f` | Discovery | New matches, recommendations, "found for you" |
| Clay | `#c9785b` | Attention | Warnings, time-sensitive info, secondary destructive actions. Sparingly. |
| Sage | `#a8b39c` | Support | Tags, secondary statuses, subtle backgrounds |

Contrast on Parchment: Ink 14.0:1, Ink 70% 5.4:1, Forest 7.9:1,
Clay 3.2:1, Goldenrod 2.1:1, Sage 2.1:1. Every text token passes AA (4.5:1) on Parchment.
Ink on Goldenrod 6.7:1, on Sage 6.7:1, on Clay 4.4:1 (large or bold text only).

## Typography

- **Fraunces** (`--fg-font-family-display`, class `.fg-display`): personality.
  Wordmark, editorial headlines, introductions, discovery moments.
  Always with `font-variation-settings: "SOFT" 0, "WONK" 1`.
- **Albert Sans** (`--fg-font-family-ui`): clarity. Everything else. Self-hosted in
  `fonts/` (`css/fonts.css`), weights 500 and 600 only.
- **Minimum weight for Albert Sans is Medium** (500). Regular (400) is used only by
  Fraunces. Nothing in the UI face is set lighter than Medium.
- No other typefaces.

Scale: 12 / 14 / 15 (body) / 16 / 20 / 24 / 28 / 36, plus 96 for the sign-in wordmark.

**Control type** (set by Ted in the Figma Components page, 2026-10-15): all
control text is 16px. Labels on things you act on - buttons, links, checkbox /
radio labels, menu rows, filter labels, field labels - are **demibold**. Values, messages
and status labels - input values, filter values, message copy, the toggle's "Applied" -
are **medium** (Ted, 2026-10-15: demibold read too strong for a status). 12-14px
is for supporting text only (metadata, hints, counts, section notes).

## Components (Sprint 1)

| Class | Notes |
|---|---|
| `.fg-page` | Body base: Parchment, Ink, Albert Sans |
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

**Profile components (SC-46).** Every control is 44px tall (`--fg-size-touch-target`);
`quality_check.py` fails any shorter control in the gallery.

| Macro / class | Notes |
|---|---|
| `fg.header(context)` · `.fg-shell` `.fg-header` `.fg-wordmark` | One header for onboarding and settings: wordmark left, context right |
| `fg.intro(title, lede)` · `.fg-display--page` `.fg-lede` | Fraunces headline in Scout's voice, then what to do here. A call block puts something on the headline's right (`.fg-intro-aside`) |
| `fg.card(id, title, help)` · `.fg-card` `--emphasis` `--discovery` | Flat card on `--fg-color-surface-card` (a step lighter than Parchment) with a hairline border. Emphasis = unsaved change (Forest outline) |
| `fg.suggestion(eyebrow, statement, help)` · `.fg-voice` | Scout suggests a change: Goldenrod tint, statement in Fraunces |
| `fg.group_label(title, help)` · `.fg-eyebrow` | Sentence case, 14px demibold, secondary; never all caps |
| `.fg-split` `.fg-split-main` `.fg-side-panel` | Main column plus sticky side panel; panel drops below on phones |
| `fg.chip(label, variant, new, name)` `fg.chip_add(label)` · `.fg-chips` | Sage tint, Ink words. `avoid` = deal-breaker (Clay outline). `new` = Goldenrod ring + "New" tag. `name="titles"` adds a hidden input so the chip posts with its form |
| `fg.select(id, label, options)` · `.fg-select` | Same box as `.fg-input`, with a chevron |
| `fg.checkbox_group(name, legend, options)` · `.fg-fieldset` `.fg-legend` | A set of checkboxes answering one question |
| `fg.segmented(name, legend, options)` · `.fg-segmented` `.fg-segment` | One of a short ordered set; real radios (arrow keys work) |
| `fg.add_row(id, label, list=)` · `.fg-add-row` | Field plus its own Add button. `list="id"` wires it to a chip list with `js/profile.js` (Add or Enter clones `<template id="id-template">`) |
| `fg.status(title, detail, paused, compact)` · `.fg-status` | Is Scout watching; the words carry the state, not the dot. `compact` drops the box for one line, beside a page title |
| `fg.progress(id, steps)` · `.fg-progress` | Plain text that changes while something runs, over a bar that fills toward done; hidden until `js/resume.js` starts it |
| `fg.action_bar(note)` · `.fg-action-bar` | Sticky bottom bar with a note and the save actions |
| `fg.file_row(name, meta)` · `fg.dropzone(id, title, hint)` | A file the person added; where a file goes (`inline=True` inside a card) |
| `fg.list_row(title, meta, detail)` · `fg.list_placeholder()` · `.fg-list-empty` | Short records, placeholder shape, empty line |
| `fg.stat(value, label)` · `.fg-stats` · `fg.compare(before, after, unit)` | Counts are Ink, not Forest: a stat reports, it doesn't ask you to act |
| `.fg-row` · `.fg-visually-hidden` · `button.fg-link` | Wrapping action row; screen-reader-only text; link look for an on-page action |

Disabled buttons use the secondary fill with secondary text (5.1:1) rather than fading.

## Principles that are build rules

1. **Color supports meaning; it never carries it alone.** Every Goldenrod, Clay
   or Sage element also has text or an icon.
2. **Hierarchy from contrast and opacity, not more colors.** No new grays.
3. **Most of Scout is calm.** Semantic colors are for moments, not decoration.
4. **Free text is always left-aligned.** A container can be centered on the page, but
   wrapping text inside it (messages, descriptions, body copy) aligns left. Only short
   single-line headings or wordmarks may center.
5. **Sentence case, always.** Buttons, links, labels and headings capitalize only the
   first word and proper names ("Role preferences"; "Sign out").
6. Ask: does this feel calm, human, purposeful, like someone looking out for me?
