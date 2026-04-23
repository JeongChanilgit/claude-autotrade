"""Risk simulator — wraps an Engine run with RiskRules enforcement.

Lives *above* the Engine layer. Engines themselves are pure execution adapters
and know nothing about risk. This simulator:

1. Applies Kelly sizing (multiply size_hint by kelly_fraction) at the signal
   stage.
2. Applies EOD flatten (force side=0 at eod_time_utc each UTC day).
3. Runs the engine once to get a baseline equity curve.
4. Scans the curve for loss-limit and MDD halt events. If any fire, rewrites
   signals to 0 over the halt window and re-runs. Iterates until stable or up
   to `max_iterations` (typically ≤ 2 passes suffice).

Halt semantics:
- daily_loss_limit_pct: from each UTC 00:00, if equity drops more than
  daily_loss_limit_pct below that day's opening equity, force side=0 until
  the next UTC 00:00.
- weekly_loss_limit_pct: from each UTC Monday 00:00, similarly for the
  remainder of that ISO week.
- monthly_loss_limit_pct: from each UTC 1st of month, similarly for the rest
  of that calendar month.
- mdd_halt_pct: if cumulative drawdown from the historical peak exceeds
  mdd_halt_pct, force side=0 for the remainder of the backtest window
  (permanent halt, no recovery).
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import pandas as pd

from kairos.backtest.protocols import (
    BacktestResult,
    BarsData,
    CostModel,
    Engine,
    RiskRules,
    SignalFrame,
    Strategy,
    SymbolSpec,
)

log = logging.getLogger("kairos.backtest.risk_simulator")


_MAX_ITERATIONS = 3


# ---------------------------------------------------------------------------
# Fixed-signal strategy: lets us feed pre-computed signals back into an Engine
# ---------------------------------------------------------------------------

class _FixedSignalStrategy:
    """Strategy adapter that returns a pre-computed SignalFrame verbatim."""

    def __init__(self, name: str, signals: SignalFrame) -> None:
        self.name = name
        self.default_params: Mapping[str, object] = {}
        self._signals = signals

    def generate_signals(
        self,
        data: BarsData,
        params: Mapping[str, object],
    ) -> SignalFrame:
        # Validate alignment — the engine will also do this but fail early here.
        if not self._signals.df.index.equals(data.ohlcv.index):
            raise ValueError(
                "Pre-computed signals index does not match data.ohlcv.index"
            )
        return self._signals


# ---------------------------------------------------------------------------
# Halt detection helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HaltWindow:
    """A contiguous span [start, end] (both inclusive) where side is forced to 0."""

    start: pd.Timestamp
    end: pd.Timestamp
    reason: str  # "daily" | "weekly" | "monthly" | "mdd"


def _detect_halts(
    equity: pd.Series,
    rules: RiskRules,
    initial_cash: float,
) -> list[HaltWindow]:
    """Scan equity curve, produce list of halt windows (overlap allowed)."""
    halts: list[HaltWindow] = []
    if len(equity) == 0:
        return halts

    # --- Calendar group halts (daily / weekly / monthly) ---
    # For each group, anchor equity = first bar's value; trigger when equity
    # drops below anchor * (1 - limit/100); halt until group end.
    for group_freq, limit_pct, reason in (
        ("D", rules.daily_loss_limit_pct, "daily"),
        ("W-MON", rules.weekly_loss_limit_pct, "weekly"),
        ("MS", rules.monthly_loss_limit_pct, "monthly"),
    ):
        if limit_pct <= 0 or limit_pct >= 100:
            continue
        threshold = 1.0 - limit_pct / 100.0
        # Group labels for each timestamp (start-anchored)
        if group_freq == "D":
            group_key = equity.index.floor("D")
        elif group_freq == "W-MON":
            # ISO week start (Monday)
            idx = equity.index
            # .isocalendar().week doesn't give a timestamp directly;
            # use weekday offset trick.
            day_of_week = idx.weekday  # Monday=0
            week_start = idx.floor("D") - pd.to_timedelta(day_of_week, unit="D")
            group_key = week_start
        else:  # "MS" — month start
            idx = equity.index
            # Floor to month start while preserving UTC tz (to_period drops tz).
            group_key = pd.DatetimeIndex(
                [pd.Timestamp(year=t.year, month=t.month, day=1, tz=idx.tz) for t in idx]
            )

        key_series = pd.Series(group_key, index=equity.index)

        for group_start_ts, group_idx in equity.groupby(key_series).groups.items():
            if len(group_idx) == 0:
                continue
            grp_equity = equity.loc[group_idx]
            anchor = float(grp_equity.iloc[0])
            floor = anchor * threshold
            breach_mask = grp_equity < floor
            if not breach_mask.any():
                continue
            first_breach = grp_equity.index[breach_mask][0]
            group_end = grp_equity.index[-1]
            halts.append(HaltWindow(first_breach, group_end, reason))

    # --- MDD halt (permanent for rest of window) ---
    if 0 < rules.mdd_halt_pct < 100:
        peak = equity.cummax()
        dd = (equity - peak) / peak  # negative values
        mdd_limit = -rules.mdd_halt_pct / 100.0
        breach = dd <= mdd_limit
        if breach.any():
            first = equity.index[breach][0]
            halts.append(HaltWindow(first, equity.index[-1], "mdd"))

    return halts


# ---------------------------------------------------------------------------
# Signal transforms
# ---------------------------------------------------------------------------

def _apply_kelly_and_eod(
    sf: SignalFrame,
    eod_flatten: bool,
    eod_time_utc: str,
    kelly_fraction: float,
) -> SignalFrame:
    """Apply Kelly sizing to size_hint and EOD flatten to side."""
    df = sf.df.copy()
    df["size_hint"] = (df["size_hint"] * kelly_fraction).clip(0.0, 1.0)

    if eod_flatten:
        hh, mm = (int(x) for x in eod_time_utc.split(":"))
        # At exactly eod_time_utc on each UTC day, force side=0.
        match = (df.index.hour == hh) & (df.index.minute == mm)
        df.loc[match, "side"] = np.int8(0)

    return SignalFrame(df=df)


def _apply_halts(sf: SignalFrame, halts: list[HaltWindow]) -> SignalFrame:
    """Force side=0 (and size_hint=0) over each halt window."""
    if not halts:
        return sf
    df = sf.df.copy()
    for h in halts:
        mask = (df.index >= h.start) & (df.index <= h.end)
        df.loc[mask, "side"] = np.int8(0)
        df.loc[mask, "size_hint"] = 0.0
    return SignalFrame(df=df)


# ---------------------------------------------------------------------------
# RiskSimulator
# ---------------------------------------------------------------------------

class RiskSimulator:
    """Wraps an Engine with RiskRules enforcement."""

    def __init__(self, engine: Engine, rules: RiskRules) -> None:
        self.engine = engine
        self.rules = rules

    def run(
        self,
        strategy: Strategy,
        data: BarsData,
        params: Mapping[str, object],
        costs: CostModel,
        symbol_spec: SymbolSpec,
        initial_cash: Decimal,
    ) -> BacktestResult:
        raw = strategy.generate_signals(data, params)
        if not raw.df.index.equals(data.ohlcv.index):
            raise ValueError("Strategy signals index mismatch with data")

        current_signals = _apply_kelly_and_eod(
            raw,
            eod_flatten=self.rules.eod_flatten,
            eod_time_utc=self.rules.eod_time_utc,
            kelly_fraction=self.rules.kelly_fraction,
        )

        last_result: BacktestResult | None = None
        initial_cash_f = float(initial_cash)

        for iteration in range(_MAX_ITERATIONS):
            result = self.engine.run(
                strategy=_FixedSignalStrategy(strategy.name, current_signals),
                data=data,
                params=params,
                costs=costs,
                symbol_spec=symbol_spec,
                initial_cash=initial_cash,
            )
            halts = _detect_halts(result.equity_curve, self.rules, initial_cash_f)

            if not halts:
                log.info(
                    "risk_simulator converged after %d pass(es), no halts",
                    iteration + 1,
                )
                return result

            # Apply halts and iterate.
            log.info(
                "risk_simulator iteration %d: %d halt window(s) — %s",
                iteration + 1,
                len(halts),
                {h.reason for h in halts},
            )
            current_signals = _apply_halts(current_signals, halts)
            last_result = result

        log.warning(
            "risk_simulator did not converge within %d iterations", _MAX_ITERATIONS
        )
        # Return the last run's result (signals include the most recent halts).
        assert last_result is not None
        return last_result


__all__ = ["RiskSimulator", "HaltWindow"]
