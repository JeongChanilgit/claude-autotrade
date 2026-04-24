"""Golden-case tests for Strategy M — Supertrend Follower."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kairos.backtest.protocols import BarsData, SignalFrame, Strategy
from kairos.strategies.supertrend import SupertrendStrategy


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
    s: Strategy = SupertrendStrategy()
    assert s.name == "supertrend"
    assert "atr_window" in s.default_params


def test_gc_m1_long_direction_on_rally() -> None:
    """Quiet range followed by a sustained rally — Supertrend flips to +1
    and stays long for several bars after the flip."""
    n = 60
    close = np.full(n, 100.0, dtype=np.float64)
    for i in range(30):
        close[i] = 100.0 + (0.05 if i % 2 else -0.05)
    # Monotonic rally from bar 30 onward.
    for i in range(30, n):
        close[i] = 100.0 + (i - 29) * 1.5

    bars = _make_bars(close)
    sig = SupertrendStrategy().generate_signals(bars, {"atr_window": 10, "atr_mult": 3.0})
    side = sig.df["side"].to_numpy()
    # Somewhere in the rally we should see a +1 stretch.
    assert (side == 1).any(), "expected at least one +1 bar during rally"
    # The last few bars should be +1.
    assert side[-1] == 1, f"expected +1 at final bar, got {side[-1]}"


def test_gc_m2_flip_to_short_on_crash() -> None:
    """After an up-trend, a sharp crash through the lower band flips
    direction to -1."""
    n = 80
    close = np.full(n, 100.0, dtype=np.float64)
    for i in range(15):
        close[i] = 100.0 + (0.05 if i % 2 else -0.05)
    # Rally bars 15..44
    for i in range(15, 45):
        close[i] = 100.0 + (i - 14) * 1.5
    # Sharp crash bars 45..79
    top = close[44]
    for i in range(45, n):
        close[i] = top - (i - 44) * 2.5

    bars = _make_bars(close)
    sig = SupertrendStrategy().generate_signals(bars, {"atr_window": 10, "atr_mult": 3.0})
    side = sig.df["side"].to_numpy()
    # After the crash we expect -1 in the tail.
    assert side[-1] == -1, f"expected -1 at final bar, got {side[-1]}"


def test_idempotent_same_input_same_output() -> None:
    rng = np.random.default_rng(42)
    n = 96
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.3, size=n))
    bars = _make_bars(close)
    a = SupertrendStrategy().generate_signals(bars, {})
    b = SupertrendStrategy().generate_signals(bars, {})
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
    sig = SupertrendStrategy().generate_signals(bars, {})
    assert len(sig.df) == 0
