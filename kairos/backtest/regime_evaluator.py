"""Per-regime strategy evaluation harness.

Runs a strategy over every registered RegimeWindow and reports (a) per-regime
BacktestResult metrics, (b) the Phase 3 gate: "break-even or better on each
regime family". Used both for interactive inspection and for the Phase 3
Go/No-Go verification.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pandas as pd

from kairos.backtest.protocols import (
    BacktestResult,
    BarsData,
    CostModel,
    Engine,
    Strategy,
    SymbolSpec,
)
from kairos.backtest.regimes import REGIMES, RegimeName, RegimeWindow, slice_bars


@dataclass(frozen=True, slots=True)
class RegimeEvaluation:
    """Per-window outcome."""
    window: RegimeWindow
    result: BacktestResult
    bars: int
    total_return: float
    sharpe: float
    mdd: float

    @property
    def break_even(self) -> bool:
        """True iff the equity curve ended at or above its start."""
        return self.total_return >= 0.0


@dataclass(frozen=True, slots=True)
class RegimeFamilyReport:
    """Aggregated view over one regime family (e.g. all 'crash' windows)."""
    name: RegimeName
    evaluations: tuple[RegimeEvaluation, ...]

    @property
    def break_even(self) -> bool:
        """Family passes iff *every* window in the family is break-even+."""
        return all(e.break_even for e in self.evaluations)

    @property
    def mean_sharpe(self) -> float:
        vals = [e.sharpe for e in self.evaluations if pd.notna(e.sharpe)]
        return float(sum(vals) / len(vals)) if vals else float("nan")


def _total_return(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    first = float(equity.iloc[0])
    last = float(equity.iloc[-1])
    if first <= 0:
        return 0.0
    return last / first - 1.0


def evaluate_regimes(
    strategy: Strategy,
    data: BarsData,
    engine: Engine,
    *,
    costs: CostModel,
    symbol_spec: SymbolSpec,
    initial_cash: Decimal,
    params: Mapping[str, Any] | None = None,
    windows: Iterable[RegimeWindow] = REGIMES,
    min_bars: int = 60,
) -> tuple[RegimeEvaluation, ...]:
    """Run strategy on each window; skip windows with < min_bars of data.

    Returns evaluations in the order of `windows` (but omitting empty slices).
    Caller is responsible for passing a BarsData that spans the desired period.
    """
    params = params if params is not None else strategy.default_params
    out: list[RegimeEvaluation] = []
    for w in windows:
        sliced = slice_bars(data, w)
        if len(sliced.ohlcv) < min_bars:
            continue
        result = engine.run(
            strategy=strategy,
            data=sliced,
            params=params,
            costs=costs,
            symbol_spec=symbol_spec,
            initial_cash=initial_cash,
        )
        metrics = result.metrics
        out.append(
            RegimeEvaluation(
                window=w,
                result=result,
                bars=len(sliced.ohlcv),
                total_return=_total_return(result.equity_curve),
                sharpe=float(metrics.get("sharpe", float("nan"))),
                mdd=float(metrics.get("mdd", float("nan"))),
            )
        )
    return tuple(out)


def group_by_family(
    evaluations: Iterable[RegimeEvaluation],
) -> dict[RegimeName, RegimeFamilyReport]:
    """Group a flat list of evaluations by regime family name."""
    bucket: dict[RegimeName, list[RegimeEvaluation]] = {}
    for e in evaluations:
        bucket.setdefault(e.window.name, []).append(e)
    return {
        name: RegimeFamilyReport(name=name, evaluations=tuple(evals))
        for name, evals in bucket.items()
    }


def all_families_break_even(evaluations: Iterable[RegimeEvaluation]) -> bool:
    """Phase 3 gate: every regime family must be break-even+ across every window."""
    families = group_by_family(evaluations)
    if not families:
        return False
    return all(r.break_even for r in families.values())


__all__ = [
    "RegimeEvaluation",
    "RegimeFamilyReport",
    "all_families_break_even",
    "evaluate_regimes",
    "group_by_family",
]
