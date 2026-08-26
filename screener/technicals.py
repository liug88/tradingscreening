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

    is_up_day = px > float(close.iloc[-2])
    latest_vol = float(volume.iloc[last])

    return {
        "close": px,
        "prev_close": float(close.iloc[-2]),
        "rsi14": _f(rsi14.iloc[last]),
        "rsi_min_recent": _f(rsi14.iloc[-oversold_lookback:].min()),
        "williams_r14": _f(wr14.iloc[last]),
        "williams_r_min_recent": _f(wr14.iloc[-oversold_lookback:].min()),
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
        "above_ema200": bool(px > ema200.iloc[last]),
        "atr14": _f(atr14.iloc[last]),
        "atr_pct": _f(atr14.iloc[last] / px) if px else None,
        "hv20": _f(hv20.iloc[last]),
        "avg_volume_30d": float(volume.iloc[-30:].mean()),
        "volume_vs_20d": _f(latest_vol / avg_vol_20) if avg_vol_20 else None,
        "up_day_volume_expansion": bool(is_up_day and latest_vol > avg_vol_20),
        "high_52w": high_52w,
        "low_52w": low_52w,
        "pct_above_52w_low": _f((px - low_52w) / low_52w) if low_52w else None,
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
