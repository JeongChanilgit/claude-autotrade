"""Golden-case tests for Strategy O — Swing Trend Pullback (R:R)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, Strategy
from kairos.strategies.swing_trend_rr import SwingTrendRrStrategy


def _make_bars(
    close: np.ndarray,
    high: np.ndarray | None = None,
    low: np.ndarray | None = None,
) -> BarsData:
    n = len(close)
    idx = pd.date_range("2024-01-01 00:00", periods=n, freq="15min", tz="UTC")
    close = close.astype(np.float64)
    high = (close + 0.1) if high is None else high.astype(np.float64)
    low = (close - 0.1) if low is None else low.astype(np.float64)
    vol = np.ones(n, dtype=np.float64)
    ohlcv = pd.DataFrame(
        {
            "open": close.copy(),
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
            "quote_volume": vol * close,
            "taker_buy_volume": vol * 0.5,
            "taker_buy_quote_volume": vol * 0.5 * close,
            "trade_count": np.ones(n, dtype=np.int64),
        },
        index=idx,
    )
    return BarsData(ohlcv=ohlcv, symbol="TESTUSDT", interval="15m")


def test_protocol_conformance() -> None:
    s: Strategy = SwingTrendRrStrategy()
    assert s.name == "swing_trend_rr"
    assert "ema_fast" in s.default_params
    assert "risk_reward" in s.default_params


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
    sig = SwingTrendRrStrategy().generate_signals(bars, {})
    assert len(sig.df) == 0


def test_idempotent_same_input_same_output() -> None:
    rng = np.random.default_rng(11)
    n = 200
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.3, size=n))
    bars = _make_bars(close)
    a = SwingTrendRrStrategy().generate_signals(bars, {})
    b = SwingTrendRrStrategy().generate_signals(bars, {})
    pd.testing.assert_frame_equal(a.df, b.df)


def _pullback_reclaim_base() -> np.ndarray:
    """Rising series that establishes uptrend, with a pullback at bar 20 and
    reclaim at bar 21. ema_fast=3 > ema_slow=5 throughout, close[20]<ef[20]
    and close[21]>ef[21] (verified numerically).
    """
    n = 30
    close = np.zeros(n, dtype=np.float64)
    for i in range(n):
        close[i] = 100.0 + i
    close[20] = 115.0   # pullback below ema_fast
    close[21] = 125.0   # reclaim above ema_fast
    close[22:] = 126.0
    return close


def test_gc_o1_long_entry_then_target_hit() -> None:
    """Pullback-reclaim enters long at bar 21; bar 22 high spikes past target."""
    close = _pullback_reclaim_base()
    n = len(close)
    high = close + 0.1
    low = close - 0.1
    # Bar 22: keep close tame but spike the high far above the target.
    close[22] = 126.0
    high[22] = 200.0
    low[22] = 125.0
    close[23:] = 126.0
    high[23:] = 126.1
    low[23:] = 125.9

    bars = _make_bars(close, high=high, low=low)
    params = {
        "ema_fast": 3,
        "ema_slow": 5,
        "swing_window": 3,
        "risk_reward": 2.0,
        "holding_bars": 60,
    }
    sig = SwingTrendRrStrategy().generate_signals(bars, params).df
    side = sig["side"].to_numpy()
    stop = sig["stop"].to_numpy()
    target = sig["target"].to_numpy()

    assert side[21] == 1, f"expected long entry at bar 21, got side={side[21]}"
    assert np.isfinite(stop[21]) and stop[21] < 125.0
    assert np.isfinite(target[21]) and target[21] > 125.0
    assert abs((target[21] - 125.0) - 2.0 * (125.0 - stop[21])) < 1e-9
    # Bar 22 target hit -> flat.
    assert side[22] == 0
    assert np.isnan(stop[22])


def test_gc_o2_long_entry_then_stop_hit() -> None:
    """Same entry at bar 21; bar 22 low crashes through the swing-low stop."""
    close = _pullback_reclaim_base()
    n = len(close)
    high = close + 0.1
    low = close - 0.1
    # Bar 22: collapse low far below swing-low to guarantee stop hit.
    close[22] = 100.0
    high[22] = 125.5
    low[22] = 50.0
    close[23:] = 100.0
    high[23:] = 100.1
    low[23:] = 99.9

    bars = _make_bars(close, high=high, low=low)
    params = {
        "ema_fast": 3,
        "ema_slow": 5,
        "swing_window": 3,
        "risk_reward": 2.0,
        "holding_bars": 60,
    }
    sig = SwingTrendRrStrategy().generate_signals(bars, params).df
    side = sig["side"].to_numpy()

    assert side[21] == 1
    # Bar 22 stop hit -> flat.
    assert side[22] == 0
