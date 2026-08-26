---
version: 1
slug: "site-index-html"
primary_target: "site/index.html"
related_targets: ["site/style.css","site/app.js"]
---

# The morning sheet

**Mode: Operate.** One reader, one task, once a day: scan ten names and pick two
or three to research. Scanability and consistency outrank expression. The
Isotype world is expressive, but it earns its place by making quantities
countable — not by decorating the page.

## Direction

Vienna Isotype (Neurath / Arntz), candidate 3 of 7 by resonance, seed
`ed34c172`. Full contract is the HTML comment at the top of `site/index.html`;
the system is in `DESIGN.md`. Staging is the source's own — title band, key
declared once, stacked rows on a shared baseline.

The governing rule, and the reason this world was chosen: **a greater quantity
is shown by more signs, never by a bigger sign.** The reader thinks in outcomes,
not greeks, so a 0.15 delta is a decimal she has to translate and eight and a
half filled marks out of ten is a quantity she can see.

## Decisions a later pass should not quietly undo

- **Two tallies per row, never three.** Checks passed and odds of keeping the
  premium. The composite score is a plain figure on purpose — rank already
  orders it, and a third tally competes with the two she acts on.
- **The tally columns are pinned** (214px / 194px, both breakpoints). Sized to
  content they reflow on whichever row carries a long company name, and one row
  breaking its baseline while nine hold it reads as an error, not adaptation.
- **The count-in index is per tally, not cumulative down the page.** A running
  index leaves the bottom rows blank for seconds, which reads as missing data.
- **Field blue and lettering blue are separate tokens** (`--blue-field`,
  `--on-field`). One token cannot do both jobs: in dark mode lettering must
  lighten while a field must deepen.
- **Red is only ever risk.** Ten names typically miss five or six of eleven
  criteria; a missed criterion is a hollow mark — an absent unit — never an
  alarm. The risk chip lives on its own line in the key, outside the mark grid.
- **Direction arrows must be honest.** The arrow is the non-colour cue, so a
  flat reading gets an en-dash and the words "flat" / "level with yesterday",
  not an up arrow on 0%.
- **The copy fallback shows the prompt, pre-selected.** If the clipboard is
  blocked, telling her to press Ctrl+C while nothing is selected is a dead end.

## Copy rules

Plain English first, trader vocabulary second — confirmed with the user. Never
imply a track record: there is no backtest, no win rate, no performance history.
Say plainly and visibly that quotes are delayed and reflect the prior close.

## Known detector finding, deliberately kept

`single-font` (Jost throughout). The Vienna Method was set in Renner's Futura
and Jost is its open-licence revival — this is the face the notation was drawn
for. Hierarchy is carried by weight (400/500/600), the fixed rem scale, and
uppercase tracked labels. Do not "fix" this by pairing a second family.
