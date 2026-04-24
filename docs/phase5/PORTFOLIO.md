# Phase 5 — Portfolio Backtest Contracts (FROZEN)

Single-symbol backtest mode is **removed**. Every run is a portfolio of N≥1 symbols
sharing one capital pool, one risk simulator, one equity curve.

## New protocol additions (`kairos/backtest/protocols.py`)

```python
@dataclass(frozen=True, slots=True)
class PortfolioBars:
    """Aligned multi-symbol input for a portfolio backtest.

    All BarsData entries cover the same [start, end) window and the same
    interval. Symbols whose listing date is later than `start` have their
    BarsData ohlcv start at the listing date — the portfolio engine treats
    pre-listing bars as flat (no signal, no position).
    """
    bars: dict[str, BarsData]                    # symbol -> per-symbol bars
    interval: Interval
    weights: dict[str, float]                    # symbol -> capital weight, sum=1.0

    def __post_init__(self) -> None:
        assert abs(sum(self.weights.values()) - 1.0) < 1e-9
        assert set(self.bars.keys()) == set(self.weights.keys())


@dataclass(frozen=True, slots=True)
class PortfolioResult:
    """Output of a portfolio engine run."""
    equity_curve: pd.Series                      # portfolio equity, UTC index
    per_symbol_equity: dict[str, pd.Series]      # equity contribution per symbol
    trades: pd.DataFrame                         # all trades, columns include `symbol`
    metrics: Mapping[str, float]                 # portfolio-level metrics
    per_symbol_metrics: dict[str, Mapping[str, float]]
    engine_name: EngineName
    params: Mapping[str, object]


class PortfolioEngine(Protocol):
    """Multi-symbol engine. Replaces single-symbol Engine in Phase 5."""
    name: EngineName

    def run(
        self,
        strategies: dict[str, Strategy],         # symbol -> strategy instance
        portfolio: PortfolioBars,
        params: dict[str, Mapping[str, object]], # symbol -> strategy params
        costs: CostModel,
        symbol_specs: dict[str, SymbolSpec],
        initial_cash: Decimal,
    ) -> PortfolioResult: ...
```

The single-symbol `Engine` and `BacktestResult` types are kept in `protocols.py`
for unit tests of strategies but **dashboard/API/RiskSimulator no longer use them**.

## Trade frame addition

`PortfolioResult.trades` is the single-symbol `BacktestResult.trades` schema
PLUS a leading `symbol: string` column. All other columns identical.

## Capital sharing semantics

- Single broker / single cash balance. Cash is debited/credited atomically per fill.
- Each symbol's signal is converted to a target notional = `weight[symbol] * portfolio_equity * size_hint`.
- Position limit per symbol = `weight[symbol] * portfolio_equity * MAX_LEVERAGE` where `MAX_LEVERAGE=3.0` (frozen — not a parameter; rationale: keeps cross-symbol margin call cascading bounded).
- If two symbols simultaneously want long, both allocations sum and may exceed cash; in that case sizes scale down proportionally so total notional ≤ `portfolio_equity * MAX_LEVERAGE`.

## Pre-listing handling

For each symbol, determine `listing_ts = bars[symbol].ohlcv.index[0]`. Before
that timestamp:
- per_symbol_equity[symbol] = `weight[symbol] * initial_cash` (held flat as cash)
- no signals issued, no fees
After listing the symbol participates normally.

## Portfolio-level RiskSimulator

`risk_simulator.PortfolioRiskSimulator(rules: RiskRules)`:
- Computes daily/weekly/monthly P&L on the **portfolio equity curve** (sum across symbols).
- On halt (any limit breached): force-flat **all** symbols at the next bar open. Resume only after the limit window passes.
- MDD halt: drawdown computed on portfolio equity; on breach, halt remainder of run.
- EOD flatten: at `eod_time_utc` daily, all symbols flat; positions reopen on next day's first bar if signals still active.
- Kelly sizing: `size_hint *= kelly_fraction` is applied per-symbol identically.

## Removed / migrated

- `BacktestResult` stays alive only for `tests/unit/test_strategy_*.py` (strategy unit tests still produce single-symbol `SignalFrame` and feed it through a degenerate single-symbol engine).
- `RiskSimulator` (single-symbol) is **removed**. All callers move to `PortfolioRiskSimulator`.
- `kairos/dashboard/api/jobs.py::run_backtest_job` now calls a portfolio engine. Single-symbol jobs are expressed as `symbols=[X]`, `weights={X: 1.0}`.
