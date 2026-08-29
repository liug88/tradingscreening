/* The scoring model, again, in the browser.
 *
 * This is a port of screener/score.py. Every component function here has the
 * same name and the same arithmetic as the one there, and it reads the fields
 * under the names run.py publishes them by, so the two agree by construction:
 * at the settings the screener shipped with, rescore() returns the score the
 * file already carries. That equality is the test worth keeping -- it is what
 * makes the sliders honest, since she is moving the real model rather than a
 * lookalike -- and tests/test_score_parity.py holds it to it, name by name and
 * field by field, against a real published payload.
 *
 * Two ways it has already broken, both of which looked correct: adding the
 * components with a plain reduce, and rounding by scaling. See fsum() and
 * round() below.
 *
 * Why a second copy at all: the published file holds every name that cleared
 * the gates, with every input the model reads. Re-ranking them is pure
 * arithmetic over data already on her machine -- no network, no server, no
 * cost, no wait. The one thing it cannot do is widen the net, and the page says
 * so where it matters: the gates ran at five hundred and seventy names, and a
 * name they dropped was never published to be re-scored.
 *
 * If score.py changes, this changes with it.
 */

/* ---- the shared helpers --------------------------------------------- */

/* Linear 0..1 between two points. Works in either direction. */
function ramp(value, zeroAt, fullAt) {
  if (value === null || value === undefined) return 0;
  if (fullAt === zeroAt) return value === fullAt ? 1 : 0;
  return Math.max(0, Math.min(1, (value - zeroAt) / (fullAt - zeroAt)));
}

/* The rolling minimum, falling back to today's reading when it is missing.
   Not `?? today`: the key is present and null on a short history, and both a
   missing key and a null one have to fall back the same way. */
function recent(tech, recentKey, todayKey) {
  const value = tech[recentKey];
  return value === null || value === undefined ? tech[todayKey] : value;
}

const nil = (v) => v === null || v === undefined;

/* Add the way Python's sum() adds. Since 3.12 it carries the rounding error
   forward (Neumaier) instead of dropping it, and a plain a + b + c does not:
   USB's seven components come to 72.05 one way and 72.05000000000001 the other,
   which is the difference between a published 47 and a recomputed 47.1. Used
   everywhere score.py calls sum(). */
function fsum(values) {
  let total = 0;
  let carried = 0;
  for (const value of values) {
    const sum = total + value;
    carried += Math.abs(total) >= Math.abs(value)
      ? (total - sum) + value
      : (value - sum) + total;
    total = sum;
  }
  return total + carried;
}

/* ---- the seven components ------------------------------------------- */

/* How stretched this name got recently, across four readings, weighted for how
   much each adds rather than equally:

       RSI            50%   momentum, the reading she works from
       stochastic %D  20%   where it closed in its own range, plus the cross
       MFI            20%   the same shape as RSI but weighted by volume
       Bollinger %B   10%   position inside its own volatility, not momentum

   Scored on the recent minimum rather than today's reading: measured only as of
   today, this and bounce() are opposed, and the falling knives win every time.

   Williams %R is deliberately not a fifth term. It is `100 + raw %K`, the same
   arithmetic on the same window, so scoring both would count one measurement
   twice and make a single signal look like two agreeing. */
function oversold(row, cfg) {
  const tech = row.technicals || {};
  const rsi = recent(tech, "rsi_min_recent", "rsi14");

  let rsiPart;
  if (nil(rsi)) {
    rsiPart = 0;
  } else if (rsi >= cfg.rsi_ideal_low && rsi <= cfg.rsi_ideal_high) {
    rsiPart = 1;
  } else if (rsi < cfg.rsi_ideal_low) {
    /* Still oversold, but the deeper it goes the more it looks like a stock
       that is falling rather than one that is stretched. */
    rsiPart = 0.6 + 0.4 * ramp(rsi, 10, cfg.rsi_ideal_low);
  } else {
    rsiPart = ramp(rsi, cfg.rsi_zero_above, cfg.rsi_ideal_high);
  }

  let stretched;
  if (nil(cfg.stoch_oversold) || nil(tech.stoch_d)) {
    /* Published before the composite shipped, or too short a history to
       compute it. Reproduce the two-reading mix exactly as it scored. */
    const wr = recent(tech, "williams_r_min_recent", "williams_r14");
    stretched = 0.7 * rsiPart + 0.3 * ramp(wr, -50, cfg.williams_r_oversold);
  } else {
    const d = recent(tech, "stoch_d_min_recent", "stoch_d");
    /* The level says how far it fell; only the cross says it stopped. Kept to
       a fifth of the term because the turn is already priced below, by the
       bounce multiplier -- this is the stochastic's own read of it. */
    let stochPart = 0.8 * ramp(d, 50, cfg.stoch_oversold);
    if (tech.stoch_cross_up) stochPart += 0.2;
    const mfi = recent(tech, "mfi_min_recent", "mfi14");
    const pctB = recent(tech, "bb_percent_b_min_recent", "bb_percent_b");
    stretched =
      0.50 * rsiPart +
      0.20 * stochPart +
      0.20 * ramp(mfi, 50, cfg.mfi_oversold) +
      0.10 * ramp(pctB, 0.5, cfg.bb_oversold);
  }

  /* Being cheap only counts once something has turned. Without this the two
     components are parallel and a stock in free fall earns near-full credit
     precisely because it is falling. Scaling rather than gating keeps a
     partial turn worth partial credit. */
  /* A file published before this rule shipped carries no floor, and must
     re-score to exactly what it says. 1 is the old behaviour. */
  const floor = nil(cfg.oversold_unconfirmed_floor) ? 1 : cfg.oversold_unconfirmed_floor;
  return stretched * (floor + (1 - floor) * bounce(row));
}

/* Evidence the fall has actually stopped. */
function bounce(row) {
  const tech = row.technicals || {};
  let total = 0;
  if (tech.above_ema9) total += 0.30;
  if (tech.above_ema20) total += 0.15;
  if (tech.macd_cross_up) {
    total += 0.20;
    /* A cross below the zero line is a reversal off a low; above it is just
       continuation, which is not what she is looking for here. */
    if (tech.macd_below_zero) total += 0.10;
  }
  if (tech.up_day_volume_expansion) total += 0.25;
  return total;
}

/* Price under a 200-day the 50-day is also under, and nothing turning yet.
   The three conditions are ANDed deliberately: below the 200-day alone is
   common and often temporary, the 50 under the 200 says the decline reshaped
   both averages, and no bounce says it has not stopped. */
function inDowntrend(row, cfg) {
  if (nil(cfg.downtrend_bounce_max)) return false;  /* published before the rule */
  const tech = row.technicals || {};
  if (tech.above_ema200 !== false) return false;
  if (tech.golden_cross !== false) return false;
  return bounce(row) <= cfg.downtrend_bounce_max;
}

/* Is the premium rich relative to how much the stock actually moves? */
function premiumRichness(row, cfg) {
  const ratioPart = ramp(row.iv_hv, 0.90, cfg.iv_hv_rich);
  if (nil(row.iv_percentile)) return ratioPart;
  /* Once there is enough history, "rich for this stock" beats "rich in
     absolute terms" -- a utility at IV 30 is expensive, a biotech at 30 cheap. */
  return 0.5 * ratioPart + 0.5 * (row.iv_percentile / 100);
}

function salesGrowth(row, cfg) {
  const fund = row.fundamentals;
  if (!fund) return 0.4; // unknown, not rewarded -- but not a failure either
  return 0.65 * ramp(fund.revenue_yoy, -0.05, cfg.rev_yoy_target)
       + 0.35 * ramp(fund.revenue_qoq, -0.10, 0);
}

function marginTrend(row, cfg) {
  const fund = row.fundamentals;
  if (!fund) return 0.4;
  const parts = [fund.gross_margin_change, fund.operating_margin_change]
    .filter((change) => !nil(change))
    .map((change) => ramp(change, -0.01, 0.01));
  return parts.length ? fsum(parts) / parts.length : 0.4;
}

/* How much room is there between the strike and trouble? */
function strikeSafety(row) {
  const tech = row.technicals || {};
  const trade = row.trade;
  if (!trade) return 0;

  const atr = tech.atr14;
  /* Distance to breakeven in daily ranges: how many normal days of movement
     before this trade is underwater. */
  const cushion = atr ? ramp((tech.close - trade.breakeven) / atr, 0, 2.5) : 0;

  const support = tech.support_60d;
  const belowSupport = support
    ? ramp(trade.strike, support * 1.05, support * 0.95)
    : 0;
  return 0.6 * cushion + 0.4 * belowSupport;
}

function tradeQuality(row, cfg) {
  const trade = row.trade;
  if (!trade) return 0;
  const yieldPart = ramp(trade.annualized_pct, 0, cfg.ann_yield_target);
  /* A great-looking yield you cannot fill at the mid is not a great yield. */
  return yieldPart * (1 - 0.3 * ramp(trade.spread_pct, 0.05, 0.15));
}

/* ---- what the other rankings ask ------------------------------------- */

/* The contract a ranking scored, under the name the published file gives it.
   The put's sits at the top level, where every file has carried it; the call's
   rides inside its own ranking block, beside the score that read it. The other
   two rankings score a company and have none. */
function contractOf(row, profile) {
  if (profile === "put") return row.trade;
  if (profile === "call") return (row.call || {}).contract;
  return null;
}

/* premium_richness, read from the other side of the trade.

   Its exact inverse, on the same two numbers, and that is the point rather
   than a shortcut: the name whose premium is richest to sell is the most
   expensive to buy. Measuring cheapness some other way would let the two
   quietly agree, and hiding that disagreement would cost her the one thing the
   toggle is for.

   Unknown is where they part. With no implied-vol reading at all the ramp
   returns zero, and inverting zero would hand a name nobody could measure full
   credit for being cheap. So an unmeasured name scores the same 0.4 the
   fundamentals use: not rewarded, not treated as a failure. */
function ivCheapness(row, cfg) {
  if (nil(row.iv_hv) && nil(row.iv_percentile)) return 0.4;
  return 1 - premiumRichness(row, cfg);
}

/* Can she get out of this call at a fair price, and does it have time?

   Not tradeQuality under another name. That one is mostly annualised yield,
   which is a seller's whole reason for being there. A buyer has no yield --
   what she needs is a spread she can afford to cross twice, someone on the
   other side of it, and enough time for the thesis to come true. Weighted
   toward the spread because it is the one she pays directly, in cash, twice. */
function contractQuality(row, cfg) {
  const call = contractOf(row, "call");
  if (!call) return 0;
  return 0.45 * ramp(call.spread_pct, cfg.call_spread_wide, cfg.call_spread_tight)
       + 0.30 * ramp(call.open_interest, 0, cfg.call_oi_deep)
       + 0.25 * ramp(call.dte, 0, cfg.call_dte_ample);
}

/* The chart she asked for by name, as a number: above the 200-day (30%), the
   50 over the 200 (25%), the averages in order (25%), and how young the cross
   is (20%). Four facts, not one fact four times -- price can be over the
   200-day while the 50 is still under it, which is a bounce inside a
   downtrend. */
function trendStructure(row, cfg) {
  const tech = row.technicals || {};
  let parts = 0;
  if (tech.above_ema200) parts += 0.30;
  if (tech.golden_cross) parts += 0.25;
  if (tech.full_stack) parts += 0.25;
  /* Null means the cross is older than the frame, not that it never happened;
     golden_cross tells those apart. An old cross scores no freshness either
     way, so both read zero and neither is punished. */
  if (!nil(tech.golden_cross_days_ago) && tech.golden_cross) {
    parts += 0.20 * ramp(tech.golden_cross_days_ago, cfg.trend_cross_fresh_days, 0);
  }
  return parts;
}

/* Revenue that keeps rising, not revenue that rose once: half how many of the
   published quarters rose, a fifth an unbroken run ending at the latest, and
   three tenths how far it actually travelled year on year. The last term is
   there because counting steps alone ties a business growing 5% a year with
   one growing 157%. */
function revenueExpanding(row, cfg) {
  const history = (row.fundamentals || {}).revenue_history || [];
  const figures = history.map((q) => q.revenue).filter((v) => !nil(v));
  if (figures.length < 2) return 0.4;  /* unknown, as the other fundamentals read it */

  const steps = figures.slice(1).map((later, i) => later > figures[i]);
  const share = steps.filter(Boolean).length / steps.length;

  let streak = 0;
  for (let i = steps.length - 1; i >= 0 && steps[i]; i -= 1) streak += 1;

  const size = ramp((row.fundamentals || {}).revenue_yoy, 0, cfg.rev_yoy_strong);
  return 0.5 * share + 0.2 * (streak / steps.length) + 0.3 * size;
}

/* Distance below the 52-week high, read as upside only where it is upside.
   Taken straight this would score a name 74% down as maximum room to run,
   which is the exact case her mother flagged, so it ramps twice: a fall is a
   discount up to room_ideal_below_high and a verdict past it. A broken trend
   then cuts what is left -- not the downtrend penalty twice over, because
   withholding credit and taking points are different claims. */
function roomToRun(row, cfg) {
  const tech = row.technicals || {};
  const offHigh = tech.pct_below_52w_high;
  if (nil(offHigh)) return 0;

  const ideal = cfg.room_ideal_below_high;
  let room = offHigh <= ideal
    ? ramp(offHigh, 0, ideal)
    : ramp(offHigh, cfg.room_broken_below_high, ideal);
  if (tech.golden_cross === false) room *= cfg.room_broken_trend_factor;
  return room;
}

/* Oversold and the turn, folded into the one question a buyer asks. The 60/40
   mirrors the 20 and 15 the put ranking gives the two separately, so a name
   does not change character between lists for a reason she cannot see. */
function entryTiming(row, cfg) {
  return 0.6 * oversold(row, cfg) + 0.4 * bounce(row);
}

const COMPONENT_FNS = {
  oversold,
  bounce: (row) => bounce(row),
  premium_richness: premiumRichness,
  sales_growth: salesGrowth,
  margin_trend: marginTrend,
  strike_safety: (row) => strikeSafety(row),
  trade_quality: tradeQuality,
  trend_structure: trendStructure,
  revenue_expanding: revenueExpanding,
  room_to_run: roomToRun,
  entry_timing: entryTiming,
  iv_cheapness: ivCheapness,
  contract_quality: contractQuality,
};

/* ---- penalties ------------------------------------------------------- */

const asPct = (v) => Math.round(Math.abs(v) * 100) + "%";

/* Reasons to knock a name down, each with the points it costs. */
function penalties(row, cfg, profile = "put") {
  const found = [];
  const tech = row.technicals || {};
  const fund = row.fundamentals;
  const contract = contractOf(row, profile);

  /* An expiry is what makes an earnings date expensive. An owner with no
     contract to lose simply holds through the print, so this charge belongs to
     the two rankings that have one -- it is a flag on the other rows, not a
     deduction. Each reads its own expiry, and the call's is months later than
     the put's, so one row can be clear on one list and charged on the other.

     Both are YYYY-MM-DD, which sorts correctly as text -- no parsing, and no
     timezone to get wrong. */
  if (contract && fund && fund.next_earnings && contract.expiry
      && fund.next_earnings <= contract.expiry) {
    found.push({
      reason: `Reports earnings ${fund.next_earnings}, before the ${contract.expiry} expiry`,
      points: cfg.earnings_before_expiry,
    });
  }

  if (!nil(row.iv_hv) && row.iv_hv > cfg.iv_hv_extreme_above) {
    found.push({
      reason: `IV is ${row.iv_hv.toFixed(1)}x realized volatility — the market is pricing a specific event`,
      points: cfg.iv_hv_extreme,
    });
  }

  if (!nil(tech.change_5d) && tech.change_5d < -cfg.gap_down_5d_pct) {
    found.push({
      reason: `Down ${asPct(tech.change_5d)} in five sessions — still falling`,
      points: cfg.gap_down_5d,
    });
  }

  /* These two describe the same illness at different severities, so only the
     worse one is charged. Measured against the low it is sitting on rather
     than a 3% flag, the milder one covers the grind along the bottom and not
     just the day of the low -- falling back to the flag when the distance was
     never measured. */
  const aboveLow = tech.pct_above_52w_low;
  const sittingLow = !nil(aboveLow)
    ? aboveLow <= cfg.near_52w_low_pct
    : Boolean(tech.at_52w_low);

  if (nil(cfg.near_52w_low_pct)) {
    /* A history file from before these rules. Reproduce it as it was scored --
       the sliders re-rank the file she is looking at, not this one. */
    if (tech.at_52w_low && !tech.above_ema200) {
      found.push({
        reason: "At a 52-week low and below its 200-day average",
        points: cfg.new_low_under_ema200,
      });
    }
  } else if (inDowntrend(row, cfg)) {
    const offHigh = tech.pct_below_52w_high;
    const detail = offHigh ? `, ${asPct(offHigh)} below its 52-week high` : "";
    found.push({
      reason: `Still in a confirmed downtrend${detail} — 50-day under the 200-day, nothing turning yet`,
      points: cfg.downtrend_confirmed,
    });
  } else if (sittingLow && !tech.above_ema200) {
    found.push({
      reason: nil(aboveLow) || aboveLow < 0.005
        ? "Sitting on its 52-week low and below its 200-day average"
        : `Within ${asPct(aboveLow)} of its 52-week low and below its 200-day average`,
      points: cfg.new_low_under_ema200,
    });
  }

  if (row.catalyst && row.catalyst.verdict === "structural") {
    found.push({
      reason: `Selloff looks structural, not temporary: ${row.catalyst.headline || ""}`
        .replace(/[:\s]+$/, ""),
      points: cfg.catalyst_structural,
    });
  }

  return found;
}

/* ---- the composite --------------------------------------------------- */

const HALF = "5" + "0".repeat(16);

/* Round the way Python's round() rounds. It reads the exact value of the double
   and breaks a genuine tie toward the even digit. Scaling cannot get there:
   47.049999999999997 times ten is exactly 470.5, a tie the number never had, and
   Math.round takes it up to 47.1 where Python stays at 47. The decimal expansion
   is exact where the multiply is not, so the tie is found there instead. */
function round(value, dp) {
  if (!Number.isFinite(value)) return value;
  const away = Number(value.toFixed(dp));       // toFixed ties away from zero
  const wide = value.toFixed(dp + 17);          // wide enough to see a real tie
  if (wide.slice(-17) !== HALF) return away;
  const down = Number(wide.slice(0, wide.length - 17));
  return Math.round(down * 10 ** dp) % 2 === 0 ? down : away;
}

/* One name's score under one set of settings, with the breakdown that made it.
   Shaped exactly like score.py's return so renderBreakdown cannot tell the
   difference between a published score and a recomputed one. */
/* The sliders are relative importance, not points. Rescaling here is what keeps
   a tuned score out of 100 and therefore comparable to the one the file shipped
   with -- and at this morning's weights, which already sum to 100, it is the
   identity. */
function normalise(weights) {
  const total = fsum(Object.values(weights));
  if (!total) return weights;  // everything at zero: every score is zero, fairly
  return Object.fromEntries(
    Object.entries(weights).map(([k, v]) => [k, (v * 100) / total]));
}

/* One ranking's penalty points: the shared block, with its own overrides on
   top. A 52-week low under a falling 200-day costs more over six months than
   over five weeks, and the published file carries both blocks so the page can
   say which is which. */
function penaltyConfig(settings, profile) {
  return Object.assign({}, settings.penalties, settings["penalties_" + profile] || {});
}

/* The weight block naming the components one ranking scores. `put` keeps the
   unsuffixed name every published file has used. */
const weightsFor = (settings, profile) =>
  profile === "put" ? settings.weights : settings["weights_" + profile];

function rescore(row, settings, profile = "put") {
  const { scoring } = settings;
  const weights = normalise(weightsFor(settings, profile));

  /* Only the components this ranking asks for. A profile is a weight block and
     nothing more -- scoring the whole registry and multiplying the rest by
     zero would give the same total and a breakdown full of empty rows. */
  const components = {};
  for (const [name, weight] of Object.entries(weights)) {
    const raw = COMPONENT_FNS[name](row, scoring);
    components[name] = {
      raw: round(raw, 4),
      points: round(raw * weight, 2),
      max: round(weight, 1),
    };
  }

  const applied = penalties(row, penaltyConfig(settings, profile), profile);
  const gross = fsum(Object.values(components).map((c) => c.points));
  const total = Math.max(0, gross - fsum(applied.map((p) => p.points)));

  const out = {
    score: round(total, 1),
    score_before_penalties: round(gross, 1),
    components,
    penalties: applied,
    badges: row.badges,  // the checklist is a fact about the stock, not a setting
  };
  /* Shaped like the block it read, because the page swaps this one in whole.
     The call's contract lives inside its ranking block, so leaving it out here
     would drop it on the first slider move -- and the second move would then
     score a name with no call at all. The put's sits at the top level and
     survives on its own. */
  if (profile === "call" && row.call && row.call.contract) {
    out.contract = row.call.contract;
  }
  return out;
}

/* The whole list under one set of settings, re-ranked.

   Ranks are reassigned, because a rank is a position in the list she is
   looking at and it would be a lie to keep the old one. Nothing is added or
   dropped: these are the names the gates already admitted. */
function rescoreAll(names, settings, profile = "put") {
  return names
    .map((row) => ({ ...row, ...rescore(row, settings, profile) }))
    .sort((a, b) => b.score - a.score)
    .map((row, i) => ({ ...row, rank: i + 1 }));
}

/* A name with one of its other strikes swapped in, so the score reflects the
   contract she is actually looking at. Both components that read the trade --
   strike safety and trade quality -- move with it, which is the point: a safer
   strike really does earn a different score, and the page should say so. */
function withStrike(row, id) {
  if (!row.trade || id === row.trade.id) return row;
  const alternatives = row.trade.alternatives || [];
  const swap = alternatives.find((alt) => alt.id === id);
  if (!swap) return row;
  /* The alternatives travel with the swapped contract, so a second change
     starts from the same full set rather than from whatever is left. */
  return { ...row, trade: { ...swap, alternatives, swapped: true } };
}

window.Score = { rescore, rescoreAll, withStrike, normalise, ramp, penalties,
                 weightsFor, penaltyConfig };
