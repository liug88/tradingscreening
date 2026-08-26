/* Reads data/latest.json and draws the sheet.
   Every quantity that matters is rendered as a countable tally; the raw
   figures live one layer down, behind "the numbers". See DESIGN.md. */

const $ = (sel) => document.querySelector(sel);

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const money = (v, dp = 2) =>
  v == null ? "—" : "$" + v.toLocaleString("en-US",
    { minimumFractionDigits: dp, maximumFractionDigits: dp });

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

  row.appendChild(renderNumbers(pick, trade));
  return row;
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
    ["IV percentile", pick.iv_percentile == null
      ? "building" : pct(pick.iv_percentile, 0)],
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
function buildPrompt(data) {
  const lines = [];
  lines.push(
    "I sell cash-secured puts on stocks that have sold off but are showing signs " +
    "of bouncing, aiming to collect the premium without being assigned the shares.");
  lines.push("");
  lines.push(
    `My screener ran on ${data.as_of} against ${data.universe_size} US stocks with ` +
    "weekly options and returned these ten, ranked. All quotes are delayed and " +
    "reflect the prior close.");
  lines.push("");

  data.picks.forEach((p) => {
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

function wireCopy(data) {
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
    const text = buildPrompt(data);
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

function wireNumbersToggle() {
  const box = $("#numbers-toggle");
  let saved = null;
  try { saved = localStorage.getItem("show-numbers"); } catch { /* private mode */ }
  box.checked = saved === "yes";
  const apply = () => {
    document.querySelectorAll("details.numbers")
      .forEach((d) => { d.open = box.checked; });
  };
  box.addEventListener("change", () => {
    apply();
    try { localStorage.setItem("show-numbers", box.checked ? "yes" : "no"); }
    catch { /* nothing to do; the toggle still works for this visit */ }
  });
  apply();
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

function render(data) {
  const picks = data.picks || [];

  $("#masthead-sub").textContent =
    `${picks.length} names from ${data.universe_size} stocks with weekly options. ` +
    `Screened ${longDate(data.as_of)}. Prices and option quotes are delayed and ` +
    `reflect the prior close — check the strike live before you trade.`;

  /* A weekend or a holiday is not staleness; four clear days is. */
  const age = daysBetween(data.as_of);
  if (age > 4) {
    notice(`This list is ${age} days old — the morning run has not published ` +
           `since ${longDate(data.as_of)}. Treat every price here as out of date.`);
  }

  const rows = $("#rows");
  rows.textContent = "";
  if (!picks.length) {
    rows.appendChild(el("p", "empty",
      "Nothing cleared the safety filters today. That is a real answer, not a " +
      "failure — it means no stock had both the setup and a put worth selling."));
  } else {
    rows.dataset.counted = "no";
    picks.forEach((p) => rows.appendChild(renderRow(p)));
    setTimeout(() => { rows.dataset.counted = "yes"; }, 1200);
  }

  $("#key-note").textContent =
    "The checks come straight from your own written criteria. The ranking is a " +
    "weighted score, not a count of ticks — open “the numbers” on any name to " +
    "see exactly what it passed, missed, and what could not be measured.";

  renderReddit(data.reddit);

  $("#footer-note").textContent = data.catalyst_ran
    ? "The “why it fell” note on each name is written by Claude from recent " +
      "news. It never picks or ranks the stocks — every number above is computed."
    : "The “why it fell” notes did not run for this list, so each name shows " +
      "its numbers only. Every number above is computed, never written by a model.";

  $("#footer-meta").textContent =
    `Generated ${data.generated_at.replace("T", " ").replace("+00:00", "")} UTC ` +
    `in ${data.elapsed_seconds}s. Sources: Yahoo Finance, Cboe delayed quotes, ` +
    `ApeWisdom. No account, brokerage, or personal data is used anywhere.`;

  wireCopy(data);
  wireNumbersToggle();
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
