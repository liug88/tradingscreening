"""Quarterly revenue growth and margin trend.

This is the "improving sales and improving margin" half of Mom's rules -- the
part that separates a company having a bad month from a company in decline.

Fundamentals only change when a company reports, so results are cached and the
cache is invalidated by the earnings date rather than by a fixed timer. That
turns ~150 requests a day into ~10.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

from .cache import JsonCache
from .yahoo import YahooSession, YahooError

log = logging.getLogger(__name__)

METRICS = [
    "quarterlyTotalRevenue",
    "quarterlyGrossProfit",
    "quarterlyOperatingIncome",
]
THREE_YEARS_AGO = 3 * 365 * 86400
MAX_CACHE_DAYS = 100  # backstop when we have no earnings date to key off


def _series(payload: dict, key: str) -> dict[str, float]:
    """Pull one metric out of a fundamentals-timeseries payload, keyed by report date."""
    out: dict[str, float] = {}
    for item in payload.get("timeseries", {}).get("result", []):
        for entry in item.get(key) or []:
            if entry and entry.get("reportedValue"):
                out[entry["asOfDate"]] = float(entry["reportedValue"]["raw"])
    return out


def _growth(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None or prior <= 0:
        return None
    return current / prior - 1.0


def _margin(profit: float | None, revenue: float | None) -> float | None:
    if profit is None or revenue is None or revenue <= 0:
        return None
    return profit / revenue


def _earnings_date(payload: dict) -> str | None:
    try:
        result = payload["quoteSummary"]["result"][0]
        dates = result.get("calendarEvents", {}).get("earnings", {}).get("earningsDate") or []
    except (KeyError, IndexError, TypeError):
        return None
    stamps = [d["raw"] for d in dates if isinstance(d, dict) and d.get("raw")]
    if not stamps:
        return None
    # A range means Yahoo is estimating; take the earlier edge, which is the
    # conservative choice when the question is "does this land before expiry?"
    return datetime.fromtimestamp(min(stamps), tz=timezone.utc).date().isoformat()


def fetch(symbol: str, session: YahooSession) -> dict:
    """Revenue growth, margin trend and next earnings date for one symbol."""
    payload = session.fundamentals(symbol, METRICS, since=int(time.time()) - THREE_YEARS_AGO)
    revenue = _series(payload, "quarterlyTotalRevenue")
    gross = _series(payload, "quarterlyGrossProfit")
    operating = _series(payload, "quarterlyOperatingIncome")

    quarters = sorted(revenue)
    latest = quarters[-1] if quarters else None
    prior = quarters[-2] if len(quarters) >= 2 else None
    year_ago = quarters[-5] if len(quarters) >= 5 else None

    gross_now = _margin(gross.get(latest), revenue.get(latest))
    gross_prev = _margin(gross.get(prior), revenue.get(prior))
    op_now = _margin(operating.get(latest), revenue.get(latest))
    op_prev = _margin(operating.get(prior), revenue.get(prior))

    try:
        next_earnings = _earnings_date(session.quote_summary(symbol, ["calendarEvents"]))
    except YahooError:
        next_earnings = None

    return {
        "latest_quarter": latest,
        "revenue_yoy": _growth(revenue.get(latest), revenue.get(year_ago)),
        "revenue_qoq": _growth(revenue.get(latest), revenue.get(prior)),
        "gross_margin": gross_now,
        "gross_margin_change": None if gross_now is None or gross_prev is None else gross_now - gross_prev,
        "operating_margin": op_now,
        "operating_margin_change": None if op_now is None or op_prev is None else op_now - op_prev,
        "profitable": None if op_now is None else op_now > 0,
        "next_earnings": next_earnings,
        "revenue_history": [
            {"quarter": q, "revenue": revenue[q]} for q in quarters[-6:]
        ],
    }


def _is_stale(entry: dict, today: date) -> bool:
    """Stale once the company has reported again."""
    reported = entry.get("next_earnings")
    if reported is None:
        return False
    try:
        return date.fromisoformat(reported) < today
    except ValueError:
        return True


def load_many(
    symbols: list[str], session: YahooSession, cache: JsonCache, workers: int = 4
) -> dict[str, dict]:
    """Cached fundamentals for a list of symbols. Misses are fetched concurrently."""
    today = date.today()
    results: dict[str, dict] = {}
    to_fetch: list[str] = []

    for symbol in symbols:
        cached = cache.get(f"fundamentals:{symbol}", max_age_days=MAX_CACHE_DAYS)
        if cached is not None and not _is_stale(cached, today):
            results[symbol] = cached
        else:
            to_fetch.append(symbol)

    if to_fetch:
        log.info("fundamentals: %d cached, fetching %d", len(results), len(to_fetch))

        def one(symbol: str) -> tuple[str, dict | None]:
            try:
                return symbol, fetch(symbol, session)
            except (YahooError, KeyError, IndexError, TypeError, ValueError) as exc:
                log.debug("%s: fundamentals failed (%s)", symbol, exc)
                return symbol, None

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for symbol, data in pool.map(one, to_fetch):
                if data is not None:
                    cache.set(f"fundamentals:{symbol}", data)
                    results[symbol] = data

    return results
