"""Turning Mom's criteria into a rank.

Her rules ANDed together return nothing most days, which is the same frustration
she has with Gemini. So only the tradability filters are gates; everything else
is scored 0-100 and the list is always ten deep.

Every card also carries a badge row -- which rules the name actually passes -- so
"ranked #3" is never a black box. Weights and thresholds live in config.yaml.
"""

from __future__ import annotations

from datetime import date


def _ramp(value: float | None, zero_at: float, full_at: float) -> float:
    """Linear 0..1 between two points. Works in either direction."""
    if value is None:
        return 0.0
    if full_at == zero_at:
        return 1.0 if value == full_at else 0.0
    fraction = (value - zero_at) / (full_at - zero_at)
    return max(0.0, min(1.0, fraction))


def _recent(tech: dict, recent_key: str, today_key: str) -> float | None:
    """The rolling minimum, falling back to today's reading when it's missing.

    Not `.get(key, default)`: the key can be present and None on a short history,
    which would return None rather than falling back.
    """
    value = tech.get(recent_key)
    return tech.get(today_key) if value is None else value


def _oversold(row: dict, cfg: dict) -> float:
    """How stretched this name got recently, across four readings.

    Scored on the recent minimum rather than today's. Measured only as of today,
    this component and `_bounce` are mathematically opposed: reclaiming the
    9-day EMA drags RSI back toward 50, so a name could score on one or the
    other but essentially never both -- and the falling knives won every time.
    The washout and the turn are both part of the setup, not alternatives.

    The four are weighted for how much each adds, not equally:

        RSI            50%   momentum, the reading she works from
        stochastic %D  20%   where it closed in its own range, plus the cross
        MFI            20%   the same shape as RSI but weighted by volume
        Bollinger %B   10%   position inside its own volatility, not momentum

    Williams %R is deliberately NOT a fifth term. It is `100 + raw %K`, the same
    arithmetic on the same window, so scoring both would count one measurement
    twice and make a single signal look like two that agree. It stays published
    under the name she knows, and %D is what carries its weight here.
    """
    tech = row["tech"]
    rsi = _recent(tech, "rsi_min_recent", "rsi14")
    if rsi is None:
        rsi_part = 0.0
    elif cfg["rsi_ideal_low"] <= rsi <= cfg["rsi_ideal_high"]:
        rsi_part = 1.0
    elif rsi < cfg["rsi_ideal_low"]:
        # Still oversold, but the deeper it goes the more it looks like a stock
        # that is falling rather than one that is stretched.
        rsi_part = 0.6 + 0.4 * _ramp(rsi, 10.0, cfg["rsi_ideal_low"])
    else:
        rsi_part = _ramp(rsi, cfg["rsi_zero_above"], cfg["rsi_ideal_high"])

    if "stoch_oversold" not in cfg or tech.get("stoch_d") is None:
        # Published before the composite shipped, or too short a history to
        # compute it. Reproduce the two-reading mix exactly as it scored.
        wr = _recent(tech, "williams_r_min_recent", "williams_r14")
        stretched = 0.7 * rsi_part + 0.3 * _ramp(wr, -50.0, cfg["williams_r_oversold"])
    else:
        d = _recent(tech, "stoch_d_min_recent", "stoch_d")
        # The level says how far it fell; only the cross says it stopped. Kept
        # to a fifth of the term because the turn is already priced below, by
        # the bounce multiplier -- this is the stochastic's own read of it.
        stoch_part = 0.8 * _ramp(d, 50.0, cfg["stoch_oversold"])
        if tech.get("stoch_cross_up"):
            stoch_part += 0.2
        mfi = _recent(tech, "mfi_min_recent", "mfi14")
        pct_b = _recent(tech, "bb_percent_b_min_recent", "bb_percent_b")
        stretched = (
            0.50 * rsi_part
            + 0.20 * stoch_part
            + 0.20 * _ramp(mfi, 50.0, cfg["mfi_oversold"])
            + 0.10 * _ramp(pct_b, 0.5, cfg["bb_oversold"])
        )

    # Being cheap only counts once something has turned. Without this the two
    # components are parallel and a stock in free fall earns near-full credit
    # precisely because it is falling: RBLX scored 19.55/20 here on 2026-08-26
    # while 74% below its high, its EMAs fully inverted, with `_bounce` at 0.0.
    # Scaling rather than gating keeps a partial turn worth partial credit.
    # A file published before this rule shipped carries no floor, and must
    # re-score to exactly what it says. 1.0 is the old behaviour.
    floor = cfg.get("oversold_unconfirmed_floor", 1.0)
    return stretched * (floor + (1.0 - floor) * _bounce(row, cfg))


def _bounce(row: dict, cfg: dict) -> float:
    """Evidence the fall has actually stopped."""
    tech = row["tech"]
    total = 0.0
    if tech.get("above_ema9"):
        total += 0.30
    if tech.get("above_ema20"):
        total += 0.15
    if tech.get("macd_cross_up"):
        total += 0.20
        # A cross below the zero line is a reversal off a low; above it is just
        # continuation, which is not what she's looking for here.
        if tech.get("macd_below_zero"):
            total += 0.10
    if tech.get("up_day_volume_expansion"):
        total += 0.25
    return total


def in_downtrend(row: dict, cfg: dict) -> bool:
    """Price under a 200-day that the 50-day is also under, and nothing turning.

    The three conditions are deliberately ANDed. Below the 200-day alone is
    common and often temporary; the 50 under the 200 as well says the decline
    has lasted long enough to reshape both averages; no bounce says it has not
    stopped. Together they describe the chart her mother recognised on sight.
    """
    if "downtrend_bounce_max" not in cfg:
        return False  # published before the rule shipped
    tech = row["tech"]
    if tech.get("above_ema200") is not False:
        return False
    if tech.get("golden_cross") is not False:
        return False
    return _bounce(row, cfg) <= cfg["downtrend_bounce_max"]


def _premium_richness(row: dict, cfg: dict) -> float:
    """Is the option premium rich relative to how much the stock actually moves?"""
    ratio_part = _ramp(row.get("iv_hv"), 0.90, cfg["iv_hv_rich"])
    percentile = row.get("iv_percentile")
    if percentile is None:
        return ratio_part
    # Once we have enough history, "rich for this stock" beats "rich in absolute
    # terms" -- a utility at IV 30 is expensive, a biotech at IV 30 is cheap.
    return 0.5 * ratio_part + 0.5 * (percentile / 100.0)


def _sales_growth(row: dict, cfg: dict) -> float:
    fund = row.get("fund")
    if not fund:
        return 0.4  # unknown, not rewarded -- but not treated as a failure either
    yoy = _ramp(fund.get("revenue_yoy"), -0.05, cfg["rev_yoy_target"])
    qoq = _ramp(fund.get("revenue_qoq"), -0.10, 0.0)
    return 0.65 * yoy + 0.35 * qoq


def _margin_trend(row: dict, cfg: dict) -> float:
    fund = row.get("fund")
    if not fund:
        return 0.4
    parts = [
        _ramp(change, -0.01, 0.01)
        for change in (fund.get("gross_margin_change"), fund.get("operating_margin_change"))
        if change is not None
    ]
    return sum(parts) / len(parts) if parts else 0.4


def _strike_safety(row: dict, cfg: dict) -> float:
    """How much room is there between the strike and trouble?"""
    tech, trade = row["tech"], row.get("trade")
    if not trade:
        return 0.0

    breakeven, spot, atr = trade["breakeven"], tech["close"], tech.get("atr14")
    # Distance to breakeven measured in daily ranges: "how many normal days of
    # movement before this trade is underwater."
    cushion = _ramp((spot - breakeven) / atr, 0.0, 2.5) if atr else 0.0

    support = tech.get("support_60d")
    below_support = _ramp(trade["strike"], support * 1.05, support * 0.95) if support else 0.0
    return 0.6 * cushion + 0.4 * below_support


def _trade_quality(row: dict, cfg: dict) -> float:
    trade = row.get("trade")
    if not trade:
        return 0.0
    yield_part = _ramp(trade.get("annualized_pct"), 0.0, cfg["ann_yield_target"])
    # A great-looking yield you can't fill at the mid isn't a great yield.
    spread_factor = 1.0 - 0.3 * _ramp(trade.get("spread_pct"), 0.05, 0.15)
    return yield_part * spread_factor


# ---- what the other rankings ask ----------------------------------------
#
# Selling a put, buying the stock and buying a call are one directional bet
# with three payoffs, so the gates that find "beaten up but not broken" find
# the setup for all of them. Only the ranking differs. These four components
# are what BUY and LONG rank on; the three above them that read a contract are
# what the put ranking adds.


def _trend_structure(row: dict, cfg: dict) -> float:
    """The chart she asked for by name, as a number.

    Her words, relayed: "which one has the best technical chart as defined by
    best parameters, like best average, 50 days crossing over to 200 days."
    That is four separate facts, and they are not the same fact said four
    times -- price can be over the 200-day while the 50 is still under it,
    which is a bounce inside a downtrend rather than an uptrend.

        above the 200-day   30%   the base case: is this thing above water
        golden cross        25%   the one she named, 50 over 200
        full stack          25%   price > 20 > 50 > 200, the order not the level
        cross freshness     20%   how young the cross is

    Freshness is in both profiles rather than LONG alone. A cross three weeks
    old has more runway left than one from two years ago on either horizon,
    and LONG already says what it means by weighting the whole component 35
    against BUY's 25. A second near-identical component to carry one term
    would be worse code and no more honest.
    """
    tech = row["tech"]
    parts = 0.0
    if tech.get("above_ema200"):
        parts += 0.30
    if tech.get("golden_cross"):
        parts += 0.25
    if tech.get("full_stack"):
        parts += 0.25

    # None means the cross is older than the frame, not that it never happened
    # -- `golden_cross` tells those two apart. An old cross scores no freshness
    # either way, so both read zero here and neither is punished for it.
    days = tech.get("golden_cross_days_ago")
    if days is not None and tech.get("golden_cross"):
        parts += 0.20 * _ramp(days, cfg["trend_cross_fresh_days"], 0.0)
    return parts


def _revenue_expanding(row: dict, cfg: dict) -> float:
    """Revenue that keeps rising, not revenue that rose once.

    `_sales_growth` already asks how fast the latest quarter grew. This asks a
    different question -- how many of the published quarters rose at all --
    because "expanding revenue" is a shape over time, and one good quarter
    against a weak one a year ago can fake it.

    Five quarters ship on every name, so there are four comparisons. That is
    also the ceiling on what can be asked: five quarters cannot answer whether
    growth is accelerating, and they cannot separate a seasonal Q4 from a real
    one -- sequential steps read a retailer's January as a fall every year,
    which is why the shape terms below are a share of the steps rather than a
    verdict.

    Counting steps alone is not enough, though, and the pool says so plainly:
    36 of 214 names rose in all four quarters, which ties a business growing 5%
    a year with one growing 157%. So a third of this reads how far revenue
    actually travelled. It takes that from `revenue_yoy` -- the latest quarter
    against the same quarter a year before -- rather than deriving it from the
    sequential steps, because a year-on-year comparison is seasonality-proof by
    construction and that is exactly what the steps are not.

    `_sales_growth` reads the same field on a lower ramp and is not in these
    weight blocks. The two are not the same question at a different scale: that
    one asks whether the business is still growing at all, this one asks how
    hard, because over months the size of the growth is a third of the thesis
    rather than a sanity check on it.
    """
    fund = row.get("fund")
    history = (fund or {}).get("revenue_history") or []
    figures = [q.get("revenue") for q in history if q.get("revenue") is not None]
    if len(figures) < 2:
        return 0.4  # unknown, the same reading the other fundamental terms give

    steps = [later > earlier for earlier, later in zip(figures, figures[1:])]
    share = sum(steps) / len(steps)

    # An unbroken run ending at the latest quarter is worth more than the same
    # count of rises scattered through the year: it is the difference between a
    # business expanding and one that bounces around.
    streak = 0
    for rose in reversed(steps):
        if not rose:
            break
        streak += 1

    size = _ramp(fund.get("revenue_yoy"), 0.0, cfg["rev_yoy_strong"])
    return 0.5 * share + 0.2 * (streak / len(steps)) + 0.3 * size


def _room_to_run(row: dict, cfg: dict) -> float:
    """Distance below the 52-week high, read as upside only where it is upside.

    The naive form of this component is the reason it needs a docstring. Taken
    straight, "how far below its high" would have scored RBLX -- 74% down, the
    50 under the 200, nothing turning -- as maximum room to run, which is the
    exact name her mother flagged and the exact opposite of the truth.

    So it ramps twice. Up to `room_ideal_below_high` the fall is a discount and
    more of it is better. Past that it stops being a discount and starts being
    a verdict, and the credit ramps back down to nothing by
    `room_broken_below_high`.

    Then a broken trend cuts what is left. This reads `golden_cross`, which the
    downtrend penalty reads too, but it is not the same charge twice: the
    penalty deducts for danger, and this simply declines to call a collapse
    upside. Withholding credit and taking points are different claims.
    """
    off_high = row["tech"].get("pct_below_52w_high")
    if off_high is None:
        return 0.0

    ideal = cfg["room_ideal_below_high"]
    if off_high <= ideal:
        room = _ramp(off_high, 0.0, ideal)
    else:
        room = _ramp(off_high, cfg["room_broken_below_high"], ideal)

    if row["tech"].get("golden_cross") is False:
        room *= cfg["room_broken_trend_factor"]
    return room


def _entry_timing(row: dict, cfg: dict) -> float:
    """Oversold and the turn, folded into the one question a buyer asks.

    The put ranking scores these separately because a seller weighs them
    differently -- the premium is paid for the fear, and the turn only decides
    whether she keeps it. A buyer is asking one thing: is this a good morning
    to own it. The 60/40 split mirrors the 20 and 15 the put profile gives the
    two, so a name does not change character between lists for a reason she
    cannot see.
    """
    return 0.6 * _oversold(row, cfg) + 0.4 * _bounce(row, cfg)


_COMPONENTS = {
    "oversold": _oversold,
    "bounce": _bounce,
    "premium_richness": _premium_richness,
    "sales_growth": _sales_growth,
    "margin_trend": _margin_trend,
    "strike_safety": _strike_safety,
    "trade_quality": _trade_quality,
    "trend_structure": _trend_structure,
    "revenue_expanding": _revenue_expanding,
    "room_to_run": _room_to_run,
    "entry_timing": _entry_timing,
}

# Which config block holds each ranking's weights. A profile is nothing more
# than a weight block: the names here are what run.py publishes under and what
# the page toggles between, and adding one is adding a block to config.yaml.
#
# `score()` computes only the components its profile names, so the put ranking
# never pays for a trend reading it does not use, and the buy ranking never
# asks a name with no contract about its bid-ask spread.
PROFILES = {"put": "weights", "buy": "weights_buy", "long": "weights_long"}

# The funnel narrows in stages, and each stage can only rank on what it has
# already paid to fetch. Ranking on a subset keeps the expensive calls -- option
# chains, fundamentals -- off names that were never going to make the ten.
STAGE_TECHNICAL = ("oversold", "bounce")
STAGE_FUNDAMENTAL = ("oversold", "bounce", "sales_growth", "margin_trend")


def partial_score(row: dict, config: dict, names: tuple[str, ...]) -> float:
    """Rank on a subset of components -- for narrowing mid-funnel."""
    weights, cfg = config["weights"], config["scoring"]
    return sum(_COMPONENTS[name](row, cfg) * weights[name] for name in names)


def penalty_config(config: dict, profile: str = "put") -> dict:
    """One ranking's penalty block: the shared numbers, with its overrides.

    Most charges cost the same whatever she means to do with the name -- a
    structural catalyst is bad news for a buyer and a seller alike. The few
    that differ are written in config.yaml as `penalties_long` and hold only
    the numbers that change, so each rule keeps one home and a profile says
    only how it differs from the rest.
    """
    return {**config["penalties"], **config.get("penalties_" + profile, {})}


def penalties(row: dict, cfg: dict, profile: str = "put") -> list[dict]:
    """Reasons to knock a name down, each with the points it costs."""
    found = []
    tech, fund, trade = row["tech"], row.get("fund"), row.get("trade")

    # An expiry is what makes an earnings date expensive: the option is priced
    # and gone before the stock has finished reacting. Owning the stock, the
    # date is a thing to know -- the row still says so -- rather than a thing to
    # charge for, because she can simply hold through it. CALLS will rejoin this
    # when it ships, and should cost more than the put does: a call is a wasting
    # asset and a gap against it is unrecoverable.
    if profile == "put" and trade and fund and fund.get("next_earnings"):
        try:
            reports = date.fromisoformat(fund["next_earnings"])
            if reports <= date.fromisoformat(trade["expiry"]):
                found.append(
                    {
                        "reason": f"Reports earnings {fund['next_earnings']}, before the {trade['expiry']} expiry",
                        "points": cfg["earnings_before_expiry"],
                    }
                )
        except ValueError:
            pass

    iv_hv = row.get("iv_hv")
    if iv_hv is not None and iv_hv > cfg["iv_hv_extreme_above"]:
        found.append(
            {
                "reason": f"IV is {iv_hv:.1f}x realized volatility — the market is pricing a specific event",
                "points": cfg["iv_hv_extreme"],
            }
        )

    change_5d = tech.get("change_5d")
    if change_5d is not None and change_5d < -cfg["gap_down_5d_pct"]:
        found.append(
            {
                "reason": f"Down {abs(change_5d):.0%} in five sessions — still falling",
                "points": cfg["gap_down_5d"],
            }
        )

    # These two describe the same illness at different severities, so only the
    # worse one is charged. Stacking them cost a name 30 points for one fact.
    #
    # `at_52w_low` is a 3% band, so the old form of the milder test fired on the
    # day of the low and went quiet while the stock ground along the bottom --
    # the whole danger zone, unpenalised. Measured against the low it is sitting
    # on instead, it covers the grind.
    above_low = tech.get("pct_above_52w_low")
    # Falls back to the 3% flag when the measured distance is missing, so a
    # short history keeps the old behaviour rather than losing the penalty.
    sitting_low = (
        above_low <= cfg.get("near_52w_low_pct", 0.0)
        if above_low is not None
        else bool(tech.get("at_52w_low"))
    )
    near_low = sitting_low and not tech.get("above_ema200")

    if "near_52w_low_pct" not in cfg:
        # A history file from before these rules. Reproduce it as it was scored
        # -- the sliders re-rank the file she is looking at, not this one.
        if tech.get("at_52w_low") and not tech.get("above_ema200"):
            found.append(
                {
                    "reason": "At a 52-week low and below its 200-day average",
                    "points": cfg["new_low_under_ema200"],
                }
            )
    elif in_downtrend(row, cfg):
        off_high = tech.get("pct_below_52w_high")
        detail = f", {off_high:.0%} below its 52-week high" if off_high else ""
        found.append(
            {
                "reason": f"Still in a confirmed downtrend{detail} — 50-day under the 200-day, nothing turning yet",
                "points": cfg["downtrend_confirmed"],
            }
        )
    elif near_low:
        found.append(
            {
                "reason": (
                    "Sitting on its 52-week low and below its 200-day average"
                    if above_low is None or above_low < 0.005
                    else f"Within {above_low:.0%} of its 52-week low and below its 200-day average"
                ),
                "points": cfg["new_low_under_ema200"],
            }
        )

    catalyst = row.get("catalyst")
    if catalyst and catalyst.get("verdict") == "structural":
        found.append(
            {
                "reason": f"Selloff looks structural, not temporary: {catalyst.get('headline', '')}".strip(": "),
                "points": cfg["catalyst_structural"],
            }
        )

    return found


def badges(row: dict, config: dict) -> list[dict]:
    """Mom's checklist, item by item. `passed` is None when we couldn't tell."""
    tech, fund, cfg = row["tech"], row.get("fund"), config["scoring"]
    gates = config["gates"]

    def growth(key: str, threshold: float) -> bool | None:
        if not fund or fund.get(key) is None:
            return None
        return fund[key] > threshold

    def margin_improving() -> bool | None:
        if not fund:
            return None
        changes = [
            fund.get("gross_margin_change"),
            fund.get("operating_margin_change"),
        ]
        present = [c for c in changes if c is not None]
        return any(c > 0 for c in present) if present else None

    rsi = tech.get("rsi14")
    wr = tech.get("williams_r14")

    def near_support() -> bool | None:
        """Within 5% of the 60-day shelf, or sitting on the 52-week low.

        The 60-day low is the reference that matters for a 35-day trade -- a
        stock can be 60% above its 52-week low and still be resting right on the
        support that decides whether this put expires worthless.
        """
        close, support = tech.get("close"), tech.get("support_60d")
        if close is None or not support:
            return None
        return (close - support) / support <= 0.05 or bool(tech.get("at_52w_low"))

    return [
        {"label": "RSI below 35", "passed": None if rsi is None else rsi < 35},
        {"label": "Williams %R below -80", "passed": None if wr is None else wr < cfg["williams_r_oversold"]},
        {"label": "Sales up >10% YoY", "passed": growth("revenue_yoy", cfg["rev_yoy_target"])},
        {"label": "Sales up QoQ", "passed": growth("revenue_qoq", 0.0)},
        {"label": "Margins improving", "passed": margin_improving()},
        {"label": "Above 9-day EMA", "passed": tech.get("above_ema9")},
        {"label": "Above 20-day EMA", "passed": tech.get("above_ema20")},
        {"label": "MACD crossing up", "passed": bool(tech.get("macd_cross_up"))},
        {"label": "Volume expanding on green", "passed": bool(tech.get("up_day_volume_expansion"))},
        {"label": "Near support", "passed": near_support()},
        {"label": "Volume over 500k", "passed": tech.get("avg_volume_30d", 0) >= gates["min_avg_volume_30d"]},
    ]


def check_gates(row: dict, config: dict, profile: str = "put") -> list[str]:
    """Empty list means tradeable. Anything in it means dropped, with the reason."""
    tech, gates = row["tech"], config["gates"]
    failures = []

    if tech.get("close", 0) < gates["min_price"]:
        failures.append(f"price ${tech.get('close', 0):.2f} below ${gates['min_price']:.0f}")
    if tech.get("avg_volume_30d", 0) < gates["min_avg_volume_30d"]:
        failures.append(f"30d volume {tech.get('avg_volume_30d', 0):,.0f} too thin")

    rsi = tech.get("rsi14")
    if rsi is None:
        failures.append("no RSI")
    else:
        if rsi >= gates["max_rsi"]:
            failures.append(f"RSI {rsi:.1f} -- already overbought")
        stretched = _recent(tech, "rsi_min_recent", "rsi14")
        if stretched >= gates["max_rsi_recent"]:
            failures.append(f"RSI never dropped below {stretched:.1f} -- never oversold")

    market_cap = row.get("market_cap")
    if market_cap is not None and market_cap < gates["min_market_cap"]:
        failures.append(f"market cap ${market_cap / 1e9:.2f}B below ${gates['min_market_cap'] / 1e9:.0f}B")
    # The one gate that asks about a contract rather than about a company. She
    # can buy a stock there is no put worth selling against, so the other
    # rankings drop this and keep every other gate: price, volume, market cap
    # and the two RSI bounds describe the name, not the trade.
    if profile == "put" and row.get("trade") is None:
        failures.append("no put in the target delta and liquidity range")

    return failures


def score(row: dict, config: dict, profile: str = "put") -> dict:
    """Composite 0-100 for one candidate, with the breakdown that produced it.

    `profile` picks the question being asked -- income, weeks, months -- and
    with it a weight block naming the components that answer it. The same 62
    names go through all of them, and a name can rank first on one and last on
    another for a reason: high implied volatility pays a seller and costs a
    buyer. That disagreement is the useful part.
    """
    weights, cfg = config[PROFILES[profile]], config["scoring"]

    components = {}
    for name in weights:
        value = _COMPONENTS[name](row, cfg)
        components[name] = {
            "raw": round(value, 4),
            "points": round(value * weights[name], 2),
            "max": weights[name],
        }

    applied = penalties(row, penalty_config(config, profile), profile=profile)
    gross = sum(c["points"] for c in components.values())
    total = max(0.0, gross - sum(p["points"] for p in applied))

    return {
        "score": round(total, 1),
        "score_before_penalties": round(gross, 1),
        "components": components,
        "penalties": applied,
        "badges": badges(row, config),
    }
