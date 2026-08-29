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
    # The same download, kept rather than discarded. Selling a put and buying a
    # call are the same directional bet with different payoffs, and the bytes
    # for both arrived in one response.
    calls: list[Contract]


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
    """Download one symbol's option chain from CBOE.

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

    sides: dict[str, list[Contract]] = {"P": [], "C": []}
    for raw in data.get("options", []):
        parsed = parse_occ(raw.get("option", ""))
        if parsed is None:
            continue
        expiry, right, strike = parsed
        if right not in sides:
            continue
        iv = float(raw.get("iv") or 0.0)
        delta = float(raw.get("delta") or 0.0)
        if iv <= 0 or delta == 0:
            continue  # CBOE zeroes these out on contracts with no real market
        sides[right].append(
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

    puts, calls = sides["P"], sides["C"]
    # A name with calls and no sellable put is still a name -- the buy and hold
    # rankings want it, and so does the call ranking. Dropping the whole chain
    # here because one side came back empty would take all three with it.
    if not puts and not calls:
        return None
    return Chain(symbol=symbol, spot=spot, puts=puts, calls=calls)


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


def _fillable(
    contracts: list[Contract], spot: float, opt: dict, side: str = "put"
) -> list[Contract]:
    """The contracts in one expiry that would actually fill at a sane price.

    Both sides end up under spot, for opposite reasons, so the moneyness rule
    is written per side rather than shared as a coincidence: the put she sells
    has to be out of the money, and the call she buys at 0.65 delta is in it by
    construction. The call also takes the strike sitting exactly at spot, which
    is the most liquid contract on the board and no worse for being borderline.
    """
    return [
        c
        for c in contracts
        if opt["min_delta"] <= abs(c.delta) <= opt["max_delta"]
        and c.bid >= opt["min_bid"]
        and c.open_interest >= opt["min_open_interest"]
        and c.spread_pct is not None
        and c.spread_pct <= opt["max_spread_pct"]
        and (c.strike < spot if side == "put" else c.strike <= spot)
    ]


def _best_in_expiry(
    contracts: list[Contract], spot: float, opt: dict, side: str = "put"
) -> Contract | None:
    """The strike closest to the target delta, among contracts that would fill."""
    tradeable = _fillable(contracts, spot, opt, side)
    if not tradeable:
        return None
    return min(tradeable, key=lambda c: abs(abs(c.delta) - opt["target_delta"]))


def _describe_call(call: Contract, spot: float, as_of: date) -> dict:
    """One call as a buyer reads it: what it costs, and what has to happen.

    Not _describe with the signs flipped. A seller asks "what do I keep, and
    where does it start to hurt"; a buyer asks "what do I pay, and how far does
    the stock have to move". Different questions, so different fields.

    `time_value_share` is the whole argument for a 0.65 delta over a cheap
    out-of-the-money call: it is the fraction of the price that decays to
    nothing if the stock stands still, and it is what she is really choosing
    when she picks a strike.
    """
    dte = (call.expiry - as_of).days
    cost = call.mid
    intrinsic = max(0.0, spot - call.strike)
    breakeven = call.strike + cost
    return {
        "id": f"{call.expiry.isoformat()}@{call.strike:g}",
        "expiry": call.expiry.isoformat(),
        "dte": dte,
        "strike": call.strike,
        "bid": call.bid,
        "ask": call.ask,
        "cost": round(cost, 3),
        "spread_pct": round(call.spread_pct, 4),
        "delta": round(abs(call.delta), 4),
        "iv": round(call.iv, 4),
        "open_interest": call.open_interest,
        "volume": call.volume,
        # One contract is 100 shares, so this is the whole cheque -- and the
        # number that says what leverage actually costs in cash.
        "outlay": round(cost * 100.0, 2),
        "intrinsic": round(intrinsic, 3),
        "time_value": round(cost - intrinsic, 3),
        "time_value_share": round((cost - intrinsic) / cost, 4) if cost > 0 else None,
        "breakeven": round(breakeven, 2),
        "pct_to_breakeven": round((breakeven - spot) / spot, 4),
        # What 100 shares would have cost instead. Not a suggestion to buy
        # either one -- it is the denominator that makes the outlay mean
        # something.
        "shares_equivalent": round(spot * 100.0, 2),
    }


def _describe(put: Contract, spot: float, as_of: date) -> dict:
    """One contract as the page and the scorer read it.

    Shared by the suggested put and every alternative beside it, so a strike she
    switches to carries exactly the fields the score was computed from -- there
    is no second, thinner shape to keep in step.
    """
    dte = (put.expiry - as_of).days
    credit = put.mid
    return_pct = credit / put.strike
    return {
        # Strike alone stops identifying a contract once the ladder crosses
        # expiries, and the page needs a key it can put in a radio button.
        "id": f"{put.expiry.isoformat()}@{put.strike:g}",
        "expiry": put.expiry.isoformat(),
        "dte": dte,
        "strike": put.strike,
        "bid": put.bid,
        "ask": put.ask,
        "credit": round(credit, 3),
        "spread_pct": round(put.spread_pct, 4),
        "delta": round(abs(put.delta), 4),
        "keep_premium_odds": round(1.0 - abs(put.delta), 4),
        "iv": round(put.iv, 4),
        "open_interest": put.open_interest,
        "volume": put.volume,
        "cash_secured": round(put.strike * 100.0, 2),
        "return_pct": round(return_pct, 5),
        "annualized_pct": round(return_pct * 365.0 / dte, 5) if dte > 0 else None,
        "breakeven": round(put.strike - credit, 2),
        "pct_below_spot": round((spot - put.strike) / spot, 4),
    }


# The rungs the alternatives ladder aims at. Anything outside the configured
# delta range is filtered out before it is reached, so this can stay wider than
# any one config.
DELTA_LADDER = (0.10, 0.15, 0.20, 0.25, 0.30, 0.35)


def _ladder(
    puts: list[Contract], spot: float, opt: dict, as_of: date, chosen: Contract
) -> list[dict]:
    """The other puts worth considering on this name, safest first.

    The chain is already downloaded and every one of these was already thrown
    away, so this costs nothing but the bytes. It is what turns "how safe do you
    want the strike?" into a control on the page rather than a re-run: the whole
    point of the screen is not being assigned, and the strike is the dial that
    decides it.

    Drawn from every expiry in the DTE window rather than just the chosen one,
    because measured on real chains a single expiry usually holds nought to four
    fillable puts -- the open-interest and spread filters are strict, and they
    should be. Restricted to one expiry this shipped 33 alternatives across 43
    names, which is not a control. Ordered by delta rather than by strike, since
    across expiries the strike is no longer what says which one is safer.
    """
    tradeable = _fillable(puts, spot, opt)

    picked: dict[tuple, Contract] = {}
    for target in DELTA_LADDER:
        nearest = min(tradeable, key=lambda p: abs(abs(p.delta) - target), default=None)
        # Rungs collide on a coarse chain, and the suggested put is not an
        # alternative to itself.
        if nearest is not None and nearest is not chosen:
            picked[(nearest.expiry, nearest.strike)] = nearest

    return [
        _describe(put, spot, as_of)
        for put in sorted(picked.values(), key=lambda p: abs(p.delta))
    ]


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

    trade = _describe(best, chain.spot, as_of)
    every = [put for puts in by_expiry.values() for put in puts]
    trade["alternatives"] = _ladder(every, chain.spot, opt, as_of, best)
    return trade


def select_call(chain: Chain, config: dict, as_of: date | None = None) -> dict | None:
    """Pick the call to suggest, or None if nothing in this chain is buyable.

    The same shape as select_put over a different set of numbers, out of
    `config["call"]`: further out in time, because a thesis about a chart needs
    longer than five weeks to come true, and deep enough in the money that most
    of what she pays is intrinsic value rather than time. The cheap
    out-of-the-money call is the one that expires worthless, and it is the one
    this deliberately does not pick.

    No ladder. The put has alternatives because the strike is the dial that
    decides assignment, which is the thing she is trying to avoid. A call buyer
    has no equivalent question: there the strike sets leverage, and offering
    her more leverage is not a safety control.
    """
    as_of = as_of or date.today()
    opt = config["call"]

    by_expiry: dict[date, list[Contract]] = {}
    for call in chain.calls:
        dte = (call.expiry - as_of).days
        if opt["min_dte"] <= dte <= opt["max_dte"]:
            by_expiry.setdefault(call.expiry, []).append(call)

    best = None
    for expiry in sorted(by_expiry, key=lambda e: abs((e - as_of).days - opt["target_dte"])):
        best = _best_in_expiry(by_expiry[expiry], chain.spot, opt, "call")
        if best is not None:
            break
    if best is None:
        return None

    return _describe_call(best, chain.spot, as_of)
