/* Reads data/latest.json and draws the sheet.
   Every quantity that matters is rendered as a countable tally; the raw
   figures live one layer down, behind "the numbers". See DESIGN.md. */

const $ = (sel) => document.querySelector(sel);

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/* Coerced rather than trusted. Two of these land in innerHTML, and a string
   passed through toLocaleString comes back unchanged -- so a text value
   arriving where a number was expected would be written to the page as markup.
   Number() makes that impossible instead of merely unlikely. */
const money = (v, dp = 2) => {
  const n = Number(v);
  return v == null || !Number.isFinite(n) ? "—" : "$" + n.toLocaleString("en-US",
    { minimumFractionDigits: dp, maximumFractionDigits: dp });
};

const pct = (v, dp = 0) =>
  v == null ? "—" : (v * 100).toFixed(dp) + "%";

/* Revenue growth arrives as a ratio, and a real one in this data set was
   41.58 -- that is +4,158%, not 41.6%. Past a few hundred percent a multiple
   is the only reading that stays honest. */
const growth = (v) => {
  if (v == null) return "—";
  if (v > 3) return "×" + (1 + v).toFixed(1);
  return (v >= 0 ? "+" : "") + (v * 100).toFixed(1) + "%";
};

/* Company names arrive in registry form -- "UnitedHealth Group Incorporated".
   The legal suffix costs a whole line and carries nothing she scans for. */
const cleanName = (name) =>
  (name || "").replace(
    /,?\s+(Incorporated|Corporation|Company|Limited|Holdings|Inc\.?|Corp\.?|Co\.?|Ltd\.?|plc|N\.V\.|S\.A\.)$/i,
    "").trim() || name;

const shortDate = (iso) => {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]}`;
};

const longDate = (iso) => {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US",
    { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
};

const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
};

/* ---- tallies -------------------------------------------------------- */

/* A tally is an image of a quantity, so it carries the plain sentence as its
   label. A screen reader gets the sentence, never eleven anonymous squares.

   The count-in index is per tally, not cumulative down the page: every row
   counts itself at once. A running index across ten rows would leave the
   bottom of the list blank for seconds, which reads as missing data. */
function tally(marks, label) {
  const wrap = el("div", "tally");
  wrap.setAttribute("role", "img");
  wrap.setAttribute("aria-label", label);
  marks.forEach((kind, i) => {
    const mark = el("span", "mark mark--" + kind);
    mark.style.setProperty("--i", i);
    wrap.appendChild(mark);
  });
  return wrap;
}

function badgeMarks(badges) {
  return badges.map((b) =>
    b.passed === true ? "filled" : b.passed === false ? "hollow" : "unknown");
}

/* Odds render to the nearest half unit rather than rounding to whole marks:
   77% is seven and a half, and eight would overstate it. */
function unitMarks(fraction, kind = "kept", total = 10) {
  const halves = Math.round(fraction * total * 2);
  const out = [];
  for (let i = 0; i < total; i++) {
    const filled = halves - i * 2;
    if (filled >= 2) out.push(kind);
    else if (filled === 1) out.push(kind === "kept" ? "kept-half" : "half");
    else out.push("hollow");
  }
  return out;
}

/* ---- the score, broken out ------------------------------------------ */

/* The weights differ -- oversold is worth 20 points, margin trend 10 -- so a
   mark has to be worth the same everywhere or the rows can't be compared down
   the column. One mark is 2.5 points: the breakdown is 40 marks, and what she
   counts is the score itself. */
const POINTS_PER_MARK = 2.5;

/* In weight order, matching the table in docs/specs/screener.md.

   Named for what they measure, not for how they feel. The first draft said
   "Oversold enough" and "Premium is rich", which tell a reader who already
   knows nothing, and tell a reader who sells puts for a living less than she
   walked up with. The audience here reads an option chain in the morning.

   note() gets the live config, so every threshold it quotes is the number the
   screen actually ran under this morning rather than one written down once and
   left to drift. */
const COMPONENTS = [
  ["oversold", "RSI + Williams %R", (c) =>
    `The lowest RSI of the last 10 sessions, not today's. Full credit inside ` +
    `${c.rsi_ideal_low}–${c.rsi_ideal_high}, none at ${c.rsi_zero_above} or above, ` +
    `and less again below ${c.rsi_ideal_low} — past a point it stops looking stretched ` +
    `and starts looking broken. Williams %R under ` +
    `${String(c.williams_r_oversold).replace("-", "−")} is the other 30%.`],

  ["premium_richness", "IV richness", (c) =>
    `Half IV ÷ realised volatility, full credit at ${c.iv_hv_rich.toFixed(2)}×. Half where ` +
    `IV sits in this name's own 12-month range, which is the fairer question — ` +
    `IV 30 is expensive for a utility and cheap for a biotech.`],

  ["bounce", "EMA / MACD / volume", () =>
    `Four checks that the fall has stopped: above the 9-day EMA (30%), above ` +
    `the 20-day (15%), MACD crossing up (20%, plus 10% if it crosses below the ` +
    `zero line), volume expanding on an up day (25%).`],

  ["sales_growth", "Revenue growth", (c) =>
    `65% year-on-year, full credit at ${pct(c.rev_yoy_target, 0)}. 35% quarter-on-quarter. ` +
    `Scores 0.4 when the filing is missing — unknown, not punished.`],

  ["margin_trend", "Margin trend", () =>
    `Gross and operating margin against the same quarter last year. Full credit ` +
    `at +1 point, nothing at −1. Also 0.4 when unknown.`],

  ["strike_safety", "Strike cushion", () =>
    `60% the gap from today's price to breakeven, measured in ATRs — how many ` +
    `normal days of movement before the trade is underwater, full credit at 2.5. ` +
    `40% the strike sitting below the 60-day low.`],

  ["trade_quality", "Annualised yield", (c) =>
    `Annualised return on the cash secured, full credit at ${pct(c.ann_yield_target, 0)}. Cut by ` +
    `up to 30% as the bid-ask spread widens: a yield you cannot fill at the mid ` +
    `is not a yield.`],
];

/* "Ranked third" should never be a black box -- PRODUCT.md principle 4. The
   figures were always computed; this is where they become readable. */
function renderBreakdown(components) {
  if (!components) return null;
  const wrap = el("div", "breakdown");
  wrap.appendChild(el("p", "breakdown__lede",
    "Where the score came from. One mark is 2.5 points, so a longer row earned " +
    "more — the marks stay the same size."));

  COMPONENTS.forEach(([key, label]) => {
    const part = components[key];
    if (!part || !part.max) return;
    const line = el("div", "breakdown__line");
    line.appendChild(el("span", "breakdown__label", label));
    line.appendChild(tally(
      unitMarks(part.points / part.max, "filled", Math.round(part.max / POINTS_PER_MARK)),
      `${label}: ${part.points.toFixed(1)} of ${part.max} points`));
    line.appendChild(el("span", "breakdown__value",
      `${part.points.toFixed(1)} / ${part.max}`));
    wrap.appendChild(line);
  });
  return wrap;
}

/* ---- names she has already seen ------------------------------------- */

const ordinal = (n) => {
  const teens = n % 100;
  if (teens >= 11 && teens <= 13) return n + "th";
  return n + (["th", "st", "nd", "rd"][n % 10] || "th");
};

/* A name can come back for a good reason -- still oversold, still bouncing --
   so nothing is ever hidden. What she needs at a glance is whether there is
   anything new to look at: the screener picks the expiry nearest 35 days out
   and the delta nearest 0.20, so a name that keeps scoring well hands back the
   identical contract until something moves. */
function seenChip(seen) {
  if (!seen) return null;
  const chip = el("span", "seen",
    `${ordinal(seen.days)} day · ${seen.same_contract ? "same put" : "new put"}`);
  chip.dataset.fresh = seen.same_contract ? "no" : "yes";
  chip.title = seen.same_contract
    ? `Same strike and expiry as the last list. On the list since ${longDate(seen.since)}.`
    : `On the list since ${longDate(seen.since)}, but today the screen picked a ` +
      `different strike or expiry.`;
  return chip;
}

/* ---- rows ----------------------------------------------------------- */

function renderRow(pick) {
  const row = el("article", "row");
  const trade = pick.trade || {};
  const passes = pick.badges.filter((b) => b.passed === true).length;
  const unknown = pick.badges.filter((b) => b.passed == null).length;
  const total = pick.badges.length;

  /* guide block -- rank, ticker, and the composite score.
     The score is deliberately NOT a tally: rank already orders it, and a third
     tally per row would compete with the two quantities she acts on. */
  const guide = el("div", "guide");
  guide.appendChild(el("span", "guide__rank", "No. " + pick.rank));
  guide.appendChild(el("span", "guide__ticker", pick.symbol));
  const score = el("div", "guide__score");
  score.appendChild(document.createTextNode("score "));
  score.appendChild(el("strong", null, pick.score.toFixed(0)));
  guide.appendChild(score);
  row.appendChild(guide);

  /* name and price */
  const name = el("div", "name");
  name.appendChild(el("h3", null, cleanName(pick.name)));
  const chip = seenChip(pick.seen);
  if (chip) name.appendChild(chip);
  name.appendChild(el("div", "name__price", money(pick.price)));
  if (pick.change_5d != null) {
    const pctMove = Math.abs(pick.change_5d * 100);
    const moved = pctMove >= 0.25;
    const move = el("div", "name__move", moved
      ? `${pctMove.toFixed(1)}% over 5 days`
      : "flat over 5 days");
    move.dataset.dir = !moved ? "flat" : pick.change_5d < 0 ? "down" : "up";
    name.appendChild(move);
  }
  row.appendChild(name);

  /* the eleven checks */
  const checks = el("div", "metric");
  checks.appendChild(el("span", "metric__label", "The checks"));
  const missNote = unknown ? `, ${unknown} unknown` : "";
  checks.appendChild(tally(badgeMarks(pick.badges),
    `Passes ${passes} of ${total} criteria${missNote}`));
  const checkVal = el("div", "metric__value");
  checkVal.innerHTML = `<strong>${passes} of ${total}</strong> checks pass`;
  checks.appendChild(checkVal);
  row.appendChild(checks);

  /* odds of keeping the premium -- the signature quantity on the page */
  const odds = el("div", "metric");
  odds.appendChild(el("span", "metric__label", "Keep the premium"));
  if (trade.keep_premium_odds != null) {
    odds.appendChild(tally(unitMarks(trade.keep_premium_odds),
      `About a ${pct(trade.keep_premium_odds)} chance you keep the premium and are not assigned the shares`));
    const oddsVal = el("div", "metric__value");
    oddsVal.innerHTML = `<strong>${pct(trade.keep_premium_odds)}</strong> chance`;
    odds.appendChild(oddsVal);
  } else {
    odds.appendChild(el("div", "metric__value", "—"));
  }
  row.appendChild(odds);

  /* the trade */
  const tradeBox = el("div", "trade");
  tradeBox.appendChild(el("span", "metric__label", "The trade"));
  if (trade.strike != null) {
    tradeBox.appendChild(el("span", "trade__strike",
      `Sell the ${money(trade.strike)} put`));
    const line = el("div", "trade__line");
    line.innerHTML =
      `expires ${shortDate(trade.expiry)} &middot; ${trade.dte} days<br>` +
      `collect <span class="trade__credit">${money(trade.credit * 100, 0)}</span>` +
      ` &middot; set aside ${money(trade.cash_secured, 0)}`;
    tradeBox.appendChild(line);
  } else {
    tradeBox.appendChild(el("div", "trade__line", "No sellable put today."));
  }
  row.appendChild(tradeBox);

  /* risk flags -- red is spent here and nowhere else */
  if (pick.penalties && pick.penalties.length) {
    const flags = el("div", "flags");
    pick.penalties.forEach((p) => flags.appendChild(el("span", "flag", p.reason)));
    row.appendChild(flags);
  }

  /* why it fell */
  if (pick.catalyst) {
    const cat = el("div", "catalyst");
    const verdict = el("span", "catalyst__verdict", pick.catalyst.verdict);
    verdict.dataset.v = pick.catalyst.verdict;
    cat.appendChild(verdict);
    cat.appendChild(document.createTextNode(
      pick.catalyst.headline ? pick.catalyst.headline + " — " : ""));
    cat.appendChild(document.createTextNode(pick.catalyst.reason || ""));
    row.appendChild(cat);
  }

  const why = whyPicked(pick, trade);
  if (why) row.appendChild(el("p", "why", why));

  row.appendChild(renderNumbers(pick, trade));
  return row;
}

/* Why this name, in a sentence or four.

   Built here rather than published. Ranking is the browser's job now, so the
   thirty bench names need this too -- writing it on the page means forty
   blurbs for no bytes and no new key in the payload to guard.

   Read against the thresholds the model scores on, not the badges. Those are
   two different questions and they routinely disagree: the RSI badge asks
   whether a name is oversold *today*, while _oversold() scores rsi_min_recent,
   how far it got pushed *recently*. UNH bottomed at 33 and has since recovered
   to 48 -- badge false, thesis intact, and the badge's answer written as prose
   would have called that shallow. */
const OVERSOLD_DEFAULTS = {
  rsi_ideal_low: 28,
  rsi_ideal_high: 38,
  rsi_zero_above: 50,
  williams_r_oversold: -80,
};

function whyPicked(pick, trade) {
  const passed = new Set((pick.badges || []).filter((b) => b.passed).map((b) => b.label));
  const tech = pick.technicals || {};
  const cfg = Object.assign({}, OVERSOLD_DEFAULTS,
    (view.data && view.data.config && view.data.config.scoring) || {});
  const parts = [];

  /* The fall, and the part of it that has already been given back. */
  const low = tech.rsi_min_recent != null ? tech.rsi_min_recent : tech.rsi14;
  if (low != null) {
    const wr = tech.williams_r_min_recent != null ? tech.williams_r_min_recent : tech.williams_r14;
    const confirmed = wr != null && wr < cfg.williams_r_oversold;
    /* The recovery is the half of the thesis the low alone cannot show, and
       it belongs on the clause carrying the low -- not tacked onto whatever
       sentence happens to end the branch. */
    const back = tech.rsi14 != null && tech.rsi14 - low >= 5 ? Math.round(tech.rsi14) : null;
    if (low < cfg.rsi_ideal_low) {
      parts.push(`It fell hard — RSI bottomed near ${Math.round(low)}` +
                 (back ? `, back to ${back} now` : "") + ". " +
                 `That deep is as often a stock in real trouble as one that is merely stretched.`);
    } else if (low <= cfg.rsi_ideal_high) {
      parts.push(`It sold off far enough to look stretched rather than broken — RSI bottomed near ${Math.round(low)}` +
                 (confirmed ? ", with Williams %R agreeing" : "") +
                 (back ? `, and has recovered to ${back} since` : "") + ".");
    } else if (low < cfg.rsi_zero_above) {
      parts.push(`The dip was mild — RSI only reached ${Math.round(low)}` +
                 (back ? ` and is back to ${back}` : "") +
                 `, so this one earns its place on the turn more than on the washout.`);
    } else {
      parts.push(`It never really got oversold — RSI held above ${Math.round(low)} throughout.`);
    }
  }

  /* The turn. These four are exactly what _bounce() scores. */
  const turns = [];
  if (passed.has("Above 9-day EMA")) turns.push("back above its 9-day average");
  if (passed.has("Above 20-day EMA")) turns.push("above its 20-day");
  if (passed.has("MACD crossing up")) turns.push("MACD crossing up");
  if (passed.has("Volume expanding on green")) turns.push("volume expanding on an up day");
  parts.push(turns.length
    ? `The turn is showing — ${list(turns)}.`
    : "The turn has not shown up yet — none of the four bounce signals have fired, so this is the washout without the confirmation.");

  /* Why the premium is worth collecting. Percentile first: rich for this stock
     beats rich in the abstract, which is the order _premium_richness prefers. */
  if (pick.iv_percentile != null) {
    parts.push(`Options are priced richly for this name — implied volatility sits in its ` +
               `${ordinal(Math.round(pick.iv_percentile))} percentile for the year, which is what you are paid for.`);
  } else if (pick.iv_hv != null && pick.iv_hv > 1) {
    parts.push(`Options are pricing about ${pick.iv_hv.toFixed(1)}× the movement the stock has actually ` +
               `been making, which is what you are paid for.`);
  }

  /* Where the strike sits -- the one line that answers "will I be assigned".
     Computed against support_60d rather than read off the "Near support"
     badge, which is about where the *stock* sits, not the strike. */
  if (trade && trade.strike != null && trade.pct_below_spot != null) {
    const support = tech.support_60d;
    let where = "";
    if (support && trade.strike <= support) {
      where = ", under the low it has held for 60 days";
    } else if (passed.has("Near support")) {
      where = ", though the stock is already sitting on that 60-day low";
    }
    parts.push(`The suggested strike is ${pct(trade.pct_below_spot)} below today’s price${where} — ` +
               `that is how far it can fall before you are assigned.`);
  }

  return parts.join(" ");
}

/* "a, b and c" -- the page never uses a serial comma. */
function list(items) {
  if (items.length <= 1) return items[0] || "";
  return items.slice(0, -1).join(", ") + " and " + items[items.length - 1];
}

/* The jargon layer. Plain English is the page; this is what she opens when she
   wants to check the actual figure. */
function renderNumbers(pick, trade) {
  const det = el("details", "numbers");
  det.appendChild(el("summary", null, "The numbers"));

  const t = pick.technicals || {};
  const f = pick.fundamentals || {};
  const pairs = [
    ["RSI (14)", t.rsi14 == null ? "—" : t.rsi14.toFixed(1)],
    ["Williams %R", t.williams_r14 == null ? "—" : t.williams_r14.toFixed(1)],
    ["Implied volatility", pct(pick.iv, 0)],
    ["IV ÷ realised vol", pick.iv_hv == null ? "—" : pick.iv_hv.toFixed(2)],
    /* Already a percentage -- run.py:iv_percentile multiplies by 100 before it
       rounds. pct() would do it a second time and print 8830%. Not visible
       yet only because the cache needs 20 daily readings and has 2. */
    ["IV percentile", pick.iv_percentile == null
      ? "building" : pick.iv_percentile.toFixed(0) + "%"],
    ["Delta", trade.delta == null ? "—" : trade.delta.toFixed(3)],
    ["Annualised on cash", pct(trade.annualized_pct, 1)],
    ["Breakeven", money(trade.breakeven)],
    ["Strike below price", pct(trade.pct_below_spot, 1)],
    ["Bid / ask", trade.bid == null ? "—"
      : `${money(trade.bid)} / ${money(trade.ask)}`],
    ["Open interest", trade.open_interest == null
      ? "—" : trade.open_interest.toLocaleString("en-US")],
    ["Sales, year on year", growth(f.revenue_yoy)],
    ["Sales, quarter on quarter", growth(f.revenue_qoq)],
    ["Operating margin", f.operating_margin == null
      ? "—" : pct(f.operating_margin, 1)],
    ["Next earnings", f.next_earnings || "not scheduled"],
    ["50-day EMA", t.ema50 == null ? "—" : money(t.ema50)],
    ["200-day EMA", t.ema200 == null ? "—" : money(t.ema200)],
    ["52-week low", t.low_52w == null ? "—" : money(t.low_52w)],
  ];

  const grid = el("div", "numbers__grid");
  pairs.forEach(([k, v]) => {
    const cell = el("div");
    cell.appendChild(el("span", "k", k));
    cell.appendChild(el("span", "v", v));
    grid.appendChild(cell);
  });
  det.appendChild(grid);

  /* The score breakdown lives here rather than in the row: the row carries two
     tallies and a third would compete with the two she acts on. */
  const breakdown = renderBreakdown(pick.components);
  if (breakdown) det.appendChild(breakdown);

  /* Kept out of the grid: a caveat this long wraps a cell onto two ragged
     lines and breaks the rhythm of every row beside it. */
  if (pick.iv_percentile == null) {
    det.appendChild(el("p", "misses",
      "IV percentile reads “building” because it needs about three months of " +
      "daily readings before it means anything. It started collecting on the " +
      "first run."));
  }

  const missed = pick.badges.filter((b) => b.passed === false).map((b) => b.label);
  if (missed.length) {
    det.appendChild(el("p", "misses", "Misses: " + missed.join(", ") + "."));
  }
  const unsure = pick.badges.filter((b) => b.passed == null).map((b) => b.label);
  if (unsure.length) {
    det.appendChild(el("p", "misses", "Couldn’t tell: " + unsure.join(", ") + "."));
  }

  if (pick.buzz) {
    det.appendChild(el("p", "misses",
      `Reddit: ${pick.buzz.mentions.toLocaleString("en-US")} mentions today, ` +
      `ranked #${pick.buzz.rank} across all tickers.`));
  }
  return det;
}

/* ---- reddit --------------------------------------------------------- */

function renderReddit(rows) {
  if (!rows || !rows.length) return;
  const list = $("#reddit-list");
  rows.slice(0, 10).forEach((r) => {
    const li = el("li");
    li.appendChild(el("span", "reddit__ticker", r.ticker));
    if (r.name) li.appendChild(el("span", "reddit__name", r.name));
    li.appendChild(el("span", "reddit__count",
      r.mentions.toLocaleString("en-US") + " mentions"));
    if (r.mention_change != null) {
      const moved = Math.abs(r.mention_change * 100) >= 0.5;
      const ch = el("span", "reddit__change", moved
        ? Math.abs(r.mention_change * 100).toFixed(0) + "% vs yesterday"
        : "level with yesterday");
      /* A rise arrow on a flat reading is a small lie, and the arrow is the
         non-colour cue, so it has to be right. */
      ch.dataset.dir = !moved ? "flat" : r.mention_change > 0 ? "up" : "down";
      li.appendChild(ch);
    } else {
      li.appendChild(el("span", "reddit__held", "new to the board"));
    }
    list.appendChild(li);
  });
  $("#reddit-section").hidden = false;
}

/* ---- the cross-check prompt ----------------------------------------- */

/* She deliberately runs the same question past Gemini and Perplexity. That is
   a feature of how she works, so the job here is to make it take ten seconds
   instead of an hour -- not to talk her out of it. */
function buildPrompt() {
  const data = view.data;
  const names = shownNames();
  const lines = [];
  lines.push(
    "I sell cash-secured puts on stocks that have sold off but are showing signs " +
    "of bouncing, aiming to collect the premium without being assigned the shares.");
  lines.push("");
  lines.push(
    `My screener ran on ${data.as_of} against ${data.universe_size} US stocks with ` +
    `weekly options. These are the ${names.length} I am looking at, and the number ` +
    "against each is where it ranked. All quotes are delayed and reflect the prior " +
    "close.");
  lines.push("");

  names.forEach((p) => {
    const t = p.trade || {};
    const tech = p.technicals || {};
    const f = p.fundamentals || {};
    lines.push(`${p.rank}. ${p.symbol} — ${p.name} — $${p.price}`);
    if (t.strike != null) {
      lines.push(
        `   sell the $${t.strike} put expiring ${t.expiry} (${t.dte} days), ` +
        `collect about $${(t.credit * 100).toFixed(0)}, ` +
        `${pct(t.keep_premium_odds)} chance of keeping it`);
    }
    lines.push(
      `   RSI ${tech.rsi14 == null ? "n/a" : tech.rsi14.toFixed(1)}` +
      `, IV/realised vol ${p.iv_hv == null ? "n/a" : p.iv_hv.toFixed(2)}` +
      `, sales YoY ${growth(f.revenue_yoy)}` +
      `, next earnings ${f.next_earnings || "not scheduled"}`);
    const missed = p.badges.filter((b) => b.passed === false).map((b) => b.label);
    if (missed.length) lines.push(`   misses: ${missed.join(", ")}`);
    lines.push("");
  });

  lines.push("Please cross-check this list. For each name, tell me:");
  lines.push("1. Why it sold off recently, and whether that cause looks temporary " +
             "or a permanent change in the business.");
  lines.push("2. Anything scheduled before the expiry date that could move it " +
             "sharply — earnings, regulatory decisions, court dates, guidance.");
  lines.push("3. Whether you would rank any of these differently, and why.");
  lines.push("");
  lines.push("Cite your sources. If you do not know something, say so rather " +
             "than guessing.");
  return lines.join("\n");
}

function wireCopy() {
  const btn = $("#copy-btn");
  const box = $("#copy-fallback");
  const note = $("#copy-fallback-note");
  const area = $("#copy-fallback-text");

  const settle = (label) => {
    btn.textContent = label;
    btn.dataset.state = "done";
    setTimeout(() => {
      btn.textContent = btn.dataset.idle;
      delete btn.dataset.state;
    }, 5000);
  };

  btn.addEventListener("click", async () => {
    const text = buildPrompt();
    box.hidden = true;
    try {
      await navigator.clipboard.writeText(text);
      settle("Copied — paste into Gemini or Perplexity");
      return;
    } catch {
      /* Clipboard access can be blocked outright by the browser. */
    }
    /* The prompt goes on the page, visible and already selected. Telling her
       to press Ctrl+C while nothing is selected is a dead end, and one
       keystroke is the difference between that and a working morning. */
    area.value = text;
    box.hidden = false;
    area.focus();
    area.select();
    area.scrollTop = 0;   /* select() leaves it at the end; show the start */
    if (document.execCommand && document.execCommand("copy")) {
      box.hidden = true;
      settle("Copied — paste into Gemini or Perplexity");
      return;
    }
    note.textContent =
      "This browser blocked the copy. The prompt is below and already " +
      "selected — press Ctrl+C, then paste it into Gemini or Perplexity.";
    settle("Prompt is below — press Ctrl+C");
  });
}

/* ---- the numbers toggle --------------------------------------------- */

/* Rows are rebuilt whenever she pages or filters, so the toggle has to be
   re-applied to the new ones rather than only wired once. */
function applyNumbers() {
  const open = $("#numbers-toggle").checked;
  document.querySelectorAll("details.numbers").forEach((d) => { d.open = open; });
}

function wireNumbersToggle() {
  const box = $("#numbers-toggle");
  let saved = null;
  try { saved = localStorage.getItem("show-numbers"); } catch { /* private mode */ }
  box.checked = saved === "yes";
  box.addEventListener("change", () => {
    applyNumbers();
    try { localStorage.setItem("show-numbers", box.checked ? "yes" : "no"); }
    catch { /* nothing to do; the toggle still works for this visit */ }
  });
  applyNumbers();
}

/* ---- page ----------------------------------------------------------- */

function daysBetween(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const then = Date.UTC(y, m - 1, d);
  const now = new Date();
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((today - then) / 86400000);
}

function notice(text) {
  $("#notice-text").textContent = text;
  $("#notice-band").hidden = false;
}

/* ---- what she is looking at ----------------------------------------- */

/* The screener scores every name that survives the options stage -- about
   seventy-five of them, each with a chain and a chosen put -- and only the top
   ten used to be published. The rest now ride along in the same file, so "show
   me ten different ones" is a slice of data already on the page: no re-run, no
   network, nothing to wait for.

   There is deliberately no "run it again" button. A re-run puts the same data
   through the same criteria and returns the same list. The control worth
   having is this one. */
const view = {
  data: null,
  offset: 0,
  newOnly: false,
  settings: null,   // her weights; starts as this morning's and can be put back
  baseline: null,   // this morning's, kept so "changed" is a fact not a guess
  strikePref: null,
};

const everyName = () => (view.data.picks || []).concat(view.data.bench || []);
const pageSize = () => (view.data.picks || []).length || 10;

/* Nothing she has moved. Worth asking rather than always re-scoring, because
   the published order is not quite score order: the catalyst penalty lands
   after the ten are chosen, so a researched name carrying a structural flag
   stays in the ten rather than being overtaken by a bench name nobody looked
   into. Re-sorting on arrival would quietly undo that. Once she has changed
   something, re-sorting is the entire point. */
const untouched = () =>
  !view.strikePref &&
  (!view.settings ||
    JSON.stringify(view.settings) === JSON.stringify(view.baseline));

/* The delta each preference aims at. Delta is roughly the market's own odds of
   assignment, so this really is a safety dial and not a cosmetic one. */
const STRIKE_TARGETS = { safe: 0.12, balanced: 0.20, rich: 0.30 };

/* The put closest to the delta she asked for, out of the ones already quoted
   in this name's chosen expiry. */
function preferredStrike(row) {
  if (!view.strikePref || !row.trade) return row;
  const target = STRIKE_TARGETS[view.strikePref];
  const choices = [row.trade].concat(row.trade.alternatives || []);
  const nearest = choices.reduce((best, alt) =>
    Math.abs(alt.delta - target) < Math.abs(best.delta - target) ? alt : best);
  return Score.withStrike(row, nearest.id);
}

/* The list as she has tuned it. Names are never added or removed here -- these
   are the forty-odd the gates already admitted this morning. */
const tunedNames = () =>
  untouched()
    ? everyName()
    : Score.rescoreAll(everyName().map(preferredStrike), view.settings);

const pool = () => {
  const names = tunedNames();
  return view.newOnly ? names.filter((p) => !p.seen) : names;
};
const shownNames = () => pool().slice(view.offset, view.offset + pageSize());

const scrollToNames = () => $("#names").scrollIntoView({
  behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
  block: "start",
});

function renderList() {
  const names = pool();
  const shown = shownNames();
  const rows = $("#rows");

  rows.textContent = "";
  if (!shown.length) {
    rows.appendChild(el("p", "empty", view.newOnly
      ? "Every name on today’s list has been on it before. That is a real " +
        "answer, not a gap — the same setups are still the best ones available."
      : "Nothing cleared the safety filters today. That is a real answer, not a " +
        "failure — it means no stock had both the setup and a put worth selling."));
  } else {
    rows.dataset.counted = "no";
    shown.forEach((p) => rows.appendChild(renderRow(p)));
    setTimeout(() => { rows.dataset.counted = "yes"; }, 1200);
  }

  applyNumbers();
  updateControls(names.length);
  tunedNote();
}

function updateControls(count) {
  const size = pageSize();
  const last = Math.min(view.offset + size, count);

  /* The heading has to describe what is actually underneath it. Leaving "the
     ten names" over ranks 11 to 20 is a small lie the page can avoid. */
  $("#names").textContent = view.offset > 0
    ? "Ranked below the ten"
    : view.newOnly
      ? "New on the list today"
      : untouched()
        ? "The ten names"
        : "The ten names, your weights";

  $("#list-count").textContent = count
    ? `Showing ${view.offset + 1}–${last} of ${count} ranked names.`
    : "";
  $("#more-btn").disabled = view.offset + size >= count;
  $("#reset-btn").hidden = view.offset === 0;

  /* Only this morning's ten were researched, so anything else is numbers
     alone -- whether she paged down to it or her own weights lifted it into
     view. Better to say that than to let a blank note read as "no news". */
  const researched = (view.data.picks || []).map((p) => p.symbol);
  $("#deep-note").hidden = !view.data.catalyst_ran ||
    shownNames().every((p) => researched.includes(p.symbol));
}

function wireControls() {
  const repeats = everyName().some((p) => p.seen);

  /* An older list republished by the fallback has no bench and no repeat marks,
     so there is nothing here to operate. */
  $("#controls").hidden = !(view.data.bench || []).length && !repeats;
  $("#new-only-label").hidden = !repeats;

  $("#more-btn").addEventListener("click", () => {
    view.offset += pageSize();
    renderList();
    scrollToNames();
  });
  $("#reset-btn").addEventListener("click", () => {
    view.offset = 0;
    renderList();
    scrollToNames();
  });
  $("#new-only").addEventListener("change", (e) => {
    view.newOnly = e.target.checked;
    view.offset = 0;
    renderList();
  });
}

/* ---- her own settings ------------------------------------------------ */

/* The screen ran on one set of weights this morning. These move the same ones,
   in her browser, over the names already in the file: no re-run, no network,
   nothing stored. A reload is this morning's list again.

   The limit is worth stating plainly, and the panel does. Weights re-rank; they
   cannot admit a name the gates dropped, because that name was never published.
   Price, volume, market cap and the RSI ceiling ran against all 570 symbols. */

const clone = (value) => JSON.parse(JSON.stringify(value));

const WORDS = ["none", "one", "two", "three", "four", "five",
               "six", "seven", "eight", "nine", "ten"];
const count = (n) => WORDS[n] || String(n);

const STRIKE_CHOICES = [
  ["safe", "Safer", "Further below the price. Less premium, and less chance of being assigned."],
  ["balanced", "As screened", "The put the screen picked this morning."],
  ["rich", "Richer", "Closer to the price. More premium, and more chance of being assigned."],
];

function renderWeights() {
  const grid = $("#tuning-weights");
  grid.textContent = "";

  COMPONENTS.forEach(([key, label, note]) => {
    if (view.baseline.weights[key] === undefined) return;

    const line = el("div", "weight");
    const name = el("label", "weight__label", label);
    name.htmlFor = "w-" + key;
    line.appendChild(name);

    const slider = document.createElement("input");
    slider.type = "range";
    slider.id = "w-" + key;
    slider.className = "weight__slider";
    slider.min = "0";
    slider.max = "40";
    slider.step = "1";
    slider.value = String(view.settings.weights[key]);
    slider.addEventListener("input", () => {
      view.settings.weights[key] = Number(slider.value);
      readout.textContent = slider.value;
      view.offset = 0;
      renderList();
    });
    line.appendChild(slider);

    const readout = el("span", "weight__value", String(view.settings.weights[key]));
    line.appendChild(readout);

    /* What the slider is actually weighing, with the thresholds it ran under.
       A slider she cannot see the mechanics of is a slider she has to trust,
       and the whole reason this panel exists is that she should not have to. */
    if (note && view.baseline.scoring) {
      line.appendChild(el("p", "weight__note", note(view.baseline.scoring)));
    }
    grid.appendChild(line);
  });
}

function renderStrikeChoice() {
  const wrap = $("#tuning-strike");
  /* Thin chains are normal -- the open-interest and spread filters are strict.
     If nothing on the list has a second quoted put, there is no dial to offer. */
  const swappable = everyName().filter(
    (p) => ((p.trade || {}).alternatives || []).length).length;
  wrap.hidden = !swappable;
  if (!swappable) return;

  const box = $("#strike-choice");
  box.textContent = "";
  STRIKE_CHOICES.forEach(([value, label, why]) => {
    const button = el("button", "segmented__option", label);
    button.type = "button";
    button.setAttribute("role", "radio");
    button.title = why;
    const chosen = (view.strikePref || "balanced") === value;
    button.setAttribute("aria-checked", chosen ? "true" : "false");
    button.addEventListener("click", () => {
      view.strikePref = value === "balanced" ? null : value;
      view.offset = 0;
      renderStrikeChoice();
      renderList();
    });
    box.appendChild(button);
  });
}

/* What actually changed, in names. This is the answer to the question the
   sliders exist to ask -- "do the stocks change?" -- and a re-sorted list on
   its own does not answer it. */
function tunedNote() {
  const note = $("#tuned-note");
  $("#tuning-reset").hidden = untouched();

  if (untouched()) {
    note.hidden = true;
    return;
  }

  const morning = (view.data.picks || []).map((p) => p.symbol);
  const now = tunedNames().slice(0, pageSize());
  const arrived = now.filter((p) => !morning.includes(p.symbol));
  const swapped = now.filter((p) => (p.trade || {}).swapped).length;

  let text;
  if (!arrived.length) {
    text = now.some((p, i) => p.symbol !== morning[i])
      ? "Your settings. The same ten names as this morning, in a different order."
      : "Your settings. The same ten names, in the same order — these weights " +
        "do not change the ranking.";
  } else {
    text = `Your settings. ${cap(count(now.length - arrived.length))} of this ` +
      `morning's ten are still in the top ten; ` +
      `${arrived.map((p) => p.symbol).join(", ")} moved up from the bench.`;
  }
  if (swapped) {
    text += ` ${cap(count(swapped))} ${swapped === 1 ? "name shows" : "names show"} ` +
      `a different put from the one screened.`;
  }
  note.textContent = text;
  note.hidden = false;
}

const cap = (word) => word.charAt(0).toUpperCase() + word.slice(1);

function wireTuning() {
  const config = view.data.config;
  /* An older list republished by the fallback predates the published config,
     and there is nothing to tune against. */
  if (!config || !config.weights) return;

  view.baseline = clone(config);
  view.settings = clone(config);

  $("#tuning").hidden = false;
  $("#tuning-floor").textContent =
    `What this cannot do is widen the net. Price, volume, market cap and the ` +
    `RSI ceiling were applied to all ${view.data.universe_size} symbols this ` +
    `morning, and only the ${everyName().length} that came through are in this ` +
    `file. Moving those means a fresh run.`;

  renderWeights();
  renderStrikeChoice();

  $("#tuning-reset").addEventListener("click", () => {
    view.settings = clone(view.baseline);
    view.strikePref = null;
    view.offset = 0;
    renderWeights();
    renderStrikeChoice();
    renderList();
  });
}

/* ---- the chat -------------------------------------------------------- */

/* The deployed Worker. Empty until it is deployed, and empty is a working
   state: the page is a list first, and it has to render its ten names with the
   chat switched off, unreachable, or out of budget. */
const CHAT_URL = "https://put-screen-chat.tradingscreening.workers.dev/";

const chat = { key: "", turns: [], busy: false };

/* She opens a bookmark that carries the passphrase, so she never types one.
   It is taken out of the address bar immediately -- it is a lock on the spend,
   not a login, but there is no reason to leave it sitting in a screenshot. */
function readChatKey() {
  const url = new URL(location.href);
  const given = url.searchParams.get("k");
  if (given) {
    try { sessionStorage.setItem("chat-key", given); } catch { /* private mode */ }
    url.searchParams.delete("k");
    history.replaceState(null, "", url.pathname + url.search + url.hash);
    return given;
  }
  try { return sessionStorage.getItem("chat-key") || ""; } catch { return ""; }
}

/* Her turn takes the solid blue field with reversed lettering, the answer sits
   on the stock: the same two-weight relationship the guide blocks use, so the
   conversation reads as part of the page rather than a widget dropped onto it. */
function bubble(who, text) {
  const wrap = el("div", "bubble bubble--" + who);
  wrap.appendChild(el("p", "bubble__who", who === "you" ? "You" : "The screen"));
  const body = el("div", "bubble__body");
  body.textContent = text;
  wrap.appendChild(body);
  $("#chat-log").appendChild(wrap);
  return body;
}

function chatNote(text) {
  const note = $("#chat-note");
  note.textContent = text || "";
  note.hidden = !text;
}

function chatBusy(busy) {
  chat.busy = busy;
  $("#chat-send").disabled = busy;
  $("#chat-send").textContent = busy ? "Thinking…" : "Ask";
}

async function ask(question) {
  const text = question.trim();
  if (!text || chat.busy) return;

  chatNote("");
  bubble("you", text);
  $("#chat-starters").hidden = true;
  chat.turns.push({ role: "user", content: text });
  chatBusy(true);

  const body = bubble("screen", "");
  let answer = "";

  try {
    const res = await fetch(CHAT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: chat.key, messages: chat.turns }),
    });

    if (!res.ok) {
      const why = await res.json().catch(() => ({}));
      body.parentNode.remove();
      chat.turns.pop();
      chatNote(why.error || `The chat is not answering (${res.status}).`);
      return;
    }

    const reader = res.body.getReader();
    const decode = new TextDecoder();
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      answer += decode.decode(value, { stream: true });
      body.textContent = answer;
    }
  } catch {
    if (!answer) {
      body.parentNode.remove();
      chat.turns.pop();
      chatNote("The chat could not be reached. Everything above still stands — " +
               "it is computed here, not written by a model.");
      return;
    }
  } finally {
    chatBusy(false);
  }

  chat.turns.push({ role: "assistant", content: answer });
}

/* Openers built from today's own list. "What should I ask it" is the real
   barrier, and a name she can see on the page is a better prompt than an
   empty box. */
function chatStarters(data) {
  const box = $("#chat-starters");
  box.textContent = "";
  const top = (data.picks || [])[0];
  const first = (data.bench || [])[0];
  const repeat = (data.picks || []).find((p) => p.seen && p.seen.same_contract);

  const asks = [
    top && `Why did ${top.symbol} rank first?`,
    first && `Why isn't ${first.symbol} in the ten?`,
    repeat && `${repeat.symbol} is back with the same put — has anything changed?`,
    "Which of these is least likely to leave me holding the shares?",
  ].filter(Boolean).slice(0, 3);

  asks.forEach((q) => {
    const btn = el("button", "starter", q);
    btn.type = "button";
    btn.addEventListener("click", () => ask(q));
    box.appendChild(btn);
  });
}

function wireChat(data) {
  if (!CHAT_URL) return;
  chat.key = readChatKey();
  if (!chat.key) return;

  $("#chat-section").hidden = false;
  chatStarters(data);

  const input = $("#chat-input");
  $("#chat-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value;
    input.value = "";
    ask(q);
  });
  /* Enter sends, shift-enter breaks the line: she is asking a question, not
     drafting. */
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("#chat-form").requestSubmit();
    }
  });
}

/* The page never said it had run. "Screened Wednesday" reads like an archive;
   the time it finished reads like something that happened this morning. */
const ranAt = (iso) => {
  if (!iso) return "";
  const when = new Date(iso);
  if (isNaN(when.getTime())) return "";
  return ", finished " + when.toLocaleTimeString(undefined,
    { hour: "numeric", minute: "2-digit" });
};

function renderBrief(data) {
  if (!data.brief) return;
  $("#brief-text").textContent = data.brief;
  $("#brief-section").hidden = false;
}

function render(data) {
  view.data = data;
  const picks = data.picks || [];
  const deeper = (data.bench || []).length;

  $("#masthead-sub").textContent =
    `${picks.length} names from ${data.universe_size} stocks with weekly options. ` +
    `Screened ${longDate(data.as_of)}${ranAt(data.generated_at)}. Prices and option ` +
    `quotes are delayed and reflect the prior close — check the strike live before ` +
    `you trade.`;

  /* A weekend or a holiday is not staleness; four clear days is. */
  const age = daysBetween(data.as_of);
  if (age > 4) {
    notice(`This list is ${age} days old — the morning run has not published ` +
           `since ${longDate(data.as_of)}. Treat every price here as out of date.`);
  }

  $("#key-note").textContent =
    "The checks come straight from your own written criteria. The ranking is a " +
    "weighted score, not a count of ticks — open “the numbers” on any name to " +
    "see where every point came from, what it missed, and what could not be " +
    "measured.";

  $("#section-note").textContent = deeper
    ? `Ranked by score. Every name here already clears the safety filters — ` +
      `tradeable price, real volume, and a put that would actually fill. ` +
      `${deeper} more cleared them too and are ranked below.`
    : "Ranked by score. Every name here already clears the safety filters — " +
      "tradeable price, real volume, and a put that would actually fill.";

  renderBrief(data);
  wireNumbersToggle();
  wireControls();
  wireTuning();
  renderList();
  wireChat(data);
  renderReddit(data.reddit);

  $("#footer-note").textContent = data.catalyst_ran
    ? "The “why it fell” note on each name is written by Gemini from recent " +
      "news. It never picks or ranks the stocks — every number above is computed."
    : "The “why it fell” notes did not run for this list, so each name shows " +
      "its numbers only. Every number above is computed, never written by a model.";

  $("#footer-meta").textContent =
    `Generated ${data.generated_at.replace("T", " ").replace("+00:00", "")} UTC ` +
    `in ${data.elapsed_seconds}s. Sources: Yahoo Finance, Cboe delayed quotes, ` +
    `ApeWisdom. No account, brokerage, or personal data is used anywhere.`;

  wireCopy();
}

fetch("data/latest.json", { cache: "no-store" })
  .then((r) => {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  })
  .then(render)
  .catch((err) => {
    $("#masthead-sub").textContent = "Today’s list could not be loaded.";
    notice("Could not read today’s data file (" + err.message + "). " +
           "The morning run may still be in progress — try again shortly.");
    $("#rows").textContent = "";
    $("#rows").appendChild(el("p", "empty",
      "No list to show. Nothing here is out of date, because nothing loaded."));
  });
