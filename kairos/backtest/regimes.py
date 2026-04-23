"""Market regime windows for Phase 3 per-regime strategy evaluation.

The 4 regimes are hand-selected historical periods that stress each strategy
differently. Phase 3 go/no-go requires every strategy to be break-even or
better on each regime.

All timestamps are UTC-aware. Windows are half-open [start, end).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

RegimeName = Literal["uptrend", "range", "crash", "lowvol"]


@dataclass(frozen=True, slots=True)
class RegimeWindow:
    """A historical window belonging to a named regime."""

    name: RegimeName
    label: str
    start: pd.Timestamp
    end: pd.Timestamp

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("start/end must be UTC-aware")
        if self.start >= self.end:
            raise ValueError("start must be strictly before end")


def _ts(iso: str) -> pd.Timestamp:
    return pd.Timestamp(iso, tz="UTC")


# Concrete windows. These are authored by Opus — change only with justification
# (recorded in an ADR) so that Phase 3 parameter tuning doesn't quietly shift
# the goalposts.
REGIMES: tuple[RegimeWindow, ...] = (
    # Uptrends — defined loosely as daily close > SMA200 + >=+20% 30d return
    RegimeWindow("uptrend", "bull_2020H2_2021Q1", _ts("2020-10-01"), _ts("2021-04-15")),
    RegimeWindow("uptrend", "bull_2023Q4_2024Q1", _ts("2023-10-01"), _ts("2024-03-15")),
    # Range-bound — |30d return| < 10% + ATR/price < median
    RegimeWindow("range", "range_2023Q2Q3", _ts("2023-04-01"), _ts("2023-10-01")),
    # Crashes — weekly return < -15%
    RegimeWindow("crash", "luna_collapse", _ts("2022-05-07"), _ts("2022-05-15")),
    RegimeWindow("crash", "ftx_collapse", _ts("2022-11-06"), _ts("2022-11-14")),
    RegimeWindow("crash", "yen_carry_unwind_2024", _ts("2024-08-01"), _ts("2024-08-08")),
    # Low-volatility — realized 20d annualized vol < 40%
    RegimeWindow("lowvol", "lowvol_2023_summer", _ts("2023-06-01"), _ts("2023-09-01")),
)


def get_regime(name: RegimeName) -> tuple[RegimeWindow, ...]:
    """Return all windows for a given regime name."""
    return tuple(w for w in REGIMES if w.name == name)


def get_window(label: str) -> RegimeWindow:
    """Look up a window by its unique label."""
    for w in REGIMES:
        if w.label == label:
            return w
    raise KeyError(f"no regime window with label={label!r}")


def slice_bars(data, window: RegimeWindow):
    """Return a BarsData restricted to the half-open interval [window.start, window.end).

    Slicing is non-destructive; the returned BarsData shares underlying frames
    via pandas .loc filtering and is safe to pass into engines.
    """
    from kairos.backtest.protocols import BarsData

    idx = data.ohlcv.index
    mask = (idx >= window.start) & (idx < window.end)
    ohlcv = data.ohlcv.loc[mask]

    def _slice(df):
        if df is None:
            return None
        m = (df.index >= window.start) & (df.index < window.end)
        return df.loc[m]

    return BarsData(
        ohlcv=ohlcv,
        symbol=data.symbol,
        interval=data.interval,
        funding=_slice(data.funding),
        metrics=_slice(data.metrics),
    )


__all__ = [
    "REGIMES",
    "RegimeWindow",
    "RegimeName",
    "get_regime",
    "get_window",
    "slice_bars",
]
