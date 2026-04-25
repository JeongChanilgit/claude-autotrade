"""Golden-case tests for Strategy P — VWAP Target Reversion (R:R)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from kairos.backtest.protocols import BarsData, Strategy
from kairos.strategies.vwap_target_rr import VwapTargetRrStrategy


def _make_bars(
    close: np.ndarray,
    high: np.ndarray | None = None,
    low: np.ndarray | None = None,
    volume: np.ndarray | None = None,
) -> BarsData:
    n = len(close)
    idx = pd.date_range("2024-01-01 00:00", periods=n, freq="15min", tz="UTC")
    close = close.astype(np.float64)
    high = (close + 0.1) if high is None else high.astype(np.float64)
    low = (close - 0.1) if low is None else low.astype(np.float64)
    vol = np.ones(n, dtype=np.float64) if volume is None else volume.astype(np.float64)
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
    s: Strategy = VwapTargetRrStrategy()
    assert s.name == "vwap_target_rr"
    assert "sigma_mult" in s.default_params
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
    sig = VwapTargetRrStrategy().generate_signals(bars, {})
    assert len(sig.df) == 0


def test_idempotent_same_input_same_output() -> None:
    rng = np.random.default_rng(13)
    n = 96
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.3, size=n))
    bars = _make_bars(close)
    a = VwapTargetRrStrategy().generate_signals(bars, {})
    b = VwapTargetRrStrategy().generate_signals(bars, {})
    pd.testing.assert_frame_equal(a.df, b.df)


def test_gc_p1_long_entry_then_target_hit() -> None:
    """Price oscillates at 100 to build VWAP~100 and σ~small. Then deep plunge
    at bar 6 triggers long entry at close=94. Target = VWAP (~close-to-100).
    Next bar spikes high past VWAP → target hit → side=0 on bar 7.
    """
    n = 12
    # Bars 0..5 oscillate around 100 to seed VWAP and σ.
    close = np.array(
        [100.2, 99.8, 100.2, 99.8, 100.2, 99.8, 94.0, 101.0, 100.0, 100.0, 100.0, 100.0],
        dtype=np.float64,
    )
    high = close + 0.1
    low = close - 0.1
    # Target-hit bar: set high well above VWAP.
    high[7] = 200.0

    bars = _make_bars(close, high=high, low=low)
    params = {
        "sigma_mult": 2.0,
        "risk_reward": 1.5,
        "holding_bars": 24,
        "min_session_bars": 4,
    }
    sig = VwapTargetRrStrategy().generate_signals(bars, params).df
    side = sig["side"].to_numpy()
    stop = sig["stop"].to_numpy()
    target = sig["target"].to_numpy()

    assert side[6] == 1, f"expected long entry at bar 6, got {side[6]}"
    assert np.isfinite(stop[6]) and stop[6] < 94.0
    assert np.isfinite(target[6]) and target[6] > 94.0
    # Target distance D = target-close; stop = close - D/rr.
    d = target[6] - 94.0
    expected_stop = 94.0 - d / 1.5
    assert abs(stop[6] - expected_stop) < 1e-9
    # Target hit at bar 7 → flat.
    assert side[7] == 0
    assert np.isnan(stop[7])
    assert np.isnan(target[7])


def test_gc_p2_long_entry_then_stop_hit() -> None:
    """Same long entry at bar 6, but bar 7's low pushes below the stop → stop wins."""
    n = 12
    close = np.array(
        [100.2, 99.8, 100.2, 99.8, 100.2, 99.8, 94.0, 80.0, 80.0, 80.0, 80.0, 80.0],
        dtype=np.float64,
    )
    high = close + 0.1
    low = close - 0.1
    # Stop-hit bar: push low very deep.
    low[7] = 50.0
    high[7] = 94.0  # not near target

    bars = _make_bars(close, high=high, low=low)
    params = {
        "sigma_mult": 2.0,
        "risk_reward": 1.5,
        "holding_bars": 24,
        "min_session_bars": 4,
    }
    sig = VwapTargetRrStrategy().generate_signals(bars, params).df
    side = sig["side"].to_numpy()
    stop = sig["stop"].to_numpy()

    assert side[6] == 1
    assert np.isfinite(stop[6])
    # Bar 7 stop-hit -> flat.
    assert side[7] == 0
    assert np.isnan(sig["stop"].to_numpy()[7])
    assert np.isnan(sig["target"].to_numpy()[7])
