"""Strike selection and the arithmetic on the trade block.

The numbers here end up on the page as a concrete suggestion, so they get
checked by hand rather than against the implementation.
"""

from datetime import date

import pytest

from screener import options
from screener.options import Chain, Contract

TODAY = date(2026, 8, 26)

CONFIG = {
    "option": {
        "target_dte": 35,
        "min_dte": 21,
        "max_dte": 56,
        "target_delta": 0.20,
        "min_delta": 0.10,
        "max_delta": 0.35,
        "min_bid": 0.20,
        "min_open_interest": 100,
        "max_spread_pct": 0.15,
    }
}


def put(strike, delta, bid, ask, expiry, oi=500, iv=0.40, volume=10):
    return Contract(
        expiry=expiry, strike=strike, bid=bid, ask=ask, iv=iv,
        delta=-abs(delta), open_interest=oi, volume=volume,
    )


class TestFetchChain:
    """CBOE throttles. A swallowed 429 is indistinguishable from "no options
    listed", which is how a #1-ranked name vanished between two runs."""

    CHAIN = {"data": {"current_price": 100.0, "options": [
        {"option": "TEST261016P00090000", "bid": 2.0, "ask": 2.2,
         "iv": 0.4, "delta": -0.2, "open_interest": 500, "volume": 10},
    ]}}

    def _responses(self, monkeypatch, statuses):
        calls, slept = [], []

        class Response:
            def __init__(self, status):
                self.status_code = status

            def json(self):
                return TestFetchChain.CHAIN

        def get(url, **kwargs):
            calls.append(url)
            return Response(statuses[len(calls) - 1])

        monkeypatch.setattr(options.requests, "get", get)
        monkeypatch.setattr(options.time, "sleep", slept.append)
        # Module-level pacing state leaks between tests otherwise.
        monkeypatch.setattr(options, "_blocked_until", 0.0)
        monkeypatch.setattr(options, "_last_request", 0.0)
        self.slept = slept
        return calls

    def test_a_refusal_holds_every_thread_not_just_this_one(self, monkeypatch):
        """The budget is per-IP: backing off only the caller lets the other
        workers keep it drained, and the window never clears."""
        self._responses(monkeypatch, [429, 200])
        options.fetch_chain("TEST")
        assert options._blocked_until > 0

    def test_retries_through_throttling(self, monkeypatch):
        calls = self._responses(monkeypatch, [429, 429, 200])
        chain = options.fetch_chain("TEST")
        assert chain is not None and chain.spot == 100.0
        assert len(calls) == 3

    def test_gives_up_after_the_retry_budget(self, monkeypatch):
        calls = self._responses(monkeypatch, [429] * 6)
        assert options.fetch_chain("TEST", max_retries=4) is None
        assert len(calls) == 4

    def test_a_real_404_does_not_burn_retries(self, monkeypatch):
        """No options listed is an answer, not a failure to get one."""
        calls = self._responses(monkeypatch, [404, 200])
        assert options.fetch_chain("TEST") is None
        assert len(calls) == 1


class TestParseOcc:
    def test_parses_real_symbols(self):
        assert options.parse_occ("AAPL260828P00295000") == (date(2026, 8, 28), "P", 295.0)
        assert options.parse_occ("A261016C00150000") == (date(2026, 10, 16), "C", 150.0)
        assert options.parse_occ("GOOGL261016P00180000") == (date(2026, 10, 16), "P", 180.0)

    def test_handles_fractional_strikes(self):
        _, _, strike = options.parse_occ("SOFI261002P00016500")
        assert strike == 16.5

    def test_rejects_garbage(self):
        assert options.parse_occ("") is None
        assert options.parse_occ("AAPL") is None
        assert options.parse_occ("AAPL261301P00295000") is None  # month 13


class TestSelectPut:
    def test_picks_delta_closest_to_target(self):
        expiry = date(2026, 9, 30)  # 35 DTE
        chain = Chain("TEST", 100.0, [
            put(95, 0.30, 1.00, 1.10, expiry),
            put(90, 0.19, 0.60, 0.66, expiry),  # closest to 0.20
            put(85, 0.11, 0.30, 0.33, expiry),
        ])
        assert options.select_put(chain, CONFIG, TODAY)["strike"] == 90

    def test_skips_illiquid_expiry_for_a_liquid_one_further_out(self):
        """The ELF case: a newly listed weekly sits nearer the target DTE than the
        monthly but has no open interest and a 30%-wide market."""
        weekly = date(2026, 10, 2)   # 37 DTE -- nearer the 35-day target
        monthly = date(2026, 10, 16)  # 51 DTE -- where the liquidity actually is
        chain = Chain("TEST", 100.0, [
            put(90, 0.20, 2.00, 3.10, weekly, oi=0),
            put(90, 0.22, 3.00, 3.20, monthly, oi=1141),
        ])
        chosen = options.select_put(chain, CONFIG, TODAY)
        assert chosen["expiry"] == "2026-10-16"
        assert chosen["open_interest"] == 1141

    def test_prefers_target_dte_when_both_are_liquid(self):
        weekly, monthly = date(2026, 10, 2), date(2026, 10, 16)
        chain = Chain("TEST", 100.0, [
            put(90, 0.20, 3.00, 3.20, weekly),
            put(90, 0.20, 3.00, 3.20, monthly),
        ])
        assert options.select_put(chain, CONFIG, TODAY)["expiry"] == "2026-10-02"

    @pytest.mark.parametrize(
        "contract, why",
        [
            (put(90, 0.20, 0.10, 0.12, date(2026, 9, 30)), "bid below the floor"),
            (put(90, 0.20, 1.00, 1.10, date(2026, 9, 30), oi=5), "open interest too low"),
            (put(90, 0.20, 1.00, 1.60, date(2026, 9, 30)), "spread too wide"),
            (put(90, 0.05, 1.00, 1.10, date(2026, 9, 30)), "delta below the band"),
            (put(90, 0.60, 1.00, 1.10, date(2026, 9, 30)), "delta above the band"),
            (put(90, 0.20, 1.00, 1.10, date(2026, 9, 1)), "expiry too near"),
            (put(90, 0.20, 1.00, 1.10, date(2027, 1, 15)), "expiry too far"),
        ],
    )
    def test_rejects_untradeable_contracts(self, contract, why):
        assert options.select_put(Chain("TEST", 100.0, [contract]), CONFIG, TODAY) is None, why

    def test_never_suggests_an_in_the_money_put(self):
        expiry = date(2026, 9, 30)
        chain = Chain("TEST", 100.0, [put(105, 0.20, 6.00, 6.20, expiry)])
        assert options.select_put(chain, CONFIG, TODAY) is None

    def test_empty_chain(self):
        assert options.select_put(Chain("TEST", 100.0, []), CONFIG, TODAY) is None


class TestTradeMath:
    """Hand-computed against a $90 put at 35 DTE, bid 2.00 / ask 2.20."""

    @pytest.fixture
    def trade(self):
        chain = Chain("TEST", 100.0, [put(90, 0.22, 2.00, 2.20, date(2026, 9, 30))])
        return options.select_put(chain, CONFIG, TODAY)

    def test_credit_is_the_mid(self, trade):
        assert trade["credit"] == pytest.approx(2.10)

    def test_cash_secured_is_a_hundred_shares_at_the_strike(self, trade):
        assert trade["cash_secured"] == pytest.approx(9000.0)

    def test_return_is_credit_over_cash_at_risk(self, trade):
        assert trade["return_pct"] == pytest.approx(2.10 / 90, abs=1e-5)

    def test_annualized_scales_the_return_by_the_year(self, trade):
        assert trade["annualized_pct"] == pytest.approx((2.10 / 90) * 365 / 35, abs=1e-4)

    def test_breakeven_is_the_strike_less_the_credit(self, trade):
        assert trade["breakeven"] == pytest.approx(87.90)

    def test_keep_premium_odds_complement_delta(self, trade):
        assert trade["delta"] + trade["keep_premium_odds"] == pytest.approx(1.0)

    def test_distance_below_spot(self, trade):
        assert trade["pct_below_spot"] == pytest.approx(0.10)

    def test_spread_is_measured_against_the_mid(self, trade):
        assert trade["spread_pct"] == pytest.approx(0.20 / 2.10, abs=1e-4)


class TestAtmIv:
    def test_uses_the_strike_nearest_spot(self):
        expiry = date(2026, 9, 30)
        chain = Chain("TEST", 100.0, [
            put(100, 0.50, 4.0, 4.2, expiry, iv=0.30),  # at the money
            put(80, 0.10, 0.5, 0.6, expiry, iv=0.55),   # skewed, would overstate vol
        ])
        assert options.atm_iv(chain, TODAY) == pytest.approx(0.30)

    def test_none_when_no_expiry_in_range(self):
        chain = Chain("TEST", 100.0, [put(100, 0.5, 4.0, 4.2, date(2027, 6, 1))])
        assert options.atm_iv(chain, TODAY) is None
