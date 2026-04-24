"""Golden-case tests for Strategy H — RSI(2) Pullback."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kairos.backtest.protocols import BarsData, SignalFrame, Strategy
from kairos.strategies.rsi2_pullback import Rsi2PullbackStrategy


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


# Shorter SMA keeps the synthetic window small while exercising the same
# "close vs long trend SMA" gating logic as the 200-bar default.
_PARAMS = {
    "rsi_window": 2,
    "trend_sma_window": 20,
    "rsi_long_thresh": 10.0,
    "rsi_short_thresh": 90.0,
    "holding_bars": 12,
}


def test_protocol_conformance() -> None:
    s: Strategy = Rsi2PullbackStrategy()
    assert s.name == "rsi2_pullback"
    assert "rsi_window" in s.default_params
    assert "trend_sma_window" in s.default_params


def test_gc_h1_long_entry_on_rsi2_oversold_in_uptrend() -> None:
    """Linear uptrend (close > SMA(20)) followed by a sharp 3-bar dip drops
    RSI(2) below 10 → long entry on the dip-bottom bar.
    """
    n = 40
    close = np.concatenate(
        [
            np.linspace(100.0, 120.0, 25),
            np.array([119.5, 118.0, 115.0]),   # dip → rsi2 crashes at t=27
            np.full(n - 28, 117.0),
        ]
    ).astype(np.float64)
    bars = _make_bars(close)
    sig = Rsi2PullbackStrategy().generate_signals(bars, _PARAMS)
    side = sig.df["side"].to_numpy()
    assert side[27] == 1, f"expected +1 at bar 27, got {side[27]}"


def test_gc_h2_flattens_on_rsi_revert() -> None:
    """After long entry at bar 27, bar 28 bounces back to 117 — rsi2 pops
    above 50 → flatten on bar 28.
    """
    n = 40
    close = np.concatenate(
        [
            np.linspace(100.0, 120.0, 25),
            np.array([119.5, 118.0, 115.0]),
            np.full(n - 28, 117.0),
        ]
    ).astype(np.float64)
    bars = _make_bars(close)
    sig = Rsi2PullbackStrategy().generate_signals(bars, _PARAMS)
    side = sig.df["side"].to_numpy()
    assert side[27] == 1
    assert side[28] == 0


def test_idempotent_same_input_same_output() -> None:
    rng = np.random.default_rng(11)
    n = 200
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.3, size=n))
    bars = _make_bars(close.astype(np.float64))
    a = Rsi2PullbackStrategy().generate_signals(bars, _PARAMS)
    b = Rsi2PullbackStrategy().generate_signals(bars, _PARAMS)
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
    sig = Rsi2PullbackStrategy().generate_signals(bars, {})
    assert len(sig.df) == 0
