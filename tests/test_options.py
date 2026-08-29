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
    },
    "call": {
        "target_dte": 90,
        "min_dte": 60,
        "max_dte": 135,
        "target_delta": 0.65,
        "min_delta": 0.50,
        "max_delta": 0.80,
        "min_bid": 0.50,
        "min_open_interest": 50,
        "max_spread_pct": 0.12,
    },
}


def put(strike, delta, bid, ask, expiry, oi=500, iv=0.40, volume=10):
    return Contract(
        expiry=expiry, strike=strike, bid=bid, ask=ask, iv=iv,
        delta=-abs(delta), open_interest=oi, volume=volume,
    )


def call(strike, delta, bid, ask, expiry, oi=500, iv=0.40, volume=10):
    """Same contract, the other right. CBOE signs call deltas positive."""
    return Contract(
        expiry=expiry, strike=strike, bid=bid, ask=ask, iv=iv,
        delta=abs(delta), open_interest=oi, volume=volume,
    )


def chain(puts=(), calls=(), spot=100.0):
    """A chain shaped the way fetch_chain returns one: two sides, either of
    which can be empty."""
    return Chain("TEST", spot, list(puts), list(calls))


class TestFetchChain:
    """CBOE throttles. A swallowed 429 is indistinguishable from "no options
    listed", which is how a #1-ranked name vanished between two runs."""

    CHAIN = {"data": {"current_price": 100.0, "options": [
        {"option": "TEST261016P00090000", "bid": 2.0, "ask": 2.2,
         "iv": 0.4, "delta": -0.2, "open_interest": 500, "volume": 10},
        {"option": "TEST261120C00090000", "bid": 12.0, "ask": 12.5,
         "iv": 0.4, "delta": 0.7, "open_interest": 500, "volume": 10},
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


class TestBothSides:
    """One download, two rights. The parser used to drop the calls at the door,
    which meant the call ranking could not exist without a second fetch."""

    def test_keeps_the_calls_as_well_as_the_puts(self, monkeypatch):
        monkeypatch.setattr(options, "_wait_turn", lambda: None)

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return TestFetchChain.CHAIN

        monkeypatch.setattr(options.requests, "get", lambda *a, **k: Response())
        got = options.fetch_chain("TEST")
        assert [c.strike for c in got.puts] == [90.0]
        assert [c.strike for c in got.calls] == [90.0]
        assert got.calls[0].delta == 0.7


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
        board = chain([
            put(95, 0.30, 1.00, 1.10, expiry),
            put(90, 0.19, 0.60, 0.66, expiry),  # closest to 0.20
            put(85, 0.11, 0.30, 0.33, expiry),
        ])
        assert options.select_put(board, CONFIG, TODAY)["strike"] == 90

    def test_skips_illiquid_expiry_for_a_liquid_one_further_out(self):
        """The ELF case: a newly listed weekly sits nearer the target DTE than the
        monthly but has no open interest and a 30%-wide market."""
        weekly = date(2026, 10, 2)   # 37 DTE -- nearer the 35-day target
        monthly = date(2026, 10, 16)  # 51 DTE -- where the liquidity actually is
        board = chain([
            put(90, 0.20, 2.00, 3.10, weekly, oi=0),
            put(90, 0.22, 3.00, 3.20, monthly, oi=1141),
        ])
        chosen = options.select_put(board, CONFIG, TODAY)
        assert chosen["expiry"] == "2026-10-16"
        assert chosen["open_interest"] == 1141

    def test_prefers_target_dte_when_both_are_liquid(self):
        weekly, monthly = date(2026, 10, 2), date(2026, 10, 16)
        board = chain([
            put(90, 0.20, 3.00, 3.20, weekly),
            put(90, 0.20, 3.00, 3.20, monthly),
        ])
        assert options.select_put(board, CONFIG, TODAY)["expiry"] == "2026-10-02"

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
        assert options.select_put(chain([contract]), CONFIG, TODAY) is None, why

    def test_never_suggests_an_in_the_money_put(self):
        expiry = date(2026, 9, 30)
        board = chain([put(105, 0.20, 6.00, 6.20, expiry)])
        assert options.select_put(board, CONFIG, TODAY) is None

    def test_empty_chain(self):
        assert options.select_put(chain([]), CONFIG, TODAY) is None


class TestTradeMath:
    """Hand-computed against a $90 put at 35 DTE, bid 2.00 / ask 2.20."""

    @pytest.fixture
    def trade(self):
        board = chain([put(90, 0.22, 2.00, 2.20, date(2026, 9, 30))])
        return options.select_put(board, CONFIG, TODAY)

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


class TestSelectCall:
    """Deep enough in the money that most of what she pays is intrinsic. The
    cheap out-of-the-money call is the one that expires worthless, and it is
    the one this deliberately does not pick."""

    NEAR = date(2026, 11, 20)   # 86 days out -- closest to the 90-day target
    FAR = date(2026, 12, 18)    # 114

    def test_picks_delta_closest_to_target(self):
        got = options.select_call(chain(calls=[
            call(80, 0.79, 21.0, 21.5, self.NEAR),
            call(95, 0.65, 8.0, 8.4, self.NEAR),
            call(99, 0.55, 5.0, 5.2, self.NEAR),
        ]), CONFIG, TODAY)
        assert got["strike"] == 95

    def test_prefers_target_dte_when_both_are_liquid(self):
        got = options.select_call(chain(calls=[
            call(95, 0.65, 8.0, 8.4, self.NEAR),
            call(95, 0.65, 9.0, 9.4, self.FAR),
        ]), CONFIG, TODAY)
        assert got["expiry"] == self.NEAR.isoformat()

    def test_takes_the_strike_sitting_exactly_at_spot(self):
        """The most liquid contract on the board, and no worse for being
        borderline. The put rule is the opposite and stays that way."""
        got = options.select_call(chain(calls=[
            call(100, 0.55, 5.0, 5.2, self.NEAR)]), CONFIG, TODAY)
        assert got["strike"] == 100

    def test_never_suggests_a_call_above_spot(self):
        assert options.select_call(chain(calls=[
            call(105, 0.55, 3.0, 3.2, self.NEAR)]), CONFIG, TODAY) is None

    @pytest.mark.parametrize("contract, why", [
        (call(95, 0.65, 0.30, 0.34, NEAR), "bid below the floor"),
        (call(95, 0.65, 8.0, 8.4, NEAR, oi=20), "open interest too low"),
        (call(95, 0.65, 7.5, 8.9, NEAR), "spread too wide"),
        (call(95, 0.40, 8.0, 8.4, NEAR), "delta below the band"),
        (call(95, 0.90, 8.0, 8.4, NEAR), "delta above the band"),
        (call(95, 0.65, 8.0, 8.4, date(2026, 9, 30)), "expiry too near"),
        (call(95, 0.65, 8.0, 8.4, date(2027, 3, 19)), "expiry too far"),
    ])
    def test_rejects_unbuyable_contracts(self, contract, why):
        assert options.select_call(chain(calls=[contract]), CONFIG, TODAY) is None, why

    def test_a_chain_with_puts_and_no_calls(self):
        """Common, not exceptional: the long-dated board is far thinner than
        the 35-day one, and the other rankings still want the name."""
        got = chain(puts=[put(90, 0.22, 2.0, 2.2, date(2026, 9, 30))])
        assert options.select_call(got, CONFIG, TODAY) is None
        assert options.select_put(got, CONFIG, TODAY) is not None


class TestCallMath:
    """What she pays, and how far the stock has to move. Checked by hand --
    none of it is the put arithmetic with the signs flipped."""

    @pytest.fixture
    def bought(self):
        return options.select_call(chain(calls=[
            call(90, 0.70, 12.0, 12.5, date(2026, 11, 20))]), CONFIG, TODAY)

    def test_cost_is_the_mid(self, bought):
        assert bought["cost"] == 12.25

    def test_outlay_is_a_hundred_shares_worth(self, bought):
        assert bought["outlay"] == 1225.0

    def test_intrinsic_is_what_it_is_already_worth(self, bought):
        assert bought["intrinsic"] == 10.0

    def test_time_value_is_the_rest_of_the_price(self, bought):
        assert bought["time_value"] == 2.25

    def test_time_value_share_is_what_decays_to_nothing(self, bought):
        """The number the 0.65 delta was chosen for, and the one that turned
        out not to behave as planned on a high-IV universe. Published on every
        row so the page can say so rather than assume it."""
        assert bought["time_value_share"] == round(2.25 / 12.25, 4)

    def test_breakeven_is_the_strike_plus_what_she_paid(self, bought):
        assert bought["breakeven"] == 102.25

    def test_pct_to_breakeven_is_how_far_the_stock_must_rise(self, bought):
        assert bought["pct_to_breakeven"] == 0.0225

    def test_shares_equivalent_is_the_denominator(self, bought):
        assert bought["shares_equivalent"] == 10000.0

    def test_it_carries_no_ladder(self, bought):
        """The put has alternatives because the strike is the dial that decides
        assignment. On a call the strike sets leverage, and offering her more
        leverage is not a safety control."""
        assert "alternatives" not in bought


class TestAtmIv:
    def test_uses_the_strike_nearest_spot(self):
        expiry = date(2026, 9, 30)
        board = chain([
            put(100, 0.50, 4.0, 4.2, expiry, iv=0.30),  # at the money
            put(80, 0.10, 0.5, 0.6, expiry, iv=0.55),   # skewed, would overstate vol
        ])
        assert options.atm_iv(board, TODAY) == pytest.approx(0.30)

    def test_none_when_no_expiry_in_range(self):
        board = chain([put(100, 0.5, 4.0, 4.2, date(2027, 6, 1))])
        assert options.atm_iv(board, TODAY) is None
