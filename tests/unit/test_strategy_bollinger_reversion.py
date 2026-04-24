"""Golden-case tests for Strategy L — Bollinger %B Reversion."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kairos.backtest.protocols import BarsData, SignalFrame, Strategy
from kairos.strategies.bollinger_reversion import BollingerReversionStrategy


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
    s: Strategy = BollingerReversionStrategy()
    assert s.name == "bollinger_reversion"
    assert "bb_window" in s.default_params


def test_gc_l1_long_entry_on_reentry_from_below() -> None:
    """Prior bar close well below lower band (%B < 0), current bar close
    back above lower band (inside the band) → long entry."""
    n = 40
    close = np.full(n, 100.0, dtype=np.float64)
    # Small oscillation for bars 0..18 to build modest std.
    for i in range(19):
        close[i] = 100.0 + (0.1 if i % 2 else -0.1)
    # Bar 19: deep plunge — close falls well below lower band.
    close[19] = 95.0
    # Bar 20: partial recovery — close above lower band (back inside band).
    close[20] = 99.5
    close[21:] = 99.5

    bars = _make_bars(close)
    sig = BollingerReversionStrategy().generate_signals(bars, {})
    side = sig.df["side"].to_numpy()
    assert side[20] == 1, f"expected +1 at bar 20, got {side[20]}"


def test_gc_l2_flattens_on_mid_cross() -> None:
    """After long entry, a close >= mid flattens the position."""
    n = 40
    close = np.full(n, 100.0, dtype=np.float64)
    for i in range(19):
        close[i] = 100.0 + (0.1 if i % 2 else -0.1)
    close[19] = 95.0
    close[20] = 99.5  # long entry here
    close[21:25] = 99.5  # hold while still below mid
    close[25:] = 101.0  # bounce above mid → flatten
    bars = _make_bars(close)
    sig = BollingerReversionStrategy().generate_signals(bars, {})
    side = sig.df["side"].to_numpy()
    assert side[20] == 1
    # Find the first bar at/after 21 where side == 0.
    flat_bars = np.where(side[21:] == 0)[0]
    assert len(flat_bars) > 0, "expected flatten after entry"


def test_idempotent_same_input_same_output() -> None:
    rng = np.random.default_rng(42)
    n = 96
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.3, size=n))
    bars = _make_bars(close)
    a = BollingerReversionStrategy().generate_signals(bars, {})
    b = BollingerReversionStrategy().generate_signals(bars, {})
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
    sig = BollingerReversionStrategy().generate_signals(bars, {})
    assert len(sig.df) == 0
