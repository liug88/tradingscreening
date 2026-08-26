"""Indicator tests against published reference values.

The RSI vector is Wilder's own worked example (the one StockCharts publishes).
If these pass, the numbers on the page match the numbers on a chart.
"""

import numpy as np
import pandas as pd
import pytest

from screener import technicals as t

WILDER_CLOSES = [
    44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955, 45.4245,
    45.8433, 46.0826, 45.8931, 46.0328, 45.6140, 46.2820, 46.2820, 46.0028,
    46.0328, 46.4116, 46.2222, 45.6439, 46.2122, 46.2521, 45.7137, 46.4515,
    45.7835, 45.3548, 44.0288, 44.1783, 44.2181, 44.5672, 43.4205, 42.6628,
    43.1314,
]

# Expected RSI(14), first value at index 14.
WILDER_RSI = [
    70.464, 66.250, 66.482, 69.346, 66.295, 57.925, 62.930, 63.257, 56.060,
    62.378, 54.707, 50.417, 39.989, 41.460, 41.874, 45.463, 37.303, 33.079,
    37.772,
]


def test_rsi_matches_wilder_reference():
    result = t.rsi(pd.Series(WILDER_CLOSES)).iloc[14:].to_numpy()
    assert t.rsi(pd.Series(WILDER_CLOSES)).iloc[:14].isna().all(), "not defined before bar 14"
    # The published table rounds its own inputs to 4dp, which the seed average
    # carries into the first few values. Once the seed decays the recursion has
    # to agree almost exactly -- that tighter check is the real proof.
    np.testing.assert_allclose(result, WILDER_RSI, atol=0.1)
    np.testing.assert_allclose(result[6:], WILDER_RSI[6:], atol=0.01)


def test_rsi_is_100_when_price_only_rises():
    rising = pd.Series(np.arange(1.0, 40.0))
    assert t.rsi(rising).iloc[-1] == 100.0


def test_rsi_is_zero_when_price_only_falls():
    falling = pd.Series(np.arange(40.0, 1.0, -1.0))
    assert t.rsi(falling).iloc[-1] == pytest.approx(0.0)


def test_rsi_returns_all_nan_when_history_too_short():
    assert t.rsi(pd.Series([1.0, 2.0, 3.0])).isna().all()


def test_williams_r_hits_the_extremes():
    high = pd.Series([10.0] * 14 + [10.0])
    low = pd.Series([5.0] * 14 + [5.0])
    at_high = t.williams_r(high, low, pd.Series([10.0] * 15))
    at_low = t.williams_r(high, low, pd.Series([5.0] * 15))
    assert at_high.iloc[-1] == pytest.approx(0.0)
    assert at_low.iloc[-1] == pytest.approx(-100.0)


def test_macd_crossover_sign_flips_with_trend():
    down_then_up = pd.Series(list(np.arange(100.0, 60.0, -1.0)) + list(np.arange(60.0, 90.0)))
    line, signal, hist = t.macd(down_then_up)
    assert line.iloc[-1] > signal.iloc[-1], "recovering series should end bullish"
    assert hist.iloc[-1] == pytest.approx(line.iloc[-1] - signal.iloc[-1])


def test_atr_equals_the_range_when_every_bar_is_identical():
    n = 40
    high = pd.Series([11.0] * n)
    low = pd.Series([10.0] * n)
    close = pd.Series([10.5] * n)
    assert t.atr(high, low, close).iloc[-1] == pytest.approx(1.0)


def test_historical_volatility_is_zero_for_a_flat_series():
    flat = pd.Series([50.0] * 40)
    assert t.historical_volatility(flat).iloc[-1] == pytest.approx(0.0)


def _synthetic_frame(n=300, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)), index=dates).clip(lower=5.0)
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": pd.Series(rng.integers(1_000_000, 5_000_000, n).astype(float), index=dates),
        }
    )


def test_compute_returns_every_field_the_scorer_needs():
    result = t.compute(_synthetic_frame())
    required = {
        "close", "rsi14", "williams_r14", "macd", "macd_cross_up", "macd_below_zero",
        "ema9", "ema20", "ema200", "above_ema9", "above_ema20", "atr14", "hv20",
        "avg_volume_30d", "up_day_volume_expansion", "high_52w", "low_52w",
        "support_60d", "change_5d",
    }
    assert required <= result.keys()
    assert 0 <= result["rsi14"] <= 100
    assert result["low_52w"] <= result["close"] <= result["high_52w"]


def test_compute_bails_out_on_short_history():
    assert t.compute(_synthetic_frame(n=30)) == {}


def test_compute_output_is_json_safe():
    """NaNs must come back as None or json.dumps writes invalid JSON."""
    import json

    result = t.compute(_synthetic_frame())
    assert "NaN" not in json.dumps(result)
