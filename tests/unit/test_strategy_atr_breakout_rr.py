"""Golden-case tests for Strategy N — ATR Breakout (R:R)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, Strategy
from kairos.strategies.atr_breakout_rr import AtrBreakoutRrStrategy


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
    s: Strategy = AtrBreakoutRrStrategy()
    assert s.name == "atr_breakout_rr"
    assert "lookback" in s.default_params
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
    sig = AtrBreakoutRrStrategy().generate_signals(bars, {})
    assert len(sig.df) == 0


def test_idempotent_same_input_same_output() -> None:
    rng = np.random.default_rng(7)
    n = 200
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.4, size=n))
    bars = _make_bars(close)
    a = AtrBreakoutRrStrategy().generate_signals(bars, {})
    b = AtrBreakoutRrStrategy().generate_signals(bars, {})
    pd.testing.assert_frame_equal(a.df, b.df)


def test_gc_n1_long_entry_then_target_hit() -> None:
    """Flat range, upside breakout at bar 6, target hit at bar 8.

    lookback=5, atr_window=5, stop_atr_mult=1.5, risk_reward=2.0.
    Bars 0..5 sit at 100 with tight ranges. Bar 6 closes at 110 with
    high=110.5 low=99.5 (wide range, so ATR jumps). Bar 7 keeps price
    above stop, bar 8 spikes high past target.
    """
    n = 12
    close = np.full(n, 100.0, dtype=np.float64)
    high = close + 0.1
    low = close - 0.1
    # Breakout bar.
    close[6] = 110.0
    high[6] = 110.5
    low[6] = 99.5
    # Hold bars — stay well above stop and below target.
    close[7] = 111.0
    high[7] = 111.5
    low[7] = 110.5
    # Target-hit bar: push high above target.
    close[8] = 130.0
    high[8] = 200.0
    low[8] = 111.0
    close[9:] = 130.0
    high[9:] = 130.1
    low[9:] = 129.9

    bars = _make_bars(close, high=high, low=low)
    params = {
        "lookback": 5,
        "atr_window": 5,
        "stop_atr_mult": 1.5,
        "risk_reward": 2.0,
        "holding_bars": 48,
    }
    sig = AtrBreakoutRrStrategy().generate_signals(bars, params).df
    side = sig["side"].to_numpy()
    stop = sig["stop"].to_numpy()
    target = sig["target"].to_numpy()

    assert side[6] == 1, f"expected long entry at bar 6, got side={side[6]}"
    assert np.isfinite(stop[6]) and stop[6] < 110.0
    assert np.isfinite(target[6]) and target[6] > 110.0
    # Target distance == 2 * (close - stop).
    assert abs((target[6] - 110.0) - 2.0 * (110.0 - stop[6])) < 1e-9
    assert side[7] == 1
    # Target hit at bar 8 -> side flips to 0 on bar 8.
    assert side[8] == 0
    # After exit, stop/target should be NaN on flat bars.
    assert np.isnan(stop[8])
    assert np.isnan(target[8])


def test_gc_n2_long_entry_then_stop_hit() -> None:
    """Same breakout setup, but bar 7 drops below the stop → exit on stop."""
    n = 12
    close = np.full(n, 100.0, dtype=np.float64)
    high = close + 0.1
    low = close - 0.1
    close[6] = 110.0
    high[6] = 110.5
    low[6] = 99.5
    # Stop hit bar: push low deep below any plausible stop.
    close[7] = 80.0
    high[7] = 110.5
    low[7] = 50.0
    close[8:] = 80.0
    high[8:] = 80.1
    low[8:] = 79.9

    bars = _make_bars(close, high=high, low=low)
    params = {
        "lookback": 5,
        "atr_window": 5,
        "stop_atr_mult": 1.5,
        "risk_reward": 2.0,
        "holding_bars": 48,
    }
    sig = AtrBreakoutRrStrategy().generate_signals(bars, params).df
    side = sig["side"].to_numpy()
    stop = sig["stop"].to_numpy()

    assert side[6] == 1
    assert np.isfinite(stop[6])
    # Bar 7 stop-hit -> flat.
    assert side[7] == 0
    assert np.isnan(sig["stop"].to_numpy()[7])
