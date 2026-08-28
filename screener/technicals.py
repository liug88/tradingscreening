"""Technical indicators.

RSI and ATR use Wilder's smoothing seeded with a simple average of the first
`period` values -- the way charting platforms draw it. That matters here: Mom
cross-checks these numbers against a chart, so an RSI of 31.2 needs to be the
same 31.2 she sees on TradingView, not a close-enough approximation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI. First value lands on bar `period`."""
    c = close.to_numpy(dtype=float)
    out = np.full(len(c), np.nan)
    if len(c) <= period:
        return pd.Series(out, index=close.index)

    delta = np.diff(c)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    avg_gain = gain[:period].mean()
    avg_loss = loss[:period].mean()
    out[period] = _rsi_value(avg_gain, avg_loss)

    for k in range(period, len(delta)):
        avg_gain = (avg_gain * (period - 1) + gain[k]) / period
        avg_loss = (avg_loss * (period - 1) + loss[k]) / period
        out[k + 1] = _rsi_value(avg_gain, avg_loss)

    return pd.Series(out, index=close.index)


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Williams %R: 0 at the period high, -100 at the period low."""
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    span = hh - ll
    return (-100.0 * (hh - close) / span).where(span != 0)


def stochastic(
    high: pd.Series, low: pd.Series, close: pd.Series,
    period: int = 14, smooth: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """Slow stochastic: %K is the smoothed raw line, %D the average of that.

    Before any smoothing, this is Williams %R flipped -- `raw %K = 100 + %R`
    exactly, since both divide the same distance by the same range. A test
    asserts it to floating point on the real universe. So the level carries no
    information this module does not already report, and the reason to compute
    it is the pair: %D lags %K, and the crossing is where a stochastic actually
    says something. Smoothed with 3 to match what a chart draws, because she
    reads these numbers off one.

    The page states the identity rather than printing two ticks and letting
    them look like two signals agreeing.
    """
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    span = hh - ll
    raw_k = (100.0 * (close - ll) / span).where(span != 0)
    k = raw_k.rolling(smooth).mean()
    return k, k.rolling(smooth).mean()


def money_flow_index(
    high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series,
    period: int = 14,
) -> pd.Series:
    """RSI weighted by volume -- the one oversold reading that is not price.

    Same ratio-of-ups-to-downs shape as RSI, but each day is counted by the
    money that changed hands rather than by the size of the move. RSI sliding
    while this holds up is selling without conviction; both sliding together is
    selling with it. That difference is the only thing here a price-only
    indicator cannot tell her.
    """
    typical = (high + low + close) / 3.0
    flow = typical * volume
    rising = typical > typical.shift(1)
    up = flow.where(rising, 0.0).rolling(period).sum()
    down = flow.where(~rising, 0.0).rolling(period).sum()
    # All-up windows would divide by zero. 100 is the correct reading there:
    # nothing sold into the period at all.
    return (100.0 - 100.0 / (1.0 + up / down)).where(down != 0, 100.0)


def bollinger_percent_b(
    close: pd.Series, period: int = 20, stdevs: float = 2.0
) -> pd.Series:
    """Where price sits inside its own bands: 0 at the lower, 1 at the upper.

    Volatility-relative rather than momentum-relative, which is why it earns a
    place beside RSI instead of duplicating it. A quiet stock 5% off its mean
    is at the band; a violent one 5% off is halfway there, and only this says
    so. Values outside 0-1 are real -- price closed beyond the band.
    """
    mid = close.rolling(period).mean()
    width = close.rolling(period).std(ddof=0) * stdevs
    return ((close - (mid - width)) / (2.0 * width)).where(width != 0)


def ema(values: pd.Series, span: int) -> pd.Series:
    return values.ewm(span=span, adjust=False).mean()


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd line, signal line, histogram)."""
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average true range, Wilder-smoothed."""
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)

    tr = true_range.to_numpy(dtype=float)
    out = np.full(len(tr), np.nan)
    if len(tr) <= period:
        return pd.Series(out, index=close.index)

    # tr[0] is NaN (no previous close), so seed from bars 1..period.
    prev = tr[1 : period + 1].mean()
    out[period] = prev
    for i in range(period + 1, len(tr)):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev

    return pd.Series(out, index=close.index)


def historical_volatility(close: pd.Series, period: int = 20) -> pd.Series:
    """Annualized stdev of daily log returns -- the 'HV' in IV/HV."""
    log_ret = np.log(close / close.shift(1))
    return log_ret.rolling(period).std() * np.sqrt(TRADING_DAYS)


def compute(
    df: pd.DataFrame, macd_cross_lookback: int = 5, oversold_lookback: int = 10
) -> dict:
    """Collapse a price history into the scalars the screen scores on.

    `df` needs open/high/low/close/volume columns indexed by date, oldest first.
    Returns {} if there isn't enough history to be meaningful.

    `oversold_lookback` is why RSI is reported twice. A stock that washed out to
    RSI 29 last week and has since reclaimed its 9-day EMA reads RSI 46 today --
    today's number hides the very setup we're looking for. The rolling minimum
    remembers the washout; today's value still says where it stands now.
    """
    if len(df) < 60:
        return {}

    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]
    if not np.isfinite(close.iloc[-1]):
        return {}  # bad price fetch -- everything downstream would be garbage

    rsi14 = rsi(close)
    wr14 = williams_r(high, low, close)
    stoch_k, stoch_d = stochastic(high, low, close)
    mfi14 = money_flow_index(high, low, close, volume)
    pct_b = bollinger_percent_b(close)
    macd_line, macd_signal, macd_hist = macd(close)
    atr14 = atr(high, low, close)
    hv20 = historical_volatility(close)

    ema9, ema20 = ema(close, 9), ema(close, 20)
    ema50, ema200 = ema(close, 50), ema(close, 200)

    last = -1
    px = float(close.iloc[last])
    avg_vol_20 = float(volume.iloc[-20:].mean())
    window_52w = close.iloc[-TRADING_DAYS:]
    low_52w = float(low.iloc[-TRADING_DAYS:].min())
    high_52w = float(high.iloc[-TRADING_DAYS:].max())

    # MACD line crossing up through the signal line within the lookback window.
    above = macd_line > macd_signal
    newly_above = above & ~above.shift(1, fill_value=False)
    crossed_up = bool(newly_above.iloc[-macd_cross_lookback:].any())

    # Same crossover shape as MACD above, on the 50/200 pair. None means the
    # cross is older than the frame, not that it never happened.
    fifty_above = ema50 > ema200
    fifty_newly_above = fifty_above & ~fifty_above.shift(1, fill_value=False)
    cross_hits = np.flatnonzero(fifty_newly_above.to_numpy())
    cross_days_ago = int(len(ema50) - 1 - cross_hits[-1]) if len(cross_hits) else None

    # %K crossing up through %D, read the same way as the MACD cross above.
    # The level says how far it fell; only the cross says it stopped.
    k_above = stoch_k > stoch_d
    k_newly_above = k_above & ~k_above.shift(1, fill_value=False)
    stoch_crossed_up = bool(k_newly_above.iloc[-macd_cross_lookback:].any())

    is_up_day = px > float(close.iloc[-2])
    latest_vol = float(volume.iloc[last])

    return {
        "close": px,
        "prev_close": float(close.iloc[-2]),
        "rsi14": _f(rsi14.iloc[last]),
        "rsi_min_recent": _f(rsi14.iloc[-oversold_lookback:].min()),
        "williams_r14": _f(wr14.iloc[last]),
        "williams_r_min_recent": _f(wr14.iloc[-oversold_lookback:].min()),
        # Reported for the name she knows it by. It is `100 + williams_r14` and
        # nothing else -- a test asserts that on real data, and the page says so
        # rather than letting one measurement look like two agreeing ones.
        "stoch_k": _f(stoch_k.iloc[last]),
        "stoch_d": _f(stoch_d.iloc[last]),
        "stoch_d_min_recent": _f(stoch_d.iloc[-oversold_lookback:].min()),
        "stoch_cross_up": stoch_crossed_up,
        "mfi14": _f(mfi14.iloc[last]),
        "mfi_min_recent": _f(mfi14.iloc[-oversold_lookback:].min()),
        "bb_percent_b": _f(pct_b.iloc[last]),
        "bb_percent_b_min_recent": _f(pct_b.iloc[-oversold_lookback:].min()),
        "oversold_lookback": oversold_lookback,
        "macd": _f(macd_line.iloc[last]),
        "macd_signal": _f(macd_signal.iloc[last]),
        "macd_hist": _f(macd_hist.iloc[last]),
        "macd_cross_up": crossed_up,
        "macd_below_zero": bool(macd_line.iloc[last] < 0),
        "ema9": _f(ema9.iloc[last]),
        "ema20": _f(ema20.iloc[last]),
        "ema50": _f(ema50.iloc[last]),
        "ema200": _f(ema200.iloc[last]),
        "above_ema9": bool(px > ema9.iloc[last]),
        "above_ema20": bool(px > ema20.iloc[last]),
        "above_ema50": bool(px > ema50.iloc[last]),
        "above_ema200": bool(px > ema200.iloc[last]),
        # The cross she asks for by name. False is not merely "not yet": the
        # 50 under the 200 is the death cross, and it is the single clearest
        # statement that the trend is still down.
        "golden_cross": bool(ema50.iloc[last] > ema200.iloc[last]),
        "golden_cross_days_ago": cross_days_ago,
        "full_stack": bool(
            px > ema20.iloc[last] > ema50.iloc[last] > ema200.iloc[last]
        ),
        "atr14": _f(atr14.iloc[last]),
        "atr_pct": _f(atr14.iloc[last] / px) if px else None,
        "hv20": _f(hv20.iloc[last]),
        "avg_volume_30d": float(volume.iloc[-30:].mean()),
        "volume_vs_20d": _f(latest_vol / avg_vol_20) if avg_vol_20 else None,
        "up_day_volume_expansion": bool(is_up_day and latest_vol > avg_vol_20),
        "high_52w": high_52w,
        "low_52w": low_52w,
        "pct_above_52w_low": _f((px - low_52w) / low_52w) if low_52w else None,
        # The mirror of the line above, and the one that stops a collapse from
        # reading as upside: 74% below the high is not "room to run".
        "pct_below_52w_high": _f((high_52w - px) / high_52w) if high_52w else None,
        "at_52w_low": bool(px <= low_52w * 1.03),
        "support_60d": float(low.iloc[-60:].min()),
        "change_5d": _f(px / float(close.iloc[-6]) - 1.0) if len(close) > 6 else None,
        "bars": len(df),
        "last_date": str(window_52w.index[-1])[:10],
    }


def _f(value) -> float | None:
    """NaN -> None, so the value survives a JSON round-trip."""
    if value is None:
        return None
    v = float(value)
    return None if np.isnan(v) else v
