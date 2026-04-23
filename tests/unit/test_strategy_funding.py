"""Golden-case tests for Strategy B (Funding Counter).

Verifies the 5 golden cases in `docs/strategies/B-funding-counter.md`,
plus idempotency and the ValueError contract on missing inputs.

All fixtures are synthetic:
  - ohlcv: 1-minute bars (enough history for SMA60 and 24h OI delta)
  - funding: 8-hour spacing
  - metrics: 5-minute spacing
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from kairos.backtest.protocols import BarsData, SignalFrame
from kairos.strategies.funding import FundingCounterStrategy

# ---------------------------------------------------------------------------
# Synthetic-data helpers
# ---------------------------------------------------------------------------

# Give a generous history buffer: 24h (1440 bars) + SMA60 warm-up, plus
# plenty of runway so the golden event falls well inside the window.
HISTORY_MINUTES = 1440 + 240  # 1680 minutes warm-up before any "event"
START = pd.Timestamp("2024-02-01 00:00:00", tz="UTC")


def _ohlcv_from_close(close: np.ndarray, index: pd.DatetimeIndex) -> pd.DataFrame:
    n = len(index)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": np.full(n, 1000.0),
            "quote_volume": close * 1000.0,
            "taker_buy_volume": np.full(n, 500.0),
            "taker_buy_quote_volume": close * 500.0,
            "trade_count": np.full(n, 100, dtype=np.int64),
        },
        index=index,
    )


def _make_funding(
    ohlcv_index: pd.DatetimeIndex,
    recent_rates: list[float],
    baseline: float = 0.0,
    future_rates: list[float] | None = None,
) -> pd.DataFrame:
    """Build an 8h-spaced funding frame.

    `recent_rates` is a list ending at the LAST 8h boundary <= ohlcv_index[-1].
    Earlier 8h slots (before the recent ones) get `baseline`.
    `future_rates` is unused here — callers build rates positionally.
    """
    # Build an 8h grid covering the full ohlcv window. Start the grid at a
    # whole-8h boundary on or before ohlcv_index[0].
    start = ohlcv_index[0].floor("8h")
    end = ohlcv_index[-1].ceil("8h")
    grid = pd.date_range(start, end, freq="8h", tz="UTC")
    rates = np.full(len(grid), baseline, dtype=float)

    # The "recent" rates occupy the last len(recent_rates) slots that are
    # strictly <= ohlcv_index[-1].
    eligible = grid[grid <= ohlcv_index[-1]]
    if len(recent_rates) > len(eligible):
        raise AssertionError("not enough funding slots for recent_rates")
    # Place recent rates at the tail of the eligible range.
    for k, r in enumerate(recent_rates):
        rates[len(eligible) - len(recent_rates) + k] = r

    if future_rates is not None:
        for k, r in enumerate(future_rates, start=1):
            pos = len(eligible) - 1 + k
            if pos < len(grid):
                rates[pos] = r

    return pd.DataFrame(
        {
            "funding_rate": rates,
            "funding_interval_hours": np.full(len(grid), 8.0),
        },
        index=grid,
    )


def _make_metrics(
    ohlcv_index: pd.DatetimeIndex,
    oi_change_pct_24h: float,
    *,
    sustained: bool = False,
) -> pd.DataFrame:
    """Build a 5m-spaced metrics frame.

    Default: flat baseline with a linear ramp only over the FINAL 24h, so the
    24h delta equals `oi_change_pct_24h` at the last bar (earlier bars see 0).

    sustained=True: steady linear ramp across the entire window AFTER the first
    24h so that 24h-delta is constantly `oi_change_pct_24h` throughout the
    late portion of the window (useful for GC-B5 which needs the confirmation
    to hold during both the short-entry phase and the bias-flip phase).
    """
    start = ohlcv_index[0].floor("5min")
    end = ohlcv_index[-1].ceil("5min")
    grid = pd.date_range(start, end, freq="5min", tz="UTC")

    base = 1_000_000.0
    oi = np.full(len(grid), base, dtype=float)

    if sustained:
        # Geometric (compounding-like) ramp over the full window so that for
        # any bar at time t (after the first 24h), oi(t) / oi(t - 24h) is a
        # constant ratio = 1 + oi_change_pct_24h. A simple way: exponential
        # growth with rate r per 5m so that (1+r)^(24h/5m) == 1+ratio.
        n_per_24h = int(pd.Timedelta(hours=24) / pd.Timedelta(minutes=5))  # 288
        r = (1.0 + oi_change_pct_24h) ** (1.0 / n_per_24h) - 1.0
        factors = (1.0 + r) ** np.arange(len(grid))
        oi = base * factors
    else:
        ramp_start = grid[-1] - pd.Timedelta(hours=24)
        ramp_mask = grid >= ramp_start
        ramp_len = int(ramp_mask.sum())
        if ramp_len > 1:
            ramp = np.linspace(0.0, oi_change_pct_24h, ramp_len)
            oi[ramp_mask] = base * (1.0 + ramp)

    return pd.DataFrame(
        {
            "sum_open_interest": oi,
            "sum_open_interest_value": oi * 50.0,
            "count_toptrader_long_short_ratio": np.full(len(grid), 1.0),
            "sum_toptrader_long_short_ratio": np.full(len(grid), 1.0),
            "count_long_short_ratio": np.full(len(grid), 1.0),
            "sum_taker_long_short_vol_ratio": np.full(len(grid), 1.0),
        },
        index=grid,
    )


def _build_bars(
    n_minutes: int,
    *,
    close_mode: str = "above_sma_tail",
    recent_funding: list[float],
    baseline_funding: float = 0.0,
    future_funding: list[float] | None = None,
    oi_change_pct_24h: float,
) -> BarsData:
    """Assemble a synthetic BarsData.

    close_mode controls the tail price path vs. SMA60:
        "above_sma_tail": close flat, then ramps UP in final 120 bars so
                          close > SMA60 at the end (triggers short entry).
        "below_sma_tail": close flat, then ramps DOWN in final 120 bars so
                          close < SMA60 at the end (triggers long entry).
        "flat": constant — close == SMA, so neither entry condition met.
    """
    index = pd.date_range(START, periods=n_minutes, freq="1min", tz="UTC")
    close = np.full(n_minutes, 100.0)

    tail = 120
    if close_mode == "above_sma_tail":
        # Gentle ramp up so SMA60 lags below close in the final region.
        close[-tail:] = np.linspace(100.0, 106.0, tail)
    elif close_mode == "below_sma_tail":
        close[-tail:] = np.linspace(100.0, 94.0, tail)
    elif close_mode == "flat":
        pass
    else:
        raise ValueError(f"unknown close_mode: {close_mode}")

    ohlcv = _ohlcv_from_close(close, index)
    funding = _make_funding(
        index,
        recent_rates=recent_funding,
        baseline=baseline_funding,
        future_rates=future_funding,
    )
    metrics = _make_metrics(index, oi_change_pct_24h)
    return BarsData(
        ohlcv=ohlcv,
        symbol="TEST",
        interval="1m",
        funding=funding,
        metrics=metrics,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def _strat() -> FundingCounterStrategy:
    return FundingCounterStrategy()


def test_default_params_match_spec() -> None:
    s = _strat()
    assert s.name == "funding_counter"
    assert s.default_params == {
        "funding_threshold": 0.0005,
        "consecutive_count": 3,
        "oi_change_min": 0.02,
        "entry_sma_window": 60,
        "holding_bars": 480,
    }


def test_signal_frame_shape_and_dtypes() -> None:
    """Sanity: columns, dtypes, and index alignment."""
    data = _build_bars(
        n_minutes=HISTORY_MINUTES + 60,
        close_mode="flat",
        recent_funding=[0.0, 0.0, 0.0],
        oi_change_pct_24h=0.0,
    )
    sf = _strat().generate_signals(data, {})
    assert isinstance(sf, SignalFrame)
    df = sf.df
    assert list(df.columns) == ["side", "size_hint", "stop", "target"]
    assert df["side"].dtype == np.int8
    assert df["size_hint"].dtype == np.float64
    assert df["stop"].dtype == np.float64
    assert df["target"].dtype == np.float64
    assert df.index.equals(data.ohlcv.index)
    # Per spec: size_hint always 1.0, stop/target always NaN.
    assert (df["size_hint"] == 1.0).all()
    assert df["stop"].isna().all()
    assert df["target"].isna().all()


def test_gc_b1_positive_funding_oi_up_price_above_sma_shorts() -> None:
    """GC-B1: 3 positive funding + OI +5% + close > SMA60 => side == -1 at tail."""
    data = _build_bars(
        n_minutes=HISTORY_MINUTES + 60,
        close_mode="above_sma_tail",
        recent_funding=[0.0010, 0.0008, 0.0007],  # +0.10%, +0.08%, +0.07%
        oi_change_pct_24h=0.05,
    )
    sf = _strat().generate_signals(data, {})
    # At the final bar, all conditions are true.
    assert sf.df["side"].iloc[-1] == -1
    # And the position must have been held continuously (no flatten intraday).
    assert (sf.df["side"].iloc[-10:] == -1).all()


def test_gc_b2_positive_funding_but_oi_flat_no_signal() -> None:
    """GC-B2: funding bias but OI change only +0.5% (< 2%) => side stays 0."""
    data = _build_bars(
        n_minutes=HISTORY_MINUTES + 60,
        close_mode="above_sma_tail",
        recent_funding=[0.0010, 0.0008, 0.0007],
        oi_change_pct_24h=0.005,  # below 0.02 threshold
    )
    sf = _strat().generate_signals(data, {})
    assert (sf.df["side"] == 0).all()


def test_gc_b3_negative_funding_oi_up_price_below_sma_longs() -> None:
    """GC-B3: 3 negative funding + OI +3% + close < SMA60 => side == +1 at tail."""
    data = _build_bars(
        n_minutes=HISTORY_MINUTES + 60,
        close_mode="below_sma_tail",
        recent_funding=[-0.0008, -0.0007, -0.0006],
        oi_change_pct_24h=0.03,
    )
    sf = _strat().generate_signals(data, {})
    assert sf.df["side"].iloc[-1] == 1
    assert (sf.df["side"].iloc[-10:] == 1).all()


def test_gc_b4_holding_bars_auto_flatten() -> None:
    """GC-B4: after holding_bars=480 elapsed, position flattens.

    Setup = GC-B1 but with enough runway AFTER entry for 480+ bars to pass
    while bias & conditions stay held. We check that exactly bar 481 post-entry
    is flat.
    """
    # We need the entry to happen well before the end so we can observe 480+
    # bars after it. Build a long tail (>600 bars) where close > SMA60 is
    # established early and stays that way.
    n = HISTORY_MINUTES + 800  # 800 bars of steady short-bias conditions
    index = pd.date_range(START, periods=n, freq="1min", tz="UTC")
    close = np.full(n, 100.0)
    # Start the ramp 800 bars before the end so close > SMA60 is satisfied
    # early in the tail and the short entry triggers with plenty of runway.
    close[-800:] = np.linspace(100.0, 108.0, 800)
    ohlcv = _ohlcv_from_close(close, index)

    # Positive funding readings across all of the last several 8h slots so the
    # short bias is satisfied throughout the entire tail (funding alignment
    # ffills; we need the 3-in-a-row window to have been met WELL before the
    # entry bar). Place 4 positives so the 3-consecutive flag is set at the
    # third of these and stays set through the end.
    funding = _make_funding(
        index,
        recent_rates=[0.0010, 0.0010, 0.0008, 0.0007],
        baseline=0.0,
    )
    metrics = _make_metrics(index, 0.05, sustained=True)
    data = BarsData(
        ohlcv=ohlcv, symbol="TEST", interval="1m", funding=funding, metrics=metrics
    )
    sf = _strat().generate_signals(data, {})
    side = sf.df["side"].to_numpy()

    # Find first short entry.
    entries = np.where(side == -1)[0]
    assert len(entries) > 0, "expected a short entry under GC-B4 setup"
    entry_idx = entries[0]
    # Exactly 480 bars held => positions at entry_idx .. entry_idx+479 are -1
    hold_end = entry_idx + 479
    assert (side[entry_idx:hold_end + 1] == -1).all()
    # Bar 481 (index entry_idx + 480) must be flat.
    assert side[entry_idx + 480] == 0


def test_gc_b5_opposite_bias_flattens() -> None:
    """GC-B5: short entry then funding flips to 3 negative readings => flatten.

    Construct a funding tape where the last 3 readings BEFORE the flip window
    are negative (flipping bias), following earlier positive readings that
    drove an initial short entry.

    We arrange the ohlcv window so the 3 most-recent 8h funding slots are
    negative, but the earlier segment (before those) had 3 positives that
    entered a short. By the final bar the opposite bias has flipped us flat.
    """
    # Build an extended window: use ~32h (4 x 8h slots) of funding events.
    n = HISTORY_MINUTES + 32 * 60  # extra 32 hours past warm-up
    index = pd.date_range(START, periods=n, freq="1min", tz="UTC")

    # Price stays above SMA60 the entire relevant tail so that long re-entry
    # condition (close < SMA) is NOT satisfied — position must therefore stay
    # flat after opposite-bias flatten (as spec allows).
    close = np.full(n, 100.0)
    close[-32 * 60:] = np.linspace(100.0, 110.0, 32 * 60)
    ohlcv = _ohlcv_from_close(close, index)

    # Funding slots (8h apart). Put 3 positives, then 3 negatives at the tail.
    # The 3 negatives are the "recent" (last) 3, flipping the bias.
    # Earlier slots: fill with 3 positives above threshold, rest baseline 0.
    start = index[0].floor("8h")
    end = index[-1].ceil("8h")
    grid = pd.date_range(start, end, freq="8h", tz="UTC")
    rates = np.zeros(len(grid), dtype=float)
    eligible = grid[grid <= index[-1]]
    k = len(eligible)
    # Last 3 eligible => negatives; preceding 3 => positives.
    rates[k - 3:k] = [-0.0008, -0.0007, -0.0006]
    if k >= 6:
        rates[k - 6:k - 3] = [0.0010, 0.0008, 0.0007]
    funding = pd.DataFrame(
        {
            "funding_rate": rates,
            "funding_interval_hours": np.full(len(grid), 8.0),
        },
        index=grid,
    )
    metrics = _make_metrics(index, 0.05, sustained=True)
    data = BarsData(
        ohlcv=ohlcv, symbol="TEST", interval="1m", funding=funding, metrics=metrics
    )

    sf = _strat().generate_signals(data, {})
    side = sf.df["side"].to_numpy()

    # At some point a short was entered in the middle of the window (while the
    # 3 positives were most recent), then flipped to flat when the 3 negatives
    # became the last 3 readings.
    assert (side == -1).any(), "expected a short entry during the positive phase"
    # After opposite bias appears, position must be flat at the final bar
    # (long re-entry condition close < SMA is NOT met here).
    assert side[-1] == 0


def test_idempotent_same_input_same_output() -> None:
    """Determinism: two runs on the same inputs produce identical frames."""
    data = _build_bars(
        n_minutes=HISTORY_MINUTES + 60,
        close_mode="above_sma_tail",
        recent_funding=[0.0010, 0.0008, 0.0007],
        oi_change_pct_24h=0.05,
    )
    s = _strat()
    a = s.generate_signals(data, {})
    b = s.generate_signals(data, {})
    pd.testing.assert_frame_equal(a.df, b.df)


def test_raises_on_missing_funding_or_metrics() -> None:
    """Missing funding or metrics must raise ValueError with a clear message."""
    data = _build_bars(
        n_minutes=HISTORY_MINUTES + 60,
        close_mode="flat",
        recent_funding=[0.0, 0.0, 0.0],
        oi_change_pct_24h=0.0,
    )
    s = _strat()

    no_funding = replace(data, funding=None)
    with pytest.raises(ValueError, match="funding"):
        s.generate_signals(no_funding, {})

    no_metrics = replace(data, metrics=None)
    with pytest.raises(ValueError, match="metrics"):
        s.generate_signals(no_metrics, {})
