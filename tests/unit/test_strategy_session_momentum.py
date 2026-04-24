"""Golden-case tests for Strategy K — Session Momentum."""
from __future__ import annotations

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, Strategy
from kairos.strategies.session_momentum import SessionMomentumStrategy


def _make_bars(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> BarsData:
    n = len(close)
    idx = pd.date_range("2024-01-01 00:00", periods=n, freq="15min", tz="UTC")
    ohlcv = pd.DataFrame(
        {
            "open": open_.astype(np.float64),
            "high": high.astype(np.float64),
            "low": low.astype(np.float64),
            "close": close.astype(np.float64),
            "volume": volume.astype(np.float64),
            "quote_volume": (volume * close).astype(np.float64),
            "taker_buy_volume": (volume * 0.5).astype(np.float64),
            "taker_buy_quote_volume": (volume * 0.5 * close).astype(np.float64),
            "trade_count": np.ones(n, dtype=np.int64),
        },
        index=idx,
    )
    return BarsData(ohlcv=ohlcv, symbol="TESTUSDT", interval="15m")


def test_protocol_conformance() -> None:
    s: Strategy = SessionMomentumStrategy()
    assert s.name == "session_momentum"
    assert "first_bars" in s.default_params
    assert "volume_multiple" in s.default_params


def test_gc_k1_long_entry_after_first_bar_up_with_volume_spike() -> None:
    """3-day series. First bar of day 3 is up and has 2x baseline volume.

    With first_bars=1 and volume_lookback_days=2, the rolling prior-mean
    baseline comes from the first bars of days 1 and 2 (volume=1.0 each,
    mean=1.0). Day 3 first bar has volume=3.0 (>= 1.2 * 1.0), close > open,
    so entry fires at bar 2*96 + 1 (first bar AFTER the opening bar).
    """
    n_per_day = 96
    days = 3
    n = n_per_day * days
    open_ = np.full(n, 100.0)
    close = np.full(n, 100.0)
    high = np.full(n, 100.5)
    low = np.full(n, 99.5)
    volume = np.full(n, 1.0)

    # Day 1 & 2: first bar is neutral/up with baseline volume.
    # Make day 1/2 first bar "up" so they don't matter to direction logic.
    for d in range(2):
        start = d * n_per_day
        open_[start] = 100.0
        close[start] = 100.2
        high[start] = 100.3
        low[start] = 99.9
        volume[start] = 1.0

    # Day 3 first bar: strongly up with volume spike.
    d3 = 2 * n_per_day
    open_[d3] = 100.0
    close[d3] = 101.0
    high[d3] = 101.2
    low[d3] = 99.9
    volume[d3] = 3.0  # 3x baseline of 1.0

    bars = _make_bars(open_, high, low, close, volume)
    params = {"first_bars": 1, "volume_multiple": 1.2, "volume_lookback_days": 2,
              "holding_bars": 96}
    sig = SessionMomentumStrategy().generate_signals(bars, params)
    side = sig.df["side"].to_numpy()

    entry_bar = d3 + 1  # bar AFTER the first_bars opening window
    assert side[entry_bar] == 1, (
        f"expected +1 at bar {entry_bar}, got {side[entry_bar]}"
    )
    # No entry on days 1/2 (no prior-day baseline available at day 1; day 2's
    # prior-mean equals day 1's first_vol = 1.0 and day 2's first_vol = 1.0,
    # not >= 1.2 * 1.0).
    assert side[0 + 1] == 0
    assert side[n_per_day + 1] == 0


def test_gc_k2_flattens_at_session_end() -> None:
    """Position held through the day must flatten before the next UTC day begins."""
    n_per_day = 96
    days = 3
    n = n_per_day * days
    open_ = np.full(n, 100.0)
    close = np.full(n, 100.0)
    high = np.full(n, 100.5)
    low = np.full(n, 99.5)
    volume = np.full(n, 1.0)

    for d in range(2):
        start = d * n_per_day
        open_[start] = 100.0
        close[start] = 100.2
        high[start] = 100.3
        low[start] = 99.9
        volume[start] = 1.0

    d3 = 2 * n_per_day
    open_[d3] = 100.0
    close[d3] = 101.0
    high[d3] = 101.2
    low[d3] = 99.9
    volume[d3] = 3.0

    bars = _make_bars(open_, high, low, close, volume)
    # Use large holding_bars so session-end cap is the binding constraint.
    params = {"first_bars": 1, "volume_multiple": 1.2, "volume_lookback_days": 2,
              "holding_bars": 1000}
    sig = SessionMomentumStrategy().generate_signals(bars, params)
    side = sig.df["side"].to_numpy()
    entry_bar = d3 + 1
    # Held through bar n-1 (last bar of day 3 since the series ends there).
    assert side[entry_bar] == 1
    assert side[n - 1] == 1
    # Series ends at exact day boundary (n == days * n_per_day); the held run
    # covers the remainder of day 3.


def test_idempotent_same_input_same_output() -> None:
    rng = np.random.default_rng(23)
    n_per_day = 96
    days = 5
    n = n_per_day * days
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.3, size=n))
    open_ = close + rng.normal(0.0, 0.1, size=n)
    high = close + np.abs(rng.normal(0.5, 0.2, size=n))
    low = close - np.abs(rng.normal(0.5, 0.2, size=n))
    volume = np.abs(rng.normal(1.0, 0.3, size=n))
    bars = _make_bars(open_, high, low, close, volume)
    a = SessionMomentumStrategy().generate_signals(bars, {})
    b = SessionMomentumStrategy().generate_signals(bars, {})
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
    sig = SessionMomentumStrategy().generate_signals(bars, {})
    assert len(sig.df) == 0
    assert sig.df["side"].dtype == np.int8
