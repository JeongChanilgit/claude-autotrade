"""Walk-forward framework for the Kairos backtest layer.

Provides a clean window iterator plus an orchestration runner.
Walk-forward "validate" is rolling within in-sample space; the OOS gate guards
a separate held-out tail (last 6 months of historical data) that stays sealed
until Phase 5.

Window generation rules:
- Validate windows tile [range_start, range_end) with non-overlapping blocks of
  size `validate_days`.
- A window is dropped if validate_end > range_end (partial validate window).
- A window is dropped if train_start = validate_start - train_days < range_start
  (partial training window — no look-back before available data).
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pandas as pd

from kairos.backtest.protocols import (
    BacktestResult,
    BarsData,
    CostModel,
    Engine,
    Strategy,
    SymbolSpec,
)
from kairos.data.oos_gate import OosGate, load_bars

log = logging.getLogger("kairos.backtest.walkforward")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WalkForwardSpec:
    """Hyper-parameters that define the rolling walk-forward scheme."""

    train_days: int    # e.g. 90 — length of each training window
    validate_days: int  # e.g. 30 — length of each validation window
    step_days: int     # e.g. 30 — step between validate window starts


@dataclass(frozen=True)
class WalkForwardWindow:
    """A single walk-forward fold."""

    train_start: pd.Timestamp
    train_end: pd.Timestamp      # exclusive; == validate_start
    validate_start: pd.Timestamp  # == train_end
    validate_end: pd.Timestamp   # exclusive


@dataclass(frozen=True)
class WalkForwardRun:
    """Results for a single walk-forward fold."""

    window: WalkForwardWindow
    train_result: BacktestResult
    validate_result: BacktestResult


# ---------------------------------------------------------------------------
# Window generator
# ---------------------------------------------------------------------------

def windows(
    spec: WalkForwardSpec,
    range_start: pd.Timestamp,
    range_end: pd.Timestamp,
) -> list[WalkForwardWindow]:
    """Generate non-overlapping validate windows that tile [range_start, range_end).

    Dropping rules:
    1. validate_end > range_end  → drop (partial validate window at tail).
    2. train_start < range_start → drop (not enough history for training).

    Both range_start and range_end must be UTC-aware Timestamps.
    """
    if range_start.tzinfo is None or range_end.tzinfo is None:
        raise ValueError("range_start and range_end must be UTC-aware Timestamps")

    result: list[WalkForwardWindow] = []

    train_td = pd.Timedelta(days=spec.train_days)
    validate_td = pd.Timedelta(days=spec.validate_days)
    step_td = pd.Timedelta(days=spec.step_days)

    # First validate window starts at range_start.
    validate_start = range_start

    while True:
        validate_end = validate_start + validate_td

        # Drop if validate window extends beyond range.
        if validate_end > range_end:
            break

        train_start = validate_start - train_td

        # Drop if training window starts before available range.
        if train_start < range_start:
            # Skip this validate position — advance by step and try again.
            validate_start = validate_start + step_td
            continue

        window = WalkForwardWindow(
            train_start=train_start,
            train_end=validate_start,
            validate_start=validate_start,
            validate_end=validate_end,
        )
        result.append(window)
        log.debug(
            "Window: train=[%s, %s) validate=[%s, %s)",
            train_start, validate_start, validate_start, validate_end,
        )

        validate_start = validate_start + step_td

    log.info(
        "Generated %d walk-forward windows over [%s, %s) with spec %s",
        len(result), range_start, range_end, spec,
    )
    return result


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_walkforward(
    strategy: Strategy,
    spec: WalkForwardSpec,
    engine: Engine,
    range_start: pd.Timestamp,
    range_end: pd.Timestamp,
    symbol: str,
    interval: Literal["1m", "15m"],
    params: Mapping[str, object],
    costs: CostModel,
    symbol_spec: SymbolSpec,
    initial_cash: Decimal,
    gate: OosGate,
    data_root: Path | None = None,
) -> list[WalkForwardRun]:
    """Run the walk-forward backtest for every generated window.

    For each window:
    - load bars for the train window (allow_oos=False — gate enforced)
    - run engine on training data → train_result
    - load bars for the validate window (allow_oos=False)
    - run engine on validate data → validate_result
    - append WalkForwardRun

    Note: the walk-forward validate window is within in-sample space unless it
    happens to fall inside the OOS gate.  The gate protects the final OOS tail
    (last ~6 months of full history) from being read at all during development.
    """
    wf_windows = windows(spec, range_start, range_end)
    runs: list[WalkForwardRun] = []

    for i, w in enumerate(wf_windows):
        log.info(
            "Walk-forward window %d/%d: train=[%s, %s) validate=[%s, %s)",
            i + 1, len(wf_windows),
            w.train_start, w.train_end,
            w.validate_start, w.validate_end,
        )

        # --- Training phase ---
        train_data = load_bars(
            symbol=symbol,
            interval=interval,
            start=w.train_start,
            end=w.train_end,
            gate=gate,
            allow_oos=False,
            data_root=data_root,
        )
        train_result = engine.run(
            strategy=strategy,
            data=train_data,
            params=params,
            costs=costs,
            symbol_spec=symbol_spec,
            initial_cash=initial_cash,
        )

        # --- Validation phase ---
        validate_data = load_bars(
            symbol=symbol,
            interval=interval,
            start=w.validate_start,
            end=w.validate_end,
            gate=gate,
            allow_oos=False,
            data_root=data_root,
        )
        validate_result = engine.run(
            strategy=strategy,
            data=validate_data,
            params=params,
            costs=costs,
            symbol_spec=symbol_spec,
            initial_cash=initial_cash,
        )

        runs.append(
            WalkForwardRun(
                window=w,
                train_result=train_result,
                validate_result=validate_result,
            )
        )

    log.info(
        "Completed %d walk-forward runs for %s %s",
        len(runs), symbol, interval,
    )
    return runs


__all__ = [
    "WalkForwardSpec",
    "WalkForwardWindow",
    "WalkForwardRun",
    "windows",
    "run_walkforward",
]
