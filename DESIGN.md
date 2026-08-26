---
name: Daily Put Screen
description: A morning screening sheet drawn in the Vienna Method — quantities are counted, not read.
colors:
  stock: "#E9ECEF"
  sheet: "#F7F9FA"
  ink: "#14181F"
  ink-2: "#444C5A"
  rule: "#B9C1CA"
  blue: "#15497A"
  red: "#B32B18"
  ochre: "#D99A08"
  green: "#1F6B47"
  stock-dark: "#10141A"
  sheet-dark: "#181D25"
  ink-dark: "#E8EDF2"
  ink-2-dark: "#A3ADBA"
  rule-dark: "#2E3742"
  blue-dark: "#6FA8DC"
  red-dark: "#F07A63"
  ochre-dark: "#E8B33D"
  green-dark: "#59C08D"
typography:
  masthead:
    fontFamily: "Jost, 'Century Gothic', Futura, 'Avenir Next', system-ui, sans-serif"
    fontSize: "3rem"
    fontWeight: 500
    lineHeight: 1.05
    letterSpacing: "-0.01em"
  ticker:
    fontFamily: "{typography.masthead.fontFamily}"
    fontSize: "1.75rem"
    fontWeight: 500
    lineHeight: 1.1
    letterSpacing: "0.01em"
  body:
    fontFamily: "{typography.masthead.fontFamily}"
    fontSize: "1.125rem"
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "normal"
  label:
    fontFamily: "{typography.masthead.fontFamily}"
    fontSize: "0.8125rem"
    fontWeight: 500
    lineHeight: 1.3
    letterSpacing: "0.09em"
  figure:
    fontFamily: "{typography.masthead.fontFamily}"
    fontSize: "1.125rem"
    fontWeight: 500
    lineHeight: 1.3
    letterSpacing: "0.01em"
rounded:
  none: "0"
  mark: "0"
  disc: "50%"
spacing:
  hair: "2px"
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "40px"
  xxl: "64px"
components:
  mark-filled:
    backgroundColor: "{colors.ink}"
    size: "14px"
    rounded: "{rounded.mark}"
  mark-hollow:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    size: "14px"
    rounded: "{rounded.mark}"
  mark-kept:
    backgroundColor: "{colors.ochre}"
    size: "14px"
    rounded: "{rounded.mark}"
  guide-block:
    backgroundColor: "{colors.blue}"
    textColor: "{colors.sheet}"
    typography: "{typography.ticker}"
    padding: "16px"
    rounded: "{rounded.none}"
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.sheet}"
    typography: "{typography.label}"
    padding: "12px 24px"
    rounded: "{rounded.none}"
  button-primary-hover:
    backgroundColor: "{colors.blue}"
    textColor: "{colors.sheet}"
  flag-risk:
    backgroundColor: "{colors.red}"
    textColor: "{colors.sheet}"
    typography: "{typography.label}"
    padding: "4px 8px"
    rounded: "{rounded.none}"
---

# Design

## Overview

The world is **Isotype** — the Vienna Method of pictorial statistics developed by
Otto Neurath, Marie Neurath and Gerd Arntz in the 1920s and 30s.

It was invented to make quantitative facts legible to people with no statistical
training, under one governing rule: **a greater quantity is shown by more signs,
never by a bigger sign.** That rule is this product's central problem already
solved. The reader thinks in outcomes, not greeks. A 0.15 delta is a decimal she
has to translate; eight and a half filled marks out of ten is a quantity she can
see without translating anything.

Two consequences bind everything below.

1. **The quantities she acts on are countable tallies, not figures.** Checks
   passed, odds of keeping the premium, Reddit interest. One grammar, learned
   once, reused everywhere. Raw figures exist, but they are the second layer.
   The composite score is deliberately *not* a tally: rank already orders it,
   and a third tally in a row competes with the two she reads.
2. **Signs differ by shape first, and by colour second.** This is native to
   Isotype — Arntz cut distinct silhouettes, and colour reinforced them. It also
   discharges the confirmed "never rely on colour alone" requirement as a
   property of the system rather than as a retrofit.

The surface is a printed sheet, not a dashboard: flat ink fields, hairline
rules, no depth of any kind.

## Colors

Strategy: **Full palette.** Four named inks, each with one fixed meaning,
declared in a key on the page itself. This is not decoration — in the Vienna
Method the key *is* part of the chart, and here it is how the ranking becomes
inspectable rather than a black box.

| Token | Meaning — fixed, never decorative |
|---|---|
| `blue` | The structural and the given. Masthead field, guide blocks, rank and score. Field blue and lettering blue are separate tokens — in dark mode lettering lightens while a field must deepen. |
| `ochre` | Premium — what she is paid, and the odds she keeps it. **Fill only, never text.** |
| `red` | Risk, and only risk. Earnings before expiry, extreme IV/HV, a structural catalyst. |
| `green` | Confirmed by the data. Used sparingly; most confirmation is carried by a filled ink mark. |
| `ink` / `ink-2` | Lettering and the default filled mark. |

**Red is scarce on purpose.** Real data shows all ten names typically miss five
or six of the eleven criteria, because the screen ranks the best available rather
than returning only perfect matches. Painting each miss red would produce a wall
of alarm and destroy the signal. A missed criterion is an **absent unit** —
a hollow mark — not an alarm.

Ground is light and that is forced, not chosen: she reads this at a desk in
morning daylight for the better part of an hour, and high contrast is a confirmed
requirement. The ground is a **cool offset stock**, deliberately not cream —
cream paper with a red accent is the house style of machine-generated interfaces,
and it is not what Isotype plates actually look like. Colour lives in flat fields
that own whole regions, not in accents sprinkled over neutral.

Dark tokens exist for viewer preference and invert the stock, not the meanings.

All text pairs clear WCAG AA and most clear AAA. Ochre clears AA against ink but
not against the sheet, which is why it is a fill and never a letterform.

## Typography

**One family: Jost**, throughout — masthead, labels, figures, body.

The reason is historical identity rather than association: the Vienna Method was
set in Paul Renner's **Futura**, and Jost is its open-licence revival. This is
the face the notation was drawn for.

- Fixed rem scale, ratio ≈1.2. No fluid clamps — she views at one consistent DPI.
- **Base size is 1.125rem / 18px with 1.6 line height.** Larger by default is a
  confirmed requirement, so it is set in the type scale rather than delegated to
  browser zoom.
- Labels are uppercase at 0.8125rem with 0.09em tracking — Isotype label style.
- All figures carry `font-variant-numeric: tabular-nums` so columns align.

## Layout

The page is one column, centred, max 1200px. Reading order is the sheet's:
masthead band → key → ten rows → Reddit panel → footer.

- A name row is a horizontal band: guide block (rank + ticker) · badge tally ·
  odds tally · trade figures · catalyst verdict.
- **Ten rows must be scannable within roughly two screens at desktop.** Collapsed
  rows stay compact; the Isotype treatment lives in the marks, not in
  poster-scale whitespace. Expression never outranks the task here.
- Responsive behaviour is **structural, never fluid type**. Marks never shrink
  below their countable size; on narrow widths the tallies reflow to more lines.
  Changing the count per line rather than the size of the sign is the Isotype-
  correct adaptation and it happens to be the accessible one.
- Rules separate; boxes do not enclose. Hairline `rule` at 1px.

## Elevation & Depth

**There is none.** No shadow, no gradient, no blur, no translucency, no lift on
hover. Lino-block printing is flat, and every layering cue is a flat field
abutting another flat field, or a hairline rule.

Hover is a solid ink bar appearing on a row's leading edge. Focus is a 2px ink
outline at 2px offset — a printer's registration mark, not a glow.

## Shapes

`rounded: 0` on every container, control, field and mark. Rounded rectangles are
a web convention, not an Isotype one.

This prohibition is deliberately narrow: **circles and cut curves are native to
Arntz's glyph vocabulary** and stay fully in play inside signs and pictograms.
What is banned is the rounded *container*, not the curve.

## Components

**The mark** is the atom. A 14px square, 2px stroke, 6px gap, sized to be counted
at a glance up to eleven.

| State | Form | Means |
|---|---|---|
| filled | solid ink square | criterion passed |
| hollow | outline square | criterion missed |
| unknown | outline square, centred dot | data unavailable — never silently a miss |
| kept | solid ochre square | one tenth of the odds of keeping the premium |
| half | square filled to the vertical midline | half a unit |

**Every tally carries `role="img"` and an `aria-label` holding the plain
sentence** — "8 and a half of 10 — an 85% chance you keep the premium." Screen
reader users get the sentence, not eleven anonymous squares.

Standard states are required on every interactive element: default, hover, focus,
active, disabled. Detail disclosure uses native `<details>`/`<summary>` so
keyboard access and no-JS operation come for free.

Motion: the marks count in on load, staggered ~30ms, because *counting* is the
world's core verb. Nothing else moves. `prefers-reduced-motion` removes it
entirely and the page is complete without it.

## Do's and Don'ts

**Do**
- Express any new quantity as a tally of ten before considering any other form.
- Give every new sign a shape difference before giving it a colour.
- Declare any new sign in the on-page key. An undeclared sign is a broken chart.
- State plain language first; keep delta, IV, IV/HV, DTE and annualised yield in
  the second layer.
- Say plainly, and visibly, that quotes are delayed and reflect the prior close.

**Don't**
- Don't size a sign to show a quantity. More signs, never a bigger sign.
- Don't set ochre as a letterform.
- Don't spend red on anything that is not genuine risk.
- Don't add a shadow, gradient, glow or rounded container.
- Don't imply a track record. There is no backtest, no win rate and no
  performance history, and nothing on the page may suggest otherwise.
- Don't let a name silently vanish. Missing data is a stated unknown, never an
  absence — inconsistency is the failure this product exists to remove.
