"""Cost model applier for the Kairos backtest layer.

Pure functions only — no side effects, no state. Engine adapters (vbt, bt)
call these to produce per-fill cost breakdowns and bar-level slippage series.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from kairos.backtest.protocols import CostModel

log = logging.getLogger("kairos.backtest.costs")


@dataclass(frozen=True, slots=True)
class FillCost:
    """Breakdown of costs for a single fill."""

    fill_price: float
    """Mid price shifted adversely by slippage. Buys fill above mid, sells below."""

    fee: float
    """abs(notional) * taker_fee_bps * 1e-4. Always positive."""

    slippage: float
    """abs(notional) * bps * 1e-4. Always positive (cost, not direction)."""


def daily_atr_per_close(ohlcv: pd.DataFrame, period: int = 14) -> pd.Series:
    """Return a bar-aligned series of (ATR_{period}D / close) ratios.

    Resample OHLCV to 1D (UTC), compute Wilder ATR over `period` days,
    divide by that day's close, then forward-fill back to the original
    bar index. First `period` days must be NaN — NOT zero.
    """
    # Resample to daily OHLCV — use OHLC aggregation rules
    daily = ohlcv[["open", "high", "low", "close"]].resample("1D", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    )
    # Drop days that had no bars (e.g. gaps)
    daily = daily.dropna(subset=["close"])

    # True Range components
    prev_close = daily["close"].shift(1)
    tr = pd.concat(
        [
            daily["high"] - daily["low"],
            (daily["high"] - prev_close).abs(),
            (daily["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Wilder smoothing alpha = 1/period, not 2/(period+1) — verified against TA-Lib ATR
    atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    # The first (period-1) rows of ewm with min_periods=period are NaN,
    # but the period-th row is the first valid. We need exactly `period` NaN days,
    # meaning day index 0..period-1 are NaN and day index period+ are finite.
    # ewm(min_periods=period) makes rows 0..period-2 NaN and row period-1 valid.
    # We want row period-1 also NaN (only day index >= period should be finite).
    # Force first `period` values to NaN explicitly.
    atr.iloc[:period] = np.nan

    atr_per_close = atr / daily["close"]

    # Forward-fill daily ratios back to the original (sub-daily) bar index.
    # reindex with ffill: each bar inherits the ratio of its UTC day.
    combined = atr_per_close.reindex(atr_per_close.index.union(ohlcv.index)).ffill()
    return combined.reindex(ohlcv.index)


def slippage_bps(cost_model: CostModel, atr_per_close: pd.Series) -> pd.Series:
    """effective = base + coef * atr_per_close.

    Where atr_per_close is NaN (warmup period), return base alone.
    Result is always >= 0.
    """
    vol_component = atr_per_close.fillna(0.0) * cost_model.slippage_vol_coef
    effective = cost_model.slippage_bps_base + vol_component
    # Clamp to non-negative — slippage is always a cost, never a rebate
    return effective.clip(lower=0.0)


def apply_fill_costs(
    side: int,
    mid_price: float,
    qty: float,
    bps: float,
    cost_model: CostModel,
) -> FillCost:
    """Return a FillCost with fill_price, fee, and slippage.

    Slippage direction: buys fill ABOVE mid (+side), sells BELOW mid (-side).
    fee and slippage are in quote currency, always positive.
    """
    notional = abs(mid_price * qty)
    slip_frac = bps * 1e-4
    fee_frac = cost_model.taker_fee_bps * 1e-4

    # side=+1 → fill above mid (adverse for buyer); side=-1 → fill below (adverse for seller)
    fill_price = mid_price * (1.0 + side * slip_frac)

    return FillCost(
        fill_price=fill_price,
        fee=notional * fee_frac,
        slippage=notional * slip_frac,
    )


def funding_cost_for_position(
    funding: pd.DataFrame | None,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    notional: float,
) -> float:
    """Sum funding_rate * notional over funding events in [start_ts, end_ts).

    Positive funding × long notional (positive) = positive cost.
    Positive funding × short notional (negative) = negative value = receipt.
    Returns 0.0 when funding is None or empty.
    """
    if funding is None or funding.empty:
        return 0.0

    # funding_time may be an int64 column (UTC ms) or already a DatetimeIndex
    if isinstance(funding.index, pd.DatetimeIndex):
        mask = (funding.index >= start_ts) & (funding.index < end_ts)
        rates = funding.loc[mask, "funding_rate"]
    else:
        # Expect a funding_time column in UTC ms
        ft = pd.to_datetime(funding["funding_time"], unit="ms", utc=True)
        mask = (ft >= start_ts) & (ft < end_ts)
        rates = funding.loc[mask, "funding_rate"]

    return float((rates * notional).sum())
