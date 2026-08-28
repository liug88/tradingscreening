"""Gates, weights and penalties.

The point of these tests is that the ranking stays explainable: a name that
scores well should score well for a reason someone can point at.
"""

import copy

import pytest
import yaml

from screener import score


@pytest.fixture(scope="module")
def config():
    """The real config -- so a bad edit to config.yaml fails these tests."""
    with open("config.yaml") as handle:
        return yaml.safe_load(handle)


def make_row(**overrides):
    """A middling candidate. Tests move one thing at a time off this baseline."""
    row = {
        "symbol": "TEST",
        "market_cap": 20e9,
        "iv_hv": 1.2,
        "iv_percentile": None,
        "tech": {
            "close": 100.0, "rsi14": 33.0, "williams_r14": -85.0,
            "macd_cross_up": True, "macd_below_zero": True,
            "above_ema9": True, "above_ema20": False, "above_ema200": True,
            "up_day_volume_expansion": True, "atr14": 3.0,
            "avg_volume_30d": 2_000_000, "support_60d": 92.0,
            "pct_above_52w_low": 0.08, "at_52w_low": False, "change_5d": -0.03,
        },
        "fund": {
            "revenue_yoy": 0.14, "revenue_qoq": 0.03,
            "gross_margin_change": 0.008, "operating_margin_change": 0.004,
            "next_earnings": "2026-12-01",
        },
        "trade": {
            "strike": 90.0, "breakeven": 87.9, "expiry": "2026-09-30",
            "annualized_pct": 0.24, "spread_pct": 0.05,
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(row.get(key), dict):
            row[key] = {**row[key], **value}
        else:
            row[key] = value
    return row


class TestRamp:
    def test_clamps_at_both_ends(self):
        assert score._ramp(5, 0, 10) == 0.5
        assert score._ramp(-5, 0, 10) == 0.0
        assert score._ramp(50, 0, 10) == 1.0

    def test_works_descending(self):
        """Williams %R and RSI both want 'lower is better'."""
        assert score._ramp(-80, -50, -80) == 1.0
        assert score._ramp(-50, -50, -80) == 0.0
        assert score._ramp(-65, -50, -80) == pytest.approx(0.5)

    def test_missing_value_scores_zero(self):
        assert score._ramp(None, 0, 10) == 0.0


class TestGates:
    def test_a_good_candidate_passes(self, config):
        assert score.check_gates(make_row(), config) == []

    @pytest.mark.parametrize(
        "overrides, expected",
        [
            ({"tech": {"close": 4.0}}, "price"),
            ({"tech": {"avg_volume_30d": 100_000}}, "volume"),
            ({"market_cap": 200e6}, "market cap"),
            ({"trade": None}, "no put"),
            ({"tech": {"rsi14": 82.9}}, "already overbought"),
            ({"tech": {"rsi14": 55.0, "rsi_min_recent": 54.0}}, "never oversold"),
        ],
    )
    def test_each_gate_rejects(self, config, overrides, expected):
        failures = score.check_gates(make_row(**overrides), config)
        assert any(expected in f for f in failures), failures

    def test_an_overbought_stock_never_reaches_the_list(self, config):
        """The ASST case: rich premium and good fundamentals ranked it second
        while RSI sat at 82.9. Premium is not a reason to sell a put on a stock
        that has already run.

        Its 10-day RSI minimum was 48.9 -- genuinely oversold last week -- so the
        recent-low half of the gate passes it. Today's reading is what stops it."""
        overbought = make_row(tech={"rsi14": 82.9, "rsi_min_recent": 48.9,
                                    "williams_r14": -6.4})
        assert score.check_gates(overbought, config) != []

    def test_a_name_that_has_already_bounced_is_still_allowed(self, config):
        """The BE case, and the reason the gate is two-sided. Bloom Energy washed
        out to RSI 42.6, reclaimed both short EMAs, and read 49.4 today -- the
        exact setup being screened for, one green session from a 50 ceiling."""
        bouncing = make_row(tech={"rsi14": 49.4, "rsi_min_recent": 42.6,
                                  "williams_r14": -52.1, "williams_r_min_recent": -90.0})
        assert score.check_gates(bouncing, config) == []

    def test_unknown_market_cap_is_not_a_rejection(self, config):
        """Yahoo occasionally omits it; that shouldn't drop an otherwise fine name."""
        assert score.check_gates(make_row(market_cap=None), config) == []


CONFIRMING = {
    "above_ema9": True, "above_ema20": True,
    "macd_cross_up": True, "macd_below_zero": True,
    "up_day_volume_expansion": True,
}

# make_row merges tech overrides into a baseline that already bounces, so a
# test about an unconfirmed name has to switch the evidence off by hand.
NO_TURN = {
    "above_ema9": False, "above_ema20": False,
    "macd_cross_up": False, "up_day_volume_expansion": False,
}


class TestOversold:
    def test_peaks_inside_the_rsi_band(self, config):
        row = make_row(tech={"rsi14": 33.0, "williams_r14": -90.0, **CONFIRMING})
        assert score._oversold(row, config["scoring"]) == pytest.approx(1.0)

    def test_overbought_scores_zero(self, config):
        row = make_row(tech={"rsi14": 65.0, "williams_r14": -10.0})
        assert score._oversold(row, config["scoring"]) == 0.0

    def test_deeply_oversold_scores_below_the_sweet_spot(self, config):
        """RSI 12 is a stock in freefall, not a stock on sale."""
        cfg = config["scoring"]
        extreme = score._oversold(make_row(tech={"rsi14": 12.0, "williams_r14": -95.0}), cfg)
        ideal = score._oversold(make_row(tech={"rsi14": 33.0, "williams_r14": -95.0}), cfg)
        assert extreme < ideal


class TestOversoldAndBounceTogether:
    """The setup is 'oversold BUT bouncing'. Scored on today's RSI alone the two
    halves are opposed -- reclaiming the 9-day EMA drags RSI back toward 50 --
    so the screen surfaced only names that were still falling."""

    def _turning(self):
        """Washed out to RSI 29 last week, now back above its short EMAs."""
        return make_row(tech={
            "rsi14": 46.0, "rsi_min_recent": 29.0,
            "williams_r14": -40.0, "williams_r_min_recent": -94.0,
            "above_ema9": True, "above_ema20": True,
            "macd_cross_up": True, "macd_below_zero": True,
            "up_day_volume_expansion": True,
        })

    def _still_falling(self):
        """Deeply oversold today and showing no sign of turning."""
        return make_row(tech={
            "rsi14": 31.0, "rsi_min_recent": 31.0,
            "williams_r14": -95.0, "williams_r_min_recent": -95.0,
            "above_ema9": False, "above_ema20": False,
            "macd_cross_up": False, "macd_below_zero": True,
            "up_day_volume_expansion": False,
        })

    def test_recent_washout_still_counts_as_oversold(self, config):
        cfg = config["scoring"]
        assert score._oversold(self._turning(), cfg) > 0.9

    def test_a_name_can_now_score_on_both_halves(self, config):
        row = self._turning()
        assert score._oversold(row, config["scoring"]) > 0.9
        assert score._bounce(row, config["scoring"]) > 0.9

    def test_the_turning_name_outranks_the_falling_knife(self, config):
        assert (
            score.score(self._turning(), config)["score"]
            > score.score(self._still_falling(), config)["score"]
        )

    def test_falls_back_to_todays_reading_when_history_is_short(self, config):
        row = make_row(tech={"rsi14": 33.0, "rsi_min_recent": None,
                             "williams_r14": -90.0, "williams_r_min_recent": None,
                             **CONFIRMING})
        assert score._oversold(row, config["scoring"]) == pytest.approx(1.0)


class TestOversoldNeedsConfirming:
    """RBLX, 2026-08-26: 74% below its high, EMAs fully inverted, no turn at
    all -- and it scored 19.55 of 20 here, because being cheap was measured
    without asking whether anything had stopped falling."""

    def _stretched(self, **extra):
        return make_row(tech={"rsi14": 33.0, "williams_r14": -90.0,
                              **NO_TURN, **extra})

    def test_a_washout_with_no_turn_keeps_only_the_floor(self, config):
        cfg = config["scoring"]
        assert score._oversold(self._stretched(), cfg) == pytest.approx(
            cfg["oversold_unconfirmed_floor"]
        )

    def test_the_turn_scales_it_the_rest_of_the_way(self, config):
        cfg = config["scoring"]
        none = score._oversold(self._stretched(), cfg)
        some = score._oversold(self._stretched(above_ema9=True), cfg)
        full = score._oversold(self._stretched(**CONFIRMING), cfg)
        assert none < some < full == pytest.approx(1.0)

    def test_confirmation_cannot_invent_credit_that_is_not_there(self, config):
        """An overbought name stays at zero however strong the bounce."""
        row = make_row(tech={"rsi14": 65.0, "williams_r14": -10.0, **CONFIRMING})
        assert score._oversold(row, config["scoring"]) == 0.0


class TestPartialScore:
    def test_technical_stage_ignores_data_it_has_not_fetched(self, config):
        """Mid-funnel we have no fundamentals or option chain yet -- ranking must
        not silently punish every name for that."""
        row = make_row(fund=None, trade=None)
        assert score.partial_score(row, config, score.STAGE_TECHNICAL) > 0

    def test_bounded_by_the_weights_it_uses(self, config):
        cap = sum(config["weights"][n] for n in score.STAGE_TECHNICAL)
        assert score.partial_score(make_row(), config, score.STAGE_TECHNICAL) <= cap

    def test_later_stages_add_more_signal(self, config):
        row = make_row()
        early = score.partial_score(row, config, score.STAGE_TECHNICAL)
        later = score.partial_score(row, config, score.STAGE_FUNDAMENTAL)
        assert later > early


class TestScoring:
    def test_stays_within_zero_and_one_hundred(self, config):
        for row in [make_row(), make_row(tech={"rsi14": 80.0}), make_row(trade=None)]:
            assert 0 <= score.score(row, config)["score"] <= 100

    def test_weights_sum_to_one_hundred(self, config):
        assert sum(config["weights"].values()) == 100

    def test_a_perfect_name_beats_a_mediocre_one(self, config):
        weak = make_row(
            tech={"rsi14": 55.0, "williams_r14": -20.0, "above_ema9": False,
                  "macd_cross_up": False, "up_day_volume_expansion": False},
            fund={"revenue_yoy": -0.08, "revenue_qoq": -0.05,
                  "gross_margin_change": -0.02, "operating_margin_change": -0.01},
        )
        assert score.score(make_row(), config)["score"] > score.score(weak, config)["score"]

    def test_missing_fundamentals_does_not_zero_the_score(self, config):
        """We'd rather rank a name we know less about than drop it silently."""
        result = score.score(make_row(fund=None), config)
        assert result["components"]["sales_growth"]["points"] > 0
        assert result["score"] > 0

    def test_components_never_exceed_their_weight(self, config):
        for component in score.score(make_row(), config)["components"].values():
            assert component["points"] <= component["max"] + 1e-9


class TestPenalties:
    def test_earnings_before_expiry(self, config):
        row = make_row(fund={"next_earnings": "2026-09-15"})  # before the 09-30 expiry
        reasons = score.penalties(row, config["penalties"])
        assert any("earnings" in r["reason"] for r in reasons)

    def test_earnings_after_expiry_is_clean(self, config):
        assert score.penalties(make_row(), config["penalties"]) == []

    def test_extreme_iv_is_flagged_as_event_risk(self, config):
        reasons = score.penalties(make_row(iv_hv=3.1), config["penalties"])
        assert any("pricing a specific event" in r["reason"] for r in reasons)

    def test_still_falling_is_flagged(self, config):
        reasons = score.penalties(make_row(tech={"change_5d": -0.22}), config["penalties"])
        assert any("five sessions" in r["reason"] for r in reasons)

    def test_structural_catalyst_costs_the_most(self, config):
        row = make_row(catalyst={"verdict": "structural", "headline": "Lost its biggest customer"})
        reasons = score.penalties(row, config["penalties"])
        assert any("structural" in r["reason"] for r in reasons)
        assert score.score(row, config)["score"] < score.score(make_row(), config)["score"]

    def test_transient_catalyst_is_not_penalised(self, config):
        row = make_row(catalyst={"verdict": "transient", "headline": "Broad market selloff"})
        assert score.penalties(row, config["penalties"]) == []

    def test_penalties_reduce_the_final_score(self, config):
        result = score.score(make_row(iv_hv=3.1), config)
        assert result["score"] < result["score_before_penalties"]

    def test_a_malformed_earnings_date_is_ignored(self, config):
        row = make_row(fund={"next_earnings": "not a date"})
        assert score.penalties(row, config["penalties"]) == []


class TestBadges:
    def test_unknown_fundamentals_read_unknown_not_failed(self, config):
        """A missing number must not look like a failed criterion on the page."""
        by_label = {b["label"]: b["passed"] for b in score.badges(make_row(fund=None), config)}
        assert by_label["Sales up >10% YoY"] is None
        assert by_label["Margins improving"] is None
        assert by_label["Above 9-day EMA"] is True  # technicals still known

    def test_reflects_the_underlying_numbers(self, config):
        by_label = {b["label"]: b["passed"] for b in score.badges(make_row(), config)}
        assert by_label["RSI below 35"] is True
        assert by_label["Williams %R below -80"] is True
        assert by_label["Above 20-day EMA"] is False
        assert by_label["Sales up >10% YoY"] is True

    def test_near_support_uses_the_recent_shelf_not_the_52_week_low(self, config):
        """A stock can sit far above its 52-week low and still be resting on the
        support that decides this trade."""
        on_support = make_row(tech={"close": 94.0, "support_60d": 92.0, "pct_above_52w_low": 0.80})
        far_above = make_row(tech={"close": 130.0, "support_60d": 92.0, "pct_above_52w_low": 0.05})
        label = "Near support"
        assert next(b for b in score.badges(on_support, config) if b["label"] == label)["passed"]
        assert not next(b for b in score.badges(far_above, config) if b["label"] == label)["passed"]

    def test_every_badge_is_labelled(self, config):
        badges = score.badges(make_row(), config)
        assert len(badges) == 11
        assert all(b["label"] and "passed" in b for b in badges)
