"""Golden-case tests for Strategy D — VWAP Reversion."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kairos.backtest.protocols import BarsData, SignalFrame, Strategy
from kairos.strategies.vwap_reversion import VwapReversionStrategy


def _make_bars(close: np.ndarray, volume: np.ndarray | None = None) -> BarsData:
    n = len(close)
    idx = pd.date_range("2024-01-01 00:00", periods=n, freq="15min", tz="UTC")
    high = close + 0.1
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
    # Structural check: an instance satisfies the Strategy Protocol.
    s: Strategy = VwapReversionStrategy()
    assert s.name == "vwap_reversion"
    assert "sigma_mult" in s.default_params


def test_gc_d1_long_entry_on_sigma_breach() -> None:
    """A staircase plunge drives close below VWAP − 2σ with Wilder-RSI < 30.

    Bars 0..4 oscillate around 100 to prime Wilder RSI. Bars 5..9 are a
    monotonically decreasing plunge; by bar 9, RSI(14) ≈ 23 (< 30) and the
    close (94.0) sits well below VWAP − 2σ.
    """
    n = 20
    close = np.full(n, 100.0, dtype=np.float64)
    for i in range(1, 5):
        close[i] = 100.0 + (0.2 if i % 2 else -0.2)
    close[5] = 99.5
    close[6] = 99.0
    close[7] = 98.0
    close[8] = 96.0
    close[9] = 94.0
    close[10:] = 94.1

    bars = _make_bars(close)
    sig = VwapReversionStrategy().generate_signals(bars, {})
    side = sig.df["side"].to_numpy()
    assert side[9] == 1, f"expected +1 at bar 9, got {side[9]}"


def test_gc_d2_flattens_on_vwap_recross() -> None:
    """After a long entry from an extreme dip, a close above VWAP flattens."""
    n = 20
    close = np.full(n, 100.0, dtype=np.float64)
    for i in range(1, 5):
        close[i] = 100.0 + (0.2 if i % 2 else -0.2)
    close[5] = 99.5
    close[6] = 99.0
    close[7] = 98.0
    close[8] = 96.0
    close[9] = 94.0  # long entry here
    close[10:13] = 94.1  # hold while still below VWAP
    close[13:] = 101.0  # sharp bounce above VWAP → flatten on bar 13
    bars = _make_bars(close)
    sig = VwapReversionStrategy().generate_signals(bars, {})
    side = sig.df["side"].to_numpy()
    assert side[9] == 1
    assert side[13] == 0


def test_idempotent_same_input_same_output() -> None:
    rng = np.random.default_rng(42)
    n = 96  # one UTC day of 15-min bars
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.3, size=n))
    bars = _make_bars(close)
    a = VwapReversionStrategy().generate_signals(bars, {})
    b = VwapReversionStrategy().generate_signals(bars, {})
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
    sig = VwapReversionStrategy().generate_signals(bars, {})
    assert len(sig.df) == 0
