"""Golden-case tests for Strategy N — Inside Bar Breakout."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kairos.backtest.protocols import BarsData, SignalFrame, Strategy
from kairos.strategies.inside_bar_breakout import InsideBarBreakoutStrategy


def _make_bars(
    close: np.ndarray,
    high: np.ndarray | None = None,
    low: np.ndarray | None = None,
    volume: np.ndarray | None = None,
) -> BarsData:
    n = len(close)
    idx = pd.date_range("2024-01-01 00:00", periods=n, freq="15min", tz="UTC")
    if high is None:
        high = close + 0.1
    if low is None:
        low = close - 0.1
    vol = volume if volume is not None else np.ones(n, dtype=np.float64)
    ohlcv = pd.DataFrame(
        {
            "open": close.astype(np.float64),
            "high": high.astype(np.float64),
            "low": low.astype(np.float64),
            "close": close.astype(np.float64),
            "volume": vol.astype(np.float64),
            "quote_volume": (vol * close).astype(np.float64),
            "taker_buy_volume": (vol * 0.5).astype(np.float64),
            "taker_buy_quote_volume": (vol * 0.5 * close).astype(np.float64),
            "trade_count": np.ones(n, dtype=np.int64),
        },
        index=idx,
    )
    return BarsData(ohlcv=ohlcv, symbol="TESTUSDT", interval="15m")


def test_protocol_conformance() -> None:
    s: Strategy = InsideBarBreakoutStrategy()
    assert s.name == "inside_bar_breakout"
    assert "ema_trend_span" in s.default_params


def test_gc_n1_long_breakout_on_inside_bar() -> None:
    """Gentle uptrend builds EMA, a mother bar with wide range is followed
    by an inside bar, then bar T+1 closes above mother-bar high and above
    EMA → long breakout."""
    n = 40
    close = np.full(n, 100.0, dtype=np.float64)
    # Gentle uptrend 0..30 to lift the EMA.
    for i in range(31):
        close[i] = 100.0 + 0.1 * i
    # Default high/low = close +/- 0.1 for bars 0..30.
    high = close + 0.1
    low = close - 0.1

    # Mother bar (idx 31): wide range, close above EMA.
    close[31] = 104.0
    high[31] = 105.0
    low[31] = 102.5
    # Inside bar (idx 32): strictly inside mother's range.
    close[32] = 103.5
    high[32] = 104.5
    low[32] = 103.0
    # Breakout bar (idx 33): close above mother's high (105.0) and above EMA.
    close[33] = 106.0
    high[33] = 106.2
    low[33] = 105.2
    # Follow-through bars to keep close above EMA.
    for i in range(34, n):
        close[i] = 106.0
        high[i] = close[i] + 0.1
        low[i] = close[i] - 0.1

    bars = _make_bars(close, high=high, low=low)
    sig = InsideBarBreakoutStrategy().generate_signals(
        bars, {"ema_trend_span": 10, "holding_bars": 20}
    )
    side = sig.df["side"].to_numpy()
    assert side[33] == 1, f"expected +1 at bar 33, got {side[33]}"


def test_gc_n2_flattens_on_ema_cross_down() -> None:
    """After a long entry, a close below EMA flattens the position."""
    n = 45
    close = np.full(n, 100.0, dtype=np.float64)
    for i in range(31):
        close[i] = 100.0 + 0.1 * i
    high = close + 0.1
    low = close - 0.1

    close[31] = 104.0; high[31] = 105.0; low[31] = 102.5
    close[32] = 103.5; high[32] = 104.5; low[32] = 103.0
    close[33] = 106.0; high[33] = 106.2; low[33] = 105.2  # entry
    close[34] = 106.0; high[34] = 106.1; low[34] = 105.9
    close[35] = 106.0; high[35] = 106.1; low[35] = 105.9
    # Sharp drop — close well below EMA.
    for i in range(36, n):
        close[i] = 95.0
        high[i] = 95.1
        low[i] = 94.9

    bars = _make_bars(close, high=high, low=low)
    sig = InsideBarBreakoutStrategy().generate_signals(
        bars, {"ema_trend_span": 10, "holding_bars": 50}
    )
    side = sig.df["side"].to_numpy()
    assert side[33] == 1
    # Find first bar after 33 where side == 0.
    post = side[34:]
    flats = np.where(post == 0)[0]
    assert len(flats) > 0, "expected flatten after EMA cross-down"


def test_idempotent_same_input_same_output() -> None:
    rng = np.random.default_rng(42)
    n = 96
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.3, size=n))
    bars = _make_bars(close)
    a = InsideBarBreakoutStrategy().generate_signals(bars, {})
    b = InsideBarBreakoutStrategy().generate_signals(bars, {})
    pd.testing.assert_frame_equal(a.df, b.df)


def test_empty_bars_returns_zero_length_signal() -> None:
    idx = pd.DatetimeIndex([], tz="UTC")
    ohlcv = pd.DataFrame(
        {
            "open": pd.Series(dtype=np.float64),
            "high": pd.Series(dtype=np.float64),
            "low": pd.Series(dtype=np.float64),
            "close": pd.Series(dtype=np.float64),
            "volume": pd.Series(dtype=np.float64),
            "quote_volume": pd.Series(dtype=np.float64),
            "taker_buy_volume": pd.Series(dtype=np.float64),
            "taker_buy_quote_volume": pd.Series(dtype=np.float64),
            "trade_count": pd.Series(dtype=np.int64),
        },
        index=idx,
    )
    bars = BarsData(ohlcv=ohlcv, symbol="TESTUSDT", interval="15m")
    sig = InsideBarBreakoutStrategy().generate_signals(bars, {})
    assert len(sig.df) == 0
