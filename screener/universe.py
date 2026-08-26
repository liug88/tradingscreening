"""The starting universe: stocks with weekly options.

CBOE publishes the list of symbols that have weekly options. That list is a good
proxy for "liquid enough to sell puts on" -- a stock only gets weeklys if there's
real demand for its options. It also keeps the daily run small: ~570 equities
instead of every listed ticker in the country.

ETFs are deliberately excluded. Mom's criteria include improving sales and
margins, and an ETF has neither.
"""

from __future__ import annotations

import csv
import io
import logging
import re

import requests

from .cache import JsonCache
from .yahoo import BROWSER_UA

log = logging.getLogger(__name__)

CBOE_WEEKLYS_CSV = "https://www.cboe.com/available_weeklys/get_csv_download/"
EQUITY_SECTION = "Available Weeklys - Equity"
TICKER = re.compile(r"^[A-Z][A-Z.]{0,5}$")


def fetch_equity_symbols(timeout: int = 30) -> list[str]:
    """Download and parse the CBOE weeklys list.

    The file is a schedule of expiry dates, then an ETF section, then an equity
    section. We want the last one.
    """
    response = requests.get(CBOE_WEEKLYS_CSV, headers={"User-Agent": BROWSER_UA}, timeout=timeout)
    response.raise_for_status()

    symbols: list[str] = []
    in_equities = False
    for row in csv.reader(io.StringIO(response.text)):
        cells = [c.strip() for c in row if c.strip()]
        if len(cells) == 1:
            in_equities = cells[0] == EQUITY_SECTION
        elif in_equities and len(cells) == 2 and TICKER.match(cells[0]):
            symbols.append(cells[0])

    if not symbols:
        raise RuntimeError("CBOE weeklys file parsed to zero equities -- format changed?")
    return sorted(set(symbols))


def load(cache: JsonCache, refresh_days: float = 7) -> list[str]:
    """Cached symbol list. Falls back to the stale copy if CBOE is unreachable."""
    symbols = cache.get("cboe_weekly_equities", max_age_days=refresh_days)
    if symbols:
        return symbols

    try:
        symbols = fetch_equity_symbols()
        cache.set("cboe_weekly_equities", symbols)
        log.info("universe: %d equities with weekly options", len(symbols))
        return symbols
    except Exception as exc:
        stale = cache.get("cboe_weekly_equities")
        if stale:
            log.warning("CBOE fetch failed (%s), using cached list of %d", exc, len(stale))
            return stale
        raise
