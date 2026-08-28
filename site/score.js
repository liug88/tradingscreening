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

/* How stretched this name got recently, confirmed by Williams %R. Scored on
   the recent minimum rather than today's reading: measured only as of today,
   this and bounce() are opposed, and the falling knives win every time. */
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

  const wr = recent(tech, "williams_r_min_recent", "williams_r14");
  const stretched = 0.7 * rsiPart + 0.3 * ramp(wr, -50, cfg.williams_r_oversold);

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

const COMPONENT_FNS = {
  oversold,
  bounce: (row) => bounce(row),
  premium_richness: premiumRichness,
  sales_growth: salesGrowth,
  margin_trend: marginTrend,
  strike_safety: (row) => strikeSafety(row),
  trade_quality: tradeQuality,
};

/* ---- penalties ------------------------------------------------------- */

const asPct = (v) => Math.round(Math.abs(v) * 100) + "%";

/* Reasons to knock a name down, each with the points it costs. */
function penalties(row, cfg) {
  const found = [];
  const tech = row.technicals || {};
  const fund = row.fundamentals;
  const trade = row.trade;

  /* Both are YYYY-MM-DD, which sorts correctly as text -- no parsing, and no
     timezone to get wrong. */
  if (trade && fund && fund.next_earnings && trade.expiry
      && fund.next_earnings <= trade.expiry) {
    found.push({
      reason: `Reports earnings ${fund.next_earnings}, before the ${trade.expiry} expiry`,
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

function rescore(row, settings) {
  const { scoring, penalties: penaltyPoints } = settings;
  const weights = normalise(settings.weights);

  const components = {};
  for (const [name, fn] of Object.entries(COMPONENT_FNS)) {
    const raw = fn(row, scoring);
    components[name] = {
      raw: round(raw, 4),
      points: round(raw * weights[name], 2),
      max: round(weights[name], 1),
    };
  }

  const applied = penalties(row, penaltyPoints);
  const gross = fsum(Object.values(components).map((c) => c.points));
  const total = Math.max(0, gross - fsum(applied.map((p) => p.points)));

  return {
    score: round(total, 1),
    score_before_penalties: round(gross, 1),
    components,
    penalties: applied,
    badges: row.badges,  // the checklist is a fact about the stock, not a setting
  };
}

/* The whole list under one set of settings, re-ranked.

   Ranks are reassigned, because a rank is a position in the list she is
   looking at and it would be a lie to keep the old one. Nothing is added or
   dropped: these are the names the gates already admitted. */
function rescoreAll(names, settings) {
  return names
    .map((row) => ({ ...row, ...rescore(row, settings) }))
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

window.Score = { rescore, rescoreAll, withStrike, normalise, ramp, penalties };
