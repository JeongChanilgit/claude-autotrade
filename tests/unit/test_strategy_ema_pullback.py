"""Golden-case tests for Strategy F — EMA Pullback Trend."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kairos.backtest.protocols import BarsData, SignalFrame, Strategy
from kairos.strategies.ema_pullback import EmaPullbackStrategy


def _make_bars(
    close: np.ndarray,
    high: np.ndarray | None = None,
    low: np.ndarray | None = None,
) -> BarsData:
    n = len(close)
    idx = pd.date_range("2024-01-01 00:00", periods=n, freq="15min", tz="UTC")
    hi = high if high is not None else close + 0.3
    lo = low if low is not None else close - 0.3
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


# Smaller EMA spans keep the golden-case window tractable while exercising the
# same alignment / pullback / reclaim logic as the defaults.
_SMALL_PARAMS = {"ema_fast": 5, "ema_mid": 10, "ema_slow": 20, "holding_bars": 32}


def test_protocol_conformance() -> None:
    s: Strategy = EmaPullbackStrategy()
    assert s.name == "ema_pullback"
    assert "ema_fast" in s.default_params
    assert "holding_bars" in s.default_params


def test_gc_f1_long_pullback_entry() -> None:
    """Strong uptrend establishes long-trend stack; one bar's low dips to
    ema_fast and the following close reclaims it → long entry.

    Constructed so the EMA(5/10/20) stack aligns long by bar ~20, then bars
    25..26 form a pullback whose low touches ema_fast at bar 26, and bar 27
    reclaims with a close above ema_fast.
    """
    n = 40
    close = np.concatenate(
        [
            np.linspace(100.0, 120.0, 25),
            np.array([119.5, 119.0]),   # pullback
            np.array([120.5]),          # reclaim -> entry at t=27
            np.linspace(121.0, 125.0, n - 28),
        ]
    ).astype(np.float64)
    high = close + 0.3
    low = close - 0.3
    # Force bar 26 to print a low at/below ema_fast[26] so that the T-1
    # pullback check on bar 27 is satisfied.
    low[26] = 118.5
    bars = _make_bars(close, high=high, low=low)
    sig = EmaPullbackStrategy().generate_signals(bars, _SMALL_PARAMS)
    side = sig.df["side"].to_numpy()
    assert side[27] == 1, f"expected +1 at bar 27, got {side[27]}"


def test_gc_f2_flattens_on_trend_break() -> None:
    """After a long entry, a cliff drop inverts the EMA stack → flatten."""
    n = 45
    close = np.concatenate(
        [
            np.linspace(100.0, 120.0, 25),
            np.array([119.5, 119.0]),
            np.array([120.5]),   # entry at t=27
            np.array([121.0, 120.0, 110.0, 100.0, 90.0, 80.0]),
            np.full(n - 34, 80.0),
        ]
    ).astype(np.float64)
    high = close + 0.3
    low = close - 0.3
    low[26] = 118.5
    bars = _make_bars(close, high=high, low=low)
    sig = EmaPullbackStrategy().generate_signals(bars, _SMALL_PARAMS)
    side = sig.df["side"].to_numpy()
    assert side[27] == 1
    # Trend break during the cliff causes a flatten before the short-trend
    # stack inverts fully. Assert a flatten bar exists in the cliff window.
    assert (side[30:35] == 0).any()


def test_idempotent_same_input_same_output() -> None:
    rng = np.random.default_rng(42)
    n = 120
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.3, size=n))
    bars = _make_bars(close.astype(np.float64))
    a = EmaPullbackStrategy().generate_signals(bars, _SMALL_PARAMS)
    b = EmaPullbackStrategy().generate_signals(bars, _SMALL_PARAMS)
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
    sig = EmaPullbackStrategy().generate_signals(bars, {})
    assert len(sig.df) == 0
