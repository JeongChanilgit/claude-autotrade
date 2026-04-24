"""Phase 3 Go/No-Go gate driver — portfolio-shaped.

Usage (CLI):
    python -m kairos.backtest.gate_runner \\
        --symbols BTCUSDT --interval 15m \\
        --start 2020-10-01 --end 2024-08-10 \\
        [--weights '{"BTCUSDT":1.0}'] [--out reports/phase3_gate.json]

The driver:
1. Loads a PortfolioBars from parquet (via kairos.data.load.load_portfolio).
2. Computes each sub-strategy's Sharpe per symbol by running a degenerate
   single-symbol portfolio (weights={S:1.0}) per strategy, per symbol.
3. Runs the ensemble on the full multi-symbol portfolio -> CAGR / MDD.
4. Evaluates each regime window on the ensemble portfolio.
5. Runs the stress suite (LUNA / FTX / yen-carry) on the ensemble portfolio.
6. Composes a Phase3Report and persists JSON + STATUS-friendly summary.

Output JSON keeps the Phase 3 shape (strategy_sharpes/ensemble/regimes/
stress/gate/summary) but replaces the top-level single ``symbol`` key with a
list ``symbols``. Strategy-sharpes become per-(symbol, strategy) tuples.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

import pandas as pd

from kairos.backtest.engine_portfolio_vbt import PortfolioVectorbtEngine
from kairos.backtest.metrics import compute_metrics
from kairos.backtest.phase3_gate import (
    EnsembleHeadline,
    StrategySharpe,
    compose_phase3_report,
)
from kairos.backtest.protocols import (
    BacktestResult,
    CostModel,
    PortfolioBars,
    PortfolioEngine,
    PortfolioResult,
    RiskRules,
    Strategy,
    SymbolSpec,
)
from kairos.backtest.regime_evaluator import RegimeEvaluation
from kairos.backtest.regimes import REGIMES, RegimeWindow
from kairos.backtest.stress import _slice_portfolio_to_window, run_stress_suite
from kairos.data.load import load_portfolio
from kairos.strategies.ensemble import build_default_ensemble
from kairos.strategies.funding import FundingCounterStrategy
from kairos.strategies.orb import OrbStrategy
from kairos.strategies.vol_compression import VolCompressionStrategy

log = logging.getLogger("kairos.backtest.gate_runner")


# Bars-per-year for 15m cadence: 4 bars/h × 24h × 365d = 35040
BARS_PER_YEAR_15M = 35_040
BARS_PER_YEAR_1M = 525_600


def _bars_per_year(interval: str) -> int:
    if interval == "15m":
        return BARS_PER_YEAR_15M
    if interval == "1m":
        return BARS_PER_YEAR_1M
    raise ValueError(f"unsupported interval {interval!r}")


def _fallback_spec(symbol: str) -> SymbolSpec:
    """Best-effort SymbolSpec for when no cached exchangeInfo is available."""
    defaults = {
        "BTCUSDT": (Decimal("0.1"), Decimal("0.001"), Decimal("0.001"), Decimal("5")),
        "ETHUSDT": (Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal("5")),
        "SOLUSDT": (Decimal("0.001"), Decimal("1"), Decimal("1"), Decimal("5")),
    }
    tick, step, min_qty, min_notional = defaults.get(
        symbol, (Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal("5"))
    )
    return SymbolSpec(
        symbol=symbol,
        tick_size=tick,
        step_size=step,
        min_qty=min_qty,
        min_notional=min_notional,
    )


def _single_symbol_portfolio(portfolio: PortfolioBars, symbol: str) -> PortfolioBars:
    """Project a multi-symbol PortfolioBars down to a single symbol with weight 1."""
    return PortfolioBars(
        bars={symbol: portfolio.bars[symbol]},
        interval=portfolio.interval,
        weights={symbol: 1.0},
    )


@dataclass(frozen=True, slots=True)
class PerSymbolStrategySharpe:
    """Per-(symbol, strategy) Sharpe used to seed Phase3Report.strategy_sharpes.

    Flattened for the JSON output; the gate itself only looks at Sharpe values,
    so each pairing contributes independently to the pass/fail rubric.
    """
    symbol: str
    strategy: str
    sharpe: float


def _run_strategy_on_symbol(
    *,
    strategy: Strategy,
    portfolio: PortfolioBars,
    symbol: str,
    engine: PortfolioEngine,
    costs: CostModel,
    symbol_specs: dict[str, SymbolSpec],
    initial_cash: Decimal,
) -> PortfolioResult:
    """Run `strategy` on the single-symbol projection of `portfolio`."""
    sub = _single_symbol_portfolio(portfolio, symbol)
    return engine.run(
        strategies={symbol: strategy},
        portfolio=sub,
        params={symbol: dict(strategy.default_params)},
        costs=costs,
        symbol_specs={symbol: symbol_specs[symbol]},
        initial_cash=initial_cash,
    )


def _evaluate_regimes_portfolio(
    *,
    ensemble: Strategy,
    portfolio: PortfolioBars,
    engine: PortfolioEngine,
    costs: CostModel,
    symbol_specs: dict[str, SymbolSpec],
    initial_cash: Decimal,
    bars_per_year: int,
    min_bars: int = 60,
) -> tuple[RegimeEvaluation, ...]:
    """Portfolio analogue of regime_evaluator.evaluate_regimes.

    For each RegimeWindow, slice the portfolio to the window and run the
    ensemble across all symbols; synthesise a RegimeEvaluation carrying the
    portfolio equity + metrics so compose_phase3_report can consume it
    unchanged.
    """
    symbols = sorted(portfolio.bars.keys())
    strategies: dict[str, Strategy] = {sym: ensemble for sym in symbols}
    params = {sym: dict(ensemble.default_params) for sym in symbols}

    out: list[RegimeEvaluation] = []
    for w in REGIMES:
        sub = _slice_portfolio_to_window(portfolio, w)
        if sub is None:
            continue
        if min(len(b.ohlcv) for b in sub.bars.values()) < min_bars:
            continue
        pf_result = engine.run(
            strategies=strategies,
            portfolio=sub,
            params=params,
            costs=costs,
            symbol_specs=symbol_specs,
            initial_cash=initial_cash,
        )
        metrics = compute_metrics(
            pf_result.equity_curve, pf_result.trades, bars_per_year=bars_per_year
        )
        eq = pf_result.equity_curve
        total_return = 0.0
        if len(eq) >= 2 and float(eq.iloc[0]) > 0:
            total_return = float(eq.iloc[-1]) / float(eq.iloc[0]) - 1.0
        # Wrap as BacktestResult purely to satisfy the RegimeEvaluation
        # dataclass shape; downstream gate code only touches equity + metrics.
        fake_bt = BacktestResult(
            equity_curve=pf_result.equity_curve,
            trades=pf_result.trades,
            metrics=metrics,
            engine_name=pf_result.engine_name,
            params=pf_result.params,
        )
        out.append(
            RegimeEvaluation(
                window=w,
                result=fake_bt,
                bars=int(len(pf_result.equity_curve)),
                total_return=total_return,
                sharpe=float(metrics.get("sharpe", float("nan"))),
                mdd=abs(float(metrics.get("mdd", float("nan")))),
            )
        )
    return tuple(out)


def _auto_weights(symbols: list[str]) -> dict[str, float]:
    """Equal weights across the provided symbols."""
    if not symbols:
        raise ValueError("cannot auto-weight empty symbol list")
    w = 1.0 / float(len(symbols))
    out = {s: w for s in symbols}
    # Correct the final weight to close rounding drift so sum == 1.0 exactly.
    drift = 1.0 - sum(out.values())
    out[symbols[-1]] += drift
    return out


def run_gate(
    symbols: list[str],
    interval: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    weights: dict[str, float] | None = None,
    initial_cash: Decimal = Decimal("10000"),
    data_root: Path | None = None,
) -> dict:
    """End-to-end Phase 3 gate on real data. Returns dict suitable for JSON dump."""
    if not symbols:
        return {"error": "no symbols provided"}
    if weights is None:
        weights = _auto_weights(symbols)

    log.info("Loading portfolio %s %s [%s, %s)", symbols, interval, start, end)
    portfolio = load_portfolio(
        symbols=symbols,
        interval=interval,  # type: ignore[arg-type]
        start=start,
        end=end,
        weights=weights,
        data_root=data_root,
    )

    # Bail early when every symbol is empty: nothing to score.
    if all(len(portfolio.bars[s].ohlcv) == 0 for s in symbols):
        return {
            "symbols": symbols,
            "interval": interval,
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "weights": weights,
            "error": f"no ohlcv data for {symbols} {interval} [{start}, {end})",
        }

    engine = PortfolioVectorbtEngine()
    costs = CostModel()
    rules = RiskRules()
    symbol_specs = {s: _fallback_spec(s) for s in symbols}
    bars_per_year = _bars_per_year(interval)

    # (a) per-strategy Sharpe, per symbol.
    per_sym_sharpes: list[PerSymbolStrategySharpe] = []
    flat_sharpes: list[StrategySharpe] = []
    for sym in symbols:
        if len(portfolio.bars[sym].ohlcv) == 0:
            continue
        for strat in (OrbStrategy(), FundingCounterStrategy(), VolCompressionStrategy()):
            try:
                res = _run_strategy_on_symbol(
                    strategy=strat,
                    portfolio=portfolio,
                    symbol=sym,
                    engine=engine,
                    costs=costs,
                    symbol_specs=symbol_specs,
                    initial_cash=initial_cash,
                )
                sharpe = float(res.metrics.get("sharpe", float("nan")))
            except (ValueError, RuntimeError) as e:
                log.warning("Strategy %s on %s raised: %s", strat.name, sym, e)
                sharpe = float("nan")
            per_sym_sharpes.append(
                PerSymbolStrategySharpe(symbol=sym, strategy=strat.name, sharpe=sharpe)
            )
            flat_sharpes.append(
                StrategySharpe(name=f"{sym}/{strat.name}", sharpe=sharpe)
            )

    # (b) ensemble on the full portfolio.
    ensemble = build_default_ensemble()
    missing_aux = any(
        portfolio.bars[s].funding is None or portfolio.bars[s].metrics is None
        for s in symbols
    )
    if missing_aux:
        log.warning(
            "Skipping ensemble/regime/stress — funding/metrics missing for some "
            "symbols (download incomplete?)"
        )
        headline = EnsembleHeadline(cagr=float("nan"), mdd=float("nan"))
        ens_metrics: dict[str, float] = {}
        regime_evals: tuple[RegimeEvaluation, ...] = ()
        stress_results = ()
    else:
        try:
            strategies: dict[str, Strategy] = {sym: ensemble for sym in symbols}
            params = {sym: dict(ensemble.default_params) for sym in symbols}
            ens_result = engine.run(
                strategies=strategies,
                portfolio=portfolio,
                params=params,
                costs=costs,
                symbol_specs=symbol_specs,
                initial_cash=initial_cash,
            )
            ens_metrics = dict(
                compute_metrics(
                    ens_result.equity_curve,
                    ens_result.trades,
                    bars_per_year=bars_per_year,
                )
            )
            headline = EnsembleHeadline(
                cagr=float(ens_metrics.get("cagr", float("nan"))),
                mdd=abs(float(ens_metrics.get("mdd", float("nan")))),
            )

            regime_evals = _evaluate_regimes_portfolio(
                ensemble=ensemble,
                portfolio=portfolio,
                engine=engine,
                costs=costs,
                symbol_specs=symbol_specs,
                initial_cash=initial_cash,
                bars_per_year=bars_per_year,
            )

            stress_results = run_stress_suite(
                strategies=strategies,
                portfolio=portfolio,
                engine=engine,
                rules=rules,
                costs=costs,
                symbol_specs=symbol_specs,
                initial_cash=initial_cash,
                params=params,
            )
        except (ValueError, RuntimeError) as e:
            log.warning("Ensemble raised: %s", e)
            headline = EnsembleHeadline(cagr=float("nan"), mdd=float("nan"))
            ens_metrics = {}
            regime_evals = ()
            stress_results = ()

    report = compose_phase3_report(
        strategy_sharpes=flat_sharpes,
        ensemble_headline=headline,
        regime_evaluations=regime_evals,
        stress_results=stress_results,
    )

    total_bars = sum(len(portfolio.bars[s].ohlcv) for s in symbols)
    return {
        "symbols": symbols,
        "weights": weights,
        "interval": interval,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "bars": int(total_bars),
        "strategy_sharpes": [asdict(s) for s in per_sym_sharpes],
        "ensemble": {
            "cagr": headline.cagr,
            "mdd": headline.mdd,
            "total_trades": int(ens_metrics.get("total_trades", 0)),
        },
        "regimes": [
            {
                "label": e.window.label,
                "family": e.window.name,
                "bars": e.bars,
                "total_return": e.total_return,
                "sharpe": e.sharpe,
                "mdd": e.mdd,
                "break_even": bool(e.break_even),
            }
            for e in regime_evals
        ],
        "stress": [
            {
                "label": s.window.label,
                "worst_daily_return": s.worst_daily_return,
                "raw_halts": [h.reason for h in s.raw_halts],
                "daily_limit_respected": bool(s.daily_limit_respected),
            }
            for s in stress_results
        ],
        "gate": {
            "overall_pass": report.overall_pass,
            "strategy_sharpe_pass": report.strategy_sharpe_pass,
            "strategy_sharpe_failures": list(report.strategy_sharpe_failures),
            "ensemble_headline_pass": report.ensemble_headline_pass,
            "regimes_pass": report.regimes_pass,
            "failing_regime_labels": list(report.failing_regime_labels),
            "stress_pass": report.stress_pass,
            "failing_stress_labels": list(report.failing_stress_labels),
        },
        "summary": report.summary_lines(),
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 3 Go/No-Go gate runner (portfolio)")
    p.add_argument(
        "--symbols",
        nargs="+",
        default=["BTCUSDT"],
        help="one or more symbols (default: BTCUSDT)",
    )
    p.add_argument(
        "--weights",
        default=None,
        help='JSON object mapping symbol->weight (default: equal weights)',
    )
    p.add_argument("--interval", choices=["1m", "15m"], default="15m")
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (exclusive)")
    p.add_argument("--initial-cash", default="10000")
    p.add_argument(
        "--out",
        default=None,
        help="output path: JSON file (single file regardless of symbol count)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC")
    initial_cash = Decimal(args.initial_cash)
    symbols: list[str] = list(args.symbols)
    weights: dict[str, float] | None
    if args.weights:
        weights = {str(k): float(v) for k, v in json.loads(args.weights).items()}
    else:
        weights = None

    try:
        out = run_gate(
            symbols=symbols,
            interval=args.interval,
            start=start,
            end=end,
            weights=weights,
            initial_cash=initial_cash,
        )
    except Exception as e:  # noqa: BLE001 — surface as JSON error
        log.warning("run_gate failed: %s", e)
        out = {"symbols": symbols, "error": str(e)}

    text = json.dumps(out, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        for line in out.get("summary", []):
            print(line)
        print(f"\nFull report: {args.out}")
    else:
        print(text)
    return 0 if out.get("gate", {}).get("overall_pass") else 1


if __name__ == "__main__":
    sys.exit(main())
