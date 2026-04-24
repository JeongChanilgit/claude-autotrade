"""Stress-test runner for the three Phase 3 crash scenarios — portfolio form.

Wraps PortfolioRiskSimulator around a portfolio of strategies, runs each crash
window (LUNA / FTX / yen-carry-unwind) and verifies the daily loss limit is
respected on the post-risk-controls portfolio equity curve. The Phase 3 Go/No-Go
gate sections (c) "4 regimes break-even" and (d) "stress 3 scenarios daily -3%"
lean on this.

Phase 5 migration: the old single-symbol signature is gone. Callers build a
PortfolioBars over all symbols that cover the stress windows, and this module
slices each window into a fresh window-PortfolioBars (preserving weights) so
the engine sees only the window. The raw/protected split mirrors the single-
symbol behaviour: raw is the bare engine (for "halts that would have fired"),
protected is PortfolioRiskSimulator on top.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import pandas as pd

from kairos.backtest.protocols import (
    BarsData,
    CostModel,
    PortfolioBars,
    PortfolioEngine,
    PortfolioResult,
    RiskRules,
    Strategy,
    SymbolSpec,
)
from kairos.backtest.regimes import RegimeWindow, get_regime
from kairos.backtest.risk_simulator import (
    HaltWindow,
    PortfolioRiskSimulator,
    _detect_halts,
)


@dataclass(frozen=True, slots=True)
class StressResult:
    """Per-crash-window stress evaluation (portfolio-level)."""
    window: RegimeWindow
    raw_result: PortfolioResult
    """Result with NO risk controls (Kelly + halts disabled)."""
    protected_result: PortfolioResult
    """Result after PortfolioRiskSimulator has applied Kelly + EOD + halts."""
    raw_halts: tuple[HaltWindow, ...]
    """Halts that *would have* fired on the raw portfolio curve (i.e.
    risk-controls prevented these losses)."""
    worst_daily_return: float
    """Worst UTC-daily return on the protected portfolio equity curve
    (fraction)."""
    daily_limit_respected: bool
    """True iff worst_daily_return >= -(daily_loss_limit_pct + slack)."""


def _worst_daily_return(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    daily = equity.resample("1D").last().dropna()
    if len(daily) < 2:
        return 0.0
    rets = daily.pct_change().dropna()
    return float(rets.min()) if len(rets) else 0.0


def _slice_bars_to_window(
    bars: BarsData, window: RegimeWindow
) -> BarsData:
    """Return a BarsData restricted to [window.start, window.end)."""
    idx = bars.ohlcv.index
    mask = (idx >= window.start) & (idx < window.end)
    ohlcv = bars.ohlcv.loc[mask]

    def _slice(df: pd.DataFrame | None) -> pd.DataFrame | None:
        if df is None:
            return None
        m = (df.index >= window.start) & (df.index < window.end)
        return df.loc[m]

    return BarsData(
        ohlcv=ohlcv,
        symbol=bars.symbol,
        interval=bars.interval,
        funding=_slice(bars.funding),
        metrics=_slice(bars.metrics),
    )


def _slice_portfolio_to_window(
    portfolio: PortfolioBars, window: RegimeWindow
) -> PortfolioBars | None:
    """Slice every symbol's bars to the window. Returns None if any symbol
    ends up empty — the portfolio engine refuses empty per-symbol bars, so
    we skip the window in that case.
    """
    sliced: dict[str, BarsData] = {}
    for sym, bars in portfolio.bars.items():
        sub = _slice_bars_to_window(bars, window)
        if len(sub.ohlcv) == 0:
            return None
        sliced[sym] = sub
    return PortfolioBars(
        bars=sliced,
        interval=portfolio.interval,
        weights=dict(portfolio.weights),
    )


def run_stress_suite(
    strategies: dict[str, Strategy],
    portfolio: PortfolioBars,
    engine: PortfolioEngine,
    *,
    rules: RiskRules,
    costs: CostModel,
    symbol_specs: dict[str, SymbolSpec],
    initial_cash: Decimal,
    params: dict[str, Mapping[str, object]] | None = None,
    windows: Iterable[RegimeWindow] | None = None,
    slack_pct: float = 0.5,
    min_bars: int = 60,
) -> tuple[StressResult, ...]:
    """Run the portfolio through each crash window with and without risk controls.

    `slack_pct` allows overshoot past the daily limit — halts trigger after
    the threshold is breached mid-day, the engine only flattens on the next
    bar, so a few tens of bps of slippage past -3% is structurally expected.

    `params` defaults to each strategy's `default_params`.
    """
    if params is None:
        params = {sym: dict(strat.default_params) for sym, strat in strategies.items()}
    if windows is None:
        windows = get_regime("crash")

    out: list[StressResult] = []
    initial_cash_f = float(initial_cash)
    limit = -(rules.daily_loss_limit_pct + slack_pct) / 100.0

    for w in windows:
        sub = _slice_portfolio_to_window(portfolio, w)
        if sub is None:
            continue
        # Require min_bars on the shortest symbol's window; if any symbol is
        # too thin the engine output won't be statistically meaningful.
        if min(len(b.ohlcv) for b in sub.bars.values()) < min_bars:
            continue

        raw = engine.run(
            strategies=strategies,
            portfolio=sub,
            params=params,
            costs=costs,
            symbol_specs=symbol_specs,
            initial_cash=initial_cash,
        )
        raw_halts = _detect_halts(raw.equity_curve, rules, initial_cash_f)

        simulator = PortfolioRiskSimulator(engine=engine, rules=rules)
        protected = simulator.run(
            strategies=strategies,
            portfolio=sub,
            params=params,
            costs=costs,
            symbol_specs=symbol_specs,
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
