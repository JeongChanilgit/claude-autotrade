"""Golden-case tests for Strategy G — Donchian Channel Breakout."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kairos.backtest.protocols import BarsData, SignalFrame, Strategy
from kairos.strategies.donchian_breakout import DonchianBreakoutStrategy


def _make_bars(
    close: np.ndarray,
    high: np.ndarray | None = None,
    low: np.ndarray | None = None,
) -> BarsData:
    n = len(close)
    idx = pd.date_range("2024-01-01 00:00", periods=n, freq="15min", tz="UTC")
    hi = high if high is not None else close + 0.1
    lo = low if low is not None else close - 0.1
    vol = np.ones(n, dtype=np.float64)
    ohlcv = pd.DataFrame(
        {
            "open": close.astype(np.float64),
            "high": hi.astype(np.float64),
            "low": lo.astype(np.float64),
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


_PARAMS = {"channel": 10, "holding_bars": 48}


def test_protocol_conformance() -> None:
    s: Strategy = DonchianBreakoutStrategy()
    assert s.name == "donchian_breakout"
    assert "channel" in s.default_params


def test_gc_g1_long_breakout_entry() -> None:
    """Flat range [100, 100.5] for 12 bars, then a bar closes 102 — well above
    the prior 10-bar channel upper → long entry on the breakout bar.
    """
    n = 20
    close = np.concatenate(
        [
            np.linspace(100.0, 100.5, 12),
            np.array([102.0]),
            np.full(n - 13, 102.5),
        ]
    ).astype(np.float64)
    high = close + 0.1
    low = close - 0.1
    bars = _make_bars(close, high=high, low=low)
    sig = DonchianBreakoutStrategy().generate_signals(bars, _PARAMS)
    side = sig.df["side"].to_numpy()
    assert side[12] == 1, f"expected +1 at bar 12, got {side[12]}"


def test_gc_g2_flattens_on_lower_channel_break() -> None:
    """After a long breakout, a cliff drop takes close below the lower
    Donchian → flatten.
    """
    n = 40
    close = np.concatenate(
        [
            np.linspace(100.0, 100.5, 12),
            np.array([102.0]),          # breakout -> long at t=12
            np.full(10, 102.5),         # hold, channel tightens around 102
            np.full(n - 23, 95.0),      # cliff down → close breaks lower channel
        ]
    ).astype(np.float64)
    high = close + 0.1
    low = close - 0.1
    bars = _make_bars(close, high=high, low=low)
    sig = DonchianBreakoutStrategy().generate_signals(bars, _PARAMS)
    side = sig.df["side"].to_numpy()
    assert side[12] == 1
    # Somewhere in the cliff, side returns to 0 (the flatten bar).
    assert (side[23:30] == 0).any()


def test_idempotent_same_input_same_output() -> None:
    rng = np.random.default_rng(7)
    n = 120
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.3, size=n))
    bars = _make_bars(close.astype(np.float64))
    a = DonchianBreakoutStrategy().generate_signals(bars, _PARAMS)
    b = DonchianBreakoutStrategy().generate_signals(bars, _PARAMS)
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
    sig = DonchianBreakoutStrategy().generate_signals(bars, {})
    assert len(sig.df) == 0
