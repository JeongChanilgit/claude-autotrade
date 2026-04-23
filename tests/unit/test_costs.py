"""Unit tests for kairos.backtest.costs."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kairos.backtest.costs import (
    FillCost,
    apply_fill_costs,
    daily_atr_per_close,
    funding_cost_for_position,
    slippage_bps,
)
from kairos.backtest.protocols import CostModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(n_days: int = 20, bars_per_day: int = 1440, seed: int = 42) -> pd.DataFrame:
    """Synthetic 1-minute OHLCV spanning n_days UTC days."""
    rng = np.random.default_rng(seed)
    n_bars = n_days * bars_per_day
    index = pd.date_range("2024-01-01", periods=n_bars, freq="1min", tz="UTC")
    close = 30_000.0 + rng.normal(0, 100, size=n_bars).cumsum()
    spread = rng.uniform(0, 50, size=n_bars)
    high = close + spread
    low = close - spread
    ohlcv = pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": 1.0},
        index=index,
    )
    return ohlcv


def _default_cost_model() -> CostModel:
    return CostModel(taker_fee_bps=4.5, slippage_bps_base=1.0, slippage_vol_coef=0.5)


# ---------------------------------------------------------------------------
# Test 1 — ATR warmup rows are NaN, not zero
# ---------------------------------------------------------------------------

def test_daily_atr_warmup_is_nan() -> None:
    period = 14
    ohlcv = _make_ohlcv(n_days=20, bars_per_day=1440)
    result = daily_atr_per_close(ohlcv, period=period)

    assert isinstance(result, pd.Series)
    assert result.index.equals(ohlcv.index)

    # Group bars by UTC date
    dates = result.index.normalize()
    unique_dates = dates.unique().sort_values()

    # First `period` days must be ALL NaN
    for d in unique_dates[:period]:
        day_vals = result[dates == d]
        assert day_vals.isna().all(), f"Day {d} should be NaN (warmup)"

    # Day period+ (i.e. index >= period) must have finite values
    post_warmup_dates = unique_dates[period:]
    assert len(post_warmup_dates) > 0, "Need days beyond warmup"
    for d in post_warmup_dates:
        day_vals = result[dates == d]
        assert day_vals.notna().all(), f"Day {d} beyond warmup should be finite"
        assert (day_vals > 0).all(), f"ATR/close ratio should be positive"


# ---------------------------------------------------------------------------
# Test 2 — slippage_bps uses base when atr_per_close is NaN (warmup)
# ---------------------------------------------------------------------------

def test_slippage_bps_uses_base_when_warmup() -> None:
    model = _default_cost_model()
    index = pd.date_range("2024-01-01", periods=5, freq="1D", tz="UTC")
    atr = pd.Series([np.nan, np.nan, 0.002, 0.003, np.nan], index=index)

    result = slippage_bps(model, atr)

    assert result.iloc[0] == pytest.approx(model.slippage_bps_base)
    assert result.iloc[1] == pytest.approx(model.slippage_bps_base)
    # Non-NaN values should include the vol component
    assert result.iloc[2] == pytest.approx(
        model.slippage_bps_base + model.slippage_vol_coef * 0.002
    )
    # Last NaN row also uses base
    assert result.iloc[4] == pytest.approx(model.slippage_bps_base)
    # All values non-negative
    assert (result >= 0).all()


# ---------------------------------------------------------------------------
# Test 3 — apply_fill_costs: buy fill > mid; sell fill < mid
# ---------------------------------------------------------------------------

def test_apply_fill_costs_buy_pays_more() -> None:
    model = _default_cost_model()
    mid = 30_000.0
    qty = 1.0
    bps = 2.0

    buy_cost = apply_fill_costs(side=+1, mid_price=mid, qty=qty, bps=bps, cost_model=model)
    sell_cost = apply_fill_costs(side=-1, mid_price=mid, qty=qty, bps=bps, cost_model=model)

    assert isinstance(buy_cost, FillCost)
    assert buy_cost.fill_price > mid, "Buy should fill above mid"
    assert sell_cost.fill_price < mid, "Sell should fill below mid"

    expected_slip = mid * qty * bps * 1e-4
    assert buy_cost.slippage == pytest.approx(expected_slip)
    assert sell_cost.slippage == pytest.approx(expected_slip)

    expected_fee = mid * qty * model.taker_fee_bps * 1e-4
    assert buy_cost.fee == pytest.approx(expected_fee)
    assert sell_cost.fee == pytest.approx(expected_fee)


# ---------------------------------------------------------------------------
# Test 4 — fee and slippage scale linearly with qty
# ---------------------------------------------------------------------------

def test_fee_scales_linearly_with_qty() -> None:
    model = _default_cost_model()
    mid = 25_000.0
    bps = 3.0

    cost1 = apply_fill_costs(side=+1, mid_price=mid, qty=1.0, bps=bps, cost_model=model)
    cost2 = apply_fill_costs(side=+1, mid_price=mid, qty=2.0, bps=bps, cost_model=model)

    assert cost2.fee == pytest.approx(2 * cost1.fee)
    assert cost2.slippage == pytest.approx(2 * cost1.slippage)


# ---------------------------------------------------------------------------
# Test 5 — funding cost: long through positive funding → positive cost
# ---------------------------------------------------------------------------

def test_funding_cost_long_pays_positive_funding() -> None:
    rate = 0.0001  # +0.01%
    notional = 10_000.0
    start = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
    end = pd.Timestamp("2024-01-02 00:00:00", tz="UTC")

    funding_times = [
        pd.Timestamp("2024-01-01 00:00:00", tz="UTC"),
        pd.Timestamp("2024-01-01 08:00:00", tz="UTC"),
        pd.Timestamp("2024-01-01 16:00:00", tz="UTC"),
    ]
    funding = pd.DataFrame(
        {"funding_rate": [rate, rate, rate]},
        index=pd.DatetimeIndex(funding_times),
    )

    result = funding_cost_for_position(funding, start, end, notional)
    expected = 3 * rate * notional  # 3.0
    assert result == pytest.approx(expected, rel=1e-9)
    assert result > 0, "Long paying positive funding should be a positive cost"


# ---------------------------------------------------------------------------
# Test 6 — funding cost: short through positive funding → negative (receipt)
# ---------------------------------------------------------------------------

def test_funding_cost_short_receives_positive_funding() -> None:
    rate = 0.0001
    notional = -10_000.0  # short
    start = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
    end = pd.Timestamp("2024-01-02 00:00:00", tz="UTC")

    funding_times = [
        pd.Timestamp("2024-01-01 00:00:00", tz="UTC"),
        pd.Timestamp("2024-01-01 08:00:00", tz="UTC"),
        pd.Timestamp("2024-01-01 16:00:00", tz="UTC"),
    ]
    funding = pd.DataFrame(
        {"funding_rate": [rate, rate, rate]},
        index=pd.DatetimeIndex(funding_times),
    )

    result = funding_cost_for_position(funding, start, end, notional)
    expected = 3 * rate * notional  # -3.0
    assert result == pytest.approx(expected, rel=1e-9)
    assert result < 0, "Short receiving positive funding should be negative (receipt)"


# ---------------------------------------------------------------------------
# Test 7 — funding_cost_for_position: None / empty → 0.0
# ---------------------------------------------------------------------------

def test_funding_cost_none_returns_zero() -> None:
    start = pd.Timestamp("2024-01-01", tz="UTC")
    end = pd.Timestamp("2024-01-02", tz="UTC")
    assert funding_cost_for_position(None, start, end, 10_000.0) == 0.0
    empty = pd.DataFrame({"funding_rate": pd.Series([], dtype=float)})
    assert funding_cost_for_position(empty, start, end, 10_000.0) == 0.0


# ---------------------------------------------------------------------------
# Test 8 — end_ts is exclusive (event at end_ts not included)
# ---------------------------------------------------------------------------

def test_funding_cost_end_ts_is_exclusive() -> None:
    rate = 0.0001
    notional = 10_000.0
    start = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
    end = pd.Timestamp("2024-01-01 08:00:00", tz="UTC")  # exclusive

    funding = pd.DataFrame(
        {"funding_rate": [rate, rate]},
        index=pd.DatetimeIndex([
            pd.Timestamp("2024-01-01 00:00:00", tz="UTC"),
            pd.Timestamp("2024-01-01 08:00:00", tz="UTC"),  # should be excluded
        ]),
    )

    result = funding_cost_for_position(funding, start, end, notional)
    # Only the 00:00 event is in [start, end)
    assert result == pytest.approx(rate * notional)


# ---------------------------------------------------------------------------
# Test 9 — slippage_bps result is always non-negative
# ---------------------------------------------------------------------------

def test_slippage_bps_never_negative() -> None:
    model = CostModel(taker_fee_bps=4.5, slippage_bps_base=0.0, slippage_vol_coef=0.0)
    index = pd.date_range("2024-01-01", periods=10, freq="1D", tz="UTC")
    atr = pd.Series([np.nan] * 5 + [0.0, 0.001, 0.002, -0.001, np.nan], index=index)
    result = slippage_bps(model, atr)
    assert (result >= 0).all()
