"""Option chains, and picking the put to suggest.

Source is CBOE's delayed-quote feed, which hands back delta and IV per contract
already computed. Delta is the number this whole strategy turns on: it is close
enough to the market's own estimate of the odds the put finishes in the money,
which is to say the odds Mom gets assigned the stock she was trying not to buy.

Everything CBOE-specific is in fetch_chain(). If that feed ever changes, swapping
it out means rewriting one function -- select_put() works on our own shapes.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime

import requests

from .yahoo import BROWSER_UA

log = logging.getLogger(__name__)

CBOE_CHAIN = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"

# CBOE's limit is a volume budget, not a rate: measured here, 20 back-to-back
# requests always succeed, 75 loses roughly 16, and once the budget is spent it
# takes about 45 seconds to refill. So the fix is to stay under it rather than
# to recover from it -- at ~1.2 requests a second a 75-name batch finishes
# without ever being refused. The retry below is the safety net, not the plan.
MIN_REQUEST_INTERVAL = 0.8
THROTTLE_BACKOFF = 5  # seconds, doubling -- has to reach the ~45s refill

_throttle = threading.Lock()
_last_request = 0.0
_blocked_until = 0.0


def _wait_turn() -> None:
    """Space requests out, across threads."""
    global _last_request
    with _throttle:
        now = time.monotonic()
        wait = max(_blocked_until - now, _last_request + MIN_REQUEST_INTERVAL - now)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


def _back_off(seconds: float) -> None:
    """Hold every thread, not just the one that got refused.

    The budget is per-IP, so a backoff that only pauses the caller doesn't work:
    the other workers keep draining it and the window never clears. Two names
    were still being dropped for exactly this reason after per-request retries
    were already in place.
    """
    global _blocked_until
    with _throttle:
        _blocked_until = max(_blocked_until, time.monotonic() + seconds)

# OCC symbol: root, then YYMMDD, then C/P, then strike x1000 in 8 digits.
# Anchored from the right because roots vary in length.
OCC = re.compile(r"^(?P<root>.+?)(?P<ymd>\d{6})(?P<right>[CP])(?P<strike>\d{8})$")


@dataclass
class Contract:
    expiry: date
    strike: float
    bid: float
    ask: float
    iv: float
    delta: float
    open_interest: int
    volume: int

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread_pct(self) -> float | None:
        return None if self.mid <= 0 else (self.ask - self.bid) / self.mid


@dataclass
class Chain:
    symbol: str
    spot: float
    puts: list[Contract]


def parse_occ(symbol: str) -> tuple[date, str, float] | None:
    match = OCC.match(symbol)
    if not match:
        return None
    try:
        expiry = datetime.strptime(match.group("ymd"), "%y%m%d").date()
    except ValueError:
        return None
    return expiry, match.group("right"), int(match.group("strike")) / 1000.0


def fetch_chain(symbol: str, timeout: int = 30, max_retries: int = 4) -> Chain | None:
    """Download one symbol's put chain from CBOE.

    Retries on throttling rather than giving up, because the caller can't tell
    the difference: a swallowed 429 looks exactly like "this stock has no
    options", and the name just disappears from the list. Mom came here because
    her lists were inconsistent -- silently dropping a name on a busy morning
    would rebuild the problem inside the tool meant to fix it.
    """
    url = CBOE_CHAIN.format(symbol=symbol.replace(".", ""))

    data = None
    for attempt in range(max_retries):
        _wait_turn()
        try:
            response = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=timeout)
            if response.status_code == 200:
                data = response.json()["data"]
                break
            if response.status_code in (429, 500, 502, 503, 504):
                log.debug("%s: chain http %d, retrying", symbol, response.status_code)
                _back_off(THROTTLE_BACKOFF * 2**attempt)
                continue
            log.debug("%s: chain http %d", symbol, response.status_code)
            return None
        except (requests.RequestException, ValueError, KeyError) as exc:
            log.debug("%s: chain fetch failed (%s)", symbol, exc)
            time.sleep(THROTTLE_BACKOFF * 2**attempt)

    if data is None:
        log.warning("%s: no chain after %d tries", symbol, max_retries)
        return None

    spot = float(data.get("current_price") or 0.0)
    if spot <= 0:
        return None

    puts: list[Contract] = []
    for raw in data.get("options", []):
        parsed = parse_occ(raw.get("option", ""))
        if parsed is None:
            continue
        expiry, right, strike = parsed
        if right != "P":
            continue
        iv = float(raw.get("iv") or 0.0)
        delta = float(raw.get("delta") or 0.0)
        if iv <= 0 or delta == 0:
            continue  # CBOE zeroes these out on contracts with no real market
        puts.append(
            Contract(
                expiry=expiry,
                strike=strike,
                bid=float(raw.get("bid") or 0.0),
                ask=float(raw.get("ask") or 0.0),
                iv=iv,
                delta=delta,
                open_interest=int(raw.get("open_interest") or 0),
                volume=int(raw.get("volume") or 0),
            )
        )

    return Chain(symbol=symbol, spot=spot, puts=puts) if puts else None


def atm_iv(chain: Chain, as_of: date, min_dte: int = 20, max_dte: int = 60) -> float | None:
    """IV of the near-the-money put -- the cleanest read on this stock's vol.

    Uses at-the-money rather than the strike we suggest because far-out-of-the-
    money puts carry skew, which would make every stock look like its premium
    is rich.
    """
    candidates = [
        put for put in chain.puts if min_dte <= (put.expiry - as_of).days <= max_dte
    ]
    if not candidates:
        return None
    nearest = min(candidates, key=lambda p: abs(p.strike - chain.spot))
    return nearest.iv


def _best_in_expiry(puts: list[Contract], spot: float, opt: dict) -> Contract | None:
    """The strike closest to the target delta, among contracts that would fill."""
    tradeable = [
        put
        for put in puts
        if opt["min_delta"] <= abs(put.delta) <= opt["max_delta"]
        and put.bid >= opt["min_bid"]
        and put.open_interest >= opt["min_open_interest"]
        and put.spread_pct is not None
        and put.spread_pct <= opt["max_spread_pct"]
        and put.strike < spot  # out of the money only
    ]
    if not tradeable:
        return None
    return min(tradeable, key=lambda p: abs(abs(p.delta) - opt["target_delta"]))


def select_put(chain: Chain, config: dict, as_of: date | None = None) -> dict | None:
    """Pick the put to suggest, or None if nothing in this chain is tradeable.

    Expiries are tried nearest-the-target-DTE first, but an expiry only wins if
    it actually contains a fillable contract. That ordering matters: a weekly
    listed two days ago can sit closer to 35 DTE than the monthly while having
    no open interest at all, and taking it on date proximity alone would suggest
    a strike with a 30%-wide spread.
    """
    as_of = as_of or date.today()
    opt = config["option"]

    by_expiry: dict[date, list[Contract]] = {}
    for put in chain.puts:
        dte = (put.expiry - as_of).days
        if opt["min_dte"] <= dte <= opt["max_dte"]:
            by_expiry.setdefault(put.expiry, []).append(put)

    best = None
    for expiry in sorted(by_expiry, key=lambda e: abs((e - as_of).days - opt["target_dte"])):
        best = _best_in_expiry(by_expiry[expiry], chain.spot, opt)
        if best is not None:
            break
    if best is None:
        return None

    expiry = best.expiry
    dte = (expiry - as_of).days
    credit = best.mid
    cash_secured = best.strike * 100.0
    return_pct = credit / best.strike
    return {
        "expiry": expiry.isoformat(),
        "dte": dte,
        "strike": best.strike,
        "bid": best.bid,
        "ask": best.ask,
        "credit": round(credit, 3),
        "spread_pct": round(best.spread_pct, 4),
        "delta": round(abs(best.delta), 4),
        "keep_premium_odds": round(1.0 - abs(best.delta), 4),
        "iv": round(best.iv, 4),
        "open_interest": best.open_interest,
        "volume": best.volume,
        "cash_secured": round(cash_secured, 2),
        "return_pct": round(return_pct, 5),
        "annualized_pct": round(return_pct * 365.0 / dte, 5) if dte > 0 else None,
        "breakeven": round(best.strike - credit, 2),
        "pct_below_spot": round((chain.spot - best.strike) / chain.spot, 4),
    }
