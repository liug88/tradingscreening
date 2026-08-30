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
/* ---- the four questions ---------------------------------------------- */

/* One screen, four orders. The gates that find a beaten-up name with the
   business intact find the setup for all four; what differs is the ranking.
   So this is one screen ranked four ways rather than four screeners -- and a
   name can sit first on one list and fortieth on another for the same reason,
   because high implied volatility pays a seller and costs a buyer. Watching a
   name move when she flips the toggle is the most useful thing on the page.

   Two of the four need a contract to exist -- the put she sells and the call
   she buys -- and each of those lists carries only the names that have one.
   The other two rank the company, and always have an answer.

   `put` keeps the unsuffixed weight key every published file has used, so a
   file written before the others still ranks and still reads. */
const PROFILE_ORDER = ["put", "buy", "long", "call"];

const PROFILES = {
  put: {
    label: "Sell puts",
    horizon: "income",
    heading: "The ten names",
    verb: "sell a put against",
    note: (more) =>
      "Ranked by score. Every name here already clears the safety filters — " +
      "tradeable price, real volume, and a put that would actually fill." +
      (more ? ` ${more} more cleared them too and are ranked below.` : ""),
  },
  buy: {
    label: "Buy",
    horizon: "weeks",
    heading: "Ten to buy",
    verb: "buy",
    note: (more) =>
      "Ranked on the dip, the chart and the revenue. No option is involved, so " +
      "names with no put worth selling are ranked here too — she can own a " +
      "stock there is nothing to sell against." +
      (more ? ` ${more} more are ranked below.` : ""),
  },
  long: {
    label: "Hold",
    horizon: "months",
    heading: "Ten to hold",
    verb: "hold",
    note: (more) =>
      "Ranked on the chart and the revenue alone. Today's RSI is left out on " +
      "purpose — over six months it is noise, and if both horizons scored the " +
      "dip they would be one list." +
      (more ? ` ${more} more are ranked below.` : ""),
  },
  call: {
    label: "Buy calls",
    horizon: "with an expiry",
    heading: "Ten to buy calls on",
    verb: "buy a call on",
    note: (more) =>
      "The same names read from the other side of the option: cheap implied " +
      "volatility scores here and rich volatility scores on the sell-puts " +
      "list, for the same reason. Only names carrying a long-dated in-the-" +
      "money call worth buying are ranked, which is far fewer than the other " +
      "lists." +
      (more ? ` ${more} more are ranked below.` : ""),
  },
};

/* A label is a plain string unless what the component measured depends on the
   file being read, in which case it is a function of the same scoring config
   the notes get. `view.baseline` is unset for a payload with no config block,
   which is exactly the old-file case, so the empty object is the right read. */
const componentName = (label) =>
  (typeof label === "function" ? label((view.baseline || {}).scoring || {}) : label);

const COMPONENTS = [
  /* Two labels and two notes, because a file published before the composite
     shipped scored two readings rather than four, and its panel should say
     what that morning actually did. */
  ["oversold",
    (c) => (c.stoch_oversold == null ? "RSI + Williams %R"
                                     : "RSI / stochastic / MFI / %B"),
    (c) => (c.stoch_oversold == null
      ? `The lowest RSI of the last 10 sessions, not today's. Full credit inside ` +
        `${c.rsi_ideal_low}–${c.rsi_ideal_high}, none at ${c.rsi_zero_above} or above, ` +
        `and less again below ${c.rsi_ideal_low} — past a point it stops looking stretched ` +
        `and starts looking broken. Williams %R under ` +
        `${String(c.williams_r_oversold).replace("-", "−")} is the other 30%.`
      : `Four readings of how far it fell, each taken at its lowest of the last 10 ` +
        `sessions rather than today's. RSI is half of it: full credit inside ` +
        `${c.rsi_ideal_low}–${c.rsi_ideal_high}, none at ${c.rsi_zero_above} or above, and ` +
        `less again below ${c.rsi_ideal_low} — past a point it stops looking stretched and ` +
        `starts looking broken. Stochastic %D under ${c.stoch_oversold} is 20%, a fifth of ` +
        `which is the %K/%D cross rather than the level. Money flow under ${c.mfi_oversold} ` +
        `is another 20% — RSI weighted by volume, the only one of the four that is not ` +
        `pure price. Bollinger %B at the lower band is the last 10%. Williams %R is not ` +
        `scored: it is the stochastic flipped, and counting it again would make one ` +
        `reading look like two agreeing. The whole component then scales by the bounce ` +
        `below — cheap only counts once something has turned.`)],

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

  /* The four the buy and hold rankings score. They sit in the same table
     because a slider is a slider: renderWeights draws whichever of these the
     ranking in view actually weighs and skips the rest, so one table serves
     three panels. */

  ["trend_structure", "EMA stack / golden cross", (c) =>
    `The chart she asked for by name, as four facts rather than a verdict: ` +
    `price above the 200-day (30%), the 50-day above the 200-day — the golden ` +
    `cross (25%), the averages in order with price at the front (25%), and how ` +
    `young the cross is, full credit the day it happens and none by day ` +
    `${c.trend_cross_fresh_days} (20%). Four rather than one, because price can ` +
    `sit above the 200-day while the 50 is still under it, and that is a bounce ` +
    `inside a downtrend.`],

  ["revenue_expanding", "Revenue trend", (c) =>
    `Revenue that keeps rising, not revenue that rose once. Half is how many of ` +
    `the published quarters rose, a fifth an unbroken run ending at the latest, ` +
    `and three tenths how far it actually travelled — full credit at ` +
    `${pct(c.rev_yoy_strong, 0)} year on year. Without that last part a business ` +
    `growing 5% a year ties with one growing 150%. Scores 0.4 when fewer than ` +
    `two quarters are on file: unknown, not punished.`],

  ["room_to_run", "Room to the 52-week high", (c) =>
    `How far below its 52-week high it sits, counted as upside only where it is ` +
    `upside. Full credit at ${pct(c.room_ideal_below_high, 0)} down; past that it ` +
    `falls away again and reaches nothing at ${pct(c.room_broken_below_high, 0)}, ` +
    `because a stock 74% off its high is not a discount, it is a verdict. If the ` +
    `50-day is under the 200-day, whatever is left is cut to ` +
    `${pct(c.room_broken_trend_factor, 0)} of it.`],

  ["entry_timing", "Oversold + the turn", () =>
    `The fall and the bounce folded into the one question a buyer asks: 60% how ` +
    `far it fell on the readings above, 40% whether the fall has stopped. The ` +
    `same two the sell-puts list scores separately, in the same proportion, so a ` +
    `name does not change character between lists for a reason you cannot see.`],

  /* The two only the call list weighs. Same table, same rule: renderWeights
     draws whichever of these the ranking in view actually pays for. */

  ["iv_cheapness", "IV cheapness", (c) =>
    `IV richness above, read from the other side of the trade: one minus the ` +
    `same two numbers, because the premium that is rich to sell is expensive to ` +
    `buy. Full credit at or under 0.90× realised volatility and at the bottom of ` +
    `this name's own 12-month range, none at ${c.iv_hv_rich.toFixed(2)}× and the top ` +
    `of it. Scores 0.4 when neither reading came back — unknown, not cheap. It is ` +
    `the one component that ranks the two option lists against each other.`],

  ["contract_quality", "The call itself", (c) =>
    `Whether the contract can be traded, which is a different question from ` +
    `whether the stock is worth owning. 45% the bid-ask spread — full credit at ` +
    `${pct(c.call_spread_tight, 0)}, none at ${pct(c.call_spread_wide, 0)} — because a ` +
    `buyer crosses it twice, going in and coming out. 30% open interest, full ` +
    `credit at ${c.call_oi_deep.toLocaleString("en-US")} contracts. 25% how much time ` +
    `she is buying, full credit at ${c.call_dte_ample} days.`],
];

/* One ranking's answer for one name. The sell-puts result sits at the top
   level of a card, where every published file has carried it; the other three
   are nested under their own key. Reading through here rather than at each call
   site means a row, a blurb and a breakdown all ask the same question. */
const scoreOf = (row, profile) =>
  (profile || view.profile) === "put" ? row : (row[(profile || view.profile)] || {});

/* The call the ranking scored. It rides inside its own block, beside the score
   it produced, and stays there when she moves a slider because Score.rescore
   hands it back -- so one read serves the published row and the tuned one. */
const callOf = (row) => ((row || {}).call || {}).contract;

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
    const name = componentName(label);
    const line = el("div", "breakdown__line");
    line.appendChild(el("span", "breakdown__label", name));
    line.appendChild(tally(
      unitMarks(part.points / part.max, "filled", Math.round(part.max / POINTS_PER_MARK)),
      `${name}: ${part.points.toFixed(1)} of ${part.max} points`));
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
  const profile = view.profile;
  const result = scoreOf(pick, profile);
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
  score.appendChild(el("strong", null,
    result.score == null ? "—" : result.score.toFixed(0)));
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

  /* The last two columns are the two quantities the list she is on turns on.
     Selling, that is the odds and the contract. Owning, there is no contract at
     all -- so they become the two things she named when she asked for these
     lists: the chart, and whether the revenue is expanding. Buying a call, the
     chart is what the thesis rests on and the contract is what it costs. */
  if (profile === "put") {
    row.appendChild(oddsSlot(trade));
    row.appendChild(tradeSlot(trade));
  } else if (profile === "call") {
    row.appendChild(trendSlot(pick));
    row.appendChild(callSlot(pick));
  } else {
    row.appendChild(trendSlot(pick));
    row.appendChild(revenueSlot(pick));
  }

  /* risk flags -- red is spent here and nowhere else */
  if (result.penalties && result.penalties.length) {
    const flags = el("div", "flags");
    result.penalties.forEach((p) => flags.appendChild(el("span", "flag", p.reason)));
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

  const why = profile === "put" ? whyPicked(pick, trade) : whyOwned(pick, profile);
  if (why) row.appendChild(el("p", "why", why));

  const chart = renderChart(pick);
  if (chart) row.appendChild(chart);

  row.appendChild(renderNumbers(pick, trade, result));
  return row;
}

/* ---- the last two columns, per list ---------------------------------- */

/* Odds of keeping the premium -- the signature quantity on the sell-puts page. */
function oddsSlot(trade) {
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
  return odds;
}

function tradeSlot(trade) {
  const box = el("div", "trade");
  box.appendChild(el("span", "metric__label", "The trade"));
  if (trade.strike != null) {
    box.appendChild(el("span", "trade__strike",
      `Sell the ${money(trade.strike)} put`));
    const line = el("div", "trade__line");
    line.innerHTML =
      `expires ${shortDate(trade.expiry)} &middot; ${trade.dte} days<br>` +
      `collect <span class="trade__credit">${money(trade.credit * 100, 0)}</span>` +
      ` &middot; set aside ${money(trade.cash_secured, 0)}`;
    box.appendChild(line);
  } else {
    box.appendChild(el("div", "trade__line", "No sellable put today."));
  }
  return box;
}

/* The chart, as the three facts that either hold or do not -- one mark each, in
   the same ink and meaning the same thing as the checks one column to the left,
   so the tally and the count underneath it agree.

   Deliberately not the component's score: that would put a ten-mark tally over a
   three-mark count and the two would read as a contradiction. The score is in
   the breakdown, where it is labelled, and the fourth thing it counts -- how
   fresh the cross is -- is the line under this one. */
function trendSlot(pick) {
  const t = pick.technicals || {};
  const box = el("div", "metric");
  box.appendChild(el("span", "metric__label", "The chart"));

  const facts = [
    ["price above the 200-day", t.above_ema200],
    ["the 50-day above the 200-day", t.golden_cross],
    ["the averages fully in order", t.full_stack],
  ];
  const inPlace = facts.filter(([, v]) => v === true).length;
  const known = facts.filter(([, v]) => v != null).length;

  box.appendChild(tally(
    facts.map(([, v]) => (v === true ? "filled" : v === false ? "hollow" : "unknown")),
    facts.map(([name, v]) =>
      `${name}: ${v === true ? "yes" : v === false ? "no" : "not known"}`).join("; ")));

  const val = el("div", "metric__value");
  val.innerHTML = known
    ? `<strong>${inPlace} of ${known}</strong> in place`
    : "<strong>—</strong>";
  box.appendChild(val);

  const age = t.golden_cross_days_ago;
  box.appendChild(el("div", "metric__sub",
    t.golden_cross === false ? "50-day still under the 200-day"
      : t.golden_cross && age != null ? `50 crossed the 200 ${age} days ago`
      : t.golden_cross ? "50 above the 200, crossed long ago"
      : "cross unknown"));
  return box;
}

/* "Expanding revenue" as something she can see rather than read. Five quarters
   is what the filings ship, so four comparisons -- enough to tell a business
   growing every quarter from one that grew once.

   The bars are drawn against the largest of the five and not against zero, so
   what they show is the shape and not the size. That would be a bad axis on a
   chart; here the figures are underneath and the shape is the whole question. */
function revenueSlot(pick) {
  const f = pick.fundamentals || {};
  const history = (f.revenue_history || []).filter((q) => q.revenue != null);
  const box = el("div", "trade");
  box.appendChild(el("span", "metric__label", "Revenue"));

  if (history.length < 2) {
    box.appendChild(el("div", "trade__line",
      "No filing history published — scored as unknown, not as bad."));
    return box;
  }

  const top = Math.max(...history.map((q) => q.revenue));
  const bars = el("div", "revbars");
  history.forEach((q, i) => {
    const bar = el("span", "revbars__bar");
    bar.style.height = Math.max(4, (q.revenue / top) * 100).toFixed(0) + "%";
    if (i && q.revenue > history[i - 1].revenue) bar.dataset.up = "1";
    bar.title = `${q.quarter}: ${bigMoney(q.revenue)}`;
    bars.appendChild(bar);
  });
  box.appendChild(bars);

  const rose = history.slice(1).filter((q, i) => q.revenue > history[i].revenue).length;
  const line = el("div", "trade__line");
  line.innerHTML = `<strong>up in ${rose} of ${history.length - 1}</strong> quarters` +
    (f.revenue_yoy == null ? "" : `<br>${growth(f.revenue_yoy)} year on year`);
  box.appendChild(line);

  /* An earnings date is a deduction on the sell-puts list, because an expiry is
     what makes it expensive. Owning shares through a print is just a thing that
     happens, so here it is a date rather than a charge. */
  if (f.next_earnings) {
    box.appendChild(el("div", "trade__line", `reports ${shortDate(f.next_earnings)}`));
  }
  return box;
}

/* Revenue arrives in dollars and runs to twelve figures. */
const bigMoney = (v) =>
  v == null ? "—"
    : Math.abs(v) >= 1e9 ? "$" + (v / 1e9).toFixed(1) + "bn"
    : "$" + (v / 1e6).toFixed(0) + "m";

/* The call, as a buyer reads it: what it costs, and how far the stock has to
   move before that was worth paying.

   No gold ink anywhere in here. Gold is the premium she keeps, and on this
   list the premium is the thing she pays -- one colour meaning both would be
   the same fault as spending red twice. The outlay is set in the weight a
   credit gets and in no colour at all. */
function callSlot(pick) {
  const call = callOf(pick);
  const box = el("div", "trade");
  box.appendChild(el("span", "metric__label", "The call"));

  if (!call) {
    box.appendChild(el("div", "trade__line", "No call worth buying today."));
    return box;
  }

  box.appendChild(el("span", "trade__strike", `Buy the ${money(call.strike)} call`));
  const line = el("div", "trade__line");
  line.innerHTML =
    `expires ${shortDate(call.expiry)} &middot; ${call.dte} days<br>` +
    `pay <strong>${money(call.outlay, 0)}</strong> &middot; ` +
    `${money(call.shares_equivalent, 0)} buys the shares instead`;
  box.appendChild(line);

  /* What has to happen, in the two numbers a chart-shaped thesis leaves out.
     Both ride on every call row -- see options._describe_call -- and the
     second is the argument for this strike rather than a cheaper one. */
  const needs = el("div", "trade__line");
  needs.innerHTML =
    `above ${money(call.breakeven)} to break even &middot; ` +
    `<strong>${pct(call.pct_to_breakeven, 1)}</strong> up` +
    (call.time_value_share == null ? ""
      : `<br>${pct(call.time_value_share)} of the price is time, and decays`);
  box.appendChild(needs);
  return box;
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

/* The same job for the three lists that do not sell a put. Different
   sentences, because they answer a different question: the put blurb ends on
   where the strike sits, and here there is either no strike at all or one she
   is buying rather than selling.

   Written off the components the ranking actually scored rather than off the
   technicals directly, so the prose cannot claim credit the score withheld.
   The hold list does not score today's dip and its blurb must not talk about
   one; the call list does not score the distance to the 52-week high and its
   blurb must not either. */
function whyOwned(pick, profile) {
  const t = pick.technicals || {};
  const f = pick.fundamentals || {};
  const scored = scoreOf(pick, profile).components || {};
  const parts = [];

  /* The chart, in the order the averages actually stand. Four shapes, because
     "above the 200-day" and "the 50 above the 200" are different claims and the
     gap between them is the bounce-inside-a-downtrend case. */
  const age = t.golden_cross_days_ago;
  const crossAge = t.golden_cross && age != null
    ? `, a cross ${age} days old` : "";
  if (t.full_stack) {
    parts.push(`The chart is in the order she asked for — price above the ` +
               `20-day, above the 50, and the 50 above the 200${crossAge}.`);
  } else if (t.golden_cross && t.above_ema200) {
    parts.push(`The 50-day is above the 200-day and price is above both` +
               `${crossAge}, though the averages are not yet fully in order.`);
  } else if (t.above_ema200) {
    parts.push("Price is back above its 200-day average while the 50-day is " +
               "still below it — a bounce inside a downtrend rather than a " +
               "trend that has turned.");
  } else if (t.above_ema200 === false) {
    parts.push("It is still under its 200-day average, which is the one thing " +
               "the chart she described has to have.");
  }

  /* Revenue: how many quarters rose, then how far it travelled. Both, because
     four small rises and one large one are the same count and not the same
     business. */
  const history = (f.revenue_history || []).filter((q) => q.revenue != null);
  const yoy = f.revenue_yoy == null ? "" : `, ${growth(f.revenue_yoy)} against a year ago`;
  if (history.length >= 2) {
    const of = history.length - 1;
    const rose = history.slice(1).filter((q, i) => q.revenue > history[i].revenue).length;
    parts.push(rose === of
      ? `Revenue rose in every one of the last ${count(of)} quarters filed${yoy}.`
      : rose === 0
        ? `Revenue did not rise in any of the last ${count(of)} quarters filed${yoy}.`
        : `Revenue rose in ${count(rose)} of the last ${count(of)} quarters filed${yoy}.`);
  } else {
    parts.push("No revenue history is filed for this one, so the growth half of " +
               "the ranking scored it as unknown rather than marking it down.");
  }

  /* Distance below the high, and what the ranking made of it. The second half
     matters: without it a name 74% down reads as the most upside on the page,
     which is exactly the row that frightened her. */
  const off = t.pct_below_52w_high;
  const room = (scored.room_to_run || {}).raw;
  if (off != null && scored.room_to_run) {
    parts.push(room != null && room < 0.15
      ? `It is ${pct(off)} below its 52-week high. Past a point that stops being ` +
        `room to recover and starts being a verdict, and the ranking scores it ` +
        `that way — almost none of the points for distance.`
      : `It sits ${pct(off)} below its 52-week high, which is the distance the ` +
        `ranking counts as room to recover.`);
  }

  /* The turn -- only where the ranking pays for it. A weight of zero is a
     deliberate choice in config.yaml and the page should say so rather than
     quietly leaving a sentence out. */
  const timing = scored.entry_timing;
  if (timing && timing.max) {
    const passed = new Set((pick.badges || []).filter((b) => b.passed).map((b) => b.label));
    const turns = [];
    if (passed.has("Above 9-day EMA")) turns.push("back above its 9-day average");
    if (passed.has("Above 20-day EMA")) turns.push("above its 20-day");
    if (passed.has("MACD crossing up")) turns.push("MACD crossing up");
    if (passed.has("Volume expanding on green")) turns.push("volume expanding on an up day");
    parts.push(turns.length
      ? `The turn is showing — ${list(turns)}.`
      : "The turn has not shown up yet — none of the four bounce signals have " +
        "fired, so this is the washout without the confirmation.");
  } else if (timing) {
    parts.push("Today's dip is not scored on this list at all. Over six months " +
               "it is noise, and scoring it would make this the buy list again.");
  }

  /* Volatility, but only where it is scored -- and it is scored the opposite
     way here. A quarter of this ranking turns on it, and it is the reason a
     name can sit first on one list and nowhere on the other. */
  const cheap = scored.iv_cheapness;
  if (cheap && cheap.max) {
    const readings = [];
    if (pick.iv_hv != null) {
      readings.push(`implied volatility is ${pick.iv_hv.toFixed(2)}× what the stock ` +
                    `has actually been doing`);
    }
    if (pick.iv_percentile != null) {
      readings.push(`it sits at the ${pick.iv_percentile.toFixed(0)}th percentile of ` +
                    `its own last twelve months`);
    }
    parts.push(!readings.length
      ? "Neither volatility reading came back, so the option was scored as " +
        "unmeasured rather than as cheap — a quarter of this ranking withheld."
      : `The option is ${cheap.raw >= 0.5 ? "cheap" : "not cheap"} on the readings ` +
        `that came back — ${list(readings)}. That is the one the sell-puts list ` +
        `scores the other way round.`);
  }

  /* What she would actually buy, and what has to happen for it to have been
     worth buying. Last, because it is the only sentence with a deadline in it. */
  const call = callOf(pick);
  if (call) {
    parts.push(`The contract that fits is the ${money(call.strike)} call expiring ` +
      `${shortDate(call.expiry)}: ${money(call.outlay, 0)} for the upside on 100 shares ` +
      `that would cost ${money(call.shares_equivalent, 0)} outright. It has to be above ` +
      `${money(call.breakeven)} — ${pct(call.pct_to_breakeven, 1)} up — by then to have ` +
      `made anything, and under ${money(call.strike)} it expires worthless.`);
  }

  return parts.join(" ");
}

/* "a, b and c" -- the page never uses a serial comma. */
function list(items) {
  if (items.length <= 1) return items[0] || "";
  return items.slice(0, -1).join(", ") + " and " + items[items.length - 1];
}

/* ---- the chart, in figures ------------------------------------------ */

/* Three things she has been opening a chart beside the page to check: where
   the moving averages sit against each other, what MACD reads against its
   signal line, and how far the stock is off its high. All three were in the
   payload already and none of them was on the row, so she was reading the page
   and then checking the page. This is a rendering gap, not a data one, which
   is why it works on files published before it shipped.

   The averages are sorted rather than given a verdict. Where "price" lands in
   the order *is* the trend, and reading it takes no arithmetic: price at the
   front is an uptrend, price at the back is the shape that frightened her. */
const STACK = [
  ["price",   (t, fallback) => (t.close == null ? fallback : t.close)],
  ["20-day",  (t) => t.ema20],
  ["50-day",  (t) => t.ema50],
  ["200-day", (t) => t.ema200],
];

function macdLine(t) {
  if (t.macd == null) return null;
  const parts = ["MACD " + t.macd.toFixed(2)];
  if (t.macd_signal != null) {
    parts.push("signal " + t.macd_signal.toFixed(2));
    parts.push(t.macd > t.macd_signal ? "above its signal" : "below its signal");
  }
  parts.push(t.macd < 0 ? "under zero" : "over zero");
  return parts.join(" · ");
}

/* ---- the chart, drawn ----------------------------------------------- */

/* The figures below say what the chart says, and she still had to open a real
   one to see the shape: a name 20% off its high and a name 70% off read the
   same on a row of numbers. So the payload carries the line now as well as the
   reading, and this draws it.

   Two panels, one x-axis, one width, so a turn in the price sits directly over
   the crossing that called it. No library -- the page has no dependencies and
   the whole job is a few `<path>` elements' worth of arithmetic. */
const CHART = { w: 640, price: 116, macd: 54, pad: 3 };

const svgEl = (tag, cls, attrs) => {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  if (cls) node.setAttribute("class", cls);
  Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, value));
  return node;
};

const drawn = (values) => (values || []).some((v) => v != null);

/* A hole stays a hole. The 200-day average has no reading until 200 days sit
   behind it, and joining across the gap draws a run the stock never made. */
function linePath(values, x, y) {
  let d = "";
  let pen = false;
  values.forEach((value, i) => {
    if (value == null) { pen = false; return; }
    d += (pen ? "L" : "M") + x(i).toFixed(1) + " " + y(value).toFixed(1) + " ";
    pen = true;
  });
  return d.trim();
}

/* One panel. Its lines arrive together because they share a scale -- drawing
   the 50-day against its own range would put the crossing wherever it liked.
   `rule` is a value the scale must reach even if no line does, which is how
   MACD keeps its zero on screen when the whole reading sits below it. */
function panel(lines, cls, height, rule) {
  const live = lines.filter((line) => drawn(line.values));
  const n = Math.max(0, ...live.map((line) => line.values.length));
  if (n < 2) return null;

  const seen = live.flatMap((line) => line.values.filter((v) => v != null));
  if (rule != null) seen.push(rule);
  let lo = Math.min(...seen);
  let hi = Math.max(...seen);
  if (hi === lo) { hi += 1; lo -= 1; }  /* a flat line still needs a middle */
  /* Headroom, so the extremes are lines rather than edges. Without it a MACD
     reading that never crosses zero pins the zero rule flat along the top of
     the panel, where it looks like a border and not like a level. */
  const room = (hi - lo) * 0.07;
  lo -= room;
  hi += room;

  const x = (i) => (i / (n - 1)) * CHART.w;
  const y = (v) => CHART.pad + (1 - (v - lo) / (hi - lo)) * (height - CHART.pad * 2);

  /* Stretched to the row's width and held at a fixed height, so ten of these
     down the page are the same size and can be compared. The strokes are told
     not to stretch with it: a hairline is a hairline at any width. */
  const box = svgEl("svg", "chart__panel " + cls, {
    viewBox: `0 0 ${CHART.w} ${height}`,
    preserveAspectRatio: "none",
    /* The lines underneath say all of this in words already, so a screen
       reader is better served by them than by a shape it cannot read. */
    "aria-hidden": "true",
    focusable: "false",
  });
  if (rule != null) {
    box.appendChild(svgEl("line", "chart__zero", {
      x1: 0, x2: CHART.w, y1: y(rule), y2: y(rule),
      "vector-effect": "non-scaling-stroke",
    }));
  }
  live.forEach((line) => box.appendChild(svgEl("path", line.cls, {
    d: linePath(line.values, x, y),
    "vector-effect": "non-scaling-stroke",
  })));
  return box;
}

const chartKey = (name, label) => {
  const key = el("span", "chart__key", label);
  key.dataset.line = name;
  return key;
};

/* Price over MACD. Slowest line first, so the closing price lands on top of the
   averages it is being judged against rather than under them. */
function renderPanels(series, lastDate) {
  if (!series) return null;
  const price = panel([
    { values: series.ema200, cls: "chart__ln chart__ln--slow" },
    { values: series.ema50, cls: "chart__ln chart__ln--fast" },
    { values: series.close, cls: "chart__ln chart__ln--close" },
  ], "chart__panel--price", CHART.price);
  const macd = panel([
    { values: series.macd_signal, cls: "chart__ln chart__ln--signal" },
    { values: series.macd, cls: "chart__ln chart__ln--macd" },
  ], "chart__panel--macd", CHART.macd, 0);
  if (!price && !macd) return null;

  const wrap = el("div", "chart__panels");
  if (price) {
    wrap.appendChild(price);
    const keys = el("div", "chart__keys");
    /* No axis is drawn, so the window says its own length, and the last close
       anchors the right-hand edge to a day she can name. */
    keys.appendChild(el("span", "chart__caption",
      lastDate ? `Six months to ${shortDate(lastDate)}` : "Six months"));
    keys.appendChild(chartKey("close", "price"));
    if (drawn(series.ema50)) keys.appendChild(chartKey("fast", "50-day"));
    if (drawn(series.ema200)) keys.appendChild(chartKey("slow", "200-day"));
    wrap.appendChild(keys);
  }
  if (macd) {
    wrap.appendChild(macd);
    const keys = el("div", "chart__keys");
    keys.appendChild(el("span", "chart__caption", "MACD"));
    keys.appendChild(chartKey("macd", "line"));
    keys.appendChild(chartKey("signal", "signal"));
    wrap.appendChild(keys);
  }
  return wrap;
}

function renderChart(pick) {
  const t = pick.technicals || {};
  const price = t.close == null ? pick.price : t.close;
  const box = el("div", "chart");
  /* An old file carries no series, and the label should not promise a chart
     the page cannot draw from it. */
  const panels = renderPanels(pick.series, t.last_date);
  box.appendChild(el("span", "metric__label",
    panels ? "The chart" : "The chart, in figures"));
  let filled = false;
  if (panels) {
    box.appendChild(panels);
    filled = true;
  }

  /* The 52-week range with today marked inside it. Distance from the high is a
     number she asked for; a bar is the one way to show it without her doing
     the subtraction. */
  const lo = t.low_52w;
  const hi = t.high_52w;
  if (lo != null && hi != null && hi > lo && price != null) {
    const at = Math.min(100, Math.max(0, ((price - lo) / (hi - lo)) * 100));
    const range = el("div", "chart__range");
    range.appendChild(el("span", "chart__end", money(lo)));
    const track = el("span", "chart__track");
    const mark = el("i", "chart__mark");
    mark.style.left = at.toFixed(1) + "%";
    mark.title = money(price) + " today";
    track.appendChild(mark);
    range.appendChild(track);
    range.appendChild(el("span", "chart__end", money(hi)));
    box.appendChild(range);
    /* Distance from the high is the figure she asked for. The low side is kept
       because a name sitting on it is what the downtrend penalty charges for --
       but BE is 347% above its low, and past a few hundred percent a multiple
       is the only reading that stays legible. Same rule as growth(). */
    const off = (price - lo) / lo;
    box.appendChild(el("div", "chart__line",
      `${pct((hi - price) / hi)} below its 52-week high, and ` +
      (off > 3 ? `${(1 + off).toFixed(1)} times its low`
               : `${pct(off)} above its low`)));
    filled = true;
  }

  const levels = STACK
    .map(([label, read]) => [label, read(t, pick.price)])
    .filter((pair) => pair[1] != null)
    .sort((a, b) => b[1] - a[1]);
  if (levels.length > 1) {
    const stack = el("div", "chart__stack");
    stack.appendChild(el("span", "chart__caption", "High to low"));
    levels.forEach(([label, value]) => {
      const item = el("span", "chart__level", `${label} ${money(value)}`);
      if (label === "price") item.dataset.price = "1";
      stack.appendChild(item);
    });
    box.appendChild(stack);
    filled = true;
  }

  const macd = macdLine(t);
  if (macd) {
    box.appendChild(el("div", "chart__line", macd));
    filled = true;
  }

  return filled ? box : null;
}

/* The jargon layer. Plain English is the page; this is what she opens when she
   wants to check the actual figure. */
function renderNumbers(pick, trade, result) {
  const det = el("details", "numbers");
  det.appendChild(el("summary", null, "The numbers"));

  const t = pick.technicals || {};
  const f = pick.fundamentals || {};
  const pairs = [
    ["RSI (14)", t.rsi14 == null ? "—" : t.rsi14.toFixed(1)],
    ["Williams %R", t.williams_r14 == null ? "—" : t.williams_r14.toFixed(1)],
    /* Spread in rather than printed with a dash: these joined the screen after
       the first files went out, and an em dash in a cell reads as a reading
       that came back empty rather than one that was never taken.

       %K sits in its own cell directly under Williams %R on purpose. Side by
       side -- 58.8 against −41.2 -- the note at the foot of this panel stops
       being a claim she has to take on trust and becomes a sum she can do. */
    ...(t.stoch_k == null ? [] : [["Stochastic %K", t.stoch_k.toFixed(1)]]),
    ...(t.stoch_d == null ? [] : [
      ["Stochastic %D", t.stoch_d.toFixed(1)],
      ["%K / %D cross", t.stoch_cross_up ? "crossed up" : "no cross"],
    ]),
    ...(t.mfi14 == null ? [] : [["Money flow (14)", t.mfi14.toFixed(1)]]),
    ...(t.bb_percent_b == null ? [] : [["Bollinger %B", t.bb_percent_b.toFixed(2)]]),
    /* Underlying readings, so they stay on a row with no contract. */
    ["Implied volatility", pct(pick.iv, 0)],
    ["IV ÷ realised vol", pick.iv_hv == null ? "—" : pick.iv_hv.toFixed(2)],
    /* Already a percentage -- run.py:iv_percentile multiplies by 100 before it
       rounds. pct() would do it a second time and print 8830%. Not visible
       yet only because the cache needs 20 daily readings and has 2. */
    ["IV percentile", pick.iv_percentile == null
      ? "building" : pick.iv_percentile.toFixed(0) + "%"],
    /* Spread in rather than shown empty. The file now carries names with no
       fillable put, and six cells of em dashes on a row that never had a
       contract reads as six readings that failed. */
    ...(trade.strike == null ? [] : [
      ["Delta", trade.delta == null ? "—" : trade.delta.toFixed(3)],
      ["Annualised on cash", pct(trade.annualized_pct, 1)],
      ["Breakeven", money(trade.breakeven)],
      ["Strike below price", pct(trade.pct_below_spot, 1)],
      ["Bid / ask", trade.bid == null ? "—"
        : `${money(trade.bid)} / ${money(trade.ask)}`],
      ["Open interest", trade.open_interest == null
        ? "—" : trade.open_interest.toLocaleString("en-US")],
    ]),
    /* The call's own figures, on the list that scored it and nowhere else --
       every other list has no call in front of her, and six more cells of em
       dashes would read as six readings that failed. */
    ...(view.profile !== "call" || !callOf(pick) ? [] : (() => {
      const c = callOf(pick);
      return [
        ["Call delta", c.delta == null ? "—" : c.delta.toFixed(3)],
        ["Contract IV", pct(c.iv, 0)],
        ["Call bid / ask", c.bid == null ? "—" : `${money(c.bid)} / ${money(c.ask)}`],
        ["Call spread", pct(c.spread_pct, 1)],
        ["Call open interest", c.open_interest == null
          ? "—" : c.open_interest.toLocaleString("en-US")],
        ["Time value", `${money(c.time_value)} of ${money(c.cost)}`],
        ["Breakeven", money(c.breakeven)],
        ["Move to breakeven", pct(c.pct_to_breakeven, 1)],
      ];
    })()),
    ["Sales, year on year", growth(f.revenue_yoy)],
    ["Sales, quarter on quarter", growth(f.revenue_qoq)],
    ["Operating margin", f.operating_margin == null
      ? "—" : pct(f.operating_margin, 1)],
    ["Next earnings", f.next_earnings || "not scheduled"],
    /* The moving averages and the 52-week range used to sit here. They are in
       the chart block now, in the order they actually stand, which is the
       reading -- and a figure that appears twice on one row is a figure she has
       to reconcile. */
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
  const breakdown = renderBreakdown((result || pick).components);
  if (breakdown) det.appendChild(breakdown);

  /* Two numbers, one measurement. Both are published on purpose -- %R is the
     name she already works from, %K is the one her reading told her to look
     for -- and the row has to say so, because "oversold on both" is the exact
     shape a false confirmation takes. */
  if (t.stoch_k != null && t.williams_r14 != null) {
    det.appendChild(el("p", "misses",
      "Stochastic %K and Williams %R are the same reading. Before smoothing, " +
      "%K is 100 plus %R — the same distance over the same range, flipped. " +
      "If both look oversold that is one signal agreeing with itself, and the " +
      "score counts it once."));
  }

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
  profile: "put",   // which of the four lists is in front of her
  offset: 0,
  newOnly: false,
  settings: null,   // her weights; starts as this morning's and can be put back
  baseline: null,   // this morning's, kept so "changed" is a fact not a guess
  strikePref: null,
};

/* Every name one ranking can hold. The file carries names with no fillable put
   -- `score: null` -- and many more with no call worth buying. Each option list
   drops its own: a null there would be scored as a contract nobody can place,
   and on the call list a missing contract would score zero for quality, which
   reads as a bad call rather than as no call at all. Buy and hold keep every
   name, which is the whole reason they are published. */
const everyName = (profile = view.profile) => {
  const all = (view.data.picks || []).concat(view.data.bench || []);
  if (profile === "put") return all.filter((p) => p.score != null);
  if (profile === "call") return all.filter((p) => p.call);
  return all;
};

/* Whether this file can answer a question at all, rather than whether the code
   knows how to draw it. A payload published before the other rankings has no
   weight block for them and no result on its names, and a site-only push
   republishes exactly such a file -- so the toggle offers what is in front of
   it and hides itself when only one ranking is there. */
const hasProfile = (profile) =>
  profile === "put" ||
  ((view.data.config || {})["weights_" + profile] != null &&
   everyName(profile).some((row) => row[profile]));

const offered = () => PROFILE_ORDER.filter(hasProfile);

const pageSize = () => (view.data.picks || []).length || 10;

/* The weight block one ranking reads. `put` keeps the unsuffixed name every
   published file has used. */
const weightKey = (profile) => (profile === "put" ? "weights" : "weights_" + profile);

/* Nothing she has moved *on the list in front of her*. Weights are per ranking,
   so a slider pushed on the sell-puts panel does not make the buy list hers,
   and the strike dial is a put control that only counts there.

   Worth asking rather than always re-scoring, because the published order is
   not quite score order: the catalyst penalty lands after the ten are chosen,
   so a researched name carrying a structural flag stays in the ten rather than
   being overtaken by a bench name nobody looked into. Re-sorting on arrival
   would quietly undo that. Once she has changed something, re-sorting is the
   entire point. */
const untouched = () => {
  if (view.profile === "put" && view.strikePref) return false;
  if (!view.settings) return true;
  const key = weightKey(view.profile);
  return JSON.stringify(view.settings[key]) === JSON.stringify(view.baseline[key]);
};

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

/* Sorted by one ranking's score and renumbered to match: a rank is a position
   in the list she is looking at, and keeping the published one would be a lie
   the moment she flips the toggle. */
const rerank = (names, profile) => names
  .slice()
  .sort((a, b) => (scoreOf(b, profile).score || 0) - (scoreOf(a, profile).score || 0))
  .map((row, i) => ({ ...row, rank: i + 1 }));

/* The list as she has tuned it. Names are never added or removed here -- these
   are the forty-odd the gates already admitted this morning.

   The file arrives in sell-puts order, so that one list is left exactly as
   published while she has changed nothing. The other two were never curated
   that way -- nothing was researched into them and nothing was held in place --
   so they are sorted here every time. */
const tunedNames = () => {
  const profile = view.profile;
  const names = everyName();
  if (untouched()) return profile === "put" ? names : rerank(names, profile);

  const scored = names.map(preferredStrike).map((row) => {
    const out = Score.rescore(row, view.settings, profile);
    /* rescore() returns the ranking it was asked for at the top level, which is
       where the put lives and where the other three do not. Putting it back under
       its own key here means every reader below sees one shape whether or not
       she has moved a slider, rather than each having to know. */
    return profile === "put" ? { ...row, ...out } : { ...row, [profile]: out };
  });
  return rerank(scored, profile);
};

const pool = () => {
  const names = tunedNames();
  return view.newOnly ? names.filter((p) => !p.seen) : names;
};
const shownNames = () => pool().slice(view.offset, view.offset + pageSize());

const scrollToNames = () => $("#names").scrollIntoView({
  behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
  block: "start",
});

/* ---- the toggle ------------------------------------------------------ */

/* Four buttons over one list of names. Flipping one re-sorts what is already
   in the file: no fetch, no re-run, nothing to wait for. The offset goes back
   to the top because rank eleven on one ranking is not rank eleven on another,
   and paging down would land her somewhere arbitrary. */
function renderRankingChoice() {
  const box = $("#ranking-choice");
  const keys = offered();
  /* One question is not a choice. An older file has only the sell-puts ranking
     in it, and the page is then exactly what it was before this shipped. */
  box.hidden = keys.length < 2;
  box.textContent = "";

  /* Gold appears only where a premium does. Declaring an ink the list in front
     of her does not use is the same fault as using one ink for two things. */
  $("#key-kept").hidden = view.profile !== "put";

  keys.forEach((key) => {
    const spec = PROFILES[key];
    const button = el("button", "segmented__option");
    button.type = "button";
    button.setAttribute("role", "radio");
    button.setAttribute("aria-checked", view.profile === key ? "true" : "false");
    button.appendChild(el("span", "segmented__name", spec.label));
    button.appendChild(el("span", "segmented__sub", spec.horizon));
    button.addEventListener("click", () => {
      if (view.profile !== key) showProfile(key);
    });
    box.appendChild(button);
  });
}

/* Everything that changes when she flips it. */
function showProfile(key) {
  view.profile = key;
  view.offset = 0;
  renderRankingChoice();
  renderStrip();
  if (view.baseline) {
    renderWeights();
    renderStrikeChoice();
  }
  renderList();
  chatStarters();
}

/* ---- what each ranking has actually done ----------------------------- */

/* Measured 2026-08-28 by tools/backtest.py -- the call ranking on 2026-08-29,
   off the same bars: five years of daily bars, the screen re-run on the first
   of each month, each list held for its own horizon.
   Written here rather than published in the payload because it is a fact about
   the model and not about this morning -- and because a number that changes
   with the file is a number nobody can check.

   Always against SPY, never a bare figure. The tape rose across the whole test
   window, so an absolute return is close to meaningless, and a bare "+15%" over
   tomorrow's names reads as a forecast. Against the index it is a claim about
   the ranking, which is the only thing a backtest can support.

   The call run is the one to read carefully. It holds the shares for 90 days,
   not the contract: there is no leverage in it, no decay, and no expiry that
   can take the whole position to zero. It says whether the ranking picked
   better names than the pool, which is the only question a price history can
   answer, and the answer was no.

   Three of these four are unflattering. They are here as measured: a backtest
   tuned until it looks good is the one kind that is worth nothing. */
const BACKTEST = {
  put: {
    span: "September 2022", tests: 47, positions: 470, hold: "35 days",
    lede: "Assigned on 17% of the 470 trades, against 22% for the pool it " +
          "picked from. That is the number this list is for — a seller who does " +
          "not want the shares is not trying to pick the biggest riser.",
    rows: [["These ten", "+1.8%"],
           ["SPY over the same windows", "+1.3%"],
           ["Everything that cleared the gates", "+1.5%"]],
    tail: "Average return over the 35 days. The middle trade returned −0.5%, so " +
          "the average is carried by the winners; it beat SPY in 26 of the 47 " +
          "windows, and was assigned less often than its own pool in 33 of them.",
  },
  buy: {
    span: "September 2022", tests: 47, positions: 470, hold: "5 weeks",
    lede: "This ranking lost — to the index, and to the pool it picked from.",
    rows: [["These ten", "+0.2%"],
           ["SPY over the same windows", "+1.3%"],
           ["Everything that cleared the gates", "+1.5%"]],
    tail: "It beat SPY in 16 of the 47 windows, and fell 10% at some point inside " +
          "the five weeks more often than the pool did — 48% against 38%. Five " +
          "weeks is a short horizon to ask a screen to be right about.",
  },
  long: {
    span: "September 2022", tests: 42, positions: 420, hold: "6 months",
    lede: "Ahead of the index and of its own pool on average — but the average " +
          "is carried by a few large winners.",
    rows: [["These ten", "+15.0%"],
           ["SPY over the same windows", "+9.2%"],
           ["Everything that cleared the gates", "+10.1%"]],
    tail: "The middle name returned 3.5%, which is less than the pool's 4.8%: the " +
          "typical name here did slightly worse and the best did much better. " +
          "46% of these positions fell 20% at some point inside the six months.",
  },
  call: {
    span: "September 2022", tests: 45, positions: 450, hold: "90 days",
    lede: "This ranking lost — to the index, and to the pool it picked from. " +
          "And it was measured holding the shares, not the call: the contract " +
          "would have multiplied every number below, in both directions.",
    rows: [["These ten", "+2.2%"],
           ["SPY over the same windows", "+4.8%"],
           ["Everything that cleared the gates", "+5.6%"]],
    tail: "The middle name returned +0.4% against the pool's +2.6%, and it beat " +
          "SPY in 17 of the 45 windows. Half ended lower than they started, and " +
          "55% fell 10% at some point inside the 90 days — on shares that is a " +
          "drawdown to sit through, on a call with an expiry running it is often " +
          "the whole position.",
  },
};

function renderStrip() {
  const box = $("#strip");
  const bt = BACKTEST[view.profile];
  box.textContent = "";
  if (!bt) {
    box.hidden = true;
    return;
  }
  box.hidden = false;

  box.appendChild(el("p", "strip__head",
    `Since ${bt.span} · ${bt.tests} monthly tests · ${bt.positions} positions · ` +
    `each held ${bt.hold}`));
  box.appendChild(el("p", "strip__lede", bt.lede));

  const rows = el("div", "strip__rows");
  bt.rows.forEach(([label, value], i) => {
    const line = el("div", "strip__row");
    if (!i) line.dataset.lead = "1";
    line.appendChild(el("span", "strip__label", label));
    line.appendChild(el("span", "strip__value", value));
    rows.appendChild(line);
  });
  box.appendChild(rows);
  box.appendChild(el("p", "strip__tail", bt.tail));

  /* The limits sit one click from the number, not in the footer. */
  const link = el("p", "strip__link");
  const a = el("a", null, "How this was measured, and what it cannot show");
  a.href = "method.html";
  link.appendChild(a);
  box.appendChild(link);
}

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
     ten names" over ranks 11 to 20 is a small lie the page can avoid, and so is
     leaving it over a list ranked on something else entirely. */
  const spec = PROFILES[view.profile];
  $("#names").textContent = view.offset > 0
    ? "Ranked below the ten"
    : view.newOnly
      ? "New on the list today"
      : untouched()
        ? spec.heading
        : spec.heading + ", your weights";

  /* Set here rather than once on load: what these names have in common is a
     different sentence on each list. */
  $("#section-note").textContent = spec.note(Math.max(0, count - size));

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

  const weights = view.settings[weightKey(view.profile)] || {};
  const baseline = view.baseline[weightKey(view.profile)] || {};

  COMPONENTS.forEach(([key, label, note]) => {
    if (baseline[key] === undefined) return;

    const line = el("div", "weight");
    const name = el("label", "weight__label", componentName(label));
    name.htmlFor = "w-" + key;
    line.appendChild(name);

    const slider = document.createElement("input");
    slider.type = "range";
    slider.id = "w-" + key;
    slider.className = "weight__slider";
    slider.min = "0";
    slider.max = "40";
    slider.step = "1";
    slider.value = String(weights[key]);
    slider.addEventListener("input", () => {
      weights[key] = Number(slider.value);
      readout.textContent = slider.value;
      view.offset = 0;
      renderList();
    });
    line.appendChild(slider);

    const readout = el("span", "weight__value", String(weights[key]));
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
     If nothing on the list has a second quoted put, there is no dial to offer.
     Neither is there on a list about owning the stock: swapping the strike
     moves no score there, and offering a dial that does nothing is worse than
     offering none. */
  const swappable = view.profile !== "put" ? 0 : everyName("put").filter(
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

  /* This morning's ten *on this list*, which for the other three rankings is not
     `picks` at all -- the file is written in sell-puts order. */
  const morning = (view.profile === "put"
    ? (view.data.picks || [])
    : rerank(everyName(), view.profile).slice(0, pageSize())).map((p) => p.symbol);
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
      `${arrived.map((p) => p.symbol).join(", ")} moved up from below them.`;
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
function chatStarters() {
  const box = $("#chat-starters");
  box.textContent = "";
  const ranked = pool();
  const top = ranked[0];
  const first = ranked[pageSize()];
  const repeat = view.profile === "put" &&
    (view.data.picks || []).find((p) => p.seen && p.seen.same_contract);

  const closing = {
    put: "Which of these is least likely to leave me holding the shares?",
    buy: "Which of these has both the strongest chart and the strongest revenue?",
    long: "Which of these would I still want to own in six months?",
    call: "Which of these has to move the least to be worth buying?",
  }[view.profile];

  const asks = [
    top && `Why did ${top.symbol} rank first?`,
    first && `Why isn't ${first.symbol} in the ten?`,
    repeat && `${repeat.symbol} is back with the same put — has anything changed?`,
    closing,
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
  chatStarters();

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

  renderBrief(data);
  wireNumbersToggle();
  wireControls();
  wireTuning();
  renderRankingChoice();
  renderStrip();
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
