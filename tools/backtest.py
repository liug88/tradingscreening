"""What would the screen have picked, and what happened next.

The honest version of a backtest, which means being clear about what is and is
not recoverable.

WHAT IS EXACT
-------------
Everything derived from price history. `technicals.compute()` reads only the
tail of the frame it is given, so truncating a symbol's history to a past date
produces exactly the indicators the screen would have computed that morning --
no look-ahead, same code path, same Wilder smoothing. Components are scored by
importing score.py's own functions rather than re-implementing them, for the
same reason site/score.js is held to a parity test: a lookalike proves nothing.

WHAT IS NOT RECOVERABLE
-----------------------
Option quotes. Cboe's endpoint serves the current chain and nothing else, and
there is no free historical source. That takes trade_quality (10 points) and
the "is there a fillable put" gate outright, and most of strike_safety.

Fundamentals are current-only too, and not for want of asking. Yahoo's
fundamentals-timeseries endpoint takes a `period1`, so this looked recoverable;
it is not. Asked for ten years it returns the same five quarters it returns for
three, on every symbol tried, and the annual series stops at four years. The
quoteSummary module gives four quarters. There is no free history here.

Using today's revenue growth to rank a pick from a year ago is look-ahead bias,
so every fundamental component is dropped rather than faked. That costs the put
run sales_growth and margin_trend (25 points), the buy run revenue_expanding
and margin_trend (30), and the long run those same two at the weights that make
them half its model (50).

THREE RUNS, NOT ONE
-------------------
`--profile put` is the original and the default: nothing about it moved. The
other two rank the same pool by what she would want to own.

                    reconstructs        held      what is missing
  put               49 of 100         35 days     option quotes, fundamentals
  buy               70 of 100         35 days     revenue, margins
  long              50 of 100        180 days     revenue, margins

The long number is the one to keep in view. Half of that ranking is revenue and
margins, and this run cannot see either, so it measures the chart half alone --
which is the half her mother asked about, and still only half. Read it as "did
the chart pick better names than the pool", never as "the long list returns X%".

TWO VARIANTS, BECAUSE THE FIRST ONE TIED
----------------------------------------
`technical` scores oversold (20) and bounce (15) alone. Both cap at 1.0, so
names pile up on exactly 100 -- fifteen of them on one date tested -- and a
stable sort then ranks the tied block alphabetically. That is not a ranking,
and any result read off it is really a result about the alphabet.

`enriched` adds back the parts of two more components that price history can
still answer, which breaks the ties on the thesis instead:

  premium_richness  half. It is half IV/HV and half "where does IV sit in this
                    stock's own range". The implied half is gone for good, but
                    the second half is a percentile of a volatility series, and
                    realised volatility gives one. hv_percentile mirrors
                    run.py's iv_percentile exactly, over the trailing year.
                    Worth 10 of its 20 points.
  strike_safety     the support half. `_ramp(strike, support*1.05,
                    support*0.95)` needs only the strike and the 60-day low.
                    The cushion half needs the premium, so it stays out.
                    Worth 4 of its 10.

49 of 100 points, renormalised. Still not the shipped score. Read every number
here as "did this thesis pick better names than the pool it chose from", never
as "the app returns X%".

The two variants are reported side by side on purpose. Tilting toward rich
premium means tilting toward high volatility, and that is a real bet with a
real failure mode -- if the enriched list does worse in a falling tape, that is
the finding, not a bug.

THE STRIKE
----------
A 20-delta put's strike is not recoverable either, so it is estimated by
inverting Black-Scholes at the configured target delta, using realised
volatility in place of implied. Implied normally runs above realised, so the
real strike would have sat further below spot than this one does. The estimate
is therefore harder to survive than the real trade, which is the direction an
honest test should err in.

BIASES THAT REMAIN
------------------
The universe is the current CBOE weeklys list, so anything delisted or dropped
from it since is missing -- survivorship, and it flatters the results. The
market-cap gate is skipped for the same reason (only today's cap is available).
Every window is priced at the close, and assignment is judged at expiry.

Run:
    python -m tools.backtest                    # 3, 6 and 12 months back
    python -m tools.backtest --monthly          # every month there is data for
    python -m tools.backtest --profile long --monthly
    python -m tools.backtest --months 3 6 12 24
    python -m tools.backtest --refresh          # re-download price history
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from screener import score, technicals
from screener.cache import JsonCache
from screener.prices import fetch_histories
from screener.yahoo import YahooSession

ROOT = Path(__file__).resolve().parent.parent
HISTORY_CACHE = ROOT / "cache" / "backtest_prices.pkl"
BENCHMARK = "SPY"

# Enough history behind an entry for the 200-day EMA and the 52-week window to
# mean anything. Below this the indicators are still warming up.
MIN_BARS = 250

# run.py refuses to call an IV percentile before this many observations; the
# same floor applies to the realised-volatility stand-in.
MIN_HV_OBSERVATIONS = 60

# N(z) = 0.20  ->  z = -0.8416. Used to invert Black-Scholes for the strike.
Z_AT = {0.10: -1.2816, 0.15: -1.0364, 0.20: -0.8416, 0.25: -0.6745, 0.30: -0.5244}


# ---- the two scoring variants -------------------------------------------


def _premium_proxy(row: dict, cfg: dict) -> float:
    """The half of premium_richness that a price series can still answer.

    score._premium_richness is 0.5 * IV/HV + 0.5 * percentile. Implied vol is
    unrecoverable, so only the percentile half is scored -- and it is carried at
    half the component's weight to match.
    """
    percentile = row.get("hv_percentile")
    return 0.0 if percentile is None else percentile / 100.0


def _strike_proxy(row: dict, cfg: dict) -> float:
    """The support half of strike_safety, which needs no option premium."""
    support = row["tech"].get("support_60d")
    strike = row.get("est_strike")
    if not support or not strike:
        return 0.0
    return score._ramp(strike, support * 1.05, support * 0.95)


# What survives for a past date, per ranking:
#   profile -> which weight block the shares are read against
#   parts   -> (component, scorer, fraction of its shipped weight recoverable)
#
# Every scorer here is score.py's own function wherever the component is fully
# recoverable. The two proxies are named as proxies and carried at a fraction
# of their weight, so nothing in this table can quietly become a lookalike.
VARIANTS = {
    "technical": ("put", (
        ("oversold", score._COMPONENTS["oversold"], 1.0),
        ("bounce", score._COMPONENTS["bounce"], 1.0),
    )),
    "enriched": ("put", (
        ("oversold", score._COMPONENTS["oversold"], 1.0),
        ("bounce", score._COMPONENTS["bounce"], 1.0),
        ("premium_richness", _premium_proxy, 0.5),
        ("strike_safety", _strike_proxy, 0.4),
    )),
    # Everything the buy ranking asks that is not a fundamental. Entry timing
    # folds oversold and bounce, so this is the whole price-history half.
    "buy": ("buy", (
        ("entry_timing", score._COMPONENTS["entry_timing"], 1.0),
        ("trend_structure", score._COMPONENTS["trend_structure"], 1.0),
        ("room_to_run", score._COMPONENTS["room_to_run"], 1.0),
    )),
    # The long ranking scores entry timing at zero, so it is left out rather
    # than carried as a term that cannot move anything.
    "long": ("long", (
        ("trend_structure", score._COMPONENTS["trend_structure"], 1.0),
        ("room_to_run", score._COMPONENTS["room_to_run"], 1.0),
    )),
}

# Which variants a run reports side by side. The put run keeps both of its own,
# because the whole point of that pair is the comparison.
PROFILE_VARIANTS = {"put": ("technical", "enriched"), "buy": ("buy",), "long": ("long",)}


def variant_score(row: dict, config: dict, variant: str) -> float:
    """The variant's components, renormalised to 100.

    Rescaled the same way normalise() rescales the sliders in the browser, so a
    score here is comparable to a shipped one even though it is built from
    fewer parts.
    """
    profile, parts = VARIANTS[variant]
    weights, cfg = config[score.PROFILES[profile]], config["scoring"]
    total = sum(weights[name] * share for name, _, share in parts)
    points = 0.0
    for name, fn, share in parts:
        points += fn(row, cfg) * (weights[name] * share * 100.0 / total)
    return round(points, 1)


def recoverable_points(config: dict, variant: str) -> float:
    """How many of the shipped 100 points this variant actually reconstructs."""
    profile, parts = VARIANTS[variant]
    weights = config[score.PROFILES[profile]]
    return sum(weights[name] * share for name, _, share in parts)


def horizon(config: dict, profile: str) -> int:
    """How long the position is held, in days.

    The put's window is the option's own life, so it is read from `target_dte`
    rather than written down a second time under another name.
    """
    if profile == "put":
        return config["option"]["target_dte"]
    return config["horizon_days"][profile]


# ---- price history ------------------------------------------------------


def load_histories(symbols: list[str], refresh: bool = False) -> dict[str, pd.DataFrame]:
    """Five years of daily bars for the whole universe, cached on disk.

    One fetch serves every as-of date, and the span has to cover the longest
    forward window rather than the shortest. 3y minus the 250-bar warmup minus
    a 180-day window leaves about 18 monthly entries for the long run, which is
    too few to read; 5y restores it to roughly 40.
    """
    if HISTORY_CACHE.exists() and not refresh:
        with HISTORY_CACHE.open("rb") as fh:
            histories = pickle.load(fh)
        print(f"price history: {len(histories)} symbols from cache")
        return histories

    print(f"fetching 5y of daily bars for {len(symbols)} symbols (a few minutes)...")
    session = YahooSession()
    histories = fetch_histories(symbols, session, range_="5y")
    HISTORY_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_CACHE.open("wb") as fh:
        pickle.dump(histories, fh)
    print(f"price history: {len(histories)} symbols fetched and cached")
    return histories


# ---- the screen, as of a past date --------------------------------------


def as_of_frame(frame: pd.DataFrame, when: pd.Timestamp) -> pd.DataFrame | None:
    """The history a run on `when` would have seen, and nothing after it."""
    cut = frame.loc[frame.index <= when]
    return cut if len(cut) >= MIN_BARS else None


def hv_percentile(cut: pd.DataFrame) -> float | None:
    """Where this stock's realised vol sits in its own trailing year.

    The same arithmetic as run.py's iv_percentile, on the series it can still
    see: 100 * (observations at or below today) / observations.
    """
    hv = technicals.historical_volatility(cut["close"])
    window = hv.iloc[-technicals.TRADING_DAYS:].dropna()
    if len(window) < MIN_HV_OBSERVATIONS:
        return None
    today = float(hv.iloc[-1])
    if not np.isfinite(today):
        return None
    return round(100.0 * float((window <= today).sum()) / len(window), 1)


def passes_gates(tech: dict, config: dict) -> bool:
    """The gates that price history can answer.

    Market cap and the fillable-put gate are skipped -- neither is recoverable
    for a past date. Both would only ever remove names, so the pool tested here
    is a superset of what the real screen would have ranked.
    """
    gates = config["gates"]
    if tech.get("close", 0) < gates["min_price"]:
        return False
    if tech.get("avg_volume_30d", 0) < gates["min_avg_volume_30d"]:
        return False
    rsi = tech.get("rsi14")
    if rsi is None or rsi >= gates["max_rsi"]:
        return False
    stretched = tech.get("rsi_min_recent")
    stretched = tech.get("rsi14") if stretched is None else stretched
    return stretched < gates["max_rsi_recent"]


# ---- what happened next -------------------------------------------------


def estimated_strike(spot: float, sigma: float, days: int, delta: float) -> float:
    """The strike a put of `delta` would have carried, from Black-Scholes.

    Inverted at r = 0: |delta_put| = N(-d1), so -d1 = z and

        K = S * exp(sigma^2 T / 2 - d1 * sigma * sqrt(T))

    `sigma` is realised volatility standing in for implied. Implied is normally
    the larger of the two, so this strike sits closer to the money than the real
    one would -- a stricter test, not a kinder one.
    """
    t = days / 365.0
    d1 = -Z_AT[delta]
    return spot * math.exp((sigma * sigma * t) / 2.0 - d1 * sigma * math.sqrt(t))


def outcome(frame: pd.DataFrame, entry: pd.Timestamp, spot: float,
            strike: float, days: int) -> dict | None:
    """How the trade the screen implied would have turned out.

    Assignment is judged at expiry, which is where a cash-secured put is
    actually settled. Early assignment happens, but only when the put is deep in
    the money, and that case shows up here as a large loss anyway.

    `return_since` answers a different question from the trade -- what the stock
    did between the entry and the last bar on file, for "how have these names
    done since".
    """
    ahead = frame.loc[(frame.index > entry) & (frame.index <= entry + timedelta(days=days))]
    if ahead.empty:
        return None

    settle = float(ahead["close"].iloc[-1])
    trough = float(ahead["low"].min())
    latest = float(frame["close"].iloc[-1])

    return {
        "spot": spot,
        "strike": strike,
        "pct_below": (spot - strike) / spot,
        "settle": settle,
        "assigned": settle < strike,
        "touched": trough < strike,          # went through the strike at some point
        "stock_return": settle / spot - 1.0,
        "max_drawdown": trough / spot - 1.0,
        "return_since": latest / spot - 1.0,
        "bars_ahead": len(ahead),
    }


# ---- one as-of date -----------------------------------------------------


def top_n(pool: list[dict], variant: str, n: int, drop_downtrends: bool = False) -> list[dict]:
    """The variant's leaders.

    The secondary key matters. Scores collapse to one decimal, so ties happen;
    sorting on score alone leaves a stable sort to rank the tied block in
    whatever order the universe came in, which is alphabetical. Depth of the
    washout breaks it instead -- part of the thesis, not the alphabet.

    `drop_downtrends` is the other half of the A/B her mother prompted: the same
    ranking with the falling knives removed first, so the gate can be measured
    rather than argued about.
    """
    if drop_downtrends:
        pool = [row for row in pool if not row["downtrend"]]

    def key(row: dict) -> tuple[float, float]:
        rsi = row["rsi_recent"]
        return (-row["scores"][variant], 99.0 if rsi is None else rsi)

    return [dict(row, rank=i) for i, row in enumerate(sorted(pool, key=key)[:n], 1)]


def run_one(histories: dict[str, pd.DataFrame], when: pd.Timestamp, config: dict,
            profile: str = "put") -> dict:
    """Screen as of `when`, then measure every gated-in name forward."""
    days = horizon(config, profile)
    delta = config["option"]["target_delta"]
    pool = []

    for symbol, frame in histories.items():
        if symbol == BENCHMARK:
            continue
        cut = as_of_frame(frame, when)
        if cut is None:
            continue
        tech = technicals.compute(cut)
        if not tech or not passes_gates(tech, config):
            continue

        sigma, spot = tech.get("hv20"), tech.get("close")
        if not sigma or not spot or sigma <= 0:
            continue
        strike = estimated_strike(spot, sigma, days, delta)

        result = outcome(frame, cut.index[-1], spot, strike, days)
        if result is None:
            continue

        row = {"tech": tech, "hv_percentile": hv_percentile(cut), "est_strike": strike}
        pool.append({
            "symbol": symbol,
            "scores": {name: variant_score(row, config, name) for name in VARIANTS},
            # The phase-0 rule, evaluated as of that morning like everything
            # else here, so the gate can be applied after the fact instead of
            # needing a second pass over the price history.
            "downtrend": score.in_downtrend(row, config["penalties"]),
            "entry_date": str(cut.index[-1])[:10],
            "rsi_recent": tech.get("rsi_min_recent"),
            "hv20": sigma,
            "hv_percentile": row["hv_percentile"],
            **result,
        })

    n = config["funnel"]["final"]
    variants = PROFILE_VARIANTS[profile]
    return {
        "as_of": str(when)[:10],
        "profile": profile,
        "pool": pool,
        "picks": {name: top_n(pool, name, n) for name in variants},
        # Same ranking, falling knives removed before the cut.
        "gated": {name: top_n(pool, name, n, drop_downtrends=True) for name in variants},
    }


def benchmark_return(histories: dict, when: pd.Timestamp, days: int) -> float | None:
    frame = histories.get(BENCHMARK)
    if frame is None:
        return None
    before = frame.loc[frame.index <= when]
    ahead = frame.loc[(frame.index > when) & (frame.index <= when + timedelta(days=days))]
    if before.empty or ahead.empty:
        return None
    return float(ahead["close"].iloc[-1]) / float(before["close"].iloc[-1]) - 1.0


def entry_dates(histories: dict, config: dict, monthly: bool,
                months: list[int], profile: str = "put") -> list[pd.Timestamp]:
    """Every as-of date to test.

    Monthly walks the whole cached span: the first entry is far enough in that
    the indicators have warmed up, the last far enough back that the forward
    window has closed.
    """
    if not monthly:
        today = pd.Timestamp(date.today())
        return [today - pd.DateOffset(months=m) for m in sorted(months, reverse=True)]

    frame = histories.get(BENCHMARK)
    if frame is None:
        frame = next(iter(histories.values()))
    index = frame.index
    days = horizon(config, profile)
    first = index[MIN_BARS - 1]
    last = index[-1] - timedelta(days=days + 3)

    dates = []
    cursor = pd.Timestamp(first.year, first.month, 1) + pd.DateOffset(months=1)
    while cursor <= last:
        dates.append(cursor)
        cursor += pd.DateOffset(months=1)
    return dates


# ---- reporting ----------------------------------------------------------


def summarise(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    returns = [r["stock_return"] for r in rows]
    return {
        "n": len(rows),
        "assigned": sum(r["assigned"] for r in rows),
        "assigned_pct": sum(r["assigned"] for r in rows) / len(rows),
        "touched_pct": sum(r["touched"] for r in rows) / len(rows),
        "avg_return": statistics.fmean(returns),
        "median_return": statistics.median(returns),
        "avg_drawdown": statistics.fmean(r["max_drawdown"] for r in rows),
        "avg_pct_below": statistics.fmean(r["pct_below"] for r in rows),
        "avg_return_since": statistics.fmean(r["return_since"] for r in rows),
        "worst": min(rows, key=lambda r: r["stock_return"]),
        # How often it was wrong, and how wrong. An average return hides all of
        # this: +12% built from one +80% and nine -3% is not a screen that
        # works, and the mean cannot tell those two apart.
        "best_return": max(returns),
        "worst_return": min(returns),
        "ended_down_pct": sum(r < 0 for r in returns) / len(rows),
        "fell_10_pct": sum(r["max_drawdown"] <= -0.10 for r in rows) / len(rows),
        "fell_20_pct": sum(r["max_drawdown"] <= -0.20 for r in rows) / len(rows),
        "worst_drawdown": min(r["max_drawdown"] for r in rows),
        "downtrends": sum(r.get("downtrend", False) for r in rows),
    }


def pct(x: float | None, dp: int = 1) -> str:
    return "-" if x is None else f"{x * 100:.{dp}f}%"


COLUMNS = "{:<22}{:>13}{:>14}{:>15}"
MEASURES = (
    ("touched the strike", "touched_pct", 0),
    ("average return", "avg_return", 1),
    ("median return", "median_return", 1),
    ("average drawdown", "avg_drawdown", 1),
    ("strike below spot", "avg_pct_below", 1),
)


def three_columns(rich: dict, tech_: dict, all_: dict, measures) -> None:
    """Enriched ten, technical ten, and the pool they were both drawn from."""
    print(f"\n{'':<22}{'enriched 10':>13}{'technical 10':>14}{'all gated in':>15}")
    print("-" * 64)
    print(COLUMNS.format(
        "assigned",
        f"{rich['assigned']}/{rich['n']} ({pct(rich['assigned_pct'], 0)})",
        f"{tech_['assigned']}/{tech_['n']} ({pct(tech_['assigned_pct'], 0)})",
        f"{all_['assigned']}/{all_['n']} ({pct(all_['assigned_pct'], 0)})"))
    for label, key, dp in measures:
        print(COLUMNS.format(label, pct(rich[key], dp), pct(tech_[key], dp),
                             pct(all_[key], dp)))


def report_window(result: dict, config: dict, spy: float | None) -> None:
    """One as-of date in full: the enriched ten, then all three columns."""
    pool, picks = result["pool"], result["picks"]
    days = horizon(config, result.get("profile", "put"))
    rich = summarise(picks["enriched"])
    tech_ = summarise(picks["technical"])
    all_ = summarise(pool)

    print()
    print("=" * 82)
    print(f"AS OF {result['as_of']}   ({all_['n']} names cleared the gates, "
          f"{days}-day forward window)")
    print("=" * 82)

    print(f"\n{'#':>2}  {'sym':<6} {'score':>5} {'hv%':>4}  {'spot':>8} {'strike':>8} "
          f"{'below':>6}  {'return':>7} {'worst':>7} {'since':>8}  outcome")
    print("-" * 82)
    for r in picks["enriched"]:
        hvp = "-" if r["hv_percentile"] is None else f"{r['hv_percentile']:.0f}"
        print(f"{r['rank']:>2}  {r['symbol']:<6} {r['scores']['enriched']:>5.1f} {hvp:>4}  "
              f"{r['spot']:>8.2f} {r['strike']:>8.2f} {pct(r['pct_below'], 1):>6}  "
              f"{pct(r['stock_return']):>7} {pct(r['max_drawdown']):>7} "
              f"{pct(r['return_since']):>8}  "
              f"{'ASSIGNED' if r['assigned'] else 'kept premium'}")

    three_columns(rich, tech_, all_,
                  MEASURES + (("return to today", "avg_return_since", 1),))
    if spy is not None:
        print(COLUMNS.format(f"{BENCHMARK} over the window", pct(spy), "", ""))

    overlap = len({r["symbol"] for r in picks["enriched"]}
                  & {r["symbol"] for r in picks["technical"]})
    print(f"\nthe two tens share {overlap} of 10 names")
    for name, summary in (("enriched", rich), ("technical", tech_)):
        worst = summary["worst"]
        print(f"worst of the {name:<10} {worst['symbol']:<6} {pct(worst['stock_return'])} "
              f"(low {pct(worst['max_drawdown'])})")


def report_rolling(results: list[dict], config: dict, benchmarks: list) -> None:
    """Every window at once. The only sample here big enough to read."""
    days = horizon(config, "put")
    print()
    print("=" * 82)
    print(f"ROLLING: {len(results)} monthly entries, {days}-day forward window")
    print("=" * 82)

    print(f"\n{'as of':<12}{'pool':>6}{'enriched':>22}{'technical':>22}{'SPY':>9}")
    print(f"{'':<12}{'':>6}{'assigned':>11}{'return':>11}"
          f"{'assigned':>11}{'return':>11}{'':>9}")
    print("-" * 82)

    for result, spy in zip(results, benchmarks):
        rich = summarise(result["picks"]["enriched"])
        tech_ = summarise(result["picks"]["technical"])
        if not rich["n"]:
            continue
        print(f"{result['as_of']:<12}{len(result['pool']):>6}"
              f"{rich['assigned']:>8}/{rich['n']:<3}{pct(rich['avg_return']):>11}"
              f"{tech_['assigned']:>8}/{tech_['n']:<3}{pct(tech_['avg_return']):>11}"
              f"{pct(spy, 1):>9}")

    print()
    print("-" * 82)
    print("ALL TRADES POOLED")
    print("-" * 82)

    rich = summarise([r for res in results for r in res["picks"]["enriched"]])
    tech_ = summarise([r for res in results for r in res["picks"]["technical"]])
    all_ = summarise([r for res in results for r in res["pool"]])
    spies = [s for s in benchmarks if s is not None]

    print(COLUMNS.format("trades", str(rich["n"]), str(tech_["n"]), str(all_["n"])))
    three_columns(rich, tech_, all_, MEASURES)
    if spies:
        print(COLUMNS.format(f"{BENCHMARK} per window",
                             pct(statistics.fmean(spies)), "", ""))

    beat = sum(1 for res in results
               if summarise(res["picks"]["enriched"]).get("assigned_pct", 1.0)
               < summarise(res["pool"]).get("assigned_pct", 0.0))
    print(f"\nwindows where the enriched ten was assigned less often than its pool: "
          f"{beat} of {len(results)}")


TWO = "{:<34}{:>12}{:>14}"

SPREAD = (
    ("average return", "avg_return", 1),
    ("median return", "median_return", 1),
    ("best single name", "best_return", 0),
    ("worst single name", "worst_return", 0),
)
WRONGNESS = (
    ("ended the window lower", "ended_down_pct", 0),
    ("fell 10% inside the window", "fell_10_pct", 0),
    ("fell 20% inside the window", "fell_20_pct", 0),
    ("worst drawdown any name reached", "worst_drawdown", 0),
)


def report_accuracy(results: list[dict], config: dict, benchmarks: list,
                    profile: str) -> None:
    """Not "what did it return" but "how often was it wrong, and how wrong".

    Three questions, in the order they decide whether the ranking is worth
    trusting: does it beat the pool it chose from and the index, how wide is the
    spread behind the average, and does the falling-knife gate her mother
    prompted actually help.
    """
    # The last one, which is the richest: the put run reports `technical` beside
    # `enriched` to show what the tie-breaking is worth, but `enriched` is the
    # ranking, and an accuracy report on the weaker of the two would understate
    # the thing being judged.
    variant = PROFILE_VARIANTS[profile][-1]
    days = horizon(config, profile)
    picked = [r for res in results for r in res["picks"][variant]]
    pool = [r for res in results for r in res["pool"]]
    gated = [r for res in results for r in res["gated"][variant]]
    spies = [s for s in benchmarks if s is not None]
    if not picked:
        print("\nno positions -- the cached history is too short for this horizon")
        return

    print()
    print("=" * 60)
    print(f"{profile.upper()}: {len(results)} monthly entries, {days}-day hold, "
          f"{len(picked)} positions")
    print(f"reconstructs {recoverable_points(config, variant):.0f} of the 100 "
          f"shipped points")
    print("=" * 60)

    ten, all_ = summarise(picked), summarise(pool)
    print(f"\n{'':<34}{'the ten':>12}{'all gated in':>14}")
    print("-" * 60)
    for label, key, dp in SPREAD:
        print(TWO.format(label, pct(ten[key], dp), pct(all_[key], dp)))
    if spies:
        print(TWO.format(f"{BENCHMARK} over the same windows",
                         pct(statistics.fmean(spies)), ""))

    print("\nHOW OFTEN IT WAS WRONG")
    for label, key, dp in WRONGNESS:
        print(TWO.format(label, pct(ten[key], dp), pct(all_[key], dp)))

    beat = sum(1 for res in results
               if summarise(res["picks"][variant]).get("avg_return", -9)
               > (res["benchmark_return"] or -9))
    print(f"\nwindows where the ten beat {BENCHMARK}: {beat} of {len(results)}")

    report_knives(results, pool, ten, summarise(gated), variant)


def report_knives(results: list[dict], pool: list[dict], ten: dict,
                  with_gate: dict, variant: str) -> None:
    """Did the gate her mother prompted actually help.

    The comparison that carries the answer is not gated-ten against ten, which
    on every profile tested comes back identical -- `trend_structure` and
    `room_to_run` already rank a falling knife so low that none reaches a top
    ten, so there is nothing for a gate to remove. It is knives against the rest
    of the pool, which is where the difference the gate is meant to catch either
    exists or does not.
    """
    knives = [r for r in pool if r["downtrend"]]
    rest = [r for r in pool if not r["downtrend"]]
    print("\nTHE FALLING KNIVES  (below the 200-day, 50 under it, nothing turning)")
    if not knives or not rest:
        print("  none in the pool")
        return

    a, b_ = summarise(knives), summarise(rest)
    print(f"{'':<34}{'the knives':>12}{'everyone else':>14}")
    print("-" * 60)
    print(TWO.format("share of the pool",
                     pct(len(knives) / len(pool), 0), pct(len(rest) / len(pool), 0)))
    for label, key, dp in SPREAD[:2] + WRONGNESS[:3]:
        print(TWO.format(label, pct(a[key], dp), pct(b_[key], dp)))

    ranked = sorted(pool, key=lambda r: -r["scores"][variant])
    places = [i for i, r in enumerate(ranked, 1) if r["downtrend"]]
    print(f"\nbest-ranked knife sits at #{places[0]} of {len(ranked)} on this "
          f"ranking, so the gate\nremoved {ten['n'] - with_gate['n']} of "
          f"{ten['n']} positions from the ten: the components already bury them.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--profile", choices=sorted(PROFILE_VARIANTS), default="put",
                        help="which ranking to test: put (default), buy or long")
    parser.add_argument("--months", type=int, nargs="+", default=[3, 6, 12],
                        help="how many months back to place each entry")
    parser.add_argument("--monthly", action="store_true",
                        help="roll an entry every month across all cached history")
    parser.add_argument("--refresh", action="store_true",
                        help="re-download price history instead of using the cache")
    parser.add_argument("--json", type=Path, help="also write the full result here")
    args = parser.parse_args(argv)

    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    symbols = JsonCache(ROOT / "cache" / "universe.json").get("cboe_weekly_equities")
    if not symbols:
        print("no cached universe -- run the screener once first", file=sys.stderr)
        return 1

    histories = load_histories(sorted(set(symbols) | {BENCHMARK}), refresh=args.refresh)
    span = histories[BENCHMARK].index if BENCHMARK in histories else next(iter(histories.values())).index
    print(f"  bars on file: {span[0].date()} to {span[-1].date()}")
    for name in PROFILE_VARIANTS[args.profile]:
        print(f"  {name:<10} reconstructs {recoverable_points(config, name):.0f} "
              f"of the 100 shipped points")

    days = horizon(config, args.profile)
    dates = entry_dates(histories, config, args.monthly, args.months, args.profile)
    print(f"  {len(dates)} entry dates at a {days}-day horizon")
    results, benchmarks = [], []
    for when in dates:
        result = run_one(histories, when, config, args.profile)
        spy = benchmark_return(histories, when, days)
        result["benchmark_return"] = spy
        results.append(result)
        benchmarks.append(spy)
        if not args.monthly:
            report_window(result, config, spy)

    if args.monthly:
        if args.profile == "put":
            report_rolling(results, config, benchmarks)
        report_accuracy(results, config, benchmarks, args.profile)

    if args.json:
        payload = results
        if args.monthly:
            payload = [{k: v for k, v in r.items() if k != "pool"} for r in results]
        args.json.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
        print(f"\nfull result written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
