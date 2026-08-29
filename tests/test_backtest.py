"""The parts of the backtest that decide what a number means.

Nothing here fetches or reads the price cache. What is worth pinning is not
whether pandas can slice a frame -- it is the arithmetic that turns a run into
a claim: how much of the shipped model a variant actually reconstructs, how
long a position is held, and whether the summary can hide a bad result behind
a good average.
"""

import pandas as pd
import pytest
import yaml

from screener import score
from tools import backtest as b


@pytest.fixture(scope="module")
def config():
    with open("config.yaml", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def position(symbol="AAA", ret=0.0, drawdown=0.0, downtrend=False, **scores):
    """One measured position, in the shape run_one puts in the pool."""
    return {
        "symbol": symbol, "downtrend": downtrend, "rsi_recent": 30.0,
        "scores": {name: scores.get(name, 50.0) for name in b.VARIANTS},
        "spot": 100.0, "strike": 90.0, "pct_below": 0.10,
        "settle": 100.0 * (1 + ret), "assigned": False, "touched": False,
        "stock_return": ret, "max_drawdown": drawdown, "return_since": ret,
        "bars_ahead": 20,
    }


class TestHorizon:
    def test_the_put_reads_the_life_of_its_own_option(self, config):
        """Not written down twice: `target_dte` is the number that decides both
        which contract is picked and how long the test runs."""
        assert b.horizon(config, "put") == config["option"]["target_dte"]

    def test_the_two_horizons_are_actually_different(self, config):
        """The whole reason for two lists. If they matched, LONG would be BUY
        with different weights and the second toggle would be decoration."""
        assert b.horizon(config, "long") > b.horizon(config, "buy") * 3

    def test_every_profile_that_can_be_run_has_one(self, config):
        for profile in b.PROFILE_VARIANTS:
            assert b.horizon(config, profile) > 0


class TestVariants:
    def test_every_variant_names_a_real_profile_and_real_components(self, config):
        for name, (profile, parts) in b.VARIANTS.items():
            assert profile in score.PROFILES, name
            weights = config[score.PROFILES[profile]]
            for component, _, share in parts:
                assert component in weights, f"{name}: {component}"
                assert 0 < share <= 1.0, f"{name}: {component}"

    def test_every_run_reports_what_it_could_not_see(self, config):
        """A variant claiming 100 points would mean the fundamentals came back,
        and they did not -- Yahoo caps quarterly revenue at five quarters. If
        this ever passes at 100, the docstring is lying about look-ahead."""
        for profile, variants in b.PROFILE_VARIANTS.items():
            for name in variants:
                points = b.recoverable_points(config, name)
                assert 0 < points < 100, f"{name} claims {points}"

    def test_the_long_run_reconstructs_only_half_its_model(self, config):
        """Stated in the docstring and worth pinning: revenue and margins are
        half of that ranking, so the run measures the chart alone."""
        assert b.recoverable_points(config, "long") == pytest.approx(50)

    def test_a_score_is_renormalised_to_100(self, config):
        """Fewer parts, same scale -- otherwise a variant built from half the
        model would look like half a score rather than a ranking."""
        cfg = config["scoring"]
        perfect = {"tech": {"above_ema200": True, "golden_cross": True,
                            "full_stack": True, "golden_cross_days_ago": 0,
                            "pct_below_52w_high": cfg["room_ideal_below_high"]},
                   "fund": None}
        assert b.variant_score(perfect, config, "long") == pytest.approx(100.0)

    def test_the_put_variants_did_not_move(self, config):
        """Published numbers hang off these two. Adding profiles beside them
        must not quietly rescale what method.html already reports."""
        assert b.recoverable_points(config, "technical") == pytest.approx(35)
        assert b.recoverable_points(config, "enriched") == pytest.approx(49)


class TestTheCallVariant:
    """The fourth run, and the one with the most missing from it: half its vol
    reading, all of its revenue, and the contract it is named for."""

    def test_it_reconstructs_the_chart_the_timing_and_half_the_vol(self, config):
        assert b.recoverable_points(config, "call") == pytest.approx(57.5)

    def test_it_sees_neither_the_contract_nor_the_revenue(self, config):
        """Option quotes are unrecoverable and today's revenue would be
        look-ahead, so both are dropped rather than faked -- which is 30 of the
        100 points this ranking actually ships with."""
        _, parts = b.VARIANTS["call"]
        named = {name for name, _, _ in parts}
        assert "contract_quality" not in named
        assert "revenue_expanding" not in named

    def test_cheapness_is_the_premium_proxy_turned_around(self, config):
        """The same reading from the other side of the trade, here as much as
        in the shipped model -- otherwise the backtest would be measuring a
        ranking the app does not have."""
        row = {"hv_percentile": 80.0, "tech": {}}
        assert (b._cheapness_proxy(row, config["scoring"])
                == pytest.approx(1 - b._premium_proxy(row, config["scoring"])))

    def test_an_unmeasured_name_is_not_called_cheap(self, config):
        """The premium proxy reads an unknown percentile as zero, and inverting
        that would hand every name with too little history full credit for
        being cheap. Withheld the same way the shipped component withholds it.
        """
        assert b._cheapness_proxy({"hv_percentile": None, "tech": {}},
                                  config["scoring"]) == 0.4


class TestTopN:
    def test_ranks_on_the_named_variant(self):
        pool = [position("LOW", buy=10.0), position("HIGH", buy=90.0)]
        assert [r["symbol"] for r in b.top_n(pool, "buy", 2)] == ["HIGH", "LOW"]

    def test_ties_break_on_the_washout_not_the_alphabet(self):
        deep = dict(position("ZZZ", buy=50.0), rsi_recent=12.0)
        shallow = dict(position("AAA", buy=50.0), rsi_recent=40.0)
        assert [r["symbol"] for r in b.top_n([shallow, deep], "buy", 2)] == ["ZZZ", "AAA"]

    def test_the_gate_removes_knives_before_the_cut_not_after(self):
        """Removing them after would leave a short list. The point of the A/B is
        that both sides pick the same number of names."""
        pool = [position("K%d" % i, buy=90.0, downtrend=True) for i in range(3)]
        pool += [position("G%d" % i, buy=10.0) for i in range(3)]
        assert len(b.top_n(pool, "buy", 3, drop_downtrends=True)) == 3
        assert all(r["symbol"].startswith("G")
                   for r in b.top_n(pool, "buy", 3, drop_downtrends=True))

    def test_without_the_gate_the_knives_win(self):
        pool = [position("K", buy=90.0, downtrend=True), position("G", buy=10.0)]
        assert b.top_n(pool, "buy", 1)[0]["symbol"] == "K"


class TestSummarise:
    def test_an_empty_set_is_not_a_crash(self):
        assert b.summarise([]) == {"n": 0}

    def test_the_average_cannot_hide_the_spread(self):
        """The failure the accuracy report exists for: +12% built from one +80%
        and nine -3% is not a screen that works, and the mean cannot say so."""
        rows = [position(ret=0.80)] + [position(ret=-0.03) for _ in range(9)]
        s = b.summarise(rows)
        assert s["avg_return"] > 0
        assert s["median_return"] < 0
        assert s["ended_down_pct"] == pytest.approx(0.9)

    def test_counts_how_far_names_fell_inside_the_window(self):
        rows = [position(drawdown=-0.05), position(drawdown=-0.15),
                position(drawdown=-0.35)]
        s = b.summarise(rows)
        assert s["fell_10_pct"] == pytest.approx(2 / 3)
        assert s["fell_20_pct"] == pytest.approx(1 / 3)
        assert s["worst_drawdown"] == pytest.approx(-0.35)

    def test_a_name_that_ended_flat_after_falling_still_counts_as_having_fallen(self):
        """Where it ended and how far it went are different questions, and only
        the second one describes what she would have watched."""
        s = b.summarise([position(ret=0.0, drawdown=-0.30)])
        assert s["ended_down_pct"] == 0.0
        assert s["fell_20_pct"] == 1.0

    def test_counts_the_knives_it_was_handed(self):
        s = b.summarise([position(downtrend=True), position()])
        assert s["downtrends"] == 1


class TestEntryDates:
    """A longer hold has to end before the data does, or the last entries are
    measured over a window that has not closed."""

    @staticmethod
    def _histories(years=5):
        index = pd.bdate_range(end="2026-08-28", periods=int(years * 252))
        frame = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0,
                              "close": 1.0, "volume": 1.0}, index=index)
        return {b.BENCHMARK: frame}

    def test_a_longer_horizon_gives_fewer_entries(self, config):
        histories = self._histories()
        short = b.entry_dates(histories, config, True, [], "buy")
        long_ = b.entry_dates(histories, config, True, [], "long")
        assert len(short) > len(long_)

    def test_five_years_leaves_enough_long_entries_to_read(self, config):
        """3y minus the 250-bar warmup minus a 180-day window left about 18,
        which is why the fetch asks for 5y."""
        assert len(b.entry_dates(self._histories(), config, True, [], "long")) >= 35

    def test_no_entry_runs_off_the_end_of_the_data(self, config):
        histories = self._histories()
        last = histories[b.BENCHMARK].index[-1]
        for profile in b.PROFILE_VARIANTS:
            days = b.horizon(config, profile)
            for when in b.entry_dates(histories, config, True, [], profile):
                assert when + pd.Timedelta(days=days) <= last, profile

    def test_every_entry_has_the_warmup_behind_it(self, config):
        histories = self._histories()
        first = histories[b.BENCHMARK].index[b.MIN_BARS - 1]
        for when in b.entry_dates(histories, config, True, [], "buy"):
            assert when >= first
