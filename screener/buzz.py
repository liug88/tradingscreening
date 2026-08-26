"""Reddit mention counts, from ApeWisdom.

Mom asked what's being talked about most on Reddit. This is that, and it is
context rather than a signal -- it never touches the score. A stock trending on
Reddit is a stock whose implied volatility is about to be expensive for a reason
that has nothing to do with its margins.

The API is free and unauthenticated. It counts mentions across the main
investing subreddits over a rolling 24 hours, alongside the previous 24 so the
direction is visible.
"""

from __future__ import annotations

import html
import logging

import requests

log = logging.getLogger(__name__)

API = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/{page}"
PAGES = 3  # ~300 tickers, well past anything our screen would surface


def fetch(pages: int = PAGES, timeout: int = 20) -> dict[str, dict]:
    """Mention counts keyed by ticker. Missing pages are skipped, not fatal."""
    out: dict[str, dict] = {}
    for page in range(1, pages + 1):
        try:
            response = requests.get(API.format(page=page), timeout=timeout)
            response.raise_for_status()
            results = response.json().get("results", [])
        except (requests.RequestException, ValueError) as exc:
            log.warning("reddit buzz page %d failed (%s)", page, exc)
            continue

        for item in results:
            ticker = item.get("ticker")
            if not ticker:
                continue
            mentions = int(item.get("mentions") or 0)
            before = int(item.get("mentions_24h_ago") or 0)
            out[ticker] = {
                # Carried in the value too: `top()` returns a bare list, and
                # without this the page has counts with nothing to label them.
                "ticker": ticker,
                "rank": item.get("rank"),
                "name": html.unescape(item.get("name") or ""),
                "mentions": mentions,
                "mentions_24h_ago": before,
                "upvotes": int(item.get("upvotes") or 0),
                "rank_24h_ago": item.get("rank_24h_ago"),
                "mention_change": None if not before else round(mentions / before - 1.0, 3),
            }
    return out


def top(mentions: dict[str, dict], limit: int = 15) -> list[dict]:
    """The most-discussed names, for the sidebar panel."""
    ranked = sorted(mentions.values(), key=lambda item: item.get("rank") or 10**6)
    return ranked[:limit]
