"""Unit tests for kairos.strategies.orb.OrbStrategy.

Covers the 5 golden cases defined in ``docs/strategies/A-orb.md`` plus an
idempotency test and a Strategy-protocol structural-typing check.

The volume and ATR filters require 20 prior UTC days of synthetic history.
To keep tests fast, each prior day provides just its first 15 one-minute bars
(enough to feed both the 20-day volume baseline and Wilder ATR14). Gaps
between prior-day bars and the target-day bars are tolerated by the strategy.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from kairos.backtest.protocols import BarsData, SignalFrame, Strategy
from kairos.strategies.orb import OrbStrategy

TARGET_DATE = pd.Timestamp("2024-01-21", tz="UTC")  # 20 prior days: 2024-01-01..20
PRIOR_DAYS = 20
OR_MINUTES = 15

# The prior-day synthetic pattern intentionally has a wide true range so
# Wilder ATR14 / close exceeds the 1% atr_min_pct filter comfortably.
PRIOR_HIGH = 102.0
PRIOR_LOW = 98.0
PRIOR_OPEN = 100.0
PRIOR_CLOSE = 100.0
# First-15m volume per prior day used by the volume baseline.
PRIOR_OR_VOLUME_TOTAL = 15.0  # 1.0 per bar × 15 bars
# Target-day first-15m total volume; must be >= 1.5 × avg(prior) = 1.5 × 1.0/bar = 22.5 total
TARGET_OR_VOLUME_PER_BAR = 2.0  # → 30.0 total for 15 bars, > 22.5 → passes filter


def _prior_day_bars(day: pd.Timestamp) -> pd.DataFrame:
    """15 one-minute bars from day 00:00 to 00:14, synthesising a wide-range
    daily candle so ATR14 is large enough to pass the atr_min_pct filter.
    """
    index = pd.date_range(day, periods=15, freq="1min", tz="UTC")
    # Make a bar whose [low, high] = [98, 102] so daily TR = 4.0. Put extremes
    # in the middle bars and open=close=100 at the ends.
    opens = np.full(15, PRIOR_OPEN)
    closes = np.full(15, PRIOR_CLOSE)
    highs = np.full(15, PRIOR_OPEN)
    lows = np.full(15, PRIOR_OPEN)
    highs[7] = PRIOR_HIGH
    lows[8] = PRIOR_LOW
    volume = np.ones(15)
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volume,
            "quote_volume": volume * 100.0,
            "taker_buy_volume": volume * 0.5,
            "taker_buy_quote_volume": volume * 50.0,
            "trade_count": np.ones(15, dtype=np.int64),
        },
        index=index,
    )


def _build_target_day(
    bars_per_row: list[tuple[str, float, float, float, float, float]],
    target_date: pd.Timestamp = TARGET_DATE,
) -> pd.DataFrame:
    """Build a target-day OHLCV frame from ``(hhmm, open, high, low, close, volume)``."""
    rows = []
    index = []
    for hhmm, o, h, lo, c, v in bars_per_row:
        hh, mm = int(hhmm[:2]), int(hhmm[2:])
        ts = target_date + pd.Timedelta(hours=hh, minutes=mm)
        index.append(ts)
        rows.append(
            {
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": v,
                "quote_volume": v * 100.0,
                "taker_buy_volume": v * 0.5,
                "taker_buy_quote_volume": v * 50.0,
                "trade_count": 1,
            }
        )
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(index, tz="UTC"))
    df["trade_count"] = df["trade_count"].astype(np.int64)
    return df


def _assemble_bars(target_day_df: pd.DataFrame) -> BarsData:
    """Concatenate 20 prior days of history with the target-day frame."""
    prior = pd.concat(
        [_prior_day_bars(TARGET_DATE - pd.Timedelta(days=(PRIOR_DAYS - i))) for i in range(PRIOR_DAYS)]
    )
    ohlcv = pd.concat([prior, target_day_df])
    ohlcv = ohlcv.sort_index()
    return BarsData(ohlcv=ohlcv, symbol="TESTUSDT", interval="1m")


def _or_bars_standard() -> list[tuple[str, float, float, float, float, float]]:
    """00:00-00:14 bars forming OR_high=100.5, OR_low=99.5, range=1.0.

    Volume per bar = 2.0 → 30.0 total → passes the 1.5× filter.
    """
    bars = []
    # Alternate above/below to make OR_high=100.5, OR_low=99.5.
    closes = [100.5, 99.5] * 7 + [100.0]  # 15 values
    for i, c in enumerate(closes):
        hh, mm = 0, i
        hhmm = f"{hh:02d}{mm:02d}"
        # high = max(100.5, c), low = min(99.5, c); keeps OR bounds tight.
        high = max(100.5, c)
        low = min(99.5, c)
        bars.append((hhmm, c, high, low, c, TARGET_OR_VOLUME_PER_BAR))
    return bars


def _after_or_bar(hhmm: str, o: float, h: float, lo: float, c: float, v: float = 2.0):
    return (hhmm, o, h, lo, c, v)


# ---------------------------------------------------------------------------
# GC-A1: OR formation → long breakout
# ---------------------------------------------------------------------------

def test_gc_a1_long_breakout():
    or_bars = _or_bars_standard()
    # 00:15 close=100.6 (breakout), 00:16 close=101.0, then hold through 00:30.
    post_bars = [
        _after_or_bar("0015", 100.0, 100.6, 100.0, 100.6),
        _after_or_bar("0016", 100.6, 101.0, 100.6, 101.0),
    ]
    # Fill 00:17..00:30 flat at 101.0 (no stop, no target-hit).
    for m in range(17, 31):
        post_bars.append(_after_or_bar(f"00{m:02d}", 101.0, 101.0, 100.6, 101.0))
    target_day = _build_target_day(or_bars + post_bars)
    bars = _assemble_bars(target_day)
    sf = OrbStrategy().generate_signals(bars, {})

    # Slice only the target-day rows
    day_slice = sf.df.loc[TARGET_DATE : TARGET_DATE + pd.Timedelta(minutes=30)]
    side = day_slice["side"].to_numpy()
    assert list(side[:15]) == [0] * 15
    # Index 15 (00:15) onward = +1 through end of fixture (00:30 inclusive).
    assert np.all(side[15:] == 1), f"Expected all +1 from 00:15, got {side[15:]}"
    # Stop / target values
    entry_row = day_slice.iloc[15]
    assert entry_row["stop"] == pytest.approx(99.5)
    assert entry_row["target"] == pytest.approx(102.5)
    # size_hint check
    assert entry_row["size_hint"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# GC-A2: Volume filter fails → flat all day
# ---------------------------------------------------------------------------

def test_gc_a2_volume_filter_fail():
    # Same OHLC shape as GC-A1 but with volume = 1.0 per bar (total 15.0),
    # equal to the prior-day baseline → 1.0× < 1.5× → filter fails.
    or_bars = []
    closes = [100.5, 99.5] * 7 + [100.0]
    for i, c in enumerate(closes):
        hhmm = f"00{i:02d}"
        high = max(100.5, c)
        low = min(99.5, c)
        or_bars.append((hhmm, c, high, low, c, 1.0))  # volume=1.0
    post_bars = [
        ("0015", 100.0, 100.6, 100.0, 100.6, 1.0),
        ("0016", 100.6, 101.0, 100.6, 101.0, 1.0),
    ]
    for m in range(17, 31):
        post_bars.append((f"00{m:02d}", 101.0, 101.0, 100.6, 101.0, 1.0))
    target_day = _build_target_day(or_bars + post_bars)
    bars = _assemble_bars(target_day)
    sf = OrbStrategy().generate_signals(bars, {})
    day_slice = sf.df.loc[TARGET_DATE : TARGET_DATE + pd.Timedelta(minutes=30)]
    assert (day_slice["side"] == 0).all()
    assert day_slice["size_hint"].eq(0.0).all()


# ---------------------------------------------------------------------------
# GC-A3: Short breakdown
# ---------------------------------------------------------------------------

def test_gc_a3_short_breakdown():
    or_bars = _or_bars_standard()
    # 00:15 close=99.3 — breaks below OR_low=99.5.
    post_bars = [
        _after_or_bar("0015", 100.0, 100.0, 99.3, 99.3),
        _after_or_bar("0016", 99.3, 99.3, 99.0, 99.0),
    ]
    for m in range(17, 31):
        post_bars.append(_after_or_bar(f"00{m:02d}", 99.0, 99.0, 98.5, 99.0))
    target_day = _build_target_day(or_bars + post_bars)
    bars = _assemble_bars(target_day)
    sf = OrbStrategy().generate_signals(bars, {})
    day_slice = sf.df.loc[TARGET_DATE : TARGET_DATE + pd.Timedelta(minutes=30)]
    side = day_slice["side"].to_numpy()
    assert list(side[:15]) == [0] * 15
    assert side[15] == -1
    entry_row = day_slice.iloc[15]
    assert entry_row["stop"] == pytest.approx(100.5)
    assert entry_row["target"] == pytest.approx(97.5)


# ---------------------------------------------------------------------------
# GC-A4: Stop hit, no re-entry same day
# ---------------------------------------------------------------------------

def test_gc_a4_stop_hit_no_reentry():
    or_bars = _or_bars_standard()
    post_bars = [
        _after_or_bar("0015", 100.0, 100.6, 100.0, 100.6),  # long entry
        _after_or_bar("0016", 100.6, 100.7, 100.3, 100.4),
        _after_or_bar("0017", 100.4, 100.5, 100.2, 100.3),
        _after_or_bar("0018", 100.3, 100.3, 100.0, 100.1),
        _after_or_bar("0019", 100.1, 100.1, 99.8, 99.9),
        _after_or_bar("0020", 99.9, 99.9, 99.4, 99.5),  # low=99.4 < stop=99.5
        _after_or_bar("0021", 99.5, 99.8, 99.5, 99.7),
        _after_or_bar("0022", 99.7, 100.0, 99.7, 99.9),
        _after_or_bar("0023", 99.9, 100.2, 99.9, 100.1),
        _after_or_bar("0024", 100.1, 100.4, 100.1, 100.3),
        _after_or_bar("0025", 100.3, 100.6, 100.3, 100.6),  # 2nd breakout: ignored
        _after_or_bar("0026", 100.6, 100.8, 100.6, 100.7),
    ]
    target_day = _build_target_day(or_bars + post_bars)
    bars = _assemble_bars(target_day)
    sf = OrbStrategy().generate_signals(bars, {})
    day_slice = sf.df.loc[TARGET_DATE : TARGET_DATE + pd.Timedelta(minutes=30)]
    side = day_slice["side"].to_numpy()
    # Indices 0..14 OR; 15..19 +1 (entry through bar before stop); 20.. = 0
    assert list(side[:15]) == [0] * 15
    assert list(side[15:20]) == [1, 1, 1, 1, 1]
    assert (side[20:] == 0).all()


# ---------------------------------------------------------------------------
# GC-A5: session_end flatten
# ---------------------------------------------------------------------------

def test_gc_a5_session_end_flatten():
    or_bars = _or_bars_standard()
    # Long entry at 00:15. Then carry through to 23:59. Ensure no stop/target hit
    # by keeping price in a narrow band that never touches stop=99.5 or target=102.5.
    post_bars: list[tuple[str, float, float, float, float, float]] = [
        _after_or_bar("0015", 100.0, 100.6, 100.0, 100.6),
    ]
    # Emit one bar per minute from 00:16 to 23:59 with price stable around 101.
    ts = pd.Timestamp("2024-01-21 00:16", tz="UTC")
    end_ts = pd.Timestamp("2024-01-21 23:59", tz="UTC")
    minute = ts
    while minute <= end_ts:
        hh = minute.hour
        mm = minute.minute
        post_bars.append((f"{hh:02d}{mm:02d}", 101.0, 101.2, 100.8, 101.0, 2.0))
        minute = minute + pd.Timedelta(minutes=1)
    target_day = _build_target_day(or_bars + post_bars)
    bars = _assemble_bars(target_day)
    sf = OrbStrategy().generate_signals(bars, {})

    # Convert target-day slice for assertions.
    day_slice = sf.df.loc[TARGET_DATE : TARGET_DATE + pd.Timedelta(hours=23, minutes=59)]
    # side at 00:15..23:54 should be +1; 23:55..23:59 should be 0.
    held_slice = day_slice.loc[
        TARGET_DATE + pd.Timedelta(minutes=15) : TARGET_DATE + pd.Timedelta(hours=23, minutes=54)
    ]
    flat_slice = day_slice.loc[
        TARGET_DATE + pd.Timedelta(hours=23, minutes=55) : TARGET_DATE + pd.Timedelta(hours=23, minutes=59)
    ]
    assert (held_slice["side"] == 1).all(), "expected all +1 from 00:15 through 23:54"
    assert (flat_slice["side"] == 0).all(), "expected side=0 from 23:55 onward"


# ---------------------------------------------------------------------------
# Idempotency: same input → same output
# ---------------------------------------------------------------------------

def test_idempotency():
    or_bars = _or_bars_standard()
    post_bars = [
        _after_or_bar("0015", 100.0, 100.6, 100.0, 100.6),
        _after_or_bar("0016", 100.6, 101.0, 100.6, 101.0),
    ]
    for m in range(17, 31):
        post_bars.append(_after_or_bar(f"00{m:02d}", 101.0, 101.0, 100.6, 101.0))
    target_day = _build_target_day(or_bars + post_bars)
    bars = _assemble_bars(target_day)
    strat = OrbStrategy()
    sf1 = strat.generate_signals(bars, {})
    sf2 = strat.generate_signals(bars, {})
    pd.testing.assert_frame_equal(sf1.df, sf2.df)


# ---------------------------------------------------------------------------
# Structural typing: OrbStrategy conforms to Strategy Protocol
# ---------------------------------------------------------------------------

def test_orb_is_strategy_protocol_compatible():
    strat: Strategy = OrbStrategy()  # structural typing at mypy level
    # Runtime: confirm the attributes/methods exist with the expected shapes.
    assert strat.name == "orb"
    assert isinstance(strat.default_params, dict) or hasattr(strat.default_params, "__getitem__")
    # Default params match the spec's defaults table.
    expected_defaults = {
        "or_minutes": 15,
        "session_start_utc": "00:00",
        "session_end_utc": "23:55",
        "volume_multiple": 1.5,
        "volume_lookback_days": 20,
        "atr_min_pct": 0.01,
        "tick_buffer": 0.0,
        "orr_target_mult": 2.0,
    }
    for k, v in expected_defaults.items():
        assert strat.default_params[k] == v, f"default {k} mismatch"
    # generate_signals callable with (BarsData, Mapping) → SignalFrame.
    empty_idx = pd.DatetimeIndex([], tz="UTC")
    empty_df = pd.DataFrame(
        {
            "open": pd.Series([], dtype="float64"),
            "high": pd.Series([], dtype="float64"),
            "low": pd.Series([], dtype="float64"),
            "close": pd.Series([], dtype="float64"),
            "volume": pd.Series([], dtype="float64"),
            "quote_volume": pd.Series([], dtype="float64"),
            "taker_buy_volume": pd.Series([], dtype="float64"),
            "taker_buy_quote_volume": pd.Series([], dtype="float64"),
            "trade_count": pd.Series([], dtype="int64"),
        },
        index=empty_idx,
    )
    empty_bars = BarsData(ohlcv=empty_df, symbol="TESTUSDT", interval="1m")
    out = strat.generate_signals(empty_bars, {})
    assert isinstance(out, SignalFrame)
    assert set(out.df.columns) == {"side", "size_hint", "stop", "target"}
    assert out.df["side"].dtype == np.int8
    assert out.df["size_hint"].dtype == np.float64
    assert out.df["stop"].dtype == np.float64
    assert out.df["target"].dtype == np.float64
