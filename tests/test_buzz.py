"""Reddit mention parsing, against a recorded response."""

from unittest.mock import patch

from screener import buzz

# Trimmed from a real ApeWisdom response.
PAGE = {
    "count": 962,
    "pages": 10,
    "results": [
        {"rank": 1, "ticker": "NVDA", "name": "NVIDIA", "mentions": 1195,
         "upvotes": 3456, "rank_24h_ago": 1, "mentions_24h_ago": 325},
        {"rank": 2, "ticker": "META", "name": "Meta Platforms (Facebook)", "mentions": 251,
         "upvotes": 1331, "rank_24h_ago": 10, "mentions_24h_ago": 37},
        {"rank": 3, "ticker": "SPY", "name": "SPDR S&amp;P 500 ETF Trust", "mentions": 208,
         "upvotes": 466, "rank_24h_ago": 2, "mentions_24h_ago": 180},
        {"rank": 4, "ticker": "MU", "name": "Micron Technology", "mentions": 98,
         "upvotes": 218, "rank_24h_ago": 3, "mentions_24h_ago": 121},
    ],
}


class FakeResponse:
    def __init__(self, payload=None, error=None):
        self._payload, self._error = payload, error

    def raise_for_status(self):
        if self._error:
            raise self._error

    def json(self):
        return self._payload


def fake_get(payload=PAGE, error=None):
    return patch("screener.buzz.requests.get", return_value=FakeResponse(payload, error))


class TestFetch:
    def test_keys_by_ticker(self):
        with fake_get():
            result = buzz.fetch(pages=1)
        assert set(result) == {"NVDA", "META", "SPY", "MU"}
        assert result["NVDA"]["mentions"] == 1195

    def test_computes_the_direction_of_the_change(self):
        with fake_get():
            result = buzz.fetch(pages=1)
        assert result["META"]["mention_change"] > 5  # 37 -> 251, a real spike
        assert result["MU"]["mention_change"] < 0    # 121 -> 98, cooling off

    def test_unescapes_html_in_names(self):
        with fake_get():
            result = buzz.fetch(pages=1)
        assert result["SPY"]["name"] == "SPDR S&P 500 ETF Trust"

    def test_no_prior_mentions_is_none_not_a_divide_by_zero(self):
        payload = {"results": [{"rank": 1, "ticker": "NEW", "name": "New",
                                "mentions": 40, "mentions_24h_ago": 0, "upvotes": 5}]}
        with fake_get(payload):
            assert buzz.fetch(pages=1)["NEW"]["mention_change"] is None

    def test_a_failed_page_is_skipped_not_fatal(self):
        import requests

        with fake_get(error=requests.RequestException("503")):
            assert buzz.fetch(pages=1) == {}

    def test_rows_without_a_ticker_are_ignored(self):
        with fake_get({"results": [{"rank": 1, "name": "no ticker", "mentions": 5}]}):
            assert buzz.fetch(pages=1) == {}


class TestTop:
    def test_orders_by_rank(self):
        with fake_get():
            ordered = buzz.top(buzz.fetch(pages=1), limit=3)
        assert [item["ticker"] for item in ordered] == ["NVDA", "META", "SPY"]

    def test_respects_the_limit(self):
        with fake_get():
            assert len(buzz.top(buzz.fetch(pages=1), limit=2)) == 2
