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
└── css/components.css hand-written components (.fg-*), semantic tokens only
```

Flask serves `dist/` and `css/` at `/field-guide/...`. Every template includes
`dashboard/templates/_field_guide_head.html` for fonts, tokens and components.

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

## Principles that are build rules

1. **Color supports meaning; it never carries it alone.** Every Goldenrod, Clay
   or Sage element also has text or an icon.
2. **Hierarchy from contrast and opacity, not more colors.** No new grays.
3. **Most of Scout is calm.** Semantic colors are for moments, not decoration.
4. Ask: does this feel calm, human, purposeful, like someone looking out for me?
