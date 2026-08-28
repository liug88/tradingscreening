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


# The three readings that joined RSI in the composite, all at their oversold
# extreme. A row carrying none of these scores on the old two-reading mix.
DEEP = {
    "stoch_d": 12.0, "stoch_cross_up": False,
    "mfi14": 15.0, "bb_percent_b": -0.05,
}


class TestTheOversoldComposite:
    """She read that the standard set is RSI, stochastics and others, so the
    component reads four things instead of two. Williams %R is not one of them:
    it is `100 + raw %K`, and scoring both would count one measurement twice."""

    def _row(self, **tech):
        return make_row(tech={"rsi14": 33.0, "williams_r14": -90.0,
                              **CONFIRMING, **DEEP, **tech})

    def test_every_reading_at_its_extreme_scores_full_marks(self, config):
        # The cross is the last fifth of the stochastic term, so full marks
        # needs it: four readings at the bottom AND %K turning up through %D.
        row = self._row(stoch_cross_up=True)
        assert score._oversold(row, config["scoring"]) == pytest.approx(1.0)

    def test_each_reading_carries_its_own_weight(self, config):
        """Neutralise one at a time; the drop is that reading's share."""
        cfg = config["scoring"]
        full = score._oversold(self._row(), cfg)
        drops = {
            "rsi": full - score._oversold(self._row(rsi14=60.0), cfg),
            "stoch": full - score._oversold(self._row(stoch_d=60.0), cfg),
            "mfi": full - score._oversold(self._row(mfi14=60.0), cfg),
            "bb": full - score._oversold(self._row(bb_percent_b=0.6), cfg),
        }
        assert drops["rsi"] == pytest.approx(0.50)
        assert drops["stoch"] == pytest.approx(0.20 * 0.8)
        assert drops["mfi"] == pytest.approx(0.20)
        assert drops["bb"] == pytest.approx(0.10)

    def test_the_stochastic_cross_is_worth_a_fifth_of_its_term(self, config):
        cfg = config["scoring"]
        flat = score._oversold(self._row(stoch_d=60.0), cfg)
        turning = score._oversold(self._row(stoch_d=60.0, stoch_cross_up=True), cfg)
        assert turning - flat == pytest.approx(0.20 * 0.2)

    def test_volume_is_the_thing_mfi_adds(self, config):
        """RSI sliding while money flow holds up is selling without conviction,
        and it is the only disagreement a price-only reading cannot see."""
        cfg = config["scoring"]
        conviction = score._oversold(self._row(mfi14=15.0), cfg)
        no_conviction = score._oversold(self._row(mfi14=55.0), cfg)
        assert conviction > no_conviction

    def test_williams_r_is_not_counted_a_second_time(self, config):
        """Once the composite is live, %R moves nothing -- %D carries it."""
        cfg = config["scoring"]
        assert score._oversold(self._row(williams_r14=-95.0), cfg) == pytest.approx(
            score._oversold(self._row(williams_r14=-5.0), cfg)
        )

    def test_it_reads_the_recent_low_not_todays_number(self, config):
        """Same rule RSI already follows: the washout is the setup, and today's
        reading having recovered is the turn, not a disqualification."""
        cfg = config["scoring"]
        recovered = self._row(stoch_cross_up=True,
                              stoch_d=70.0, stoch_d_min_recent=12.0,
                              mfi14=70.0, mfi_min_recent=15.0,
                              bb_percent_b=0.8, bb_percent_b_min_recent=-0.05)
        assert score._oversold(recovered, cfg) == pytest.approx(1.0)

    def test_a_file_from_before_it_shipped_scores_the_old_way(self, config):
        """No stochastic in the payload means the two-reading mix, exactly as
        published -- a site-only push republishes old history files."""
        cfg = config["scoring"]
        old = make_row(tech={"rsi14": 60.0, "williams_r14": -90.0, **CONFIRMING})
        rsi_part = score._ramp(60.0, cfg["rsi_zero_above"], cfg["rsi_ideal_high"])
        assert score._oversold(old, cfg) == pytest.approx(0.7 * rsi_part + 0.3)

    def test_the_composite_does_not_rescue_a_falling_knife(self, config):
        """Four oversold readings agreeing on a stock in free fall is still a
        stock in free fall. The bounce multiplier applies to all of them."""
        cfg = config["scoring"]
        knife = make_row(tech={"rsi14": 33.0, "williams_r14": -90.0,
                               **NO_TURN, **DEEP})
        # 0.96 is every reading at its extreme except the cross, which a name
        # that has not turned cannot have. The floor is what caps it.
        assert score._oversold(knife, cfg) == pytest.approx(
            0.96 * cfg["oversold_unconfirmed_floor"])


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


def quarters(*figures):
    """A revenue history, oldest first, in the published shape."""
    return [{"quarter": "q%d" % i, "revenue": float(v)} for i, v in enumerate(figures)]


def trending(**overrides):
    """A row with the chart she asked for: stacked averages, a young cross."""
    tech = {"above_ema200": True, "golden_cross": True, "full_stack": True,
            "golden_cross_days_ago": 0, "pct_below_52w_high": 0.35}
    return make_row(tech={**tech, **overrides})


class TestTrendStructure:
    """The four facts her mother named, and the ways they come apart."""

    def test_the_whole_chart_in_order_scores_full(self, config):
        assert score._trend_structure(trending(), config["scoring"]) == pytest.approx(1.0)

    def test_a_fully_inverted_stack_scores_nothing(self, config):
        broken = trending(above_ema200=False, golden_cross=False, full_stack=False)
        assert score._trend_structure(broken, config["scoring"]) == 0.0

    def test_above_the_200_with_the_50_still_under_it_is_not_an_uptrend(self, config):
        """The distinction the component exists for: a bounce inside a downtrend
        reads as one fact out of four, not as a healthy chart."""
        bouncing = trending(golden_cross=False, full_stack=False, golden_cross_days_ago=None)
        assert score._trend_structure(bouncing, config["scoring"]) == pytest.approx(0.30)

    def test_an_old_cross_keeps_the_level_and_loses_the_freshness(self, config):
        cfg = config["scoring"]
        old = trending(golden_cross_days_ago=cfg["trend_cross_fresh_days"])
        assert score._trend_structure(old, cfg) == pytest.approx(0.80)
        assert score._trend_structure(trending(), cfg) > score._trend_structure(old, cfg)

    def test_a_cross_older_than_the_frame_is_not_a_crash(self, config):
        """None means the cross predates the history, not that it never happened.
        It scores the same as any other old cross rather than raising."""
        assert score._trend_structure(trending(golden_cross_days_ago=None),
                                      config["scoring"]) == pytest.approx(0.80)

    def test_freshness_needs_the_cross_to_have_actually_happened(self, config):
        """A death cross 3 days old is not a young golden cross."""
        crossed_down = trending(golden_cross=False, full_stack=False, golden_cross_days_ago=3)
        assert score._trend_structure(crossed_down, config["scoring"]) == pytest.approx(0.30)


class TestRevenueExpanding:
    def test_four_rises_and_fast_growth_score_full(self, config):
        row = make_row(fund={"revenue_history": quarters(10, 11, 12, 13, 14),
                             "revenue_yoy": 0.40})
        assert score._revenue_expanding(row, config["scoring"]) == pytest.approx(1.0)

    def test_revenue_falling_every_quarter_scores_nothing(self, config):
        row = make_row(fund={"revenue_history": quarters(14, 13, 12, 11, 10),
                             "revenue_yoy": -0.30})
        assert score._revenue_expanding(row, config["scoring"]) == 0.0

    def test_a_missing_history_is_unknown_rather_than_bad(self, config):
        """The same reading the other fundamental terms give: 0.4, not zero."""
        assert score._revenue_expanding(make_row(fund=None), config["scoring"]) == 0.4
        assert score._revenue_expanding(make_row(fund={"revenue_history": quarters(10)}),
                                        config["scoring"]) == 0.4

    def test_a_run_ending_now_beats_the_same_rises_scattered(self, config):
        cfg = config["scoring"]
        ending_now = make_row(fund={"revenue_history": quarters(10, 9, 10, 11, 12)})
        ended_early = make_row(fund={"revenue_history": quarters(10, 11, 12, 13, 9)})
        assert score._revenue_expanding(ending_now, cfg) > score._revenue_expanding(ended_early, cfg)

    def test_growing_5_percent_does_not_score_like_growing_50(self, config):
        """Counting up-quarters alone tied 36 of 214 real names at full marks,
        from +4.9% a year to +157%. The size of the growth is the third term."""
        cfg = config["scoring"]
        shape = quarters(10, 11, 12, 13, 14)
        slow = make_row(fund={"revenue_history": shape, "revenue_yoy": 0.05})
        fast = make_row(fund={"revenue_history": shape, "revenue_yoy": 0.50})
        assert score._revenue_expanding(fast, cfg) > score._revenue_expanding(slow, cfg)
        assert score._revenue_expanding(fast, cfg) == pytest.approx(1.0)


class TestRoomToRun:
    def test_the_ideal_distance_below_the_high_scores_full(self, config):
        cfg = config["scoring"]
        row = trending(pct_below_52w_high=cfg["room_ideal_below_high"])
        assert score._room_to_run(row, cfg) == pytest.approx(1.0)

    def test_a_stock_at_its_high_has_no_room_left(self, config):
        assert score._room_to_run(trending(pct_below_52w_high=0.0), config["scoring"]) == 0.0

    def test_a_collapse_is_not_upside(self, config):
        """RBLX as published: 74% off its high, the 50 under the 200. A single
        ramp would have handed this the maximum. It scores zero."""
        rblx = trending(pct_below_52w_high=0.74, golden_cross=False, full_stack=False)
        assert score._room_to_run(rblx, config["scoring"]) == 0.0

    def test_the_credit_ramps_back_down_past_the_ideal(self, config):
        cfg = config["scoring"]
        middle = (cfg["room_ideal_below_high"] + cfg["room_broken_below_high"]) / 2
        assert score._room_to_run(trending(pct_below_52w_high=middle), cfg) == pytest.approx(0.5)

    def test_a_broken_trend_cuts_what_is_left(self, config):
        cfg = config["scoring"]
        intact = trending(pct_below_52w_high=cfg["room_ideal_below_high"])
        broken = trending(pct_below_52w_high=cfg["room_ideal_below_high"], golden_cross=False)
        assert score._room_to_run(broken, cfg) == pytest.approx(cfg["room_broken_trend_factor"])
        assert score._room_to_run(broken, cfg) < score._room_to_run(intact, cfg)

    def test_an_unknown_high_scores_nothing(self, config):
        assert score._room_to_run(trending(pct_below_52w_high=None), config["scoring"]) == 0.0


class TestEntryTiming:
    def test_sits_between_the_two_readings_it_folds(self, config):
        cfg = config["scoring"]
        row = make_row()
        pair = sorted([score._oversold(row, cfg), score._bounce(row, cfg)])
        assert pair[0] <= score._entry_timing(row, cfg) <= pair[1]

    def test_a_falling_knife_times_badly(self, config):
        cfg = config["scoring"]
        turning = make_row()
        falling = make_row(tech={"macd_cross_up": False, "above_ema9": False,
                                 "up_day_volume_expansion": False, "change_5d": -0.12})
        assert score._entry_timing(turning, cfg) > score._entry_timing(falling, cfg)


class TestProfiles:
    """One screen, three rankings. A profile is a weight block and nothing more."""

    def test_every_profile_names_only_components_that_exist(self, config):
        for profile, key in score.PROFILES.items():
            unknown = set(config[key]) - set(score._COMPONENTS)
            assert not unknown, f"{profile} weights name no such component: {unknown}"

    def test_every_profile_is_scored_out_of_100(self, config):
        for profile, key in score.PROFILES.items():
            assert sum(config[key].values()) == 100, profile

    def test_a_ranking_scores_only_what_its_block_names(self, config):
        result = score.score(trending(), config, profile="buy")
        assert set(result["components"]) == set(config["weights_buy"])
        assert "premium_richness" not in result["components"]

    def test_the_put_ranking_is_untouched_by_the_others(self, config):
        """The default stays what it was, so today's published list does not move
        because two more rankings were added beside it."""
        result = score.score(make_row(), config)
        assert set(result["components"]) == set(config["weights"])
        assert 0 <= result["score"] <= 100

    def test_a_name_with_no_put_can_still_be_bought(self, config):
        """The only put-specific gate. She can buy a stock there is no contract
        worth selling against, and those names are dropped from the funnel today
        for a reason that has nothing to do with the company."""
        no_contract = make_row(trade=None)
        assert score.check_gates(no_contract, config) == [
            "no put in the target delta and liquidity range"]
        assert score.check_gates(no_contract, config, profile="buy") == []
        assert score.check_gates(no_contract, config, profile="long") == []

    def test_every_other_gate_still_applies(self, config):
        thin = make_row(trade=None, tech={"avg_volume_30d": 10_000})
        assert score.check_gates(thin, config, profile="buy") != []

    def test_earnings_costs_a_seller_and_not_an_owner(self, config):
        """An expiry is what makes the date expensive. Holding the stock, she can
        simply hold through it."""
        row = make_row(fund={"next_earnings": "2026-09-01"})
        cfg = config["penalties"]
        assert [p["reason"] for p in score.penalties(row, cfg) if "earnings" in p["reason"]]
        assert score.penalties(row, cfg, profile="buy") == []

    def test_a_profile_overrides_only_what_it_names(self, config):
        base, held = config["penalties"], score.penalty_config(config, "long")
        assert held["new_low_under_ema200"] > base["new_low_under_ema200"]
        assert held["catalyst_structural"] == base["catalyst_structural"]
        assert score.penalty_config(config, "buy") == base

    def test_the_confirmed_downtrend_stays_the_heavier_charge(self, config):
        """It is the worse condition wherever it fires, and raising the other one
        on LONG must not invert that."""
        for profile in score.PROFILES:
            cfg = score.penalty_config(config, profile)
            assert cfg["downtrend_confirmed"] > cfg["new_low_under_ema200"], profile

    def test_the_falling_knife_ranks_last_on_every_list(self, config):
        """RBLX as her mother found it, against the chart she asked for."""
        rblx = make_row(tech={"above_ema200": False, "golden_cross": False,
                              "full_stack": False, "golden_cross_days_ago": 4,
                              "pct_below_52w_high": 0.74, "pct_above_52w_low": 0.11,
                              "macd_cross_up": False, "above_ema9": False,
                              "up_day_volume_expansion": False, "change_5d": -0.08})
        healthy = trending()
        for profile in score.PROFILES:
            assert (score.score(rblx, config, profile)["score"]
                    < score.score(healthy, config, profile)["score"]), profile
