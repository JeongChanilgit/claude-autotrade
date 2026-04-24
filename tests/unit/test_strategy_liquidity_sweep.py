"""Golden-case tests for Strategy J — Liquidity Sweep Reversal."""
from __future__ import annotations

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, Strategy
from kairos.strategies.liquidity_sweep import LiquiditySweepStrategy


def _make_bars(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
) -> BarsData:
    n = len(close)
    idx = pd.date_range("2024-01-01 00:00", periods=n, freq="15min", tz="UTC")
    vol = np.ones(n, dtype=np.float64)
    ohlcv = pd.DataFrame(
        {
            "open": open_.astype(np.float64),
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
    s: Strategy = LiquiditySweepStrategy()
    assert s.name == "liquidity_sweep"
    assert "lookback" in s.default_params
    assert "min_wick_pct" in s.default_params


def test_gc_j1_short_entry_on_bull_trap_sweep() -> None:
    """Bar 21 pokes above the prior 20-bar high but closes back below it.

    Bars 0..20 have high=101.0 (flat range), so prior_high at bar 21 is 101.0.
    Bar 21: high=102.0 > 101.0 (sweep), close=100.5 < 101.0 (trapped back).
    Wick pct = (102 - 100.5)/100.5 ≈ 0.0149 >= 0.002.
    Expect short entry: side[21] == -1.
    """
    n = 30
    open_ = np.full(n, 100.0, dtype=np.float64)
    high = np.full(n, 101.0, dtype=np.float64)
    low = np.full(n, 99.0, dtype=np.float64)
    close = np.full(n, 100.0, dtype=np.float64)
    open_[21] = 100.2
    high[21] = 102.0
    low[21] = 99.8
    close[21] = 100.5
    bars = _make_bars(open_, high, low, close)
    sig = LiquiditySweepStrategy().generate_signals(bars, {})
    side = sig.df["side"].to_numpy()
    assert side[21] == -1, f"expected -1 short at bar 21, got {side[21]}"


def test_gc_j2_flattens_on_holding_cap() -> None:
    """After a short entry, position flattens once holding_bars are held.

    holding_bars=8: entry at bar 21, last held bar is 28 (21+8-1),
    flatten on bar 29.
    """
    n = 35
    open_ = np.full(n, 100.0, dtype=np.float64)
    high = np.full(n, 101.0, dtype=np.float64)
    low = np.full(n, 99.0, dtype=np.float64)
    close = np.full(n, 100.0, dtype=np.float64)
    open_[21] = 100.2
    high[21] = 102.0
    low[21] = 99.8
    close[21] = 100.5
    bars = _make_bars(open_, high, low, close)
    sig = LiquiditySweepStrategy().generate_signals(bars, {})
    side = sig.df["side"].to_numpy()
    assert side[21] == -1
    # Bars 22..28 still held short.
    assert side[28] == -1, f"expected -1 at bar 28, got {side[28]}"
    # Flatten on the bar AFTER holding cap.
    assert side[29] == 0, f"expected 0 at bar 29, got {side[29]}"


def test_idempotent_same_input_same_output() -> None:
    rng = np.random.default_rng(11)
    n = 150
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.3, size=n))
    high = close + np.abs(rng.normal(0.5, 0.2, size=n))
    low = close - np.abs(rng.normal(0.5, 0.2, size=n))
    open_ = close + rng.normal(0.0, 0.1, size=n)
    bars = _make_bars(open_, high, low, close)
    a = LiquiditySweepStrategy().generate_signals(bars, {})
    b = LiquiditySweepStrategy().generate_signals(bars, {})
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
    sig = LiquiditySweepStrategy().generate_signals(bars, {})
    assert len(sig.df) == 0
    assert sig.df["side"].dtype == np.int8
