"""Quarterly revenue growth, margin trend, earnings, and what the company does.

This is the "improving sales and improving margin" half of Mom's rules -- the
part that separates a company having a bad month from a company in decline. It
also carries the three things that are facts about a company rather than about
its price: the business description, the last quarter reported and the next one
due.

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

# One request, three modules. The earnings date was already being fetched here,
# so what the company does and what it earned last quarter cost no extra round
# trip -- only a longer list.
QUOTE_MODULES = ["calendarEvents", "assetProfile", "earningsHistory"]

THREE_YEARS_AGO = 3 * 365 * 86400
MAX_CACHE_DAYS = 100  # backstop when we have no earnings date to key off

DESCRIPTION_CHARS = 220

# A full stop inside a company's own name does not end a sentence, and most of
# these summaries open with one.
ABBREVIATIONS = {
    "inc", "corp", "co", "cos", "ltd", "llc", "lp", "plc", "sa", "nv", "ag",
    "ab", "as", "asa", "oy", "oyj", "bv", "gmbh", "spa", "srl", "pte", "sdn",
    "bhd", "jsc", "pjsc", "pcl", "kk", "se", "cia", "aps", "st",
}


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


def _module(payload: dict, name: str) -> dict:
    """One module out of a quoteSummary response, or an empty dict."""
    try:
        return payload["quoteSummary"]["result"][0].get(name) or {}
    except (KeyError, IndexError, TypeError, AttributeError):
        return {}


def _raw(block) -> float | None:
    """Yahoo wraps every number as {raw, fmt}. This is the raw one, as a float."""
    if isinstance(block, dict):
        block = block.get("raw")
    if block is None or isinstance(block, bool):
        return None
    try:
        return float(block)
    except (TypeError, ValueError):
        return None


def _first_sentence(text: str | None) -> str | None:
    """The opening line of a Yahoo business summary.

    The whole summary runs to a thousand characters and she is reading ten of
    these over coffee, so only the first sentence is kept -- it is reliably the
    one that says what the company does. Splitting on the first full stop does
    not work: these open "Intel Corporation designs..." or "Micron Technology,
    Inc. designs...", and on the second shape that would publish the name and
    nothing else.
    """
    words = (text or "").split()
    if not words:
        return None

    kept: list[str] = []
    for word in words:
        kept.append(word)
        if not word.endswith((".", "!", "?")):
            continue
        stem = word.rstrip(".!?").lower()
        # "N.V.", "S.A.", "U.S." -- a full stop inside the word means the last
        # one is part of it too.
        if len(stem) < 2 or "." in stem or stem in ABBREVIATIONS:
            continue
        break

    sentence = " ".join(kept)
    if len(sentence) <= DESCRIPTION_CHARS:
        return sentence
    return sentence[:DESCRIPTION_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _last_earnings(payload: dict) -> dict | None:
    """The last quarter reported: what they earned against what was expected.

    Yahoo also returns `surprisePercent` and it is deliberately not used. The
    page says "earned 42 cents against 22 expected", so actual and estimate are
    what it needs; a percentage on top would be a second number saying the same
    thing, and one of the two would eventually disagree.
    """
    quarters = []
    for row in _module(payload, "earningsHistory").get("history") or []:
        if not isinstance(row, dict):
            continue
        stamp = _raw(row.get("quarter"))
        actual = _raw(row.get("epsActual"))
        if stamp is None or actual is None:
            continue
        quarters.append((stamp, actual, _raw(row.get("epsEstimate"))))

    if not quarters:
        return None
    stamp, actual, estimate = max(quarters)
    return {
        "quarter": datetime.fromtimestamp(stamp, tz=timezone.utc).date().isoformat(),
        "eps_actual": actual,
        "eps_estimate": estimate,
    }


def _earnings_date(payload: dict) -> str | None:
    earnings = _module(payload, "calendarEvents").get("earnings") or {}
    dates = earnings.get("earningsDate") or []
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
        summary = session.quote_summary(symbol, QUOTE_MODULES)
    except YahooError:
        summary = {}
    profile = _module(summary, "assetProfile")

    return {
        "latest_quarter": latest,
        "revenue_yoy": _growth(revenue.get(latest), revenue.get(year_ago)),
        "revenue_qoq": _growth(revenue.get(latest), revenue.get(prior)),
        "gross_margin": gross_now,
        "gross_margin_change": None if gross_now is None or gross_prev is None else gross_now - gross_prev,
        "operating_margin": op_now,
        "operating_margin_change": None if op_now is None or op_prev is None else op_now - op_prev,
        "profitable": None if op_now is None else op_now > 0,
        "next_earnings": _earnings_date(summary),
        "last_earnings": _last_earnings(summary),
        "sector": profile.get("sector") or None,
        "industry": profile.get("industry") or None,
        "description": _first_sentence(profile.get("longBusinessSummary")),
        "revenue_history": [
            {"quarter": q, "revenue": revenue[q]} for q in quarters[-6:]
        ],
    }


def _is_stale(entry: dict, today: date) -> bool:
    """Stale once the company has reported again -- or once it is missing a field.

    An entry is whatever `fetch` returned on the day it was written, and there
    is no version stamp on it. `industry` arrived with the description and the
    last quarter, so an entry without that key is one written before them, and
    keying off the earnings date alone would leave it that way for a quarter.
    Naming the key is what makes the backfill happen on the next run.
    """
    if "industry" not in entry:
        return True
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
