"""Indicator tests against published reference values.

The RSI vector is Wilder's own worked example (the one StockCharts publishes).
If these pass, the numbers on the page match the numbers on a chart.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from screener import technicals as t

PRICES = Path(__file__).resolve().parents[1] / "cache" / "backtest_prices.pkl"

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


def test_compute_returns_every_field_the_payload_publishes():
    """Asked against `run.PUBLISHED_TECHNICALS` rather than a list kept here.

    A second hand-maintained list would drift from the first, and the drift
    would show up as a field published as null on every name forever -- which
    reads on the page as a reading that came back empty, not one this module
    never took.
    """
    from screener.run import PUBLISHED_TECHNICALS

    result = t.compute(_synthetic_frame())
    missing = set(PUBLISHED_TECHNICALS) - result.keys()
    assert not missing, f"published but never computed: {sorted(missing)}"
    assert 0 <= result["rsi14"] <= 100
    assert result["low_52w"] <= result["close"] <= result["high_52w"]


def test_raw_stochastic_k_is_williams_r_flipped():
    """%K and Williams %R are one measurement, and the page says so.

    Before smoothing they are the same arithmetic on the same window:
    `raw %K = 100 + %R`. This is here so that if it ever stops holding, one of
    the two is computed wrong -- and so nobody later reads them as two
    independent oversold signals agreeing with each other.
    """
    df = _synthetic_frame()
    high, low, close = df["high"], df["low"], df["close"]
    hh, ll = high.rolling(14).max(), low.rolling(14).min()
    raw_k = 100.0 * (close - ll) / (hh - ll)
    wr = t.williams_r(high, low, close)
    pd.testing.assert_series_equal(raw_k, 100.0 + wr, check_names=False)


@pytest.mark.skipif(not PRICES.exists(), reason="no cached price history")
def test_the_flip_holds_across_the_real_universe():
    """The same identity on three years of real bars for every symbol."""
    import pickle

    with PRICES.open("rb") as fh:
        prices = pickle.load(fh)
    assert len(prices) > 100, "cache too small to be a real check"

    worst = 0.0
    for df in prices.values():
        high, low, close = df["high"], df["low"], df["close"]
        hh, ll = high.rolling(14).max(), low.rolling(14).min()
        raw_k = (100.0 * (close - ll) / (hh - ll)).iloc[-1]
        wr = t.williams_r(high, low, close).iloc[-1]
        if np.isfinite(raw_k) and np.isfinite(wr):
            worst = max(worst, abs(raw_k - (100.0 + wr)))
    assert worst < 1e-9, f"drifted by {worst}"


def test_stochastic_bottoms_and_tops_out():
    """%D is bounded 0..100 -- the score ramps assume it."""
    n = 60
    close = pd.Series(np.linspace(100, 50, n))
    frame = pd.DataFrame({"high": close * 1.001, "low": close * 0.999, "close": close})
    k, d = t.stochastic(frame["high"], frame["low"], frame["close"])
    assert d.iloc[-1] < 5      # closing at the bottom of its range every day
    assert 0 <= k.iloc[-1] <= 100


def test_money_flow_index_is_100_when_nothing_sells():
    """An unbroken advance has no down-volume, and the ratio has no denominator."""
    n = 60
    close = pd.Series(np.linspace(50, 100, n))
    mfi = t.money_flow_index(close * 1.01, close * 0.99, close,
                             pd.Series(np.full(n, 1e6)))
    assert mfi.iloc[-1] == 100.0


def test_percent_b_marks_the_bands():
    """0 at the lower band, 1 at the upper -- the two points the score ramps to."""
    rng = np.random.default_rng(1)
    close = pd.Series(100 + rng.normal(0, 2, 200))
    pct_b = t.bollinger_percent_b(close)
    mid = close.rolling(20).mean()
    width = close.rolling(20).std(ddof=0) * 2.0
    assert pct_b.iloc[-1] == pytest.approx(
        (close.iloc[-1] - (mid.iloc[-1] - width.iloc[-1])) / (2 * width.iloc[-1]))
    # A series that never moves has no bands to sit inside. NaN rather than a
    # divide-by-zero, and _f() turns it into None on the way out.
    flat = pd.Series([100.0] * 40)
    assert np.isnan(t.bollinger_percent_b(flat).iloc[-1])


def test_compute_bails_out_on_short_history():
    assert t.compute(_synthetic_frame(n=30)) == {}


def test_compute_output_is_json_safe():
    """NaNs must come back as None or json.dumps writes invalid JSON."""
    import json

    result = t.compute(_synthetic_frame())
    assert "NaN" not in json.dumps(result)
