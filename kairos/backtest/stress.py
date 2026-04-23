"""Stress-test runner for the three Phase 3 crash scenarios.

Wraps RiskSimulator around a strategy, runs each crash window (LUNA / FTX /
yen-carry-unwind) and verifies the daily loss limit is respected on the
post-risk-controls equity curve. This is what the Phase 3 Go/No-Go gate
sections (c) "4 레짐 손익분기" and (d) "스트레스 3시나리오 일일 -3% 준수" lean on.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from kairos.backtest.protocols import (
    BacktestResult,
    BarsData,
    CostModel,
    Engine,
    RiskRules,
    Strategy,
    SymbolSpec,
)
from kairos.backtest.regimes import RegimeWindow, get_regime, slice_bars
from kairos.backtest.risk_simulator import HaltWindow, RiskSimulator, _detect_halts


@dataclass(frozen=True, slots=True)
class StressResult:
    """Per-crash-window stress evaluation."""
    window: RegimeWindow
    raw_result: BacktestResult
    """Result with NO risk controls (Kelly + halts disabled)."""
    protected_result: BacktestResult
    """Result after RiskSimulator has applied Kelly + EOD + halts."""
    raw_halts: tuple[HaltWindow, ...]
    """Halts that *would have* fired on the raw curve (i.e. risk-controls
    prevented these losses)."""
    worst_daily_return: float
    """Worst UTC-daily return on the protected equity curve (fraction)."""
    daily_limit_respected: bool
    """True iff worst_daily_return ≥ -(daily_loss_limit_pct + slack)."""


def _worst_daily_return(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    daily = equity.resample("1D").last().dropna()
    if len(daily) < 2:
        return 0.0
    rets = daily.pct_change().dropna()
    return float(rets.min()) if len(rets) else 0.0


def run_stress_suite(
    strategy: Strategy,
    data: BarsData,
    engine: Engine,
    *,
    rules: RiskRules,
    costs: CostModel,
    symbol_spec: SymbolSpec,
    initial_cash: Decimal,
    params: Mapping[str, Any] | None = None,
    windows: Iterable[RegimeWindow] | None = None,
    slack_pct: float = 0.5,
    min_bars: int = 60,
) -> tuple[StressResult, ...]:
    """Run the strategy through each crash window with and without risk controls.

    `slack_pct` allows a bit of overshoot past the daily limit — the halt
    triggers *after* the threshold is breached mid-day, and the engine only
    flattens on the next bar, so a few tens of bps of slippage past -3% is
    structurally expected.
    """
    params = params if params is not None else strategy.default_params
    if windows is None:
        windows = get_regime("crash")

    out: list[StressResult] = []
    initial_cash_f = float(initial_cash)
    limit = -(rules.daily_loss_limit_pct + slack_pct) / 100.0

    for w in windows:
        sliced = slice_bars(data, w)
        if len(sliced.ohlcv) < min_bars:
            continue

        raw = engine.run(
            strategy=strategy,
            data=sliced,
            params=params,
            costs=costs,
            symbol_spec=symbol_spec,
            initial_cash=initial_cash,
        )
        raw_halts = _detect_halts(raw.equity_curve, rules, initial_cash_f)

        simulator = RiskSimulator(engine=engine, rules=rules)
        protected = simulator.run(
            strategy=strategy,
            data=sliced,
            params=params,
            costs=costs,
            symbol_spec=symbol_spec,
            initial_cash=initial_cash,
        )

        worst = _worst_daily_return(protected.equity_curve)
        out.append(
            StressResult(
                window=w,
                raw_result=raw,
                protected_result=protected,
                raw_halts=tuple(raw_halts),
                worst_daily_return=worst,
                daily_limit_respected=(not np.isnan(worst)) and worst >= limit,
            )
        )
    return tuple(out)


def all_daily_limits_respected(results: Iterable[StressResult]) -> bool:
    """Phase 3 gate helper: every stress window must respect its daily loss limit."""
    results = tuple(results)
    return bool(results) and all(r.daily_limit_respected for r in results)


__all__ = [
    "StressResult",
    "all_daily_limits_respected",
    "run_stress_suite",
]
