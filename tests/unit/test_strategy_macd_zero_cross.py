"""Golden-case tests for Strategy I — MACD Zero-Cross Momentum."""
from __future__ import annotations

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, Strategy
from kairos.strategies.macd_zero_cross import MacdZeroCrossStrategy


def _make_bars(close: np.ndarray) -> BarsData:
    n = len(close)
    idx = pd.date_range("2024-01-01 00:00", periods=n, freq="15min", tz="UTC")
    high = close + 0.1
    low = close - 0.1
    vol = np.ones(n, dtype=np.float64)
    ohlcv = pd.DataFrame(
        {
            "open": close.astype(np.float64),
            "high": high.astype(np.float64),
            "low": low.astype(np.float64),
            "close": close.astype(np.float64),
            "volume": vol,
            "quote_volume": (vol * close).astype(np.float64),
            "taker_buy_volume": (vol * 0.5).astype(np.float64),
            "taker_buy_quote_volume": (vol * 0.5 * close).astype(np.float64),
            "trade_count": np.ones(n, dtype=np.int64),
        },
        index=idx,
    )
    return BarsData(ohlcv=ohlcv, symbol="TESTUSDT", interval="15m")


def test_protocol_conformance() -> None:
    s: Strategy = MacdZeroCrossStrategy()
    assert s.name == "macd_zero_cross"
    assert "fast" in s.default_params
    assert "slow" in s.default_params
    assert "atr_min_pct" in s.default_params


def test_gc_i1_long_entry_on_macd_zero_cross_up() -> None:
    """Downtrend primes MACD below zero; strong rally drives it above zero.

    With fast=12/slow=26, the descending leg (bars 0..25) pushes MACD
    deeply negative; the recovery rally flips MACD above zero at bar 33.
    """
    n = 60
    close = np.full(n, 100.0, dtype=np.float64)
    for i in range(26):
        close[i] = 100.0 - i * 0.5
    for i in range(26, n):
        close[i] = close[25] + (i - 25) * 1.5
    bars = _make_bars(close)
    sig = MacdZeroCrossStrategy().generate_signals(bars, {})
    side = sig.df["side"].to_numpy()
    # Bar 33 is the first bar where MACD crosses from <=0 to >0.
    assert side[33] == 1, f"expected +1 long entry at bar 33, got {side[33]}"


def test_gc_i2_flattens_on_opposite_zero_cross() -> None:
    """After a long entry, a subsequent decline pushes MACD back under zero."""
    n = 80
    close = np.full(n, 100.0, dtype=np.float64)
    for i in range(26):
        close[i] = 100.0 - i * 0.5
    for i in range(26, 45):
        close[i] = close[25] + (i - 25) * 1.5
    for i in range(45, n):
        close[i] = close[44] - (i - 44) * 2.0
    bars = _make_bars(close)
    sig = MacdZeroCrossStrategy().generate_signals(bars, {})
    side = sig.df["side"].to_numpy()
    assert side[33] == 1
    # Bar 55 is where MACD first crosses back below zero.
    assert side[55] == 0, f"expected flat at bar 55, got {side[55]}"


def test_idempotent_same_input_same_output() -> None:
    rng = np.random.default_rng(17)
    n = 200
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.5, size=n))
    bars = _make_bars(close)
    a = MacdZeroCrossStrategy().generate_signals(bars, {})
    b = MacdZeroCrossStrategy().generate_signals(bars, {})
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
    sig = MacdZeroCrossStrategy().generate_signals(bars, {})
    assert len(sig.df) == 0
    assert sig.df["side"].dtype == np.int8
