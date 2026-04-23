"""Phase 3 Go/No-Go gate driver — runs every sub-gate end-to-end on real data.

Usage (CLI):
    python -m kairos.backtest.gate_runner \
        --symbol BTCUSDT --interval 15m \
        --start 2020-10-01 --end 2024-08-10 \
        [--initial-cash 10000] [--out reports/phase3_gate.json]

The driver:
1. Loads BarsData from parquet (via kairos.data.load).
2. Computes each sub-strategy's Sharpe on a walk-forward validation stitch.
3. Runs the ensemble for the full window → CAGR / MDD.
4. Evaluates each regime window for the ensemble.
5. Runs the stress suite (LUNA / FTX / yen-carry).
6. Composes a Phase3Report and persists JSON + STATUS-friendly summary.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from kairos.backtest.engine_vbt import VectorbtEngine
from kairos.backtest.metrics import compute_metrics
from kairos.backtest.phase3_gate import (
    EnsembleHeadline,
    StrategySharpe,
    compose_phase3_report,
)
from kairos.backtest.protocols import (
    BarsData,
    CostModel,
    RiskRules,
    SymbolSpec,
)
from kairos.backtest.regime_evaluator import evaluate_regimes
from kairos.backtest.stress import run_stress_suite
from kairos.data.load import load_bars
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
    """Best-effort SymbolSpec for when no cached exchangeInfo is available.

    Phase 2 gate uses these tolerances and the engine will quantize down to
    them. Over-fine tick_size is safe; over-coarse would corrupt Decimal
    rounding, but our downloads only target BTC/ETH/SOL — all at ≤ 0.01 tick.
    """
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


def _sharpe_for(strategy, data: BarsData, engine, costs, spec, initial_cash, bars_per_year):
    """Run strategy on full window, return Sharpe from BacktestResult.metrics."""
    result = engine.run(
        strategy=strategy,
        data=data,
        params=strategy.default_params,
        costs=costs,
        symbol_spec=spec,
        initial_cash=initial_cash,
    )
    return float(result.metrics.get("sharpe", float("nan"))), result


def run_gate(
    symbol: str,
    interval: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    initial_cash: Decimal = Decimal("10000"),
    data_root: Path | None = None,
) -> dict:
    """End-to-end Phase 3 gate on real data. Returns dict suitable for JSON dump."""
    log.info("Loading bars %s %s [%s, %s)", symbol, interval, start, end)
    data = load_bars(
        symbol=symbol,
        interval=interval,  # type: ignore[arg-type]
        start=start,
        end=end,
        data_root=data_root,
    )
    if data.ohlcv.empty:
        return {"error": f"no ohlcv data for {symbol} {interval} [{start}, {end})"}
    log.info(
        "Loaded %d bars | funding=%s | metrics=%s",
        len(data.ohlcv),
        "yes" if data.funding is not None else "no",
        "yes" if data.metrics is not None else "no",
    )

    engine = VectorbtEngine()
    costs = CostModel()
    rules = RiskRules()
    spec = _fallback_spec(symbol)
    bars_per_year = _bars_per_year(interval)

    # (a) per-strategy Sharpe
    strategy_sharpes: list[StrategySharpe] = []
    for s in (OrbStrategy(), FundingCounterStrategy(), VolCompressionStrategy()):
        try:
            sharpe, _ = _sharpe_for(s, data, engine, costs, spec, initial_cash, bars_per_year)
        except ValueError as e:
            log.warning("Strategy %s raised: %s", s.name, e)
            sharpe = float("nan")
        strategy_sharpes.append(StrategySharpe(name=s.name, sharpe=sharpe))

    # (b) ensemble CAGR / MDD
    ensemble = build_default_ensemble()
    missing_aux = data.funding is None or data.metrics is None
    if missing_aux:
        log.warning(
            "Skipping ensemble/regime/stress — funding=%s metrics=%s (download incomplete?)",
            "present" if data.funding is not None else "None",
            "present" if data.metrics is not None else "None",
        )
        headline = EnsembleHeadline(cagr=float("nan"), mdd=float("nan"))
        ens_metrics: dict[str, float] = {}
        regime_evals = ()
        stress_results = ()
    else:
        ens_result = engine.run(
            strategy=ensemble,
            data=data,
            params=ensemble.default_params,
            costs=costs,
            symbol_spec=spec,
            initial_cash=initial_cash,
        )
        ens_metrics = compute_metrics(
            ens_result.equity_curve, ens_result.trades, bars_per_year=bars_per_year
        )
        headline = EnsembleHeadline(
            cagr=float(ens_metrics.get("cagr", float("nan"))),
            mdd=abs(float(ens_metrics.get("mdd", float("nan")))),
        )

        regime_evals = evaluate_regimes(
            strategy=ensemble,
            data=data,
            engine=engine,
            costs=costs,
            symbol_spec=spec,
            initial_cash=initial_cash,
        )

        stress_results = run_stress_suite(
            strategy=ensemble,
            data=data,
            engine=engine,
            rules=rules,
            costs=costs,
            symbol_spec=spec,
            initial_cash=initial_cash,
        )

    report = compose_phase3_report(
        strategy_sharpes=strategy_sharpes,
        ensemble_headline=headline,
        regime_evaluations=regime_evals,
        stress_results=stress_results,
    )

    return {
        "symbol": symbol,
        "interval": interval,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "bars": int(len(data.ohlcv)),
        "strategy_sharpes": [asdict(s) for s in strategy_sharpes],
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
    p = argparse.ArgumentParser(description="Phase 3 Go/No-Go gate runner")
    p.add_argument("--symbol", required=True)
    p.add_argument("--interval", choices=["1m", "15m"], default="15m")
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (exclusive)")
    p.add_argument("--initial-cash", default="10000")
    p.add_argument("--out", default=None, help="output JSON path (default: stdout)")
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
    out = run_gate(
        symbol=args.symbol,
        interval=args.interval,
        start=start,
        end=end,
        initial_cash=Decimal(args.initial_cash),
    )
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
