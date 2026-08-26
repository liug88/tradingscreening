"""Daily price history, fetched concurrently.

This is the widest stage of the funnel -- ~570 symbols -- so it runs in a thread
pool. Yahoo rate-limits, so the pool stays small and YahooSession backs off.
A symbol that fails is dropped rather than failing the run: nine good names beat
no page at all.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from .yahoo import YahooSession, YahooError

log = logging.getLogger(__name__)

MIN_BARS = 60


def parse_chart(payload: dict) -> pd.DataFrame:
    """Yahoo chart JSON -> OHLCV frame, oldest first, gaps dropped."""
    result = payload["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]

    frame = pd.DataFrame(
        {
            "open": quote["open"],
            "high": quote["high"],
            "low": quote["low"],
            "close": quote["close"],
            "volume": quote["volume"],
        },
        index=pd.to_datetime(result["timestamp"], unit="s", utc=True).tz_convert(None).normalize(),
    )
    # Yahoo returns nulls for halted or missing sessions.
    return frame.dropna(subset=["close", "high", "low", "volume"])


def fetch_one(session: YahooSession, symbol: str, range_: str = "2y") -> pd.DataFrame | None:
    try:
        frame = parse_chart(session.chart(symbol, range_=range_))
    except (YahooError, KeyError, IndexError, TypeError, ValueError) as exc:
        log.debug("%s: price fetch failed (%s)", symbol, exc)
        return None
    return frame if len(frame) >= MIN_BARS else None


def fetch_histories(
    symbols: list[str], session: YahooSession, range_: str = "2y", workers: int = 6
) -> dict[str, pd.DataFrame]:
    """Fetch every symbol's history. Returns only the ones that came back clean."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        frames = pool.map(lambda s: (s, fetch_one(session, s, range_)), symbols)
        histories = {symbol: frame for symbol, frame in frames if frame is not None}

    missing = len(symbols) - len(histories)
    if missing:
        log.warning("no usable price history for %d of %d symbols", missing, len(symbols))
    return histories
