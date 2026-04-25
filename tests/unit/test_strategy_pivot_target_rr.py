"""Golden-case tests for Strategy Q — Pivot Target Reversion (R:R)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, Strategy
from kairos.strategies.pivot_target_rr import PivotTargetRrStrategy


def _make_bars(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
) -> BarsData:
    n = len(close)
    idx = pd.date_range("2024-01-01 00:00", periods=n, freq="15min", tz="UTC")
    close = close.astype(np.float64)
    high = high.astype(np.float64)
    low = low.astype(np.float64)
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


def _build_two_days(entry_bar_high: float, entry_bar_low: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Two UTC days of 15m bars.

    Day 1: flat around 100, single high=110 somewhere, single low=90 elsewhere,
    last close=100. So pivots for day 2: P=100, S1=90, R1=110.
    Day 2: bars flat at 95 with safe ranges; entry bar at day2 index 5 with the
    supplied high/low (used by the caller to pick which exit to stage).
    """
    bars_per_day = 96
    n = bars_per_day * 2
    close = np.full(n, 100.0, dtype=np.float64)
    high = close + 0.1
    low = close - 0.1

    # Day 1: inject a true high and a true low.
    high[10] = 110.0
    low[20] = 90.0
    # Day 1 last close = 100 (already the default).
    close[bars_per_day - 1] = 100.0
    high[bars_per_day - 1] = 100.1
    low[bars_per_day - 1] = 99.9

    # Day 2: flat at 95, high=95.1, low=94.9 (safe — low > S1=90).
    d2 = bars_per_day
    close[d2:] = 95.0
    high[d2:] = 95.1
    low[d2:] = 94.9

    # Entry bar at day2 index 5 (absolute index = bars_per_day + 5).
    entry_idx = d2 + 5
    close[entry_idx] = 95.0
    high[entry_idx] = entry_bar_high
    low[entry_idx] = entry_bar_low

    return close, high, low


def test_protocol_conformance() -> None:
    s: Strategy = PivotTargetRrStrategy()
    assert s.name == "pivot_target_rr"
    assert "risk_reward" in s.default_params
    assert "holding_bars" in s.default_params


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
    sig = PivotTargetRrStrategy().generate_signals(bars, {})
    assert len(sig.df) == 0


def test_idempotent_same_input_same_output() -> None:
    close, high, low = _build_two_days(entry_bar_high=95.1, entry_bar_low=89.5)
    bars = _make_bars(close, high, low)
    a = PivotTargetRrStrategy().generate_signals(bars, {})
    b = PivotTargetRrStrategy().generate_signals(bars, {})
    pd.testing.assert_frame_equal(a.df, b.df)


def test_gc_q1_long_entry_then_target_hit() -> None:
    """Day 1: H=110, L=90, C=100 → day 2 pivots P=100, S1=90, R1=110.

    Entry bar (day2 index 5): low=89.5 (touches S1), close=95, high=95.1.
    Target=P=100, D=5, stop = 95 - 5/2 = 92.5 (rr=2.0).
    Next bar (day2 index 6): high spikes to 101 → target hit → side=0.
    """
    close, high, low = _build_two_days(entry_bar_high=95.1, entry_bar_low=89.5)
    # Target-hit bar (day2 idx 6).
    target_bar = 96 + 6
    high[target_bar] = 101.0
    low[target_bar] = 94.9

    bars = _make_bars(close, high, low)
    params = {"risk_reward": 2.0, "holding_bars": 48}
    sig = PivotTargetRrStrategy().generate_signals(bars, params).df
    side = sig["side"].to_numpy()
    stop = sig["stop"].to_numpy()
    target = sig["target"].to_numpy()

    entry = 96 + 5
    assert side[entry] == 1, f"expected long entry at bar {entry}, got {side[entry]}"
    assert np.isfinite(target[entry]) and abs(target[entry] - 100.0) < 1e-9
    assert np.isfinite(stop[entry]) and abs(stop[entry] - 92.5) < 1e-9

    # Target hit on next bar.
    assert side[entry + 1] == 0
    assert np.isnan(stop[entry + 1])
    assert np.isnan(target[entry + 1])


def test_gc_q2_long_entry_then_stop_hit() -> None:
    """Same entry setup; bar after entry drops low below stop=92.5 → stop wins."""
    close, high, low = _build_two_days(entry_bar_high=95.1, entry_bar_low=89.5)
    stop_bar = 96 + 6
    high[stop_bar] = 95.1
    low[stop_bar] = 90.0  # below stop=92.5

    bars = _make_bars(close, high, low)
    params = {"risk_reward": 2.0, "holding_bars": 48}
    sig = PivotTargetRrStrategy().generate_signals(bars, params).df
    side = sig["side"].to_numpy()
    stop = sig["stop"].to_numpy()

    entry = 96 + 5
    assert side[entry] == 1
    assert np.isfinite(stop[entry])
    assert side[entry + 1] == 0
    assert np.isnan(sig["stop"].to_numpy()[entry + 1])
    assert np.isnan(sig["target"].to_numpy()[entry + 1])
