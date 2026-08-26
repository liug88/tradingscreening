"""Transport for Yahoo Finance.

Yahoo's data endpoints want a cookie plus a matching "crumb" token. This class
does that handshake once and every caller reuses the session. Interpretation of
the payloads lives in prices.py and fundamentals.py -- this module only fetches.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
QUOTE_BATCH = 50


class YahooError(RuntimeError):
    pass


class YahooSession:
    def __init__(self, timeout: int = 25, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": BROWSER_UA})
        self._crumb: str | None = None

    @property
    def crumb(self) -> str:
        if self._crumb is None:
            # The cookie has to be set before the crumb will validate.
            self._session.get("https://fc.yahoo.com", timeout=self.timeout)
            self._session.get("https://finance.yahoo.com/quote/AAPL", timeout=self.timeout)
            response = self._session.get(
                "https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=self.timeout
            )
            crumb = response.text.strip()
            if not crumb or len(crumb) > 32:
                raise YahooError(f"could not get a crumb (got {crumb!r})")
            self._crumb = crumb
            log.debug("yahoo crumb acquired")
        return self._crumb

    def get_json(self, url: str, params: dict | None = None, need_crumb: bool = False) -> Any:
        params = dict(params or {})
        if need_crumb:
            params["crumb"] = self.crumb

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = self._session.get(url, params=params, timeout=self.timeout)
                if response.status_code == 200:
                    return response.json()
                if response.status_code == 401 and need_crumb:
                    self._crumb = None  # stale crumb, re-handshake and retry
                    params["crumb"] = self.crumb
                    last_error = YahooError("401 unauthorized")
                elif response.status_code in (429, 500, 502, 503, 504):
                    last_error = YahooError(f"http {response.status_code}")
                else:
                    raise YahooError(f"http {response.status_code} for {url}")
            except requests.RequestException as exc:
                last_error = exc
            time.sleep(2**attempt)

        raise YahooError(f"{url} failed after {self.max_retries} tries: {last_error}")

    def chart(self, symbol: str, range_: str = "2y", interval: str = "1d") -> Any:
        """Daily OHLCV. This endpoint does not need a crumb."""
        return self.get_json(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            {"range": range_, "interval": interval},
        )

    def quotes(self, symbols: list[str]) -> dict[str, dict]:
        """Price, market cap and average volume, batched. Skips batches that fail."""
        out: dict[str, dict] = {}
        for start in range(0, len(symbols), QUOTE_BATCH):
            batch = symbols[start : start + QUOTE_BATCH]
            try:
                payload = self.get_json(
                    "https://query2.finance.yahoo.com/v7/finance/quote",
                    {"symbols": ",".join(batch)},
                    need_crumb=True,
                )
            except YahooError as exc:
                log.warning("quote batch %d failed: %s", start // QUOTE_BATCH, exc)
                continue
            for quote in payload.get("quoteResponse", {}).get("result", []):
                out[quote["symbol"]] = quote
        return out

    def fundamentals(self, symbol: str, metrics: list[str], since: int) -> Any:
        return self.get_json(
            f"https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}",
            {
                "symbol": symbol,
                "type": ",".join(metrics),
                "period1": since,
                "period2": int(time.time()),
            },
            need_crumb=True,
        )

    def quote_summary(self, symbol: str, modules: list[str]) -> Any:
        return self.get_json(
            f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}",
            {"modules": ",".join(modules)},
            need_crumb=True,
        )
