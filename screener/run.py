"""The daily run.

One pass, narrowing at every stage: every symbol with weekly options gets a
price history, the oversold ones get fundamentals, the best of those get an
option chain, and only the final ten cost anything at the AI layer.

The narrowing is what keeps this inside a free GitHub Actions run -- and it's
ordered cheapest-first on purpose, so the expensive calls only ever touch names
that are still in contention.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from . import fundamentals, options, prices, score, technicals, universe
from .cache import JsonCache
from .yahoo import YahooSession

log = logging.getLogger("screener")

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "cache"
SITE_DATA = ROOT / "site" / "data"
HISTORY_DIR = ROOT / "history"

MIN_IV_OBSERVATIONS = 20  # below this a percentile is noise, not information
MAX_IV_HISTORY = 500

# What travels with every published name. A superset of what the page draws,
# because the page also re-scores: every key score.py reads is here under the
# name score.py reads it by, so the browser can recompute a rank from the
# published file alone.
#
# That is an invariant, not a convenience, and it fails quietly. A key the
# scorer reads but this tuple omits is simply absent in the browser, which
# reads absent as "an older file" and falls back -- so the list she is shown
# and the list a slider produces would come from two different models, with
# nothing on screen to say so. `tests/test_publish_contract.py` pins it.
PUBLISHED_TECHNICALS = (
    "close", "change_5d", "rsi14", "rsi_min_recent", "williams_r14",
    "williams_r_min_recent",
    # The rest of the oversold composite. `stoch_k` is the odd one out: the
    # scorer never reads it, the row prints it, and it is published so the
    # page can say in figures that %K and %R are one reading.
    "stoch_k", "stoch_d", "stoch_d_min_recent", "stoch_cross_up",
    "mfi14", "mfi_min_recent", "bb_percent_b", "bb_percent_b_min_recent",
    "macd", "macd_signal", "macd_cross_up", "macd_below_zero",
    "ema9", "ema20", "ema50", "ema200",
    "above_ema9", "above_ema20", "above_ema200", "golden_cross",
    "atr14", "hv20", "avg_volume_30d", "volume_vs_20d",
    "up_day_volume_expansion",
    "high_52w", "low_52w", "at_52w_low", "pct_above_52w_low",
    "pct_below_52w_high",
    "support_60d", "last_date",
)


def iv_percentile(history: list[float], current: float | None) -> float | None:
    """Where today's IV sits in this stock's own range.

    "Rich" only means something relative to the stock itself -- IV 30 is
    expensive for a utility and cheap for a biotech. Returns None until there's
    enough history to say anything honest.
    """
    if current is None or len(history) < MIN_IV_OBSERVATIONS:
        return None
    return round(100.0 * sum(1 for value in history if value <= current) / len(history), 1)


def _load_config(path: Path) -> dict:
    with open(path) as handle:
        config = yaml.safe_load(handle)
    total = sum(config["weights"].values())
    if total != 100:
        raise ValueError(f"config weights sum to {total}, not 100")
    return config


def _technicals_stage(symbols: list[str], session: YahooSession, config: dict) -> list[dict]:
    """Price history -> indicators -> the cheap gates. The widest stage."""
    histories = prices.fetch_histories(symbols, session)
    gates = config["gates"]

    rows = []
    for symbol, frame in histories.items():
        tech = technicals.compute(frame)
        if not tech:
            continue
        # These gates need no extra fetch, so they run here rather than at the
        # end -- dropping a name now saves it from every stage below.
        if (
            tech["close"] < gates["min_price"]
            or tech["avg_volume_30d"] < gates["min_avg_volume_30d"]
            or tech.get("rsi14") is None
            or tech["rsi14"] >= gates["max_rsi"]
            or (tech.get("rsi_min_recent") or tech["rsi14"]) >= gates["max_rsi_recent"]
        ):
            continue
        rows.append({"symbol": symbol, "tech": tech})

    rows.sort(key=lambda r: score.partial_score(r, config, score.STAGE_TECHNICAL), reverse=True)
    log.info("technicals: %d symbols -> %d tradeable", len(histories), len(rows))
    return rows[: config["funnel"]["after_technicals"]]


def _fundamentals_stage(rows: list[dict], session: YahooSession, config: dict) -> list[dict]:
    """Market cap, revenue growth and margin trend on the technical survivors."""
    symbols = [row["symbol"] for row in rows]

    quotes = session.quotes(symbols)
    for row in rows:
        quote = quotes.get(row["symbol"], {})
        row["market_cap"] = quote.get("marketCap")
        row["name"] = quote.get("longName") or quote.get("shortName") or row["symbol"]

    min_cap = config["gates"]["min_market_cap"]
    rows = [r for r in rows if r["market_cap"] is None or r["market_cap"] >= min_cap]

    cache = JsonCache(CACHE_DIR / "fundamentals.json")
    loaded = fundamentals.load_many([r["symbol"] for r in rows], session, cache)
    cache.save()
    for row in rows:
        row["fund"] = loaded.get(row["symbol"])

    rows.sort(key=lambda r: score.partial_score(r, config, score.STAGE_FUNDAMENTAL), reverse=True)
    log.info("fundamentals: %d survive the market-cap gate", len(rows))
    return rows[: config["funnel"]["after_fundamentals"]]


def _options_stage(rows: list[dict], config: dict, as_of: date, workers: int = 2) -> list[dict]:
    """Fetch chains, pick a put, drop anything that isn't actually sellable.

    Two workers, measured rather than guessed: CBOE tolerates unlimited serial
    requests (20 back-to-back, no gap, no failures) but caps parallelism hard --
    2 workers went 20/20, 3 went 19/20, and 4 got every single request refused.
    The retry in fetch_chain is the safety net, not the plan.
    """
    iv_cache = JsonCache(CACHE_DIR / "iv_history.json")

    def one(row: dict) -> dict:
        chain = options.fetch_chain(row["symbol"])
        if chain is None:
            row["no_chain"] = True
            return row
        row["spot"] = chain.spot
        row["iv"] = options.atm_iv(chain, as_of)
        row["trade"] = options.select_put(chain, config, as_of)
        return row

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(one, rows))

    for row in rows:
        history = iv_cache.get(f"iv:{row['symbol']}") or []
        row["iv_percentile"] = iv_percentile([h["iv"] for h in history], row.get("iv"))

        hv20 = row["tech"].get("hv20")
        row["iv_hv"] = round(row["iv"] / hv20, 3) if row.get("iv") and hv20 else None

        if row.get("iv"):
            # Recorded for every candidate, not just the ten -- the percentile is
            # only worth having if the series is unbroken.
            stamped = [h for h in history if h["date"] != as_of.isoformat()]
            stamped.append({"date": as_of.isoformat(), "iv": round(row["iv"], 4)})
            iv_cache.set(f"iv:{row['symbol']}", stamped[-MAX_IV_HISTORY:])
    iv_cache.save()

    tradeable = [row for row in rows if not score.check_gates(row, config)]
    missing = [row["symbol"] for row in rows if row.get("no_chain")]
    log.info("options: %d of %d have a sellable put", len(tradeable), len(rows))
    if missing:
        # Not the same as "not tradeable" -- these are names we never got to
        # judge. Said out loud so a throttled morning doesn't look like a
        # thin one.
        log.warning("options: no chain for %d names (%s)", len(missing), ", ".join(missing[:10]))
    return tradeable


def _add_buzz(rows: list[dict]) -> list[dict]:
    """Reddit mentions: per-pick counts, plus the day's most-discussed list.

    Free and keyless, so it runs even under --no-ai. Mom asked for this by name.
    """
    from . import buzz

    try:
        mentions = buzz.fetch()
    except Exception as exc:
        log.warning("reddit buzz unavailable (%s)", exc)
        return []

    for row in rows:
        row["buzz"] = mentions.get(row["symbol"])
    return buzz.top(mentions)


def _add_catalyst(rows: list[dict], config: dict) -> dict:
    """Why each name sold off. The only part of the run that leaves the country.

    One grounded Gemini call, on the free tier. Wrapped: a page with today's
    numbers and no news beats a traceback at 6:45 in the morning.
    """
    from . import catalyst

    try:
        answer = catalyst.explain(rows, config)
    except Exception as exc:
        log.warning("catalyst layer failed (%s) -- publishing without it", exc)
        return {"ran": False, "brief": None}

    verdicts = answer["verdicts"]
    for row in rows:
        row["catalyst"] = verdicts.get(row["symbol"])
    return {"ran": bool(verdicts), "brief": answer["brief"]}


REPEAT_LOOKBACK = 6  # runs to look back over -- about a trading week


def _contract(row: dict) -> tuple | None:
    """The strike and expiry that identify one put, or None if there isn't one."""
    trade = row.get("trade") or {}
    if trade.get("strike") is None or not trade.get("expiry"):
        return None
    return (trade["strike"], trade["expiry"])


def _past_runs(as_of: date, limit: int = REPEAT_LOOKBACK) -> list[dict]:
    """Recent published lists, newest first, not counting today's.

    Reads what every run already commits to history/, so knowing what she has
    already seen costs no new data and stores nothing about her.
    """
    runs: list[dict] = []
    for path in sorted(HISTORY_DIR.glob("*.json"), reverse=True):
        if path.stem >= as_of.isoformat():
            continue
        try:
            runs.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            log.warning("history: could not read %s, skipping it", path.name)
        if len(runs) >= limit:
            break
    return runs


def _mark_repeats(rows: list[dict], past: list[dict]) -> None:
    """Say whether she has seen this name -- and this exact contract -- before.

    select_put takes the expiry nearest 35 days out and the delta nearest 0.20.
    Day over day those inputs barely move, so a name that keeps scoring well
    hands back the identical strike and expiry, and the screener has no memory
    to notice. Three states matter:

        new today
        back, same contract      -- nothing new to look at
        back, different contract -- a second angle on a setup still live

    Nothing is suppressed either way. A name still oversold and bouncing on day
    three is a true result, and hiding it would misrepresent the screen.

    Only past `picks` count, not the bench: she never saw the bench.
    """
    seen_in = [
        (run.get("as_of"), {pick["symbol"]: pick for pick in (run.get("picks") or [])})
        for run in past
    ]

    for row in rows:
        streak, since = 0, None
        for as_of, by_symbol in seen_in:  # newest first; a gap ends the run
            if row["symbol"] not in by_symbol:
                break
            streak += 1
            since = as_of

        if not streak:
            row["seen"] = None
            continue

        previous = seen_in[0][1][row["symbol"]]
        mine, theirs = _contract(row), _contract(previous)
        row["seen"] = {
            "days": streak + 1,  # counting today
            "same_contract": bool(mine and theirs and mine == theirs),
            "since": since,
        }


def _present(row: dict, result: dict, rank: int) -> dict:
    """One card's worth of data. Everything the page shows, nothing it doesn't."""
    tech = row["tech"]
    return {
        "rank": rank,
        "symbol": row["symbol"],
        "name": row.get("name", row["symbol"]),
        "score": result["score"],
        "price": round(tech["close"], 2),
        "change_5d": tech.get("change_5d"),
        "market_cap": row.get("market_cap"),
        "trade": row.get("trade"),
        "badges": result["badges"],
        "penalties": result["penalties"],
        "components": result["components"],
        "catalyst": row.get("catalyst"),
        "buzz": row.get("buzz"),
        "seen": row.get("seen"),
        # `close`, the two recent minimums and `at_52w_low` earn their bytes by
        # being re-scored rather than by being rendered. See the tuple.
        "technicals": {key: tech.get(key) for key in PUBLISHED_TECHNICALS},
        "fundamentals": row.get("fund"),
        "iv": row.get("iv"),
        "iv_hv": row.get("iv_hv"),
        "iv_percentile": row.get("iv_percentile"),
    }


def build(config: dict, limit: int | None = None, use_ai: bool = True, as_of: date | None = None) -> dict:
    """Run the whole funnel and return the payload the page reads."""
    as_of = as_of or date.today()
    started = time.time()

    session = YahooSession()
    universe_cache = JsonCache(CACHE_DIR / "universe.json")
    symbols = universe.load(universe_cache, config["universe"]["refresh_days"])
    universe_cache.save()
    if limit:
        symbols = symbols[:limit]
    log.info("universe: %d symbols", len(symbols))

    rows = _technicals_stage(symbols, session, config)
    rows = _fundamentals_stage(rows, session, config)
    rows = _options_stage(rows, config, as_of)

    # Everything that survived the options stage already has a chain, a chosen
    # put and a score. Ranking keeps all of it: the ten she reads, and the rest
    # as a bench she can pull from when the top of the list looks familiar.
    # Publishing the bench costs one slice and no extra fetches.
    ranked = sorted(
        ((row, score.score(row, config)) for row in rows),
        key=lambda pair: pair[1]["score"],
        reverse=True,
    )
    final = config["funnel"]["final"]
    scored, bench = ranked[:final], ranked[final:]

    picks = [row for row, _ in scored]
    reddit = _add_buzz(picks) if picks else []

    brief, catalyst_ran = None, False
    if use_ai and picks:
        answer = _add_catalyst(picks, config)
        catalyst_ran, brief = answer["ran"], answer["brief"]
        # The catalyst verdict is a penalty, so the final order can only be
        # settled after it lands. Only these ten were researched, so the bench
        # stays put rather than being promoted past a name that now carries a
        # verdict she can read.
        scored = sorted(
            ((row, score.score(row, config)) for row in picks),
            key=lambda pair: pair[1]["score"],
            reverse=True,
        )

    _mark_repeats([row for row, _ in ranked], _past_runs(as_of))

    return {
        "reddit": reddit,
        "catalyst_ran": catalyst_ran,
        "brief": brief,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(),
        "universe_size": len(symbols),
        # The tuning the page re-scores with. Published rather than restated in
        # JavaScript so that a weight has exactly one home: change config.yaml
        # and the browser's sliders start from the new number the next morning.
        "config": {
            "weights": config["weights"],
            "scoring": config["scoring"],
            "penalties": config["penalties"],
            "gates": config["gates"],
            "option": {
                key: config["option"][key]
                for key in ("target_delta", "min_delta", "max_delta",
                            "target_dte", "min_dte", "max_dte")
            },
        },
        "elapsed_seconds": round(time.time() - started, 1),
        "ai_enabled": use_ai,
        "note": (
            "Prices and option quotes are delayed and reflect the prior close. "
            "Verify the strike and premium live before placing any trade."
        ),
        "picks": [_present(row, result, i + 1) for i, (row, result) in enumerate(scored)],
        "bench": [
            _present(row, result, len(scored) + i + 1)
            for i, (row, result) in enumerate(bench)
        ],
    }


def _print_table(payload: dict) -> None:
    picks = payload["picks"]
    if not picks:
        print("No names cleared the gates today.")
        return

    header = f"{'#':>2} {'SYM':<6} {'SCORE':>6} {'PRICE':>8} {'RSI':>5} {'W%R':>6} {'IV/HV':>6} {'STRIKE':>8} {'EXP':>11} {'CR':>6} {'KEEP':>5} {'ANN':>7}"
    print(header)
    print("-" * len(header))
    for pick in picks:
        tech, trade = pick["technicals"], pick["trade"] or {}
        print(
            f"{pick['rank']:>2} {pick['symbol']:<6} {pick['score']:>6.1f} {pick['price']:>8.2f} "
            f"{_num(tech.get('rsi14'), '5.1f')} {_num(tech.get('williams_r14'), '6.1f')} "
            f"{_num(pick.get('iv_hv'), '6.2f')} {_num(trade.get('strike'), '8.2f')} "
            f"{trade.get('expiry', '-'):>11} {_num(trade.get('credit'), '6.2f')} "
            f"{_pct(trade.get('keep_premium_odds'), 5)} {_pct(trade.get('annualized_pct'), 7)}"
        )

    print()
    for pick in picks:
        failed = [b["label"] for b in pick["badges"] if b["passed"] is False]
        flags = [p["reason"] for p in pick["penalties"]]
        print(f"{pick['symbol']:<6} {pick['name'][:38]:<38} misses: {', '.join(failed) or 'nothing'}")
        for flag in flags:
            print(f"       ! {flag}")
        if pick.get("seen"):
            seen = pick["seen"]
            same = "same contract" if seen["same_contract"] else "different contract"
            print(f"       ~ day {seen['days']} on the list, {same}, back since {seen['since']}")
        if pick.get("catalyst"):
            print(f"       > [{pick['catalyst']['verdict']}] {pick['catalyst'].get('headline', '')}")

    bench = payload.get("bench") or []
    print(f"\n{len(picks)} names ({len(bench)} more on the bench) "
          f"from {payload['universe_size']} symbols in {payload['elapsed_seconds']}s")
    if payload.get("brief"):
        print(f"\n{payload['brief']}")


def _num(value, spec: str) -> str:
    width = int(spec.split(".")[0])
    return f"{value:{spec}}" if value is not None else "-".rjust(width)


def _pct(value, width: int) -> str:
    return f"{value:>{width}.0%}" if value is not None else "-".rjust(width)


def _write(payload: dict) -> None:
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    # The browser downloads latest.json on every visit and now carries the
    # bench too, so it goes out compact. history/ is committed and read by
    # people, so it stays indented and diffable.
    (SITE_DATA / "latest.json").write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    (HISTORY_DIR / f"{payload['as_of']}.json").write_text(
        json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    log.info("wrote site/data/latest.json and history/%s.json", payload["as_of"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Daily put-selling screen.")
    parser.add_argument("--limit", type=int, help="only scan the first N symbols (for testing)")
    parser.add_argument("--no-ai", action="store_true", help="skip the catalyst step (the one call to Gemini)")
    parser.add_argument("--dry-run", action="store_true", help="print the table, write nothing")
    parser.add_argument("--date", help="run as of YYYY-MM-DD (default: today)")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    config = _load_config(Path(args.config))
    as_of = date.fromisoformat(args.date) if args.date else date.today()

    payload = build(config, limit=args.limit, use_ai=not args.no_ai, as_of=as_of)
    _print_table(payload)
    if not args.dry_run:
        _write(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
